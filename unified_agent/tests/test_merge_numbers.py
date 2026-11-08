from unified_agent.core.numbers import extract_numbers, normalize, resolve_origin
from unified_agent.core.state import RAGContext, SQLContext


def test_extract_ignores_non_data_numbers():
    assert extract_numbers("2025 年，第 1 名，3 个维度，成交 1,200 元") == {"1200"}


def test_extract_percent_and_decimal():
    assert "12.5" in extract_numbers("同比 +12.5%")


def test_extract_ignores_list_markers_and_source_ids():
    text = ("1. 毛利率 33.31%\n2. 参考来源：[1] report_000001-2024-平安银行\n"
            "3. 差值未取得")
    assert extract_numbers(text) == {"33.31"}


def test_extract_ignores_quoted_sql():
    text = "> SELECT round(x) AS m FROM t WHERE d >= '2024-01-01' LIMIT 1000\n\n结论 33.31%"
    assert extract_numbers(text) == {"33.31"}
    assert extract_numbers("SELECT 1 AS one LIMIT 1000\n结论 33.31%") == {"33.31"}


def test_extract_ignores_section_refs_and_enumeration():
    text = ("见数字明细第 1 项。\n（2）补充说明：共 3 条证据。\n"
            "金额 11,327,151.01 元")
    assert extract_numbers(text) == {"11327151.01"}


def test_extract_still_catches_business_numbers():
    assert extract_numbers("净利润 -3,118,389.83 元，同比 -4.49%") == {
        "-3118389.83", "-4.49"}


def test_normalize_thousands_separator():
    assert normalize("1,200") == "1200"


def test_resolve_origin_by_evidence_id():
    ledger = [{"origin": "sql", "value": "1200", "evidence_id": "e1"},
              {"origin": "rag", "value": "1200", "evidence_id": "e2"}]
    assert resolve_origin("1200", ledger) == "sql"   


def test_ledger_tolerates_missing_ctx():
    from unified_agent.agents.merge import build_evidence_ledger
    assert build_evidence_ledger({}) == []
    out = build_evidence_ledger({"sql_ctx": SQLContext(
        query_result={"rows": [[1200]]})})
    assert out == [{"origin": "sql", "value": "1200", "evidence_id": "sql-row-0"}]


def test_finalize_validate_and_set():
    from unified_agent.agents.finalize import validate_and_set
    from unified_agent.core.contracts import UnifiedFinalReport
    state = {"final_report": UnifiedFinalReport(
        answer_md="无数字结论", mode="rag",
        citations=[{"chunk_id": "c1"}]).model_dump(),
        "rag_ctx": RAGContext()}
    out = validate_and_set(state)
    assert out["task_status"] == "done" and out["validation_errors"] == []


def test_sql_digest_falls_back_to_result_table():
    from unified_agent.agents.merge import _sql_digest
    sql = SQLContext(query_result={"columns": ["brand", "amount"],
                                   "rows": [["A", 100], ["B", 200], ["C", 300]]})
    text = _sql_digest(sql)
    assert "共 3 行" in text and "A | 100" in text and "缺席" not in text


def test_sql_digest_truncation_is_explicit():
    from unified_agent.agents import merge as m
    sql = SQLContext(query_result={"columns": ["v"],
                                   "rows": [[i] for i in range(400)]})
    text = m._sql_digest(sql)
    assert len(text) <= m._SQL_DIGEST_MAX_CHARS + 80   
    assert "此处展示前" in text and "完整数据已在证据台账" in text


def test_sql_digest_prefers_analysis_md():
    from unified_agent.agents.merge import _sql_digest
    sql = SQLContext(analysis_md="品牌 A 同比 +12.5%",
                     query_result={"columns": ["v"], "rows": [[1]]})
    assert _sql_digest(sql).startswith("品牌 A")


def test_merge_direct_skips_claims_extraction(monkeypatch):
    from unified_agent.agents import merge as m
    called = {"n": 0}

    def _fake_claims(_state):
        called["n"] += 1
        return []

    monkeypatch.setattr(m, "build_claims", _fake_claims)
    ctx = RAGContext(status="ok", outcome="answered", answer="结论 [1]",
                     citations=["c1"],
                     provenance=[{"chunk_id": "c1", "text": "结论"}])
    out = m.merge({"task_type": "rag", "question": "有哪些违规行为", "rag_ctx": ctx})
    assert called["n"] == 0
    rep = out["final_report"]
    assert rep["claims"] == [] and rep["answer_md"].startswith("结论")


