
from __future__ import annotations

import os

from langgraph.types import interrupt

from unified_agent.agents.lane_question import normalize as normalize_lane_question
from unified_agent.core.contracts import PendingInterrupt, ResumeCommand
from unified_agent.core.limiters import TokenBucket
from unified_agent.core.state import UnifiedState

ROUTE_CLARIFY_MAX = 2          

ROUTE_OPTIONS = (
    ("choose_rag", "查内部文档、规范或操作手册"),
    ("choose_sql", "查业务数据库（订单、销售等）"),
    ("choose_hybrid", "两者都要：既要查数，也要看文档口径"),
    ("new_question", "换个说法重新问一遍"),
    ("give_up", "放弃这次提问"),
)
ROUTE_ACTIONS = tuple(a for a, _ in ROUTE_OPTIONS)   

_LANE_CN = {"rag": "内部文档", "sql": "业务数据"}

_BUCKET = TokenBucket(
    capacity=float(os.getenv("UNIFIED_BUCKET_CAPACITY", "100")),
    refill_per_sec=float(os.getenv("UNIFIED_BUCKET_REFILL", "10")),
)

def _degrade_direction() -> str:
    d = os.getenv("UNIFIED_DEGRADE_DIRECTION", "sql")
    return d if d in ("rag", "sql") else "sql"


def _hybrid_parallel_allowed() -> bool:
    if os.getenv("UNIFIED_HYBRID_PARALLEL", "true").lower() != "true":
        return False
    return _BUCKET.acquire(2.0)     


def _available_lanes(state: UnifiedState) -> dict[str, bool]:
    kb = state.get("kb_ids")
    web_ok = bool(state.get("web_intent")) and bool(state.get("web_enabled", True))
    return {"rag": (True if kb is None else bool(kb)) or web_ok,
            "sql": state.get("sql_enabled", True)}


def _fit_lanes(state: UnifiedState) -> dict:
    t = state.get("task_type")
    if t not in ("rag", "sql", "hybrid"):
        return {}
    avail = _available_lanes(state)
    if t == "hybrid":
        keep = [lane for lane in ("rag", "sql") if avail[lane]]
        if len(keep) == 2:
            return {}
        if len(keep) == 1:
            return {"task_type": keep[0], "degraded_from": "hybrid",
                    "lane_questions": {}, "lane_decisions": []}
        return {"task_type": "rag", "degraded_from": "hybrid",
                "lane_questions": {}, "lane_decisions": []}
    if avail[t]:
        return {}
    other = "sql" if t == "rag" else "rag"
    if avail[other]:
        return {"task_type": other, "degraded_from": t,
                "lane_questions": {}, "lane_decisions": []}
    return {"task_type": "rag"} if t == "sql" else {}


def route_clarify_payload(state: UnifiedState) -> dict | None:
    if state.get("task_type") == "chitchat":
        return None
    if state.get("confidence", 1.0) >= 0.6:
        return None
    n = state.get("route_clarify_count", 0)
    if n >= ROUTE_CLARIFY_MAX:
        return None                 
    return PendingInterrupt(
        interrupt_id=f"route-{n + 1}", kind="route", lane=None,
        schema={
            "question": state.get("question", ""),
            "rationale": state.get("rationale") or "",
            "options": [{"action": a, "label": label} for a, label in ROUTE_OPTIONS],
        },
    ).model_dump()


def _exhausted_notice(lane: str) -> str:
    return (f"系统多轮澄清后仍未确定您要查的是内部文档还是业务数据，"
            f"已按默认方向「{_LANE_CN.get(lane, lane)}」作答；"
            f"如结果不符合预期，请重新提问。")


def route_clarify_exhausted(state: UnifiedState) -> dict:
    lane = _degrade_direction()
    return {"task_type": lane, "degraded_from": state.get("task_type", "rag"),
            "degrade_notice": _exhausted_notice(lane),
            "task_status": "running", "next_step": "dispatch",
            "lane_questions": {}, "lane_decisions": []}     


def apply_route_resume(state: UnifiedState, cmd: ResumeCommand) -> dict:
    n = state.get("route_clarify_count", 0) + 1
    if cmd.action == "give_up":
        return {"task_status": "cancelled", "next_step": "end", "route_clarify_count": n}
    if cmd.action == "new_question":
        return {"task_status": "running", "next_step": "router", "route_clarify_count": n}
    assert cmd.action in ROUTE_ACTIONS, f"非法路由答复：{cmd.action}"
    lane = cmd.action.removeprefix("choose_")
    patch = {"task_type": lane,
             "confidence": 1.0, "task_status": "running", "next_step": "dispatch",
             "route_clarify_count": n}
    if lane != "hybrid":
        patch.update(lane_questions={}, lane_decisions=[])
    return patch


def degrade(state: UnifiedState) -> dict:
    return {"task_type": _degrade_direction(),
            "degraded_from": state.get("task_type", "hybrid"),
            "task_status": "running", "next_step": "dispatch",
            "lane_questions": {}, "lane_decisions": []}     


_LANE_CONFIRM_PRESENT = (
    ("approve", "按这条子问题查询"),
    ("edit", "修改这条子问题"),
    ("reject", "不查这一路，只用另一路回答"),
    ("cancel", "取消整个任务"),
)
_LANE_CONFIRM_MISSING = (
    ("add", "补充这一路的问题"),
    ("reject", "不查这一路，只用另一路回答"),
    ("cancel", "取消整个任务"),
)


