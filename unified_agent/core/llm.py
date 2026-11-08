from __future__ import annotations

import json
import os
import sys as _sys
import threading
import time
from contextvars import ContextVar
from typing import Any, Callable, Optional

import requests

_TOKEN_SINK: ContextVar[Optional[Callable[[str], None]]] = ContextVar(
    "unified_llm_token_sink", default=None)

_THINK_SINK: ContextVar[Optional[Callable[[str], None]]] = ContextVar(
    "unified_llm_think_sink", default=None)

_TENANT: ContextVar[int] = ContextVar("unified_llm_tenant", default=0)

_USER: ContextVar[int] = ContextVar("unified_llm_user", default=0)

_ROUTE_TTL_SEC = float(os.getenv("UNIFIED_LLM_ROUTE_TTL", "10"))
_ROUTE_CACHE: dict[tuple[int, int], tuple[float, dict | None]] = {}
_ROUTE_LOCK = threading.Lock()

API_URL = os.getenv(
    "UNIFIED_LLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
MODEL_NAME = os.getenv("UNIFIED_LLM_MODEL", "glm-5.3-flashx")
THINKING_TYPE = os.getenv("UNIFIED_LLM_THINKING", "enabled")
THINKING_LEVEL = os.getenv("UNIFIED_LLM_THINKING_LEVEL", "low")


def _api_key() -> str:
    key = os.getenv("GLM_API_KEY")
    if not key:
        raise RuntimeError("缺少环境变量 GLM_API_KEY（unified_agent LLM 调用无法鉴权）")
    return key



def bind_tenant(tenant_id: Any) -> None:
    try:
        _TENANT.set(max(0, int(tenant_id)))
    except (TypeError, ValueError):
        _TENANT.set(0)


def bind_user(user_id: Any) -> None:
    try:
        _USER.set(max(0, int(user_id)))
    except (TypeError, ValueError):
        _USER.set(0)


def _join_url(base_url: str, path: str | None) -> str:
    base = (base_url or "").strip().rstrip("/")
    tail = (path or "/chat/completions").strip()
    if not tail.startswith("/"):
        tail = "/" + tail
    if base.endswith(tail):
        return base
    return base + tail


def _load_route(tenant_id: int, user_id: int = 0) -> dict | None:
    try:
        from sqlalchemy import select

        from allfusion_auth.db import get_engine
        from allfusion_auth.models import model_provider, model_route, user_model_pref

        with get_engine().connect() as conn:
            allowed = select(
                model_route.c.model, model_route.c.max_tokens,
                model_route.c.temperature,
                model_provider.c.base_url, model_provider.c.completions_path,
                model_provider.c.api_key_cipher, model_provider.c.key_version,
            ).select_from(model_route.join(
                model_provider,
                model_provider.c.id == model_route.c.provider_id,
            )).where(
                model_route.c.tenant_id == tenant_id,
                model_route.c.purpose == "chat",
                model_route.c.enabled == 1,
                model_provider.c.enabled == 1,
                model_provider.c.api_key_cipher.isnot(None),
            )
            row = None
            if user_id > 0:
                try:
                    pref = conn.execute(select(
                        user_model_pref.c.provider_id, user_model_pref.c.model,
                    ).where(
                        user_model_pref.c.tenant_id == tenant_id,
                        user_model_pref.c.user_id == user_id,
                        user_model_pref.c.purpose == "chat",
                    )).first()
                except Exception:  # noqa: BLE001 — 表缺失/查询失败 → 忽略偏好
                    pref = None
                if pref is not None:
                    row = conn.execute(allowed.where(
                        model_route.c.provider_id == pref.provider_id,
                        model_route.c.model == pref.model,
                    )).first()
            if row is None:
                row = conn.execute(allowed.order_by(
                    model_route.c.priority.asc(),
                    model_route.c.fallback_order.asc(),
                )).first()
    except Exception:  # noqa: BLE001 — 库/配置不可用时安静回退 env
        return None
    if row is None or not row.api_key_cipher:
        return None
    return {
        "api_url": _join_url(row.base_url, row.completions_path),
        "model": row.model,
        "cipher": bytes(row.api_key_cipher),
        "key_version": int(row.key_version or 1),
        "max_tokens": int(row.max_tokens) if row.max_tokens else None,
        "temperature": float(row.temperature) if row.temperature is not None else None,
    }


def _select_route(tenant_id: int, user_id: int = 0) -> dict | None:
    key = (tenant_id, user_id)
    now = time.monotonic()
    with _ROUTE_LOCK:
        cached = _ROUTE_CACHE.get(key)
        if cached is not None and now - cached[0] < _ROUTE_TTL_SEC:
            return cached[1]
    data = _load_route(tenant_id, user_id)
    with _ROUTE_LOCK:
        _ROUTE_CACHE[key] = (now, data)
    return data


def _resolve() -> dict:
    tenant = _TENANT.get()
    if tenant > 0:
        route = _select_route(tenant, _USER.get())
        if route:
            try:
                from allfusion_auth.crypto import decrypt_secret

                key = decrypt_secret(route["cipher"], route["key_version"])
            except Exception:  # noqa: BLE001 — 解密失败等同未配置
                key = ""
            if key:
                return {**route, "api_key": key, "from_env": False}
    return {"api_url": API_URL, "model": MODEL_NAME, "api_key": _api_key(),
            "max_tokens": None, "temperature": None, "from_env": True}


def qa_route() -> dict | None:
    try:
        route = _resolve()
    except Exception:  # noqa: BLE001 — 缺 env Key 等不得阻断车道；qa 侧自行报错
        return None
    if route.get("from_env"):
        return None
    return {"api_url": route["api_url"], "model": route["model"],
            "api_key": route["api_key"],
            "max_tokens": route.get("max_tokens"),
            "temperature": route.get("temperature")}


def _extract_json(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.splitlines()
        if len(lines) >= 3:
            clean = "\n".join(lines[1:-1]).strip()
        else:
            clean = clean.replace("```", "").strip()
    data = json.loads(clean)                      
    if not isinstance(data, dict):
        raise ValueError(f"LLM 返回的 JSON 根不是对象：{type(data).__name__}")
    return data


def _payload(system: str, user: str, *, max_tokens: int, stream: bool,
             route: dict, thinking: bool | None = None,
             tools: list[dict] | None = None,
             tool_choice: Any | None = None,
             messages: list[dict] | None = None) -> dict:
    model = route.get("model") or MODEL_NAME
    effective_tokens = max_tokens
    if route.get("max_tokens"):
        effective_tokens = min(max_tokens, int(route["max_tokens"]))

    if messages is not None:
        msgs = messages
    else:
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    payload = {
        "model": model,
        "messages": msgs,
        "stream": stream,
        "temperature": (route.get("temperature")
                        if route.get("temperature") is not None else 0.2),
        "max_tokens": effective_tokens,
    }
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
    if model.startswith("glm-5"):
        if thinking is False:
            payload["thinking"] = {"type": "enabled", "level": "low"}
        else:
            payload["thinking"] = {"type": THINKING_TYPE, "level": THINKING_LEVEL}
    elif thinking is False:
        payload["enable_thinking"] = False
    return payload


def _raise_for_status(resp) -> None:
    if resp.status_code < 400:
        return
    try:
        body = (resp.text or "")[:1000]
    except Exception:  # noqa: BLE001 — 读不到 body 也不能吞掉状态码
        body = ""
    raise RuntimeError(f"LLM HTTP {resp.status_code} {resp.reason}: {body}")


def _chat_stream(system: str, user: str, *, max_tokens: int,
                 sink: Callable[[str], None], route: dict,
                 thinking: bool | None = None) -> str:
    think_sink = _THINK_SINK.get()
    parts: list[str] = []
    with requests.post(
        route["api_url"],
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {route['api_key']}"},
        json=_payload(system, user, max_tokens=max_tokens, stream=True, route=route,
                      thinking=thinking),
        timeout=120,
        stream=True,
    ) as resp:
        _raise_for_status(resp)
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            data = raw[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            choice = (obj.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            reason = delta.get("reasoning_content") or delta.get("reasoning") or ""
            if reason and think_sink is not None:
                think_sink(reason)
            piece = delta.get("content") or ""
            if piece:
                parts.append(piece)
                sink(piece)
    return "".join(parts).strip()


def _chat_body(system: str, user: str, *, max_tokens: int, stream: bool,
               route: dict, thinking: bool | None = None,
               timeout: int = 120) -> str:
    sink = _TOKEN_SINK.get() if stream else None
    if sink is not None:
        try:
            streamed = _chat_stream(system, user, max_tokens=max_tokens,
                                    sink=sink, route=route, thinking=thinking)
            if streamed:
                return streamed
        except Exception:
            pass  

    for attempt in range(2):
        payload = _payload(system, user,
                           max_tokens=max_tokens * (2 ** attempt), stream=False,
                           route=route, thinking=thinking)
        resp = requests.post(
            route["api_url"],
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {route['api_key']}"},
            json=payload,
            timeout=timeout,
        )
        _raise_for_status(resp)
        choice = resp.json()["choices"][0]
        content = choice["message"].get("content", "")
        if content:
            return content
        if choice.get("finish_reason") != "length":
            break  
    return ""


def _chat_body_with_tools(
    system: str, user: str, *, max_tokens: int, route: dict,
    thinking: bool | None = None,
    tools: list[dict] | None = None,
    tool_choice: Any | None = None,
    tool_executor: Callable[[str, dict], str] | None = None,
    max_tool_rounds: int = 3,
    timeout: int = 120,
) -> str:
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    for _round in range(max_tool_rounds + 1):
        payload = _payload(
            system="", user="",  
            max_tokens=max_tokens * (2 if _round == 0 else 1),  
            stream=False, route=route, thinking=thinking,
            tools=tools, tool_choice=tool_choice, messages=messages,
        )
        resp = requests.post(
            route["api_url"],
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {route['api_key']}"},
            json=payload, timeout=timeout,
        )
        _raise_for_status(resp)
        choice = resp.json()["choices"][0]
        msg = choice["message"]

        model_content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            return model_content.strip()

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": model_content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        if tool_executor is None:
            return model_content.strip()

        for tc in tool_calls:
            tc_id = tc.get("id", "")
            func = tc.get("function", {})
            fname = func.get("name", "")
            fargs_str = func.get("arguments", "{}")
            try:
                fargs = json.loads(fargs_str) if isinstance(fargs_str, str) else (fargs_str or {})
            except (ValueError, TypeError):
                fargs = {}
            try:
                result = tool_executor(fname, fargs)
            except Exception as e:  # noqa: BLE001 — 工具执行失败也要把错误塞回，让 LLM 知道
                result = f"[工具执行失败：{type(e).__name__}: {e}]"
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": str(result),
            })

    last_assistant = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
    if last_assistant:
        return (last_assistant.get("content") or "").strip()
    return ""


def _chat(system: str, user: str, *, max_tokens: int, stream: bool = False,
          thinking: bool | None = None,
          tools: list[dict] | None = None,
          tool_choice: Any | None = None,
          tool_executor: Callable[[str, dict], str] | None = None,
          timeout: int = 120) -> str:
    route = _resolve()
    started = time.perf_counter()
    text = ""
    status, code = "ok", ""
    try:
        if tools:
            if stream:
                print("[llm] tools 与 stream 互斥，降级为非流式", file=_sys.stderr)
            text = _chat_body_with_tools(
                system, user, max_tokens=max_tokens, route=route,
                thinking=thinking, tools=tools, tool_choice=tool_choice,
                tool_executor=tool_executor, timeout=timeout,
            )
        else:
            text = _chat_body(system, user, max_tokens=max_tokens,
                              stream=stream, route=route, thinking=thinking,
                              timeout=timeout)
        return text
    except Exception as e:
        status, code = "error", type(e).__name__
        raise
    finally:
        try:
            from unified_agent.core.trace_sink import record
            record(
                node="llm", kind="llm", status=status, error_code=code,
                model=str(route.get("model") or MODEL_NAME),
                prompt_tokens=(len(system) + len(user)) // 4,
                completion_tokens=len(text) // 4,
                duration_ms=int((time.perf_counter() - started) * 1000),
                attrs={"prompt_len": len(system) + len(user),
                       "answer_len": len(text),
                       "fallback_env": bool(route.get("from_env")),
                       "tool_call": bool(tools)},
            )
        except Exception:  # noqa: BLE001 旁路纪律
            pass


def ask_llm_json(system: str, user: str, *, max_tokens: int = 4000,
                 thinking: bool | None = None,
                 timeout: int = 120) -> dict[str, Any]:
    return _extract_json(_chat(system, user, max_tokens=max_tokens,
                               thinking=thinking, timeout=timeout))


def ask_llm_text(system: str, user: str, *, max_tokens: int = 4000,
                 stream: bool = False, thinking: bool | None = None,
                 tools: list[dict] | None = None,
                 tool_choice: Any | None = None,
                 tool_executor: Callable[[str, dict], str] | None = None) -> str:
    return _chat(system, user, max_tokens=max_tokens, stream=stream,
                 thinking=thinking, tools=tools, tool_choice=tool_choice,
                 tool_executor=tool_executor).strip()
