from __future__ import annotations
import hashlib
import logging
import os
import re

from langchain_core.messages import AIMessage, HumanMessage

from unified_agent.core.contracts import lane_failed
from unified_agent.core.llm import ask_llm_json, ask_llm_text
from unified_agent.core.state import UnifiedState
from unified_agent.prompts import load_prompt
from memory_service.store import get_store

logger = logging.getLogger(__name__)


def _long_term_enabled() -> bool:
    return (os.getenv("UA_MEMORY_WRITE", "1") or "1").strip().lower() \
        not in ("0", "false", "no")

_LESSON_SYSTEM, _LESSON_TPL = load_prompt("lesson_reflect")
_LESSON_STORE_CAP = 160          
_LESSON_LANES = ("rag", "sql", "hybrid")
_LESSON_SOURCE = "reflexion"

_EXT_SYSTEM, _EXT_TPL = load_prompt("memory_extract")
_EXT_MAX_CHARS = 120             
_DA_HINT_TYPES = ("metric_semantic", "preference", "profile")   
_HINT_TYPES = _DA_HINT_TYPES + ("fact",)                        
_HINT_TO_MTYPE = {"metric_semantic": "sql", "preference": "general",
                  "profile": "general", "fact": "rag"}
_STAGE_FORMAL = "formal"
_STAGE_CANDIDATE = "candidate"
_CANDIDATE_HINTS = ("preference", "profile")
_EXT_CONF_DEFAULT = 0.5          
_EXT_MAX_ITEMS = 3               


def _notes_text(notes: dict) -> str:
    parts = [f"{k}: {'；'.join(v)}" for k, v in notes.items() if v]
    return " | ".join(parts)


def _qa_untrusted(state: UnifiedState) -> bool:
    for key in ("rag_ctx", "sql_ctx"):
        ctx = state.get(key)
        if ctx is not None and lane_failed(ctx):
            return True
    return False


def _validation_failed(state: UnifiedState) -> bool:
    return bool(state.get("validation_errors"))


def _confidence(raw) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return _EXT_CONF_DEFAULT
    if 1.0 < v <= 100.0:
        v /= 100.0
    return min(1.0, max(0.0, v))


def _subject(dim, value) -> tuple[str, str]:
    d = re.sub(r"\s+", " ", str(dim or "")).strip()[:20]
    v = re.sub(r"\s+", " ", str(value or "")).strip()[:60]
    return (d, v) if d and v else ("", "")