def _confirm_mode() -> str:
    return "off" if os.getenv("HYBRID_CONFIRM_MODE", "sequential").lower() == "off" else "sequential"


def lane_confirm_payload(lane: str, question: str, original: str, index: int) -> dict:
    missing = not question
    options = _LANE_CONFIRM_MISSING if missing else _LANE_CONFIRM_PRESENT
    return PendingInterrupt(
        interrupt_id=f"lane-{lane}-{index}", kind="lane_confirm", lane=lane,
        schema={
            "lane": lane,
            "lane_name": _LANE_CN.get(lane, lane),
            "index": index,
            "total": 2,
            "original": original,
            "question": question,
            "missing": missing,
            "options": [{"action": a, "label": label} for a, label in options],
        },
    ).model_dump()


def _decision(lane: str, question: str, final: str, action: str,
              user_input: str = "", normalized: str = "none") -> dict:
    return {"lane": lane, "original": question, "user_input": user_input,
            "final": final, "action": action, "missing": not question,
            "normalized": normalized}


def apply_lane_decision(lane: str, cmd: ResumeCommand, question: str,
                        original: str) -> tuple[str | None, dict]:
    action = cmd.lane_action or ("approve" if question else "reject")
    raw = str(cmd.lane_question or "").strip()
    final, source = question, "none"

    if action == "reject":
        final = ""
    elif action in ("edit", "add"):
        text = raw or (question if action == "edit" else "")
        if text:
            final, source = normalize_lane_question(text, original, extra=raw)
        else:
            final = ""                      
    elif action == "approve" and not question:
        final = ""                          

    if not final:
        action = "reject"
        source = "none"
    return (final or None), _decision(lane, question, final, action, raw, source)


def _lane_dispatch_patch(final: dict, decisions: list[dict]) -> dict:
    patch = {"lane_questions": final, "lane_decisions": decisions,
             "task_status": "running", "next_step": "dispatch"}
    for lane in ("rag", "sql"):
        if lane not in final:
            patch[f"{lane}_ctx"] = None
    if not final:
        patch.update(task_status="cancelled", next_step="end", final_report=None)
    return patch


def _auto_lane_decisions(original: str, raw_lq: dict) -> tuple[dict, list[dict]]:
    present = {lane: str((raw_lq or {}).get(lane) or "").strip() for lane in ("rag", "sql")}
    if not any(present.values()):
        decisions = [_decision(lane, "", original, "fallback_full")
                     for lane in ("rag", "sql")]
        return {lane: original for lane in ("rag", "sql")}, decisions

    final: dict[str, str] = {}
    decisions: list[dict] = []
    for lane, q in present.items():
        if q:
            final[lane] = q
            decisions.append(_decision(lane, q, q, "approve"))
        else:
            decisions.append(_decision(lane, "", "", "skip_missing"))
    return final, decisions


def _confirm_lanes(state: UnifiedState) -> dict:
    original = state.get("resolved_question") or state.get("question", "")
    raw_lq = state.get("lane_questions") or {}
    if _confirm_mode() == "off":
        final, decisions = _auto_lane_decisions(original, raw_lq)
        return _lane_dispatch_patch(final, decisions)

    final: dict[str, str] = {}
    decisions: list[dict] = []
    for index, lane in enumerate(("rag", "sql"), start=1):
        question = str(raw_lq.get(lane) or "").strip()
        answer = interrupt(lane_confirm_payload(lane, question, original, index))
        cmd = ResumeCommand(**answer)
        if cmd.lane_action == "cancel":
            decisions.append(_decision(lane, question, "", "cancel"))
            return {"task_status": "cancelled", "next_step": "end",
                    "lane_questions": {}, "lane_decisions": decisions,
                    "final_report": None}
        fq, decision = apply_lane_decision(lane, cmd, question, original)
        decisions.append(decision)
        if fq:
            final[lane] = fq
    return _lane_dispatch_patch(final, decisions)


def preflight(state: UnifiedState) -> dict:
    payload = route_clarify_payload(state)
    if payload is not None:
        answer = interrupt(payload)                    
        patch = apply_route_resume(state, ResumeCommand(**answer))
        if patch.get("task_type") == "hybrid" and patch.get("next_step") == "dispatch":
            merged = {**state, **patch}
            fit = _fit_lanes(merged)
            if fit:
                return {**patch, **fit, "task_status": "running",
                        "next_step": "dispatch", "lane_questions": {},
                        "lane_decisions": []}
            return {**patch, **_confirm_lanes(merged)}
        return patch
    if state.get("confidence", 1.0) < 0.6:
        patch = route_clarify_exhausted(state)         
        fit = _fit_lanes({**state, **patch})
        if fit:
            return {**patch, **fit, "task_status": "running",
                    "next_step": "dispatch", "lane_questions": {},
                    "lane_decisions": []}
        return patch
    if state.get("task_type") == "hybrid" and not _hybrid_parallel_allowed():
        state = {**state, **degrade(state)}
    fit = _fit_lanes(state)
    if fit:
        return {**fit, "task_status": "running", "next_step": "dispatch",
                "lane_questions": {}, "lane_decisions": []}
    if state.get("task_type") == "hybrid":
        return _confirm_lanes(state)
    return {"task_status": "running", "next_step": "dispatch",
            "lane_questions": {}, "lane_decisions": []}     