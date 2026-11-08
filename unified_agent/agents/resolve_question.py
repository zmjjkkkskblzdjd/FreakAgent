from __future__ import annotations

from unified_agent.core.llm import ask_llm_json
from unified_agent.core.state import UnifiedState
from unified_agent.observability.tracer import emit_event
from unified_agent.prompts import load_prompt

_SYSTEM, _USER_TPL = load_prompt("resolve_question")


def _digest(history) -> str:
    lines = []
    for m in (history or [])[-4:]:
        lines.append(f"- {getattr(m, 'content', m)}"[:110])
    return "\n".join(lines) if lines else "（无历史对话）"


def resolve(state: UnifiedState) -> dict:
    question = state.get("question", "")
    user = _USER_TPL.format(question=question, history_digest=_digest(state.get("history")))
    try:
        d = ask_llm_json(_SYSTEM, user)
    except Exception:
        d = None
    resolved = str((d or {}).get("resolved_question") or "").strip()
    if not resolved or len(resolved) > 500:
        resolved = question
    if resolved != question:
        emit_event("resolve", "resolve_question", "ok",
                   {"question": question[:200], "resolved_question": resolved[:200]},
                   "", state.get("session_id", "") or "")
    return {"resolved_question": resolved}
