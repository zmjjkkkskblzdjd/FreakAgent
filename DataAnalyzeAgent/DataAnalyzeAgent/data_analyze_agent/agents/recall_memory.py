from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from data_analyze_agent.agents import get_prompt
from data_analyze_agent.core import FinalReport, MemoryRecord, ask_llm_json
from data_analyze_agent.graph.state import DataAnalyzeState
from data_analyze_agent.memory.long_term import LongTermMemory
from config import get_app_config

if TYPE_CHECKING:  
    from langchain_openai import ChatOpenAI

_lt: LongTermMemory | None = None

__all__ = [
    "recall_memory_node",
    "extract_memory_candidates",
    "build_memory_record",
    "hints_by_type",
]


_MEMORY_TYPES = ("profile", "metric_semantic", "preference")
_MAX_CANDIDATES = 3      
_MAX_CONTENT_LEN = 120   

_RECALL_SCAN_TOP_K = 9
_RECALL_PER_TYPE = 2

def _parse_candidates(data: dict) -> list[dict]:
    raw = data.get("candidates")
    if not isinstance(raw, list):
        return []

    accepted: list[dict] = []
    seen: set[str] = set()
    items = [x for x in raw if isinstance(x, dict)]
    items.sort(
        key=lambda x: x["importance"]
        if isinstance(x.get("importance"), int)
        and not isinstance(x.get("importance"), bool) else 0,
        reverse=True,
    )
    for item in items:
        content = str(item.get("content", "")).strip()
        mtype = item.get("memory_type")
        importance = item.get("importance")

        if not content or len(content) > _MAX_CONTENT_LEN:
            continue
        if mtype not in _MEMORY_TYPES:
            continue
        if not isinstance(importance, int) or isinstance(importance, bool):
            continue
        if not 2 <= importance <= 5:      
            continue
        if content in seen:
            continue
        seen.add(content)
        accepted.append({
            "content": content,
            "memory_type": mtype,
            "importance": importance,
        })
        if len(accepted) >= _MAX_CANDIDATES:
            break
    return accepted

def _get_lt() -> LongTermMemory:
    global _lt
    if _lt is None:
        _lt = LongTermMemory()
    return _lt

def extract_memory_candidates(
    question: str, report: FinalReport, llm: ChatOpenAI | None = None
) -> list[dict]:
    user_content = (
        f"【用户问题】\n{question}\n\n"
        f"【助手回答】\n{report.answer_md}"
    )
    result = ask_llm_json(get_prompt("memory_extract"), user_content, llm=llm)
    if not result["ok"]:
        return []
    try:
        return _parse_candidates(result["data"])
    except Exception:
        return []


def build_memory_record(content: str, memory_type: str,
                        importance: int, thread_id: str,
                        user_id: str = "") -> MemoryRecord:
    now = datetime.now(timezone.utc).isoformat()
    memory_id = hashlib.md5(f"{thread_id}|{content}".encode("utf-8")).hexdigest()
    return MemoryRecord(
        memory_id=memory_id,
        content=content,
        memory_type=memory_type,
        importance=importance,
        source_thread_id=thread_id,
        created_at=now,
        last_accessed_at=now,
        access_count=0,
        user_id=user_id or get_app_config().memory.user_id,
    )


def recall_memory_node(state: DataAnalyzeState) -> dict:
    question = state.get("question") or ""
    if not question.strip():
        return {"memory_hints": []}
    try:
        records = _get_lt().recall(question, top_k=_RECALL_SCAN_TOP_K)
    except Exception:
        return {"memory_hints": []}
    counts: dict[str, int] = {}
    hints: list[dict] = []
    for r in records:
        if counts.get(r.memory_type, 0) >= _RECALL_PER_TYPE:
            continue
        counts[r.memory_type] = counts.get(r.memory_type, 0) + 1
        hints.append({
            "content": r.content,
            "memory_type": r.memory_type,
            "importance": r.importance,
        })
    return {"memory_hints": hints}


def hints_by_type(state: DataAnalyzeState, *types: str) -> list[str]:
    return [
        h["content"]
        for h in state.get("memory_hints") or []
        if isinstance(h, dict) and h.get("memory_type") in types and h.get("content")
    ]