from __future__ import annotations

from unified_agent.core.llm import ask_llm_text
from unified_agent.core.state import UnifiedState
from unified_agent.prompts import load_prompt

_SYSTEM, _USER_TPL = load_prompt("chitchat")

_FALLBACK = "你好，我在。有数据查询或 SQL 相关的问题都可以问我～"


def _digest(history: list | None) -> str:
    lines = []
    for m in (history or [])[-4:]:
        content = getattr(m, "content", m) if not isinstance(m, dict) else m.get("content", m)
        lines.append(f"- {str(content)[:110]}")
    return "\n".join(lines) if lines else "（无近期对话）"


def answer(state: UnifiedState) -> dict:
    user = _USER_TPL.format(
        question=state.get("question", ""),
        history_digest=_digest(state.get("history")),
    )
    try:
        text = ask_llm_text(_SYSTEM, user, max_tokens=300, stream=True)
    except Exception:                     # noqa: BLE001 寒暄降级不炸车道
        text = _FALLBACK
    if not text.strip():
        text = _FALLBACK
    return {
        "final_report": {
            "answer_md": text,
            "claims": [],
            "sql": None,
            "citations": [],
            "mode": "chitchat",
            "partial_failures": [],
        },
        "task_status": "done",
    }