def _extract_memories(state: UnifiedState, answer: str) -> list[dict]:
    lane = str(state.get("task_type") or "general")
    question = str(state.get("resolved_question") or state.get("question") or "")
    try:
        data = ask_llm_json(_EXT_SYSTEM, _EXT_TPL.format(
            lane=lane, question=question[:300], answer=answer[:800]),
            max_tokens=800, thinking=False, timeout=60)
    except Exception as e:  # noqa: BLE001 记忆抽取是旁路，不许拖垮写回
        logger.warning(f"记忆摘要抽取失败（本轮不入库）: {type(e).__name__}: {e}")
        return []
    raw = data.get("memories")
    if not isinstance(raw, list):
        raw = [data]                 
    out: list[dict] = []
    for item in raw[:_EXT_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        summary = re.sub(r"\s+", " ", str(item.get("summary") or "")).strip()
        if not summary:
            continue                 
        hint = str(item.get("hint_type") or "").strip()
        if hint not in _HINT_TYPES:  
            hint = "metric_semantic" if lane == "sql" else "fact"
        dim, value = _subject(item.get("subject_dim"), item.get("subject_value"))
        out.append({"summary": summary[:_EXT_MAX_CHARS], "hint_type": hint,
                    "confidence": _confidence(item.get("confidence")),
                    "subject_dim": dim, "subject_value": value})
    return out



def _norm(text: str) -> str:
    return re.sub(r"[0-9]+", "#", str(text or "").strip())[:80]


def _lane_ev(ctx) -> list[str]:
    code = getattr(ctx, "error_code", "") or ""
    ev = [f"error_code={code}"] if code else []
    ev += [f"status={getattr(ctx, 'status', '')}",
           f"outcome={getattr(ctx, 'outcome', '')}"]
    return ev


def _failure_signal(state: UnifiedState) -> tuple[str, str, list[str]] | None:
    sql = state.get("sql_ctx")
    if sql is not None:
        if getattr(sql, "db_error", ""):
            return "sql", "sql_exec", [
                f"error_code={getattr(sql, 'error_code', '') or '-'}",
                f"db_error={_norm(getattr(sql, 'db_error', ''))}"]
        if getattr(sql, "review_passed", None) is False and getattr(sql, "review_fix_hint", ""):
            ev = [f"fix_hint={sql.review_fix_hint}"]
            for issue in (getattr(sql, "review_issues", None) or [])[:2]:
                if isinstance(issue, dict):
                    ev.append("issue=" + _norm(
                        issue.get("message") or issue.get("detail") or str(issue)))
            return "sql", "sql_review", ev
        if lane_failed(sql):
            return "sql", "lane_failed", _lane_ev(sql)

    rag = state.get("rag_ctx")
    if rag is not None and lane_failed(rag):
        return "rag", "lane_failed", _lane_ev(rag)

    errs = [str(e) for e in (state.get("validation_errors") or [])]
    if errs:
        lane = state.get("task_type")
        lane = lane if lane in _LESSON_LANES else "hybrid"
        return lane, "validate", [f"validation_error={_norm(e)}" for e in errs[:3]]
    return None


def _fail_sig(lane: str, kind: str, evidence: list[str]) -> str:
    raw = "|".join([lane, kind] + sorted(_norm(e) for e in evidence))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _template_lesson(lane: str, kind: str, evidence: list[str]) -> str:
    head = {"validate": "校验未通过",
            "sql_exec": "SQL 执行报错",
            "sql_review": "SQL 被审查打回",
            "lane_failed": "车道执行失败"}
    first = evidence[0] if evidence else "（无证据）"
    return f"[{lane}/{kind}] 上次同类「{head.get(kind, '失败')}」：{first}"[:_LESSON_STORE_CAP]


def _llm_lesson(lane: str, kind: str, evidence: list[str]) -> str:
    user = _LESSON_TPL.format(
        lane=lane, kind=kind,
        evidence="\n".join(f"- {e}" for e in evidence))
    try:
        text = (ask_llm_text(_LESSON_SYSTEM, user, thinking=False) or "").strip()
    except Exception as e:  # noqa: BLE001 教训是旁路功能，不许拖垮主流程
        logger.warning(f"教训生成失败（将落确定性模板）: {type(e).__name__}: {e}")
        return ""
    line = next((ln.strip(" -*`") for ln in text.splitlines() if ln.strip()), "")
    return line[:_LESSON_STORE_CAP]


def _find_active_lesson(store, base: dict, fail_sig: str) -> dict | None:
    where = {"$and": [
        {"memory_type": "lesson"},
        {"fail_sig": fail_sig},
        {"user_id": base["user_id"]},
        {"tenant_id": base["tenant_id"]},
    ]}
    try:
        rows = store.find(where)
    except Exception as e:  # noqa: BLE001 治理查询失败不应影响写回
        logger.warning(f"教训查重失败（按新教训写入）: {type(e).__name__}: {e}")
        return None
    for row in rows:
        if (row.get("metadata") or {}).get("status") == "active":
            return row
    return None


def _recalled_same_lesson(state: UnifiedState, fail_sig: str) -> bool:
    for h in state.get("recalled_memory") or []:
        meta = h.get("metadata") or {}
        if meta.get("memory_type") == "lesson" and meta.get("fail_sig") == fail_sig:
            return True
    return False


def _write_lesson(store, state: UnifiedState, base: dict) -> None:
    sig = _failure_signal(state)
    if not sig:
        return
    lane, kind, evidence = sig
    fail_sig = _fail_sig(lane, kind, evidence)
    run_id = str(state.get("session_id") or "")

    existing = _find_active_lesson(store, base, fail_sig)
    if existing:
        meta = existing["metadata"] or {}
        text = existing["text"]
        importance = float(meta.get("importance", 3.0))
        ineffective = int(meta.get("ineffective", 0))
        status, reason = "active", ""
        if _recalled_same_lesson(state, fail_sig):
            importance = max(1.0, importance - 1.0)
            ineffective += 1
            if ineffective >= 2 and importance <= 1.0:
                status, reason = "archived", "ineffective"
        else:
            importance = min(5.0, importance + 1.0)   
        payload = {**base, "memory_type": "lesson", "source": _LESSON_SOURCE,
                   "lane": lane, "fail_sig": fail_sig,
                   "importance": importance, "ineffective": ineffective,
                   "status": status, "status_reason": reason, "origin_run_id": run_id,
                   "access_count": int(meta.get("access_count", 0)),
                   "last_accessed_at": float(meta.get("last_accessed_at", 0.0) or 0.0)}
        if meta.get("created_at"):
            payload["created_at"] = float(meta["created_at"])
        store.add(text, payload)
        return

    text = _llm_lesson(lane, kind, evidence) or _template_lesson(lane, kind, evidence)
    if not text:
        return
    store.add(text, {**base, "memory_type": "lesson", "source": _LESSON_SOURCE,
                     "lane": lane, "fail_sig": fail_sig, "importance": 3.0,
                     "ineffective": 0, "status": "active", "status_reason": "",
                     "origin_run_id": run_id})


def write_memory(state: UnifiedState) -> dict:
    q = state.get("question", "")
    answer = (state.get("final_report") or {}).get("answer_md", "")

    history = [HumanMessage(q)] if q else []
    if answer:
        history.append(AIMessage(answer))

    try:
        if not _long_term_enabled():
            return {"history": history}
        store = get_store()
        uid = state.get("user_id", "")
        tid = state.get("tenant_id", "default")
        base = {"user_id": uid, "tenant_id": tid}

        notes = state.get("conversation_notes") or {}
        text = _notes_text(notes)
        if text:
            store.add(text[:1000], {**base, "memory_type": "general", "source": "conversation_notes"})

        if (answer and state.get("task_type") != "chitchat"
                and not _qa_untrusted(state) and not _validation_failed(state)):
            q_mem = state.get("resolved_question") or q
            q_trunc = q_mem[:300] + "..." if len(q_mem) > 300 else q_mem
            a_trunc = answer[:500] + "..." if len(answer) > 500 else answer
            for ext in _extract_memories(state, a_trunc):
                hint = ext["hint_type"]
                stage = (_STAGE_CANDIDATE if hint in _CANDIDATE_HINTS
                         else _STAGE_FORMAL)
                store.add(f"{ext['summary']}\nQ: {q_trunc}\nA: {a_trunc}",
                          {**base, "memory_type": _HINT_TO_MTYPE.get(hint, "general"),
                           "hint_type": hint, "summary": ext["summary"],
                           "stage": stage, "confidence": ext["confidence"],
                           "subject_dim": ext["subject_dim"],
                           "subject_value": ext["subject_value"],
                           "source": "final_report",
                           "origin_run_id": str(state.get("session_id") or "")})

        try:
            _write_lesson(store, state, base)
        except Exception as e:  # noqa: BLE001 教训是旁路，失败不影响写回
            logger.warning(f"教训写入失败（忽略，不影响主流程）: {e}")

    except Exception as e:
        logger.warning(f"长期记忆写入失败（忽略，不影响主流程）: {e}")

    return {"history": history}
