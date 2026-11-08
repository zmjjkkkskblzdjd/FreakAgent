
from __future__ import annotations

import contextlib
import contextvars
import os
import sys
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:  
    from config import LLMConfig
    from langchain_openai import ChatOpenAI


def _load_env_before_langchain() -> None:
    from config import get_app_config  

    get_app_config()


_load_env_before_langchain()

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from openai import (  # noqa: E402,F401  仅供 _error_kind 分类用
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)

from data_analyze_agent.core.json_utils import parse_json_safe  # noqa: E402

__all__ = [
    "create_llm", "create_llm_for_tier", "call_llm", "ask_llm_json", "ask_llm_text",
    "new_usage_acc", "usage_scope", "snapshot_usage", "LLM_JSON_MAX_TOKENS",
    "bind_llm_budget", "reset_llm_budget",
]

LLM_JSON_MAX_TOKENS = 8000

_LLM_CACHE: dict[tuple[str, str, float, int, int], ChatOpenAI] = {}

_USAGE_ACC: contextvars.ContextVar[dict[str, dict[str, list[int]]] | None] = (
    contextvars.ContextVar("llm_usage_acc", default=None))

_LLM_BUDGET: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("da_llm_budget", default=None))


def bind_llm_budget(*, deadline: float | None = None,
                    max_retries: int | None = None) -> contextvars.Token:
    return _LLM_BUDGET.set({"deadline": deadline, "max_retries": max_retries})


def reset_llm_budget(token: contextvars.Token) -> None:
    with contextlib.suppress(ValueError):
        _LLM_BUDGET.reset(token)

_TIER_NAMES = ("chitchat", "lite", "deep", "clarify")


def create_llm_for_tier(tier: str, *, temperature: float | None = None) -> ChatOpenAI:
    if tier not in _TIER_NAMES:
        raise ValueError(f"未知模型分档 {tier!r}，合法值：{_TIER_NAMES}")
    from config import get_app_config  

    app_cfg = get_app_config()
    model_name = getattr(app_cfg.tier_models, tier)
    tier_cfg = replace(app_cfg.llm, model=model_name)
    return create_llm(tier_cfg, temperature=temperature)


def new_usage_acc() -> dict[str, dict[str, list[int]]]:
    return {}


@contextlib.contextmanager
def usage_scope(acc: dict[str, dict[str, list[int]]] | None) -> Iterator[dict]:
    if acc is None:
        yield {}
        return
    token = _USAGE_ACC.set(acc)
    try:
        yield acc
    finally:
        _USAGE_ACC.reset(token)


def snapshot_usage(acc: dict[str, dict[str, list[int]]]) -> dict[str, dict[str, int]]:
    return {m: {"in": p[0], "out": p[1]} for m, p in acc.items()}


def _record_usage(client: Any, tokens_in: int, tokens_out: int) -> None:
    acc = _USAGE_ACC.get()
    if acc is None or (tokens_in == 0 and tokens_out == 0):
        return
    model = (
        getattr(client, "model_name", None)
        or getattr(getattr(client, "bound", None), "model_name", None)
        or "unknown"
    )
    pair = acc.setdefault(model, [0, 0])
    pair[0] += tokens_in
    pair[1] += tokens_out


from typing import Callable as _Callable  # noqa: E402
_ROUTE_RESOLVER: _Callable[[], dict[str, Any] | None] | None = None


def set_route_resolver(
    resolver: _Callable[[], dict[str, Any] | None] | None,
) -> None:
    global _ROUTE_RESOLVER
    _ROUTE_RESOLVER = resolver


