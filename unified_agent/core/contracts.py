from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

from unified_agent.core.numbers import (
    approx_equal, as_decimal, extract_numbers, normalize, unit_scaled_values,
)   

TASK_STATUS_FLOW = {
    "running": {"clarify": "awaiting_clarify", "approve": "awaiting_approval",
                "finish": "done", "give_up": "cancelled"},
    "awaiting_clarify": {"resume": "running", "give_up": "cancelled"},
    "awaiting_approval": {"resume": "running"},
}

class LaneResult(BaseModel):
    lane: Literal["rag", "sql"]
    status: Literal["ok", "empty", "error"]   
    outcome: Literal["answered", "refused"]
    error_code: Optional[str] = None          
    retriable: bool = False



class NumberClaim(BaseModel):
    value: str                                
    origin: Literal["rag", "sql"]
    evidence_id: str                          
    derived: bool = False                     
    expression: Optional[str] = None          


class Claim(BaseModel):
    text: str
    numbers: list[NumberClaim] = Field(default_factory=list)
    relation: Optional[Literal["support", "contrast", "contradict"]] = None  


class SqlBlock(BaseModel):
    sql: str                                  
    injected: bool = False                    
    injection_hash: Optional[str] = None


class FactCheck(BaseModel):
    sentences: int = 0
    factual: int = 0
    supported: int = 0
    unsupported: int = 0
    gray: int = 0
    verified: int = 0
    coverage: float = 1.0
    issues: list[dict] = Field(default_factory=list)




class PartialFailure(BaseModel):
    lane: Literal["rag", "sql"]
    status: Literal["ok", "empty", "error"]
    outcome: Literal["answered", "refused"]
    error_code: Optional[str] = None          
    reason: Optional[str] = None              
    retriable: bool = False


class UnifiedFinalReport(BaseModel):
    answer_md: str
    claims: list[Claim] = Field(default_factory=list)
    sql: Optional[SqlBlock] = None
    citations: list[dict] = Field(default_factory=list)
    mode: Literal["rag", "sql", "hybrid", "chitchat", "web"]
    partial_failures: list[PartialFailure] = Field(default_factory=list)
    passthrough: bool = False
    lane_decisions: list[dict] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    validation_exempted: list[str] = Field(default_factory=list)
    channel: dict | None = None
    redactions: list[str] = Field(default_factory=list)
    refusal_reason: Optional[Literal["policy_denied", "no_evidence", "lane_failed"]] = None
    refusal_level: Optional[Literal["hard", "soft", "partial"]] = None
    fact_check: Optional[FactCheck] = None
    writes: list[dict] = Field(default_factory=list)


def lane_failed(ctx) -> bool:
    return (ctx is None or ctx.status == "error" or ctx.outcome == "refused"
            or bool(getattr(ctx, "error_code", None)))


def lane_produced(ctx) -> bool:
    return (ctx is not None and ctx.status == "ok" and ctx.outcome == "answered"
            and not getattr(ctx, "error_code", None))


def _ctx_field(ctx, name: str, default=None):
    if isinstance(ctx, dict):
        return ctx.get(name, default)
    return getattr(ctx, name, default)


def _evidence_number_values(rag, sql) -> tuple[set[Decimal], set[Decimal]]:
    exact: set[Decimal] = set()
    approx: set[Decimal] = set()          

    def add(text: str) -> None:
        for tok in extract_numbers(text):
            d = as_decimal(tok)
            if d is not None:
                exact.add(d)
        for scaled in unit_scaled_values(text).values():
            approx.update(scaled)

    prov = _ctx_field(rag, "provenance", []) or []
    cited = {str(c) for c in (_ctx_field(rag, "citations", []) or [])}
    for ev in prov:
        if not isinstance(ev, dict):
            continue
        if cited and str(ev.get("chunk_id")) not in cited:
            continue
        add(str(ev.get("text") or ""))

    qr = _ctx_field(sql, "query_result", {}) or {}
    if isinstance(qr, dict):
        for row in qr.get("rows", []) or []:
            for cell in row:
                if cell is not None:
                    add(str(cell))
    return exact, approx


def _evidence_match(d: Decimal, exact: set[Decimal], approx: set[Decimal],
                    scaled: set[Decimal] | None) -> bool:
    if d in exact or -d in exact:
        return True
    if scaled:
        for s in scaled:
            if any(approx_equal(s, e) or approx_equal(-s, e) for e in exact):
                return True
    for a in approx:
        if approx_equal(d, a) or approx_equal(-d, a):
            return True
    return False


