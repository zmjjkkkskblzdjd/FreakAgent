from unified_agent.core.contracts import (
    Claim, NumberClaim, PartialFailure, SqlBlock, UnifiedFinalReport, validate,
)
from unified_agent.core.state import RAGContext, SQLContext


def _n(value, origin, evidence_id):
    return NumberClaim(value=value, origin=origin, evidence_id=evidence_id)


def test_rag_mode_requires_citations():
    r = UnifiedFinalReport(
        answer_md="成交额 100 万", mode="rag", citations=[{"chunk_id": "c1"}],
        claims=[Claim(text="成交额 100 万", numbers=[_n("100", "rag", "e1")])],
    )
    assert validate(r, {"rag_ctx": RAGContext()}) == []


def test_sql_mode_sql_must_match_executed():
    sql = SQLContext(executed_sql="SELECT count() FROM v_fact")
    r = UnifiedFinalReport(answer_md="共 100 行", mode="sql",
                           sql=SqlBlock(sql="SELECT count() FROM v_fact"),
                           claims=[Claim(text="共 100 行", numbers=[_n("100", "sql", "e1")])])
    assert validate(r, {"sql_ctx": sql}) == []
    bad = r.model_copy(update={"sql": SqlBlock(sql="别的 SQL")})
    assert validate(bad, {"sql_ctx": sql}) != []


def test_row_count_zero_is_answered_not_empty():
    sql = SQLContext(status="ok", outcome="answered",
                     executed_sql="SELECT region, amount FROM v_fact",
                     query_result={"row_count": 0, "rows": []})
    r = UnifiedFinalReport(
        answer_md="该时段无成交记录", mode="hybrid",
        sql=SqlBlock(sql="SELECT region, amount FROM v_fact"),
        citations=[{"chunk_id": "c1"}],       
        claims=[],
    )
    assert validate(r, {"rag_ctx": RAGContext(), "sql_ctx": sql}) == []


def test_hybrid_absent_lane_must_appear_in_partial_failures():
    rag = RAGContext(status="ok", outcome="answered")
    sql = SQLContext(status="ok", outcome="refused", error_code="guard_rejected")
    r = UnifiedFinalReport(
        answer_md="文档说 100，数据未查到", mode="hybrid",
        citations=[{"chunk_id": "c1"}],
        claims=[Claim(text="文档说 100", numbers=[_n("100", "rag", "e1")])],
        partial_failures=[PartialFailure(
            lane="sql", status="ok", outcome="refused",
            error_code="guard_rejected", reason="守卫拒绝")],
    )
    assert validate(r, {"rag_ctx": rag, "sql_ctx": sql}) == []


def test_hybrid_produced_sql_lane_requires_sql_block():
    rag = RAGContext(status="ok", outcome="answered")
    sql = SQLContext(status="ok", outcome="answered", executed_sql="SELECT 1")
    r = UnifiedFinalReport(answer_md="口径一致", mode="hybrid",
                           citations=[{"chunk_id": "c1"}], claims=[])
    errs = validate(r, {"rag_ctx": rag, "sql_ctx": sql})
    assert any("report.sql 必填" in e for e in errs)


def test_mode_cross_lane_numbers_forbidden():
    rag = RAGContext(status="ok", outcome="answered")
    r = UnifiedFinalReport(
        answer_md="数据说是 42", mode="rag", citations=[{"chunk_id": "c1"}],
        claims=[Claim(text="数据说是 42", numbers=[_n("42", "sql", "e1")])],
    )
    assert any("跨车道混用" in e for e in validate(r, {"rag_ctx": rag}))


def test_subset_check_flags_number_outside_claims():
    sql = SQLContext(executed_sql="SELECT 42")
    r = UnifiedFinalReport(answer_md="结果是 42 元", mode="sql",
                           sql=SqlBlock(sql="SELECT 42"),
                           claims=[Claim(text="结果是 42 元", numbers=[_n("42", "sql", "e1")])])
    assert validate(r, {"sql_ctx": sql}) == []
    r2 = r.model_copy(update={"answer_md": "结果是 42 元，同比 +7%"})
    assert validate(r2, {"sql_ctx": sql}) != []


def test_finalize_backfills_validation_errors_into_report():
    from unified_agent.agents.finalize import validate_and_set

    rag = RAGContext(
        provenance=[{"chunk_id": "c1", "text": "期末货币资金 185799.26 万元"}],
        citations=["c1"])
    r = UnifiedFinalReport(
        answer_md="期末货币资金 185,799.26 万元，另有 999 元备注。", mode="rag",
        citations=[{"chunk_id": "c1"}],
        claims=[Claim(text="期末货币资金 185,799.26 万元",
                      numbers=[_n("185799.26", "rag", "c1")])])
    out = validate_and_set({"final_report": r.model_dump(), "rag_ctx": rag})
    assert out["validation_errors"] == ["answer_md 出现 claims 外数字：999"]
    assert out["validation_exempted"] == []
    body = UnifiedFinalReport(**out["final_report"]).model_dump()
    assert body["validation_errors"] == out["validation_errors"]


def test_finalize_backfills_validation_exempted_into_report():
    from unified_agent.agents.finalize import validate_and_set

    rag = RAGContext(
        provenance=[{"chunk_id": "c1", "text": "营业收入 1,065,713,845.00 元"}],
        citations=["c1"])
    r = UnifiedFinalReport(answer_md="营业收入 1,065,713,845 元。", mode="rag",
                           citations=[{"chunk_id": "c1"}], claims=[])
    out = validate_and_set({"final_report": r.model_dump(), "rag_ctx": rag})
    assert out["validation_errors"] == []
    assert out["validation_exempted"] == ["1065713845"]
    assert out["final_report"]["validation_exempted"] == ["1065713845"]