def _route() -> dict[str, Any] | None:
    resolver = _ROUTE_RESOLVER
    if resolver is None:
        return None
    try:
        r = resolver()
    except Exception as exc:  # noqa: BLE001 — 路由失败不得阻断车道
        print(f"[llm] 路由解析失败，回退 env：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None
    if not r or not r.get("api_key") or not r.get("api_url"):
        return None
    return r


def create_llm(cfg: LLMConfig | None = None, *, temperature: float | None = None) -> ChatOpenAI:
    if cfg is None:
        from config import get_app_config  

        cfg = get_app_config().llm

    temp = cfg.temperature if temperature is None else temperature

    route = _route()
    if route is not None:
        model = route.get("model") or cfg.model
        base_url = (route.get("api_url") or cfg.base_url).rstrip("/")
        api_key = route["api_key"]
        if route.get("temperature") is not None:
            temp = float(route["temperature"])
    else:
        model = cfg.model
        base_url = cfg.base_url.rstrip("/")
        api_key = os.getenv(cfg.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"缺少环境变量 {cfg.api_key_env}，请先在 PowerShell 中执行："
                f' $env:{cfg.api_key_env}="你的 API Key" 后重试。'
            )

    extra_body = (
        {"enable_thinking": False} if "dashscope" in base_url.lower() else None
    )

    cache_key = (model, base_url, temp, cfg.timeout, cfg.max_retries)
    budget = _LLM_BUDGET.get()
    if budget is not None:
        timeout = float(cfg.timeout)
        if budget.get("deadline") is not None:
            timeout = max(1.0, min(timeout, float(budget["deadline"]) - time.monotonic()))
        retries = (int(budget["max_retries"])
                   if budget.get("max_retries") is not None else cfg.max_retries)
        return ChatOpenAI(
            model=model, base_url=base_url, api_key=api_key, temperature=temp,
            timeout=timeout, max_retries=retries, extra_body=extra_body,
        )

    cached = _LLM_CACHE.get(cache_key)
    if cached is not None:
        return cached

    llm = ChatOpenAI(
        model=model,
        base_url=base_url,            
        api_key=api_key,
        temperature=temp,
        timeout=cfg.timeout,
        max_retries=cfg.max_retries,
        extra_body=extra_body,
    )
    _LLM_CACHE[cache_key] = llm
    return llm


def _error_kind(exc: Exception) -> str:
    if isinstance(exc, APITimeoutError):
        return "timeout"
    if isinstance(exc, RateLimitError):
        return "rate_limit"
    if isinstance(exc, APIConnectionError):
        return "connection"
    if isinstance(exc, APIStatusError):
        return "api_error"
    return "other"


def call_llm(
    messages: list[BaseMessage],
    *,
    llm: ChatOpenAI | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    try:
        client = llm if llm is not None else create_llm(temperature=temperature)
        if max_tokens is not None:
            client = client.bind(max_tokens=max_tokens)
        resp = client.invoke(messages)
    except Exception as exc:  # noqa: BLE001 —— 统一包装，见 §2.2
        return {"ok": False, "error": f"LLM 调用失败：{exc}", "kind": _error_kind(exc)}

    content = resp.content
    if not isinstance(content, str):
        content = "" if content is None else str(content)
    usage = getattr(resp, "usage_metadata", None) or {}
    tokens_in = int(usage.get("input_tokens", 0) or 0)
    tokens_out = int(usage.get("output_tokens", 0) or 0)
    _record_usage(client, tokens_in, tokens_out)   
    return {
        "ok": True,
        "data": {
            "content": content,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "raw": resp,    
        },
    }


def ask_llm_json(
    system_prompt: str,
    user_content: str,
    *,
    llm: ChatOpenAI | None = None,
    temperature: float | None = None,
    max_tokens: int | None = LLM_JSON_MAX_TOKENS,
) -> dict[str, Any]:
    base: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]
    attempts: list[str] = []
    last_error = ""
    tokens_in = tokens_out = 0

    for attempt in range(2):
        messages = list(base)
        if attempt > 0:  
            messages.append(AIMessage(content=attempts[-1]))
            messages.append(HumanMessage(content=(
                f"你上一次的输出无法解析为 JSON：{last_error}\n"
                "请只输出一个合法的 JSON 对象，不要输出 markdown 标记或解释性文字。"
            )))

        result = call_llm(messages, llm=llm, temperature=temperature, max_tokens=max_tokens)
        if not result["ok"]:
            return result

        data = result["data"]
        attempts.append(data["content"])
        tokens_in += data["tokens_in"]
        tokens_out += data["tokens_out"]
        try:
            parsed = parse_json_safe(data["content"])
        except ValueError as exc:   
            last_error = str(exc)
            continue

        return {"ok": True, "data": parsed, "tokens_in": tokens_in, "tokens_out": tokens_out}

    return {
        "ok": False,
        "error": f"经过 2 次尝试，模型仍未能返回合法 JSON 对象；最后一次错误：{last_error}",
        "kind": "llm_json",
        "attempts": [a[:200] for a in attempts],    
    }


def ask_llm_text(
    system_prompt: str,
    user_content: str,
    *,
    llm: ChatOpenAI | None = None,
    temperature: float | None = None,
    max_tokens: int | None = 800,
) -> dict[str, Any]:
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]
    return call_llm(messages, llm=llm, temperature=temperature, max_tokens=max_tokens)
