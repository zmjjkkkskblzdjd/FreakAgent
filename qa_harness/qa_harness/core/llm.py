from __future__ import annotations

import os
import sys
import time
from typing import Any, Callable

import requests
import json

DEFAULT_API_URL = os.getenv(
    "QA_LLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
DEFAULT_MODEL_NAME = os.getenv("QA_LLM_MODEL", "glm-5.3-flashx")
DEFAULT_MAX_COMPLETION_TOKENS = 4000  

MODEL_FALLBACK_CHAIN: dict[str, str | None] = {
    "glm-5.3-flashx": "glm-4.6v",
    "glm-5.3-flash": "glm-4.6v",
    "glm-4.5-air": "glm-4.6v",
    "glm-4.6v": "glm-4.7",
    "glm-4.7": None,
}

_CONTEXT_LIMIT_KEYWORDS = (
    "context", "token", "长度", "上下文", "prompt", "maximum",
)

_QUOTA_EXHAUSTED_CODE = "1113"

_EXHAUSTED_MODELS: set[str] = set()

LLM_TIMEOUT_S = float(os.getenv("QA_LLM_TIMEOUT", "150"))
LLM_MAX_RETRIES = int(os.getenv("QA_LLM_MAX_RETRIES", "5"))   


def _max_retries() -> int:
    try:
        return max(0, int(os.getenv("QA_LLM_MAX_RETRIES", str(LLM_MAX_RETRIES))))
    except ValueError:
        return LLM_MAX_RETRIES


LLM_RETRY_BASE_DELAY = 2.0   
LLM_RETRY_BACKOFF = 2.0      
RATE_LIMIT_DELAY = 10.0      
RATE_LIMIT_BACKOFF = 2.0     

_ROUTE_RESOLVER: Callable[[], dict[str, Any] | None] | None = None


def set_route_resolver(resolver: Callable[[], dict[str, Any] | None] | None) -> None:
    global _ROUTE_RESOLVER
    _ROUTE_RESOLVER = resolver


_TRACE_HOOK: Callable[[dict[str, Any]], None] | None = None


def set_trace_hook(hook: Callable[[dict[str, Any]], None] | None) -> None:
    global _TRACE_HOOK
    _TRACE_HOOK = hook


def emit_trace(payload: dict[str, Any]) -> None:
    hook = _TRACE_HOOK
    if hook is None:
        return
    try:
        hook(payload)
    except Exception:  # noqa: BLE001 旁路纪律
        pass


def _routed() -> dict[str, Any] | None:
    resolver = _ROUTE_RESOLVER
    if resolver is None:
        return None
    try:
        route = resolver()
    except Exception as exc:  # noqa: BLE001 — 路由失败不得阻断车道，但必须留痕
        print(f"[llm] 路由解析失败，回退 env：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None
    if not route or not route.get("api_key"):
        return None
    return route


def _get_error_code(response) -> str:
    try:
        return str(response.json().get("error", {}).get("code", ""))
    except Exception:
        return ""


def _is_retryable_error(e: Exception) -> bool:
    if isinstance(e, (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
    )):
        return True
    if isinstance(e, requests.exceptions.HTTPError):
        status = getattr(e.response, "status_code", None)
        if status == 429 and _get_error_code(e.response) == _QUOTA_EXHAUSTED_CODE:
            return False
        if status is not None and (status >= 500 or status == 429):
            return True
    if isinstance(e, (ConnectionResetError, ConnectionAbortedError, OSError)):
        return True
    return False

def _is_context_limit_error(response) -> bool:
    if response.status_code != 400:
        return False
    try:
        body = response.json()
        msg = str(body.get("error", {}).get("message", ""))
    except Exception:
        msg = response.text or ""
    msg_lower = msg.lower()
    return any(kw in msg_lower for kw in _CONTEXT_LIMIT_KEYWORDS)

def _is_quota_exhausted(response) -> bool:
    return response.status_code == 429 and _get_error_code(response) == _QUOTA_EXHAUSTED_CODE

def _parse_json_object(text: str) -> dict[str, Any]:
    clean_text = text.strip()
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if len(lines) >= 3:
            clean_text = "\n".join(lines[1:-1]).strip()
        elif len(lines) == 1:
            clean_text = clean_text.replace("```", "").strip()

    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"JSON 根节点不是对象 (dict)，而是 {type(data).__name__}")

    return data


def get_api_key() -> str:
    route = _routed()
    if route:
        return str(route["api_key"])
    api_key = os.getenv("GLM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "缺少环境变量 GLM_API_KEY，请先在 PowerShell 中执行："
            ' $env:GLM_API_KEY="你的 API Key"'
        )
    return api_key


def create_system_message() -> dict[str, str]:

    return {
        "role": "system",
        "content": (
            "你是一个基于小型 Agent Runtime 运行的 ReAct 风格 Agent。"
            "你需要结合用户目标、会话消息、工具结果和运行时状态，自主决定下一步。"
            "如果任务需要外部动作，请调用合适工具。"
            "如果任务已经完成，请直接输出最终自然语言答复。"
            "不要声称工具已执行成功，除非你已经看到了真实的 tool 结果。"
            "回答使用简洁清晰的中文。"
        ),
    }


def call_llm(
    api_key: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    api_url: str = DEFAULT_API_URL,
    model_name: str = DEFAULT_MODEL_NAME,
    max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
    tool_choice: str = "auto",
) -> dict[str, Any]:
    route = _routed()
    if route:
        api_url = route.get("api_url") or api_url
        model_name = route.get("model") or model_name
        api_key = str(route["api_key"])
        if route.get("max_tokens"):
            max_completion_tokens = min(max_completion_tokens,
                                        int(route["max_tokens"]))

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    _call_t0 = time.perf_counter()
    _prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)

    current_model = model_name
    while current_model is not None and current_model in _EXHAUSTED_MODELS:
        print(f"[资源包] {current_model} 此前已耗尽，直接跳过")
        current_model = MODEL_FALLBACK_CHAIN.get(current_model)

    while current_model is not None:
        payload = {
            "model": current_model,
            "messages": messages,
            "stream": False,
            "max_tokens": max_completion_tokens,
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        if current_model.startswith("glm-5"):
            payload["thinking"] = (
                {"type": "disabled"}
                if os.getenv("QA_LLM_THINKING", "").strip().lower() == "disabled"
                else {"type": "enabled", "level": "low"})
        elif os.getenv("QA_LLM_THINKING", "").strip().lower() == "disabled":
            payload["enable_thinking"] = False

        switched: str | None = None

        last_error: Exception | None = None
        max_retries = _max_retries()
        for attempt in range(max_retries + 1):
            _attempt_t0 = time.perf_counter()
            _http_status = 0
            try:
                response = requests.post(
                    api_url,
                    headers=headers,
                    json=payload,
                    timeout=LLM_TIMEOUT_S,
                )
                _http_status = response.status_code
                if response.status_code != 200:
                    if _is_quota_exhausted(response):
                        _EXHAUSTED_MODELS.add(current_model)
                        switched = MODEL_FALLBACK_CHAIN.get(current_model)
                        if switched is not None:
                            print(f"[资源包耗尽] {current_model} 无可用资源包 → 切换到 {switched}")
                            break  
                        print(f"[资源包耗尽] {current_model} 已无其他模型可切换")
                    elif _is_context_limit_error(response):
                        switched = MODEL_FALLBACK_CHAIN.get(current_model)
                        if switched is not None:
                            print(f"[上下文超限] {current_model} 上下文不足 → 切换到 {switched}")
                            break  
                        print(f"[上下文超限] {current_model} 已无更大模型可用")
                    print(f"API 错误 ({response.status_code}): {response.text[:300]}")
                response.raise_for_status()
                _msg = (response.json().get("choices") or [{}])[0].get("message") or {}
                emit_trace({
                    "kind": "llm", "node": "qa_llm.attempt", "status": "ok",
                    "model": current_model,
                    "prompt_tokens": _prompt_chars // 4,
                    "completion_tokens": len(str(_msg.get("content") or "")) // 4,
                    "duration_ms": int((time.perf_counter() - _attempt_t0) * 1000),
                    "attrs": {"attempt": attempt + 1, "max_attempts": max_retries + 1,
                              "http_status": _http_status,
                              "content_len": len(str(_msg.get("content") or "")),
                              "tool_calls": len(_msg.get("tool_calls") or []),
                              "timeout_s": LLM_TIMEOUT_S},
                })
                break  
            except Exception as e:
                last_error = e
                emit_trace({
                    "kind": "llm", "node": "qa_llm.attempt", "status": "error",
                    "error_code": type(e).__name__,
                    "model": current_model,
                    "prompt_tokens": _prompt_chars // 4,
                    "duration_ms": int((time.perf_counter() - _attempt_t0) * 1000),
                    "attrs": {"attempt": attempt + 1, "max_attempts": max_retries + 1,
                              "http_status": _http_status,
                              "timeout_s": LLM_TIMEOUT_S,
                              "retryable": _is_retryable_error(e),
                              "error": str(e)[:200]},
                })
                if not _is_retryable_error(e) or attempt >= max_retries:
                    raise  
                is_429 = isinstance(e, requests.exceptions.HTTPError) and e.response.status_code == 429
                if is_429:
                    delay = RATE_LIMIT_DELAY * (RATE_LIMIT_BACKOFF ** attempt)
                else:
                    delay = LLM_RETRY_BASE_DELAY * (LLM_RETRY_BACKOFF ** attempt)
                print(f"[LLM 重试 {attempt + 1}/{max_retries}] {type(e).__name__}: {e}，{delay:.1f}s 后重试...")
                time.sleep(delay)
        else:
            raise last_error  # type: ignore[misc]

        if switched is not None:
            current_model = switched
            while current_model is not None and current_model in _EXHAUSTED_MODELS:
                print(f"[资源包] {current_model} 此前已耗尽，直接跳过")
                current_model = MODEL_FALLBACK_CHAIN.get(current_model)
            continue

        if response.status_code == 200:
            break
    else:
        emit_trace({
            "kind": "llm", "node": "qa_llm", "status": "error",
            "error_code": "no_available_model", "model": model_name,
            "duration_ms": int((time.perf_counter() - _call_t0) * 1000),
            "attrs": {"start_model": model_name,
                      "exhausted": sorted(_EXHAUSTED_MODELS),
                      "prompt_chars": _prompt_chars},
        })
        raise RuntimeError(
            f"降级链上无可用模型（初始 {model_name!r}，已耗尽: "
            f"{sorted(_EXHAUSTED_MODELS) or '无'}），请检查资源包或补充模型"
        )

    result = response.json()
    _msg = (result.get("choices") or [{}])[0].get("message") or {}
    emit_trace({
        "kind": "llm", "node": "qa_llm", "status": "ok",
        "model": current_model,
        "prompt_tokens": _prompt_chars // 4,
        "completion_tokens": len(str(_msg.get("content") or "")) // 4,
        "duration_ms": int((time.perf_counter() - _call_t0) * 1000),
        "attrs": {"start_model": model_name, "used_model": current_model,
                  "prompt_chars": _prompt_chars, "max_tokens": max_completion_tokens,
                  "has_tools": bool(tools), "tool_choice": tool_choice,
                  "content_len": len(str(_msg.get("content") or "")),
                  "tool_calls": len(_msg.get("tool_calls") or [])},
    })
    return result["choices"][0]["message"]

def build_messages(system_prompt: str, user_content: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

def ask_llm_text(
    system_prompt: str,
    user_content: str,
    *,
    api_key: str | None = None,
    model_name: str = DEFAULT_MODEL_NAME,
) -> str:
    resolved_api_key = api_key or get_api_key()
    result = call_llm(
        api_key=resolved_api_key,
        messages=build_messages(system_prompt, user_content),
        tools=[],
        model_name=model_name,
    )
    return result.get("content", "")



def ask_llm_json(
        system_prompt: str,
        user_content: str,
        *,
        api_key: str | None = None,
        max_completion_tokens: int = 4000,
        model_name: str = DEFAULT_MODEL_NAME,
) -> dict[str, Any]:
    resolved_api_key = api_key or get_api_key()

    attempts_history: list[str] = []
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            messages = build_messages(system_prompt, user_content)

            if attempt > 0 and attempts_history:
                previous_output = attempts_history[-1]
                error_msg = str(last_error) if last_error else "未知解析错误"

                messages.append({"role": "assistant", "content": previous_output})
                correction_prompt = (
                    f"你上一次的输出无法解析为 JSON：{error_msg}。\n"
                    f"请只输出一个合法的 JSON 对象，不要输出其他内容（如 markdown 标记或解释性文字）。"
                )
                messages.append({"role": "user", "content": correction_prompt})

            result = call_llm(
                api_key=resolved_api_key,
                messages=messages,
                tools=[],
                max_completion_tokens=max_completion_tokens,
                model_name=model_name,
            )

            text = result.get("content", "").strip()
            attempts_history.append(text)

            data = _parse_json_object(text)

            return data

        except ValueError as e:
            last_error = e
            continue
        except Exception as e:
            raise e

    error_details = "\n---\n".join([
        f"Attempt {i + 1} Output:\n{out}"
        for i, out in enumerate(attempts_history)
    ])

    raise ValueError(
        f"经过 2 次尝试，模型仍未能返回合法的 JSON 对象。\n"
        f"最后一次错误原因: {last_error}\n"
        f"历史返回内容:\n{error_details}"
    ) from last_error