def validate(report: UnifiedFinalReport, state: dict,
             exempted: list[str] | None = None) -> list[str]:
    errs: list[str] = []
    if report.refusal_reason == "policy_denied":
        return errs
    if report.fact_check and report.fact_check.unsupported:
        errs.append(
            f"fact_check: {report.fact_check.unsupported} 句缺少逐字证据支撑"
            f"（factual {report.fact_check.factual}/{report.fact_check.sentences}）")
    if report.mode == "chitchat":
        return errs                  
    rag = state.get("rag_ctx")
    sql = state.get("sql_ctx")

    origins = {n.origin for c in report.claims for n in c.numbers}
    if report.mode == "rag" and "sql" in origins:
        errs.append("rag 模式出现 sql 来源数字（跨车道混用）")
    if report.mode == "sql" and "rag" in origins:
        errs.append("sql 模式出现 rag 来源数字（跨车道混用）")

    if report.mode == "rag":
        rag_failed = rag is not None and lane_failed(rag)
        if not report.citations and not rag_failed:
            errs.append("rag 模式 citations 必须非空")
        if report.sql is not None:
            errs.append("rag 模式 sql 必须为 None")
    elif report.mode == "sql":
        sql_failed = sql is not None and lane_failed(sql)
        if report.sql is None and not sql_failed:
            errs.append("sql 模式 sql 必填")
        elif report.sql is not None and sql is not None and report.sql.sql != (
                sql.executed_sql or sql.checked_sql_pre_injection or sql.sql or ""):
            errs.append("sql 与 executed_sql 不一致")
        if report.citations and any(
                c.get("source_type") != "web" for c in report.citations):
            errs.append("sql 模式 citations 只能是联网归因来源（source_type=web）")
    elif report.mode == "hybrid":
        failed_lanes = [p.lane for p in report.partial_failures]   
        rag_produced = lane_produced(rag)
        sql_produced = lane_produced(sql)
        if not rag_produced and "rag" not in failed_lanes:
            errs.append("hybrid 缺席 rag 车道未在 partial_failures 兑现")
        if not sql_produced and "sql" not in failed_lanes:
            errs.append("hybrid 缺席 sql 车道未在 partial_failures 兑现")
        if rag_produced and not report.citations:
            errs.append("hybrid 下 rag 车道已产出，citations 必须非空（qa 铁律）")
        if sql_produced and report.sql is None:
            errs.append("hybrid 下 sql 车道已产出，report.sql 必填（数据端铁律：附实际执行 SQL 原文）")

    if report.mode == "rag":
        total_failure = not lane_produced(rag)
    elif report.mode == "sql":
        total_failure = not lane_produced(sql)
    elif report.mode == "hybrid":
        total_failure = not (lane_produced(rag) or lane_produced(sql))
    else:
        total_failure = False
    passthrough = bool(getattr(report, "passthrough", False))
    if not (sql is not None and getattr(sql, "is_write", False)) and not total_failure and not passthrough:
        claim_numbers = {normalize(n.value) for c in report.claims for n in c.numbers}
        asked_numbers: set[str] = set()
        asked_texts = [str(state.get("question") or "")]
        asked_texts += [str(v) for v in (state.get("lane_questions") or {}).values() if v]
        for text in asked_texts:
            asked_numbers.update(normalize(t) for t in extract_numbers(text))
        evidence_exact, evidence_approx = _evidence_number_values(rag, sql)
        answer_for_check = report.answer_md
        if report.sql is not None and report.sql.sql:
            answer_for_check = answer_for_check.replace(report.sql.sql, "")
        answer_scaled = unit_scaled_values(answer_for_check)
        for tok in extract_numbers(answer_for_check):
            if tok in claim_numbers or tok.lstrip("-") in claim_numbers \
                    or f"-{tok}" in claim_numbers:
                continue
            if tok in asked_numbers or tok.lstrip("-") in asked_numbers \
                    or f"-{tok}" in asked_numbers:
                continue
            d = as_decimal(tok)
            if d is not None and _evidence_match(d, evidence_exact, evidence_approx,
                                                 answer_scaled.get(tok)):
                if exempted is not None:
                    exempted.append(tok)
                continue
            errs.append(f"answer_md 出现 claims 外数字：{tok}")
    return errs


class PendingInterrupt(BaseModel):
    interrupt_id: str
    kind: Literal["route", "content", "approve_write", "lane_confirm"]
    lane: Optional[Literal["rag", "sql"]] = None   
    schema: dict = Field(default_factory=dict)      


class ResumeCommand(BaseModel):
    type: Literal["clarify", "approve_write", "lane_confirm"]
    kind: Optional[Literal["route", "content", "lane"]] = None   
    action: Optional[Literal["choose_sql", "choose_rag", "choose_hybrid",
                             "new_question", "give_up"]] = None  
    lane: Optional[Literal["rag", "sql"]] = None             
    lane_action: Optional[Literal["approve", "edit", "reject", "add", "cancel"]] = None
    lane_question: Optional[str] = None      
    answer: Optional[str] = None         
    granted: Optional[bool] = None       


