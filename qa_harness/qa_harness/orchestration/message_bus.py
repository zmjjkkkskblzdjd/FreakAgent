import json
import re
from typing import Any

from core import ROUTES



def safe_invoke(agent: Any, task_summary: str) -> dict[str, Any]:
    try:
        return agent.run(task_summary)
    except (TimeoutError, ConnectionError) as e:
        return {"ok": False, "error": f"网络异常：{type(e).__name__}: {e}"}
    except (json.JSONDecodeError, ValueError) as e:
        return {"ok": False, "error": f"LLM 输出异常：{type(e).__name__}: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"子 Agent 异常：{type(e).__name__}: {e}"}



def validate_output(action: str, data: Any) -> dict[str, Any]:
    if action == "delegate_route":
        if data.route not in ROUTES:
            return {"ok": False, "reason": f"route={data.route!r} 不在合法路由 {ROUTES} 中"}
        if data.route == "kb" and data.tier == "direct":
            return {"ok": False, "reason": "route=kb 不允许 tier=direct（知识库问题不能秒回）"}
        return {"ok": True, "reason": ""}

    if action == "delegate_retrieve":
        if data.escalate and not data.escalate_reason:
            return {"ok": False, "reason": "escalate=True 但 escalate_reason 为空"}
        return {"ok": True, "reason": ""}

    if action == "delegate_answer":
        if data.is_refusal and data.citations:
            return {"ok": False, "reason": "is_refusal=True 但 citations 非空（拒答不能挂引用）"}
        return {"ok": True, "reason": ""}

    if action == "delegate_critic":
        if data.passed and data.issues:
            return {"ok": False, "reason": "passed=True 但 issues 非空（通过不应有问题）"}
        return {"ok": True, "reason": ""}

    return {"ok": True, "reason": ""}



_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk-***"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"), "Bearer ***"),
    (re.compile(r"(?i)(password|api[_-]?key|secret)\s*[=:]\s*\S+"), r"\1=***"),
]


def _sanitize_str(s: str) -> str:
    for pattern, replacement in _PATTERNS:
        s = pattern.sub(replacement, s)
    return s


def sanitize(obj: Any) -> Any:
    if isinstance(obj, str):
        return _sanitize_str(obj)
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj
