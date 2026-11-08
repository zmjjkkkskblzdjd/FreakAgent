from __future__ import annotations

import logging
import os

from unified_agent.core.state import SCHEMA_VERSION, UnifiedState
from memory_service.field_dict import MEMORY_FIELDS
from memory_service.store import get_store

logger = logging.getLogger(__name__)

_LESSON_TOP_K = 2            
_LESSON_LINE_CAP = 110       
_LESSON_INJECT_MAX = 2       


def _where(state: UnifiedState) -> dict:
    return {"$and": [
        {"$or": [{"memory_type": "general"}, {"memory_type": "rag"}, {"memory_type": "sql"}]},
        {"user_id": state.get("user_id", "")},
        {"tenant_id": state.get("tenant_id", "default")},
        {"schema_version": SCHEMA_VERSION},
        {"status": "active"},
    ]}


def _lesson_where(state: UnifiedState) -> dict:
    return {"$and": [
        {"memory_type": "lesson"},
        {"user_id": state.get("user_id", "")},
        {"tenant_id": state.get("tenant_id", "default")},
        {"schema_version": SCHEMA_VERSION},
        {"status": "active"},
    ]}


def lesson_lines(state: UnifiedState, lanes: tuple[str, ...],
                 limit: int = _LESSON_INJECT_MAX) -> list[str]:
    lines: list[str] = []
    for h in state.get("recalled_memory") or []:
        meta = h.get("metadata") or {}
        if meta.get("memory_type") != "lesson" or meta.get("lane") not in lanes:
            continue
        text = (h.get("text") or "").strip()
        if not text:
            continue
        lines.append(text[:_LESSON_LINE_CAP])
        if len(lines) >= limit:
            break
    return lines


def lesson_block(state: UnifiedState, lanes: tuple[str, ...],
                 limit: int = _LESSON_INJECT_MAX) -> str:
    lines = lesson_lines(state, lanes, limit)
    if not lines:
        return ""
    return "【历史教训（上次同类失败）】\n" + "\n".join(f"- {t}" for t in lines)


def recall(state: UnifiedState) -> dict:
    query = state.get("resolved_question") or state.get("question", "")
    if (os.getenv("UA_MEMORY_RECALL", "1") or "1").strip().lower() in ("0", "false", "no"):
        return {"recalled_memory": []}
    hits: list = []
    try:
        hits = get_store().recall(query, _where(state))
    except Exception as e:
        logger.warning(f"记忆召回失败（忽略，不阻塞问答）: {e}")
    lessons: list = []
    try:
        lessons = get_store().recall(query, _lesson_where(state), top_k=_LESSON_TOP_K)
    except Exception as e:
        logger.warning(f"教训召回失败（忽略，不影响 QA 记忆）: {e}")
    return {"recalled_memory": hits + lessons}
