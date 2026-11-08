from __future__ import annotations

import json
import sys

from langchain_core.messages import RemoveMessage, SystemMessage

from unified_agent.core.llm import ask_llm_json
from unified_agent.core.state import UnifiedState
from unified_agent.prompts import load_prompt

_SYSTEM, _USER_TPL = load_prompt("compress_notes")

_NOTE_KEYS = ("preferences", "entities", "open_items")

_HISTORY_WINDOW_TOKENS = 4000
_CLEANUP_RATIO = 0.4   
_COMPRESS_RATIO = 0.7  

HISTORY_MAX = 10       
_CLEANUP_KEEP_RECENT = 8   
_COMPRESS_KEEP_RECENT = 6  

_CLEANUP_TRUNCATE_LEN = 100  


def _merge(old: dict, new: dict) -> dict:
    return {k: list(dict.fromkeys(list(old.get(k) or []) + list(new.get(k) or [])))
            for k in _NOTE_KEYS}


def _msg_text(m) -> str:
    c = getattr(m, "content", None)
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(
            str(seg.get("text", "")) if isinstance(seg, dict) else str(seg)
            for seg in c
        )
    return str(c)


def _estimate_history_tokens(hist) -> int:
    try:
        from qa_harness.qa_harness.context.budget import count_tokens
        total = 0
        for m in hist:
            content = _msg_text(m)
            total += count_tokens(content) + 4  
        return total
    except Exception:
        total = 0
        for m in hist:
            total += len(_msg_text(m)) // 3 + 1
        return total


def _apply(hist, replacements) -> list:
    if not replacements:
        return hist
    by_id = {getattr(r, "id", None): r for r in replacements}
    return [by_id.get(getattr(m, "id", None), m) for m in hist]


def _cleanup(hist) -> list:
    cutoff = len(hist) - _CLEANUP_KEEP_RECENT
    if cutoff <= 0:
        return []
    replacements = []
    for i in range(min(cutoff, len(hist))):
        m = hist[i]
        if isinstance(m, SystemMessage):
            continue                      
        text = _msg_text(m)
        if len(text) <= _CLEANUP_TRUNCATE_LEN or not getattr(m, "id", None):
            continue                      
        try:
            replacements.append(
                m.model_copy(update={"content": text[:_CLEANUP_TRUNCATE_LEN] + "…"})
            )
        except Exception:                 
            continue
    return replacements


def compress_history(state: UnifiedState) -> dict:
    hist = state.get("history") or []
    if not hist:
        return {}

    total_tokens = _estimate_history_tokens(hist)
    util = total_tokens / _HISTORY_WINDOW_TOKENS

    if util < _CLEANUP_RATIO and len(hist) <= HISTORY_MAX:
        return {}  

    print(f"[compress] history={len(hist)}条 token≈{total_tokens} util={util:.2f}",
          file=sys.stderr)

    replacements = _cleanup(hist)
    total_tokens = _estimate_history_tokens(_apply(hist, replacements))
    util = total_tokens / _HISTORY_WINDOW_TOKENS

    if util < _COMPRESS_RATIO and len(hist) <= HISTORY_MAX:
        return {"history": replacements} if replacements else {}

    keep_recent = min(_COMPRESS_KEEP_RECENT, HISTORY_MAX)
    to_compress = hist[:-keep_recent]
    if not to_compress:
        return {}

    user = _USER_TPL.format(
        existing_notes_json=json.dumps(state.get("conversation_notes") or {},
                                       ensure_ascii=False),
        history_json=json.dumps(to_compress, ensure_ascii=False, default=str),
    )
    try:
        d = ask_llm_json(_SYSTEM, user)
    except Exception as e:
        print(f"[compress] LLM 压缩失败，history 原样保留：{e}", file=sys.stderr)
        return {}  

    notes = (d or {}).get("notes")
    if not isinstance(notes, dict) or not all(k in notes for k in _NOTE_KEYS):
        return {}

    removals = [RemoveMessage(id=getattr(m, "id", None))
                for m in to_compress if getattr(m, "id", None)]
    removal_ids = {getattr(m, "id", None) for m in to_compress}
    updates = [r for r in replacements
               if getattr(r, "id", None) not in removal_ids]   
    print(f"[compress] 压缩 {len(removals)} 条历史消息，notes 合并完成",
          file=sys.stderr)
    return {"history": updates + removals,
            "conversation_notes": _merge(state.get("conversation_notes") or {}, notes)}
