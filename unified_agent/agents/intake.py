from __future__ import annotations

import time

from unified_agent.agents import recall_catalog
from unified_agent.core.llm import ask_llm_json
from unified_agent.core.state import UnifiedState
from unified_agent.observability.tracer import emit_event
from unified_agent.prompts import load_prompt

_SYSTEM, _USER_TPL = load_prompt("intake")

_VALID_TYPES = ("rag", "sql", "hybrid", "chitchat")
_CLARIFY = "preflight_clarify"

_CHITCHAT_DATA_KEYWORDS = (
    "表名", "金额", "统计", "查询", "多少", "排名", "销售额", "营收",
    "报表", "指标", "字段", "数量", "汇总", "同比", "环比",
)


def _digest(history) -> str:
    lines = []
    for m in (history or [])[-4:]:
        lines.append(f"- {getattr(m, 'content', m)}"[:110])
    return "\n".join(lines) if lines else "（无历史对话）"


def _tenant_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_datasource(state: UnifiedState) -> bool:
    if state.get("datasource_id"):
        return True
    tid = _tenant_int(state.get("tenant_id"))
    if tid is None:
        return False
    try:
        return bool(recall_catalog._tenant_datasources(tid))
    except Exception:            # noqa: BLE001 — 平台库不可达按「无数据源」处理
        return False


def _empty_kb(state: UnifiedState) -> bool:
    return state.get("kb_ids") == []


def _snapshot(state: UnifiedState) -> str:
    try:
        return recall_catalog.snapshot_text(state) or ""
    except Exception:            # noqa: BLE001 — 快照是增强，不可用按空处理
        return ""


def _recheck(out: dict, state: UnifiedState, reason: str, thread: str) -> None:
    emit_event("intake.recheck", "intake", "ok",
               {"reason": reason, "task_type": out.get("task_type"),
                "confidence": out.get("confidence")},
               thread, state.get("session_id", ""))


def intake(state: UnifiedState, config=None) -> dict:
    t0 = time.perf_counter()
    question = state.get("question", "")
    thread = ((config or {}).get("configurable") or {}).get("thread_id", "") \
        if isinstance(config, dict) else ""
    snapshot = _snapshot(state)
    user = _USER_TPL.format(
        question=question,
        history_digest=_digest(state.get("history")),
        snapshot=snapshot or "（无）",
    )
    try:
        d = ask_llm_json(_SYSTEM, user, thinking=False, timeout=90)
    except Exception as e:                
        emit_event("intake.llm_fail", "intake", "warn",
                   {"err": type(e).__name__,
                    "ms": int((time.perf_counter() - t0) * 1000)},
                   thread, state.get("session_id", ""))
        d = None
    ms = int((time.perf_counter() - t0) * 1000)

    task_type = (d or {}).get("task_type")
    safety = str((d or {}).get("safety") or "safe").strip().lower()
    if safety not in ("safe", "review", "unsafe"):
        safety = "safe"
    web_intent = bool((d or {}).get("web_intent", False))
    if task_type == "web":
        task_type, web_intent = "rag", True
    if task_type not in _VALID_TYPES:
        return {"task_type": state.get("task_type") or "rag", "confidence": 0.3,
                "next_step": _CLARIFY, "resolved_question": question,
                "safety": safety, "web_intent": False, "_intake_ms": ms}

    resolved = str(d.get("resolved_question") or "").strip()
    if not resolved or len(resolved) > 500:   
        resolved = question
    try:
        confidence = float(d.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    out = {
        "resolved_question": resolved,
        "task_type": task_type,
        "confidence": confidence,
        "rationale": d.get("rationale"),
        "rag_tier": d.get("rag_tier", "lite"),
        "sql_tier": d.get("sql_tier", "lite"),
        "need_attribution": bool(d.get("need_attribution", False)),
        "web_intent": bool(web_intent and task_type == "rag"),
        "catalog_hints": {"snapshot": snapshot},
        "safety": safety,
    }
    if task_type == "hybrid":
        raw_lq = d.get("lane_questions") or {}
        lq: dict[str, str] = {}
        for lane in ("rag", "sql"):
            v = raw_lq.get(lane) if isinstance(raw_lq, dict) else None
            if isinstance(v, str) and v.strip():
                lq[lane] = v.strip()[:300]
        out["lane_questions"] = lq
    if confidence < 0.6:
        out["next_step"] = _CLARIFY

    t = out["task_type"]
    if t == "sql" and not _has_datasource(state):
        out["task_type"] = "rag"
        out["confidence"] = min(out["confidence"], 0.55)
        out["next_step"] = _CLARIFY
        _recheck(out, state, "no_datasource", thread)
    elif t == "hybrid":
        no_sql = not _has_datasource(state)
        no_rag = _empty_kb(state)
        if no_sql and no_rag:
            out["task_type"] = "rag"
            out["next_step"] = _CLARIFY
            _recheck(out, state, "hybrid_no_sql", thread)
            _recheck(out, state, "hybrid_no_rag", thread)
        elif no_sql:
            out["task_type"] = "rag"
            _recheck(out, state, "hybrid_no_sql", thread)
        elif no_rag:
            out["task_type"] = "sql"
            _recheck(out, state, "hybrid_no_rag", thread)
    elif t == "chitchat" and snapshot:
        if any(kw in (out["resolved_question"] or question)
               for kw in _CHITCHAT_DATA_KEYWORDS):
            out["confidence"] = 0.5          
            out["next_step"] = _CLARIFY
    elif t == "rag" and _empty_kb(state) and not out.get("web_intent"):
        out["confidence"] = min(out["confidence"], 0.55)
        out["next_step"] = _CLARIFY
        _recheck(out, state, "no_kb", thread)

    out["_intake_ms"] = ms
    return out