def test_merge_keeps_claims_when_calc_intent(monkeypatch):
    from unified_agent.agents import merge as m
    called = {"n": 0}

    def _fake_claims(_state):
        called["n"] += 1
        return []

    monkeypatch.setattr(m, "build_claims", _fake_claims)
    monkeypatch.setattr(m, "render", lambda state, claims, **kw: m._direct_report(
        state, claims, kw.get("mode") or "rag", [], []))
    ctx = RAGContext(status="ok", outcome="answered", answer="A 是 B 的 3 倍 [1]",
                     citations=["c1"],
                     provenance=[{"chunk_id": "c1", "text": "A: 300 B: 100"}])
    out = m.merge({"task_type": "rag", "question": "A 是 B 的多少倍", "rag_ctx": ctx})
    assert called["n"] == 1


def _empty_scope(names, ids=("a", "b"), **extra):
    scope = {"question": "有哪些违规行为", "effective_ids": [],
             "empty_ids": list(ids), "empty_names": list(names)}
    scope.update(extra)
    return scope


def test_scope_note_all_empty_names_libs_and_web_skipped():
    from unified_agent.agents import merge as m
    state = {"question": "有哪些违规行为", "rag_ctx": RAGContext(),
             "kb_scope": _empty_scope(["人事制度库", "财务制度库"])}
    note = m._scope_note(state, "rag")
    assert "人事制度库" in note and "财务制度库" in note
    assert "自动联网兜底已跳过" in note
    assert not any(ch.isdigit() for ch in note)


def test_scope_note_all_empty_respects_web_intent():
    from unified_agent.agents import merge as m
    ctx = RAGContext(provenance=[{"chunk_id": "w1", "doc_type": "web", "text": "政策"}])
    state = {"question": "有哪些违规行为", "rag_ctx": ctx, "kb_scope": _empty_scope(["人事制度库"])}
    assert "联网检索" in m._scope_note(state, "rag")
    state_off = {"question": "有哪些违规行为", "rag_ctx": RAGContext(),
                 "kb_scope": _empty_scope(["人事制度库"]),
                 "web_intent": True, "web_enabled": False}
    assert "联网开关已关闭" in m._scope_note(state_off, "rag")


def test_scope_note_partial_empty_lists_only_skipped_libs():
    from unified_agent.agents import merge as m
    scope = _empty_scope(["财务制度库"], effective_ids=["a"])
    note = m._scope_note({"question": "有哪些违规行为", "kb_scope": scope}, "rag")
    assert "财务制度库" in note and "排除" in note
    assert "自动联网兜底" not in note


def test_scope_note_ignores_stale_question_and_other_modes():
    from unified_agent.agents import merge as m
    stale = {"question": "新问题", "kb_scope": _empty_scope(["人事制度库"])}
    assert m._scope_note(stale, "rag") == ""
    fresh = {"question": "有哪些违规行为", "kb_scope": _empty_scope(["人事制度库"])}
    assert m._scope_note(fresh, "sql") == ""


def test_scope_note_masks_digit_library_names():
    from unified_agent.agents import merge as m
    note = m._scope_note(
        {"question": "有哪些违规行为", "kb_scope": _empty_scope(["2025 制度库"])}, "rag")
    assert "2025" not in note and "制度库" not in note
    assert "未检索到可用资料" in note


def test_direct_report_appends_scope_note_and_validate_passes():
    from unified_agent.agents import merge as m
    from unified_agent.core.contracts import validate
    ctx = RAGContext(status="ok", outcome="answered", answer="结论 [1]",
                     citations=["c1"], provenance=[{"chunk_id": "c1", "text": "结论"}])
    state = {"question": "有哪些违规行为",
             "rag_ctx": ctx,
             "kb_scope": _empty_scope(["财务制度库"], effective_ids=["a"])}
    rep = m._direct_report(state, [], "rag", [], [])
    assert "检索范围说明（系统追加）" in rep.answer_md
    assert "财务制度库" in rep.answer_md
    assert validate(rep, state) == []


def test_refusal_report_appends_scope_note():
    from unified_agent.agents import merge as m
    ctx = RAGContext(status="empty", outcome="refused")
    state = {"question": "有哪些违规行为", "rag_ctx": ctx,
             "kb_scope": _empty_scope(["人事制度库"])}
    entries, hard = m.collect_lane_failures(state, "rag")
    rep = m._refusal_report(state, "rag", entries, hard)
    assert "失败明细" in rep.answer_md
    assert "人事制度库" in rep.answer_md
    assert "自动联网兜底已跳过" in rep.answer_md


def test_scope_note_explicit_no_kb_states_web_disposition():
    from unified_agent.agents import merge as m
    web_ctx = RAGContext(provenance=[{"chunk_id": "w1", "doc_type": "web", "text": "政策"}])
    state = {"question": "今天有什么新闻", "kb_ids": [], "web_intent": True,
             "web_enabled": True, "rag_ctx": web_ctx}
    note = m._scope_note(state, "rag")
    assert "未选择任何知识库" in note
    assert "改用联网检索" in note
    assert not any(ch.isdigit() for ch in note)

    off = {"question": "今天有什么新闻", "kb_ids": [], "rag_ctx": RAGContext()}
    assert "自动联网兜底已跳过" in m._scope_note(off, "rag")