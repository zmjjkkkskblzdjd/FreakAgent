import pytest

from unified_agent.prompts import load_prompt



def test_loader_splits_intake_md():
    system, user_tpl = load_prompt("intake")
    assert "指代消解器兼分诊员" in system and "## User 模板" not in system
    assert "{question}" in user_tpl and "{history_digest}" in user_tpl
    assert "{snapshot}" in user_tpl


def test_loader_strips_doc_head_and_fence():
    system, user_tpl = load_prompt("merge_claims")
    assert "记账员" in system          
    assert "```" not in user_tpl       
    assert "{ledger_lines}" in user_tpl


def test_loader_splits_memory_extract():
    system, user_tpl = load_prompt("memory_extract")
    for key in ("confidence", "subject_dim", "subject_value"):
        assert key in system
    assert "Holder ≠ subject" in system and "第一人称显式性门槛" in system
    assert "原子声明" in system                      
    assert "memories" in system and '"memories": []' in system   
    assert "{question}" in user_tpl and "{answer}" in user_tpl



def test_loader_splits_lane_question_md():
    system, user_tpl = load_prompt("lane_question")
    assert "子问题补全器" in system
    assert "{context}" in user_tpl and "{question}" in user_tpl and "{extra}" in user_tpl



def test_resolve_question_lands(monkeypatch):
    import unified_agent.agents.resolve_question as rq

    monkeypatch.setattr(rq, "ask_llm_json",
                        lambda s, u: {"resolved_question": "2025 年销售额"})
    out = rq.resolve({"question": "那2025年呢"})
    assert out["resolved_question"] == "2025 年销售额"


@pytest.mark.parametrize("resolved", [
    "",                            
    "x" * 501,                     
])
def test_resolve_question_guardrails(monkeypatch, resolved):
    import unified_agent.agents.resolve_question as rq

    monkeypatch.setattr(rq, "ask_llm_json",
                        lambda s, u: {"resolved_question": resolved})
    out = rq.resolve({"question": "原话"})
    assert out["resolved_question"] == "原话"


def test_resolve_question_fallback_writes_question(monkeypatch):
    import unified_agent.agents.resolve_question as rq

    def boom(s, u):
        raise RuntimeError("network down")
    monkeypatch.setattr(rq, "ask_llm_json", boom)
    out = rq.resolve({"question": "本轮原话"})
    assert out["resolved_question"] == "本轮原话"


def test_resolve_question_event_emitted_only_when_changed(monkeypatch):
    import unified_agent.agents.resolve_question as rq

    events = []
    monkeypatch.setattr(rq, "emit_event",
                        lambda kind, node, status, detail, thread="", session="":
                        events.append((kind, detail)))
    monkeypatch.setattr(rq, "ask_llm_json",
                        lambda s, u: {"resolved_question": "补全版"})
    rq.resolve({"question": "原话"})
    assert events and events[0][0] == "resolve"
    assert events[0][1]["resolved_question"] == "补全版"

    events.clear()
    monkeypatch.setattr(rq, "ask_llm_json",
                        lambda s, u: {"resolved_question": "原话"})
    rq.resolve({"question": "原话"})
    assert events == []                       



def test_sql_bridge_passes_memory_hints():
    from unified_agent.adapters.sql_adapter import _to_data_state
    state = {"sql_ctx": None, "recalled_memory": [
        {"text": "华北口径按含退款", "metadata": {"memory_type": "sql"}, "distance": 0.2},
        {"text": "文档偏好", "metadata": {"memory_type": "rag"}, "distance": 0.2}]}
    da = _to_data_state(state)
    assert da["memory_hints"] == [
        {"content": "华北口径按含退款", "memory_type": "sql", "importance": pytest.approx(0.8)}]


def test_hybrid_sql_lane_gets_boundary_hint():
    from unified_agent.adapters.sql_adapter import _to_data_state
    da = _to_data_state({"sql_ctx": None, "task_type": "hybrid", "recalled_memory": []})
    assert len(da["memory_hints"]) == 1
    assert "车道边界" in da["memory_hints"][0]["content"]



def _ctx(**kw):
    from unified_agent.adapters.sql_adapter import SQLContext
    return SQLContext(question="q", **kw)


def test_failed_verdict_does_not_reach_execution():
    from unified_agent.adapters.sql_adapter import _from_data_patch
    from data_analyze_agent.core import ReviewVerdict
    state = {"sql_ctx": _ctx(sql="SELECT 1")}
    out = _from_data_patch(state, {"review_verdict": ReviewVerdict(
        passed=False, issues=["口径错"], fix_hint="fix_sql",
        risk_level="low", checked_sql="SELECT 1")})
    c = out["sql_ctx"]
    assert c.review_passed is False and c.review_fix_hint == "fix_sql"
    assert c.checked_sql_pre_injection is None       
    assert c.status == "ok" and c.outcome == "answered"   


def test_review_gate_terminal_states(monkeypatch):
    import unified_agent.adapters.sql_adapter as sa
    from unified_agent.adapters.sql_adapter import _review_gate
    monkeypatch.setattr(sa, "_auto_repair_on", lambda: True)
    assert _review_gate({"sql_ctx": _ctx(review_passed=False,
                                         review_fix_hint="fix_sql", repair_count=0)}) == {}
    assert _review_gate({"sql_ctx": _ctx()}) == {}
    assert _review_gate({"sql_ctx": _ctx(review_passed=True)}) == {}
    for hint, n, code, st in [("fix_sql", 1, "review_fix_sql", "error"),
                              ("escalate_plan", 0, "review_escalated", "error"),
                              ("need_clarify", 0, "review_need_clarify", "ok")]:
        tier = "deep" if hint == "escalate_plan" else "lite"   
        out = _review_gate({"sql_ctx": _ctx(review_passed=False,
                                            review_fix_hint=hint, repair_count=n,
                                            tier=tier)})
        c = out["sql_ctx"]
        assert c.status == st and c.outcome == "refused" and c.error_code == code
    monkeypatch.setattr(sa, "_auto_repair_on", lambda: False)
    out = _review_gate({"sql_ctx": _ctx(review_passed=False,
                                        review_fix_hint="fix_sql", repair_count=0)})
    assert out["sql_ctx"].status == "error" and out["sql_ctx"].outcome == "refused"


def test_analyst_refusal_lands_as_refused():
    from unified_agent.adapters.sql_adapter import _from_data_patch
    from types import SimpleNamespace
    state = {"sql_ctx": _ctx(sql="SELECT 1")}
    out = _from_data_patch(state, {"data_analysis": SimpleNamespace(
        answer_md="抱歉，该问题无法从现有数据回答", refused=True)})
    c = out["sql_ctx"]
    assert c.analysis_md == "抱歉，该问题无法从现有数据回答"
    assert c.outcome == "refused" and c.status == "ok"


def test_after_review_route_three_ways(monkeypatch):
    import unified_agent.adapters.sql_adapter as sa
    monkeypatch.setattr(sa, "_auto_repair_on", lambda: True)
    from unified_agent.adapters.sql_adapter import _after_review_route, END
    ok = _ctx(review_passed=True)
    loop = _ctx(review_passed=False, review_fix_hint="fix_sql", repair_count=0)
    over = _ctx(review_passed=False, review_fix_hint="fix_sql", repair_count=1)
    assert _after_review_route({"sql_ctx": ok}) == "policy_inject"
    assert _after_review_route({"sql_ctx": loop}) == "write_sql"
    assert _after_review_route({"sql_ctx": over}) == END


def test_execute_gate_runtime_fix_loop(monkeypatch):
    import unified_agent.adapters.sql_adapter as sa
    from unified_agent.adapters.sql_adapter import (
        _execute_gate, _after_execute_route, END,
    )
    monkeypatch.setattr(sa, "_auto_repair_on", lambda: True)

    failed = _ctx(status="error", retriable=True, error_code="gate: denied",
                  db_error="denied", repair_count=0)
    armed = _execute_gate({"sql_ctx": failed})["sql_ctx"]    
    assert armed.status == "ok" and armed.error_code is None
    assert armed.db_error == "denied"                        
    assert _after_execute_route({"sql_ctx": armed}) == "write_sql"

    over = _ctx(status="error", retriable=True, error_code="gate: denied",
                db_error="denied", repair_count=1)
    c = _execute_gate({"sql_ctx": over})["sql_ctx"]          
    assert c.status == "error" and c.outcome == "refused"
    assert _after_execute_route({"sql_ctx": c}) == END

    okc = _ctx(status="ok", outcome="answered")
    assert _execute_gate({"sql_ctx": okc}) == {}             
    assert _after_execute_route({"sql_ctx": okc}) == "analyze"

    monkeypatch.setattr(sa, "_auto_repair_on", lambda: False)
    c2 = _execute_gate({"sql_ctx": failed})["sql_ctx"]       
    assert c2.outcome == "refused"


def test_to_data_state_rebuilds_runtime_fix_feedback():
    from unified_agent.adapters.sql_adapter import _to_data_state
    c = _ctx(sql="SELECT 2", db_error="列名不存在", repair_count=1)
    da = _to_data_state({"sql_ctx": c})
    assert da["db_error"].message == "列名不存在"
    assert da["repair_count"] == 1


def test_to_data_state_rebuilds_failed_verdict():
    from unified_agent.adapters.sql_adapter import _to_data_state
    c = _ctx(sql="SELECT 1", review_passed=False, review_fix_hint="fix_sql",
             repair_count=1)
    da = _to_data_state({"sql_ctx": c})
    v = da["review_verdict"]
    assert v.passed is False and v.fix_hint == "fix_sql"
    assert da["repair_count"] == 1


def test_sql_subgraph_review_loop(monkeypatch):
    import unified_agent.adapters.sql_adapter as sa
    from data_analyze_agent.core import ReviewVerdict, SQLAttempt

    calls = {"write": 0}

    def fake_write(state):
        n = calls["write"]
        calls["write"] += 1
        verdict = state.get("review_verdict")
        if n == 0:
            assert verdict is None                      
            return {"current_attempt": SQLAttempt(sql="SELECT 1", version=1,
                                                  stage="draft", used_tables=[], rationale=""),
                    "repair_count": 0}
        assert verdict is not None and verdict.passed is False   
        assert state["repair_count"] == 0   
        return {"current_attempt": SQLAttempt(sql="SELECT 2", version=2,
                                              stage="draft", used_tables=[], rationale=""),
                "repair_count": 1}

    verdicts = [
        ReviewVerdict(passed=False, issues=["口径错"], fix_hint="fix_sql",
                      risk_level="low", checked_sql="SELECT 1"),
        ReviewVerdict(passed=True, issues=[], fix_hint="pass",
                      risk_level="low", checked_sql="SELECT 2"),
    ]
    review_calls = {"n": 0}

    def fake_review(state):
        v = verdicts[review_calls["n"]]
        review_calls["n"] += 1
        return {"review_verdict": v}

    card = {"card_id": "t1", "kind": "schema", "content": "表 t1", "score": 0.9,
            "match_type": "keyword"}
    monkeypatch.setattr(sa, "make_retrieve_schema_node",
                        lambda: lambda s: {"schema_docs": [card]})
    monkeypatch.setattr(sa, "plan_node", lambda s: {})
    monkeypatch.setattr(sa, "write_sql_node", fake_write)
    monkeypatch.setattr(sa, "review_sql_node", fake_review)
    monkeypatch.setattr(sa, "execute_sql_node",
                        lambda s: {"query_result": {"executed_sql": "SELECT 2",
                                                    "columns": ["x"], "rows": [[1]],
                                                    "row_count": 1}})
    monkeypatch.setattr(sa, "analyze_result_node", lambda s: {})

    g = sa.build_sql_subgraph()
    final = g.invoke({"sql_ctx": sa.SQLContext(question="q")})
    c = final["sql_ctx"]
    assert calls["write"] == 2                          
    assert c.review_passed is True and c.repair_count == 1
    assert c.executed_sql == "SELECT 2"                 
    assert c.status == "ok" and c.outcome == "answered"


def test_rag_node_crash_lands_error(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra

    def _boom():
        raise RuntimeError("超过最大工具循环次数 6")
    monkeypatch.setattr(ra, "_build_retriever", lambda: type("R", (), {"run": staticmethod(lambda s: _boom())})())

    g = ra.build_rag_subgraph()
    final = g.invoke({"question": "q", "rag_tier": "lite", "rag_ctx": None})
    c = final["rag_ctx"]
    assert c.status == "error" and c.retriable is True
    assert c.error_code.startswith("node_crash")


def test_sql_node_crash_short_circuits(monkeypatch):
    import unified_agent.adapters.sql_adapter as sa

    calls = {"review": 0, "execute": 0}

    def fake_write(state):
        raise RuntimeError("超过最大工具循环次数 6")

    def fake_review(state):
        calls["review"] += 1
        return {}
    monkeypatch.setattr(sa, "make_retrieve_schema_node",
                        lambda: lambda s: {"schema_docs": [{
                            "card_id": "t1", "kind": "schema", "content": "t",
                            "score": 0.9, "match_type": "keyword"}]})
    monkeypatch.setattr(sa, "plan_node", lambda s: {})
    monkeypatch.setattr(sa, "write_sql_node", fake_write)
    monkeypatch.setattr(sa, "review_sql_node", fake_review)
    monkeypatch.setattr(sa, "execute_sql_node",
                        lambda s: calls.__setitem__("execute", calls["execute"] + 1) or {})

    g = sa.build_sql_subgraph()
    final = g.invoke({"sql_ctx": sa.SQLContext(question="q")})
    c = final["sql_ctx"]
    assert c.status == "error" and c.retriable is True
    assert c.error_code.startswith("node_crash")
    assert calls["review"] == 0 and calls["execute"] == 0   


def test_retrieval_gate_zero_hit_closes(monkeypatch):
    import unified_agent.adapters.sql_adapter as sa
    zero = _ctx(schema=[])                       
    gc = sa._retrieval_gate({"sql_ctx": zero})["sql_ctx"]
    assert gc.status == "ok" and gc.outcome == "refused"
    assert gc.error_code == "schema_no_hit"
    assert sa._after_retrieval_route({"sql_ctx": gc}) == sa.END
    card = {"card_id": "t1", "kind": "schema", "content": "表 t1", "score": 0.9,
            "match_type": "keyword"}
    assert sa._retrieval_gate({"sql_ctx": _ctx(schema=[card])}) == {}
    lite = _ctx(schema=[card])
    assert sa._after_retrieval_route({"sql_ctx": lite}) == "write_sql"
    deep = _ctx(schema=[card], tier="deep")
    assert sa._after_retrieval_route({"sql_ctx": deep}) == "plan"


def test_review_escalation_upgrades_tier(monkeypatch):
    import unified_agent.adapters.sql_adapter as sa
    monkeypatch.setattr(sa, "_auto_repair_on", lambda: True)
    lite = _ctx(review_passed=False, review_fix_hint="escalate_plan",
                tier="lite", repair_count=2)
    assert sa._review_gate({"sql_ctx": lite}) == {}              
    assert sa._after_review_route({"sql_ctx": lite}) == "plan"   
    state = {"sql_ctx": lite}
    out = sa._from_data_patch(state, {"tier": "deep", "escalation_count": 1,
                                      "repair_count": 0})
    c = out["sql_ctx"]
    assert c.tier == "deep" and c.escalation_count == 1 and c.repair_count == 0
    deep = _ctx(review_passed=False, review_fix_hint="escalate_plan",
                tier="deep", escalation_count=1)
    gc = sa._review_gate({"sql_ctx": deep})["sql_ctx"]
    assert gc.status == "error" and gc.error_code == "review_escalated"
    assert sa._after_review_route({"sql_ctx": deep}) == sa.END


def test_escalation_event_reports_real_count(monkeypatch):
    import unified_agent.adapters.sql_adapter as sa

    events = []
    monkeypatch.setattr(sa, "emit_event",
                        lambda kind, node, status, detail, *a, **k:
                        events.append((kind, detail)))
    lite = _ctx(tier="lite", repair_count=2)
    sa._from_data_patch({"sql_ctx": lite},
                        {"tier": "deep", "escalation_count": 1, "repair_count": 0})
    assert events and events[0][0] == "escalation"
    assert events[0][1]["escalation_count"] == 1     


def test_to_data_state_carries_tier():
    from unified_agent.adapters.sql_adapter import _to_data_state
    da = _to_data_state({"sql_ctx": _ctx(tier="deep", escalation_count=1)})
    assert da["tier"] == "deep" and da["escalation_count"] == 1



def test_critic_need_more_evidence_retriggers_retrieve(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra
    from types import SimpleNamespace as NS

    calls = {"answer": 0, "retrieve": 0}
    fresh_report = ra.EvidenceReport(evidences=[], gaps=[])
    seen_reports = []

    class _FakeRetriever:
        def run(self, summary):
            calls["retrieve"] += 1
            return {"ok": True, "data": fresh_report}

    class _FakeAnswerer:
        def __init__(self, lane):
            assert lane == "deep"
        def run(self, summary, report):
            calls["answer"] += 1
            seen_reports.append(report)
            return {"ok": True, "data": NS(answer=f"v{calls['answer']}",
                                           citations=[], is_refusal=False)}

    class _FakeCritic:
        def run(self, summary, plan, draft):
            if len(seen_reports) < 2:    
                return {"ok": True, "data": NS(passed=False, fix_hint="need_more_evidence",
                                               issues=[NS(detail="缺上月数值")])}
            return {"ok": True, "data": NS(passed=True, fix_hint="pass", issues=[])}

    monkeypatch.setattr(ra, "_build_retriever", lambda: _FakeRetriever())
    monkeypatch.setattr(ra, "_build_answerer", lambda lane="lite": _FakeAnswerer(lane))
    monkeypatch.setattr(ra, "_build_critic", lambda: _FakeCritic())

    out = ra._answer({"rag_ctx": {"tier": "deep", "query": "q", "snippets": []}})
    ctx = out["rag_ctx"]
    assert calls["retrieve"] == 1                       
    assert calls["answer"] == 2                         
    assert seen_reports[1] is fresh_report              
    assert ctx.fix_rounds == 1 and ctx.last_fix_hint == "need_more_evidence"
    assert ctx.answer == "v2" and ctx.outcome == "answered"


def test_fix_round_expands_cited_chunks(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra
    from types import SimpleNamespace as NS

    fact = "关键事实：下属30人以上"
    content = "甲" * 320 + fact + "乙" * 150
    snippet = {"chunk_id": "c1", "content": content, "source": "news1.docx",
               "doc_type": "kb", "chunk_index": 0, "score": 0.9, "match_type": "vector"}
    blocks = []
    verdicts = [NS(passed=False, fix_hint="fix_draft",
                   issues=[NS(detail="漏掉高管加分第三条：下属人数要求")]),
                NS(passed=True, fix_hint="pass", issues=[])]

    class _FakeAnswerer:
        def __init__(self, lane): pass
        def run(self, summary, report):
            blocks.append(summary)
            return {"ok": True, "data": NS(answer="结论 [1]", citations=["c1"],
                                           is_refusal=False)}

    class _FakeCritic:
        def run(self, *args):
            return {"ok": True, "data": verdicts.pop(0)}

    monkeypatch.setattr(ra, "_build_answerer", lambda lane="lite": _FakeAnswerer(lane))
    monkeypatch.setattr(ra, "_build_critic", lambda: _FakeCritic())

    ra._answer({"question": "香港优才高管加分条件",
                "rag_ctx": {"tier": "deep", "query": "香港优才高管加分条件",
                            "snippets": [snippet]}})
    assert fact not in blocks[0]          
    assert fact in blocks[1]              


def test_answer_reuses_retriever_gaps(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra
    from types import SimpleNamespace as NS

    gap = "香港新资本投资者入境计划：两轮检索均无知识库证据"
    seen = {}
    snippet = {"chunk_id": "c1", "content": "澳门人才引进计划内容",
               "source": "news1.docx", "doc_type": "kb", "chunk_index": 0,
               "score": 0.9, "match_type": "vector"}

    class _FakeAnswerer:
        def __init__(self, lane): pass
        def run(self, summary, report):
            seen["gaps"] = list(report.gaps)
            seen["answer_block"] = summary
            return {"ok": True, "data": NS(answer="库内未收录该计划", citations=[],
                                           is_refusal=False)}

    class _FakeCritic:
        def run(self, summary, plan, draft):
            seen["critic_block"] = summary
            return {"ok": True, "data": NS(passed=True, fix_hint="pass", issues=[])}

    monkeypatch.setattr(ra, "_build_answerer", lambda lane="lite": _FakeAnswerer(lane))
    monkeypatch.setattr(ra, "_build_critic", lambda: _FakeCritic())

    out = ra._answer({"question": "香港有哪些投资入境计划",
                      "rag_ctx": {"tier": "deep", "query": "香港有哪些投资入境计划",
                                  "snippets": [snippet], "gaps": [gap]}})
    assert seen["gaps"] == [gap]
    assert "证据缺口" in seen["answer_block"] and gap in seen["answer_block"]
    assert "证据缺口" in seen["critic_block"]          
    assert out["rag_ctx"].gaps == [gap]


def test_critic_budget_exhausted_keeps_last_draft(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra
    from types import SimpleNamespace as NS

    calls = {"answer": 0}

    class _FakeAnswerer:
        def __init__(self, lane): pass
        def run(self, summary, report):
            calls["answer"] += 1
            return {"ok": True, "data": NS(answer=f"v{calls['answer']}",
                                           citations=[], is_refusal=False)}

    class _FakeCritic:
        def run(self, summary, plan, draft):
            return {"ok": True, "data": NS(passed=False, fix_hint="fix_draft",
                                           issues=[NS(detail="口径未标注")])}

    monkeypatch.setattr(ra, "_build_retriever", lambda: None)
    monkeypatch.setattr(ra, "_build_answerer", lambda lane="lite": _FakeAnswerer(lane))
    monkeypatch.setattr(ra, "_build_critic", lambda: _FakeCritic())

    out = ra._answer({"rag_ctx": {"tier": "deep", "query": "q", "snippets": []}})
    ctx = out["rag_ctx"]
    assert calls["answer"] == 3                         
    assert ctx.fix_rounds == 2 and ctx.last_fix_hint == "fix_draft"
    assert ctx.outcome == "answered"                    


def test_retrieve_escalation_replans(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra
    from types import SimpleNamespace as NS

    calls = {"plan": 0, "retrieve": 0}

    class _FakePlanner:
        def run(self, summary):
            calls["plan"] += 1
            return {"ok": True, "data": _fake_plan()}

    class _FakeRetriever:
        def run(self, summary):
            calls["retrieve"] += 1
            weak = calls["retrieve"] == 1          
            if weak:
                from qa_harness.qa_harness.core.types import Evidence
                return {"ok": True, "data": NS(
                    evidences=[Evidence(chunk_id="c0", content="口径散落在财报脚注",
                                        source="x", doc_type="md", chunk_index=0,
                                        score=0.1, match_type="hybrid")],
                    gaps=["缺专门文档"], escalate=weak, escalate_reason="疑似多跳")}
            from qa_harness.qa_harness.core.types import Evidence
            return {"ok": True, "data": NS(
                evidences=[Evidence(chunk_id="c1", content="成交额口径...", source="x",
                              doc_type="md", chunk_index=0, score=0.8, match_type="hybrid")],
                gaps=[], escalate=False, escalate_reason="")}

    monkeypatch.setattr(ra, "_build_planner", lambda: _FakePlanner())
    monkeypatch.setattr(ra, "_build_retriever", lambda: _FakeRetriever())

    state = {"question": "成交额和口径", "rag_tier": "lite", "rag_ctx": None}
    out = ra._retrieve(state)                       
    ctx = out["rag_ctx"]
    assert ctx.escalate_requested is True and ctx.escalate_reason == "疑似多跳"
    assert ra._after_retrieve_route({"rag_ctx": ctx}) == "plan"

    plan_out = ra._plan({"question": "成交额和口径", "rag_tier": "lite",
                         "rag_ctx": ctx})           
    ctx2 = plan_out["rag_ctx"]
    assert calls["plan"] == 1 and ctx2.tier == "deep" and "成交额官方口径" in ctx2.plan_md

    out2 = ra._retrieve({"question": "成交额和口径", "rag_tier": "deep",
                         "rag_ctx": ctx2})          
    ctx3 = out2["rag_ctx"]
    assert calls["retrieve"] == 2 and ctx3.escalate_requested is False
    assert ra._after_retrieve_route({"rag_ctx": ctx3}) == "answer"


def test_absent_evidence_skips_escalation():
    import unified_agent.adapters.rag_adapter as ra
    from unified_agent.core.state import RAGContext

    empty = RAGContext(query="销售金额统计口径", tier="lite", snippets=[],
                       escalate_requested=True, escalate_reason="证据完全缺失")
    assert ra._after_retrieve_route({"rag_ctx": empty, "kb_ids": ["default"]}) == "web_fallback"
    assert ra._after_retrieve_route({"rag_ctx": empty, "kb_ids": []}) == "answer"

    weak = RAGContext(query="成交额口径", tier="lite",
                      snippets=[{"content": "口径散落在财报脚注", "score": 0.1}],
                      escalate_requested=True, escalate_reason="疑似多跳")
    assert ra._after_retrieve_route({"rag_ctx": weak, "kb_ids": ["default"]}) == "plan"


def test_draft_escalate_recorded_then_consumed(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra
    from types import SimpleNamespace as NS

    class _FakeAnswerer:
        def __init__(self, lane): pass
        def run(self, summary, report):
            return {"ok": True, "data": NS(answer="没把握", citations=[],
                                           is_refusal=False, escalate=True,
                                           escalate_reason="证据覆盖不足")}

    class _FakePlanner:
        def run(self, summary):
            return {"ok": True, "data": _fake_plan()}

    monkeypatch.setattr(ra, "_build_answerer", lambda lane="lite": _FakeAnswerer(lane))
    monkeypatch.setattr(ra, "_build_planner", lambda: _FakePlanner())
    out = ra._answer({"rag_ctx": {"tier": "lite", "query": "q", "snippets": []}})
    ctx = out["rag_ctx"]
    assert ctx.draft_escalated is True and ctx.draft_escalate_reason == "证据覆盖不足"
    assert ctx.outcome == "answered"                
    assert ra._after_answer_route({"rag_ctx": ctx}) == "plan"     
    plan_out = ra._plan({"question": "q", "rag_tier": "lite", "rag_ctx": ctx})
    ctx2 = plan_out["rag_ctx"]
    assert ctx2.tier == "deep" and ctx2.draft_escalated is False
    assert ctx2.draft_escalate_reason == "" and ctx2.plan_md      


def test_after_answer_route_guards():
    import unified_agent.adapters.rag_adapter as ra
    assert ra._after_answer_route({"rag_ctx": {"tier": "deep",
                                               "draft_escalated": True}}) == ra.END
    assert ra._after_answer_route({"rag_ctx": {"tier": "lite", "draft_escalated": True,
                                               "status": "error"}}) == ra.END
    assert ra._after_answer_route({"rag_ctx": {"tier": "lite",
                                               "draft_escalated": False}}) == ra.END


def test_plan_consumes_answer_escalation_on_planner_failure(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra

    class _FailPlanner:
        def run(self, summary):
            return {"ok": False, "error": "LLM 超时"}

    monkeypatch.setattr(ra, "_build_planner", lambda: _FailPlanner())
    ctx = ra.RAGContext(tier="lite", query="q", draft_escalated=True,
                        draft_escalate_reason="没把握")
    out = ra._plan({"question": "q", "rag_tier": "lite", "rag_ctx": ctx})
    c = out["rag_ctx"]
    assert c.tier == "deep" and c.draft_escalated is False
    assert c.draft_escalate_reason == "" and c.plan_md == ""


def test_retrieve_never_downgrades_deep_tier(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra
    from types import SimpleNamespace as NS
    from qa_harness.qa_harness.core.types import Evidence

    class _FakeRetriever:
        def run(self, summary):
            return {"ok": True, "data": NS(
                evidences=[Evidence(chunk_id="c1", content="x", source="s",
                                    doc_type="md", chunk_index=0, score=0.8,
                                    match_type="hybrid")],
                gaps=[], escalate=True, escalate_reason="仍想升")}

    monkeypatch.setattr(ra, "_build_retriever", lambda: _FakeRetriever())
    ctx = ra.RAGContext(tier="deep", query="q")
    out = ra._retrieve({"question": "q", "rag_tier": "lite", "rag_ctx": ctx})
    c = out["rag_ctx"]
    assert c.tier == "deep"                    
    assert c.escalate_requested is False       
    assert ra._after_retrieve_route({"rag_ctx": c}) == "answer"


def test_plan_resets_stale_artifacts_on_new_question():
    import unified_agent.adapters.rag_adapter as ra
    stale = ra.RAGContext(tier="deep", query="旧问题", plan_md="旧计划",
                          answer="旧答案", draft_escalated=True,
                          draft_escalate_reason="旧申请",
                          snippets=[{"chunk_id": "c1"}], fix_rounds=2)
    out = ra._plan({"question": "新问题", "rag_tier": "lite", "rag_ctx": stale})
    c = out["rag_ctx"]                         
    assert c.query == "新问题" and c.plan_md == "" and c.answer == ""
    assert c.draft_escalated is False and c.snippets == [] and c.fix_rounds == 0
    assert c.tier == "lite"                    



def test_sql_lane_injects_resolved_question(monkeypatch):
    import unified_agent.graph.builder as b
    import unified_agent.observability.tracer as tr

    monkeypatch.setattr(tr, "emit_event", lambda *a, **k: None)   
    captured = {}

    class _FakeSqlSub:
        def invoke(self, state, config=None):
            captured["q"] = state["sql_ctx"].question
            return {"sql_ctx": state["sql_ctx"]}

    monkeypatch.setattr(b, "build_sql_subgraph", lambda: _FakeSqlSub())
    g = b.build_graph()
    r = g.builder.nodes["sql"].runnable
    r.invoke({"question": "那2025年呢", "resolved_question": "2025 年华东区销售额"})
    assert captured["q"] == "2025 年华东区销售额"

    r.invoke({"question": "本月销售额"})       
    assert captured["q"] == "本月销售额"


def test_sql_lane_refreshes_stale_ctx_question(monkeypatch):
    import unified_agent.graph.builder as b
    import unified_agent.observability.tracer as tr
    from unified_agent.core.state import SQLContext

    monkeypatch.setattr(tr, "emit_event", lambda *a, **k: None)
    captured = {}

    class _FakeSqlSub:
        def invoke(self, state, config=None):
            captured["q"] = state["sql_ctx"].question
            return {"sql_ctx": state["sql_ctx"]}

    monkeypatch.setattr(b, "build_sql_subgraph", lambda: _FakeSqlSub())
    g = b.build_graph()
    r = g.builder.nodes["sql"].runnable
    r.invoke({"question": "那2025年呢", "resolved_question": "2025 年销售额",
              "sql_ctx": SQLContext(question="2024 年销售额")})
    assert captured["q"] == "2025 年销售额"


def test_plan_boundary_uses_resolved_question():
    import unified_agent.adapters.rag_adapter as ra
    stale = ra.RAGContext(tier="deep", query="2024 年销售额", plan_md="旧计划",
                          snippets=[{"chunk_id": "c1"}])
    out = ra._plan({"question": "那2025年呢", "resolved_question": "2025 年销售额",
                    "rag_tier": "lite", "rag_ctx": stale})
    c = out["rag_ctx"]
    assert c.query == "2025 年销售额" and c.plan_md == "" and c.snippets == []


def test_web_intent_forces_search_after_retrieve(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra

    strong = ra.RAGContext(query="政策", tier="lite",
                           snippets=[{"chunk_id": "c1", "score": 0.95}])
    assert ra._after_retrieve_route(
        {"rag_ctx": strong, "kb_ids": ["default"], "web_intent": True}) == "web_fallback"
    assert ra._after_retrieve_route(
        {"rag_ctx": strong, "kb_ids": ["default"], "web_intent": True,
         "web_enabled": False}) == "answer"

    with_web = ra.RAGContext(query="政策", tier="deep",
                             snippets=[{"chunk_id": "c1", "score": 0.95},
                                       {"chunk_id": "w1", "score": 0.8, "doc_type": "web"}])
    assert ra._after_retrieve_route(
        {"rag_ctx": with_web, "kb_ids": ["default"], "web_intent": True}) == "answer"


def test_web_fallback_uses_resolved_and_dedupes(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra

    captured = {}

    def fake_search(q, top_k=6):
        captured["q"] = q
        return [{"chunk_id": "w1", "score": 0.8, "doc_type": "web",
                 "source": "https://x", "content": "网页正文"}]
    monkeypatch.setattr(ra, "search_web", fake_search)

    out = ra._web_fallback({"question": "那2025年呢",
                            "resolved_question": "2025 年华东区销售额"})
    assert captured["q"] == "2025 年华东区销售额"
    c = out["rag_ctx"]
    assert [s["chunk_id"] for s in c.snippets] == ["w1"]
    assert c.provenance[0]["doc_type"] == "web" and c.provenance[0]["text"] == "网页正文"

    again = ra._web_fallback({"resolved_question": "2025 年华东区销售额", "rag_ctx": c})
    assert [s["chunk_id"] for s in again["rag_ctx"].snippets] == ["w1"]


def test_answer_escalation_full_loop(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra
    from types import SimpleNamespace as NS
    from qa_harness.qa_harness.core.types import Evidence

    calls = {"plan": 0, "retrieve": 0, "critic": 0}
    lanes = []

    class _FakePlanner:
        def run(self, summary):
            calls["plan"] += 1
            return {"ok": True, "data": _fake_plan()}

    class _FakeRetriever:
        def run(self, summary):
            calls["retrieve"] += 1
            return {"ok": True, "data": NS(
                evidences=[Evidence(chunk_id="c1", content="成交额口径...", source="x",
                                    doc_type="md", chunk_index=0, score=0.8,
                                    match_type="hybrid")],
                gaps=[], escalate=False, escalate_reason="")}

    class _FakeAnswerer:
        def __init__(self, lane):
            lanes.append(lane)
        def run(self, summary, report):
            if lanes[-1] == "lite":
                return {"ok": True, "data": NS(answer="没把握", citations=[],
                                               is_refusal=False, escalate=True,
                                               escalate_reason="证据覆盖不足")}
            return {"ok": True, "data": NS(answer="成交额=含退款 [1]", citations=["c1"],
                                           is_refusal=False, escalate=False,
                                           escalate_reason="")}

    class _FakeCritic:
        def run(self, summary, plan, draft):
            calls["critic"] += 1
            return {"ok": True, "data": NS(passed=True, fix_hint="pass", issues=[])}

    monkeypatch.setattr(ra, "_build_planner", lambda: _FakePlanner())
    monkeypatch.setattr(ra, "_build_retriever", lambda: _FakeRetriever())
    monkeypatch.setattr(ra, "_build_answerer", lambda lane="lite": _FakeAnswerer(lane))
    monkeypatch.setattr(ra, "_build_critic", lambda: _FakeCritic())

    g = ra.build_rag_subgraph()
    final = g.invoke({"question": "q", "rag_tier": "lite", "rag_ctx": None})
    c = final["rag_ctx"]
    assert lanes == ["lite", "deep"]           
    assert calls == {"plan": 1, "retrieve": 2, "critic": 1}
    assert c.tier == "deep" and c.answer == "成交额=含退款 [1]"
    assert c.draft_escalated is False and c.outcome == "answered" and c.status == "ok"


def test_answer_escalation_terminates_when_planner_fails(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra
    from types import SimpleNamespace as NS
    from qa_harness.qa_harness.core.types import Evidence

    calls = {"plan": 0, "retrieve": 0}
    lanes = []

    class _FailPlanner:
        def run(self, summary):
            calls["plan"] += 1
            return {"ok": False, "error": "LLM 超时"}

    class _FakeRetriever:
        def run(self, summary):
            calls["retrieve"] += 1
            return {"ok": True, "data": NS(
                evidences=[Evidence(chunk_id="c1", content="x", source="s",
                                    doc_type="md", chunk_index=0, score=0.8,
                                    match_type="hybrid")],
                gaps=[], escalate=False, escalate_reason="")}

    class _FakeAnswerer:
        def __init__(self, lane):
            lanes.append(lane)
        def run(self, summary, report):
            esc = lanes[-1] == "lite"
            return {"ok": True, "data": NS(answer="答", citations=["c1"],
                                           is_refusal=False, escalate=esc,
                                           escalate_reason="没把握" if esc else "")}

    class _FakeCritic:
        def run(self, summary, plan, draft):
            return {"ok": True, "data": NS(passed=True, fix_hint="pass", issues=[])}

    monkeypatch.setattr(ra, "_build_planner", lambda: _FailPlanner())
    monkeypatch.setattr(ra, "_build_retriever", lambda: _FakeRetriever())
    monkeypatch.setattr(ra, "_build_answerer", lambda lane="lite": _FakeAnswerer(lane))
    monkeypatch.setattr(ra, "_build_critic", lambda: _FakeCritic())

    g = ra.build_rag_subgraph()
    final = g.invoke({"question": "q", "rag_tier": "lite", "rag_ctx": None})
    c = final["rag_ctx"]
    assert calls["plan"] == 1 and calls["retrieve"] == 2   
    assert lanes == ["lite", "deep"]
    assert c.tier == "deep" and c.plan_md == ""            
    assert c.draft_escalated is False and c.status == "ok"


def test_chitchat_answer_produces_final_report(monkeypatch):
    import unified_agent.agents.chitchat as cc
    monkeypatch.setattr(cc, "ask_llm_text",
                        lambda system, user, max_tokens=4000, **kw: "你好！有什么可以帮你的？")
    out = cc.answer({"question": "你好", "history": []})
    r = out["final_report"]
    assert r["answer_md"] == "你好！有什么可以帮你的？" and r["mode"] == "chitchat"
    assert r["claims"] == [] and r["citations"] == [] and r["sql"] is None
    from unified_agent.core.contracts import UnifiedFinalReport, validate
    assert validate(UnifiedFinalReport(**r), {}) == []


def test_chitchat_answer_fallback_on_llm_failure(monkeypatch):
    import unified_agent.agents.chitchat as cc

    def _boom(system, user, max_tokens=4000):
        raise RuntimeError("LLM 鉴权失败")
    monkeypatch.setattr(cc, "ask_llm_text", _boom)
    out = cc.answer({"question": "hi", "history": None})
    assert out["final_report"]["answer_md"] == cc._FALLBACK
    assert out["task_status"] == "done"



def test_retrieve_syncs_state_tier_and_question(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra

    seen = {}

    class _FakeRetriever:
        def run(self, summary):
            seen["summary"] = summary
            return {"ok": True, "data": ra.EvidenceReport(evidences=[], gaps=[])}
    monkeypatch.setattr(ra, "_build_retriever", lambda: _FakeRetriever())
    out = ra._retrieve({"question": "退货率口径", "rag_tier": "deep", "rag_ctx": None})
    ctx = out["rag_ctx"]
    assert ctx.tier == "deep" and ctx.query == "退货率口径"
    assert "deep" in seen["summary"] and "退货率口径" in seen["summary"]



def _fake_plan():
    from types import SimpleNamespace as NS
    return NS(sub_questions=[
        NS(question="成交额官方口径", search_queries=["成交额 口径"], criterion="文档明确含退款与否"),
        NS(question="上月成交额数值", search_queries=["上月 成交额"], criterion="有具体数字"),
    ], success_criteria=["每个子问题都有出处", "口径与数字一致"])


def test_plan_deep_calls_planner_and_feeds_retrieve(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra

    calls = []

    class _FakePlanner:
        def run(self, summary):
            calls.append(summary)
            return {"ok": True, "data": _fake_plan()}

    class _FakeRetriever:
        def run(self, summary):
            calls.append(summary)
            return {"ok": True, "data": ra.EvidenceReport(evidences=[], gaps=[])}
    monkeypatch.setattr(ra, "_build_planner", lambda: _FakePlanner())
    monkeypatch.setattr(ra, "_build_retriever", lambda: _FakeRetriever())

    plan_out = ra._plan({"question": "成交额和口径", "rag_tier": "deep", "rag_ctx": None})
    ctx = plan_out["rag_ctx"]
    assert "成交额官方口径" in ctx.plan_md and "整体验收标准" in ctx.plan_md
    ra._retrieve({"question": "成交额和口径", "rag_tier": "deep",
                  "rag_ctx": ctx.model_dump()})
    assert len(calls) == 2 and "【检索计划】" in calls[1]     


def test_plan_lite_skips_planner(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra

    def _boom():
        raise AssertionError("lite 档不得调 planner")
    monkeypatch.setattr(ra, "_build_planner", _boom)
    assert ra._plan({"question": "q", "rag_tier": "lite", "rag_ctx": None}) == {}


def test_plan_planner_failure_falls_back(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra

    class _BadPlanner:
        def run(self, summary):
            return {"ok": False, "error": "llm down"}
    monkeypatch.setattr(ra, "_build_planner", lambda: _BadPlanner())
    out = ra._plan({"question": "q", "rag_tier": "deep", "rag_ctx": None})
    ctx = out["rag_ctx"]
    assert ctx.plan_md == "" and ctx.tier == "deep"      



def test_context_brief_filters_sql_and_caps_memory():
    import unified_agent.adapters.rag_adapter as ra

    state = {"history": [], "recalled_memory": [
        {"text": "rag口径：GMV含退款", "metadata": {"memory_type": "rag"}, "distance": 0.1},
        {"text": "用户偏好表格展示", "metadata": {"memory_type": "general"}, "distance": 0.2},
        {"text": "华北口径按含退款", "metadata": {"memory_type": "sql"}, "distance": 0.3},
    ]}
    brief = ra._context_brief(state)
    assert "GMV含退款" in brief and "用户偏好表格展示" in brief
    assert "华北口径按含退款" not in brief      
    assert "对话上下文" not in brief            


def test_context_brief_history_window_and_roles():
    import unified_agent.adapters.rag_adapter as ra
    from langchain_core.messages import AIMessage, HumanMessage

    msgs = [HumanMessage("q1"), AIMessage("a1"),
            HumanMessage("q2"), AIMessage("a2"), HumanMessage("那第二个呢")]
    brief = ra._context_brief({"history": msgs, "recalled_memory": []})
    assert "那第二个呢" in brief and "q1：" not in brief     
    assert "用户" in brief and "助手" in brief


def test_context_brief_empty_state_returns_empty():
    import unified_agent.adapters.rag_adapter as ra

    assert ra._context_brief({}) == ""
    assert ra._context_brief({"history": [], "recalled_memory": [
        {"text": "sql口径", "metadata": {"memory_type": "sql"}}]}) == ""   


def test_task_summary_injects_memory_blocks():
    import unified_agent.adapters.rag_adapter as ra
    from langchain_core.messages import HumanMessage

    c = ra.RAGContext(query="退货率口径", tier="lite")
    state = {"history": [HumanMessage("上次问的GMV是什么")],
             "recalled_memory": [{"text": "rag：GMV含退款", "metadata": {"memory_type": "rag"}}]}
    s = ra._task_summary(c, state)
    assert "【对话上下文（近期）】" in s and "【相关记忆（跨会话）】" in s
    assert s.index("【用户问题】") > s.index("【相关记忆（跨会话）】")   
    assert "本轮实际要求如下" in s                                      
    assert "本轮实际要求如下" not in ra._task_summary(c)                 
    assert ra._task_summary(c, {}) == ra._task_summary(c)   


def test_retrieve_passes_memory_context(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra
    from langchain_core.messages import HumanMessage

    seen = {}

    class _FakeRetriever:
        def run(self, summary):
            seen["summary"] = summary
            return {"ok": True, "data": ra.EvidenceReport(evidences=[], gaps=[])}
    monkeypatch.setattr(ra, "_build_retriever", lambda: _FakeRetriever())

    ra._retrieve({"question": "那第二个呢", "rag_tier": "lite", "rag_ctx": None,
                  "history": [HumanMessage("第一，退货率；第二，退款率")],
                  "recalled_memory": [{"text": "rag：退款率含部分退款",
                                       "metadata": {"memory_type": "rag"}}]})
    assert "那第二个呢" in seen["summary"]
    assert "第一，退货率；第二，退款率" in seen["summary"]
    assert "退款率含部分退款" in seen["summary"]



def test_build_claims_parses_and_drops_invalid(monkeypatch):
    import unified_agent.agents.merge as m

    def fake(system, user, **kw):
        return {"claims": [
            {"text": "本月成交 1200 元", "numbers": [
                {"value": "1200", "origin": "sql", "evidence_id": "sql-row-0"}]},
            {"text": "坏条目", "numbers": [
                {"value": "9", "origin": "video", "evidence_id": "x"}]},  
        ]}
    monkeypatch.setattr(m, "ask_llm_json", fake)
    claims = m.build_claims({"question": "q", "rag_ctx": None, "sql_ctx": None})
    assert len(claims) == 1 and claims[0].numbers[0].evidence_id == "sql-row-0"


def test_claims_ledger_and_digest_are_capped(monkeypatch):
    import unified_agent.agents.merge as m

    ledger = [{"origin": "rag", "value": f"第{i}行 数值{i}", "evidence_id": f"c{i}"}
              for i in range(m._LEDGER_MAX_LINES + 12)]
    lines = m._ledger_lines(ledger, "数值")
    assert len(lines.splitlines()) == m._LEDGER_MAX_LINES

    seen = {}

    def fake(system, user, **kw):
        seen["user"] = user
        return {"claims": []}

    monkeypatch.setattr(m, "ask_llm_json", fake)
    from unified_agent.core.state import SQLContext
    m.build_claims({"question": "q", "rag_ctx": None,
                    "sql_ctx": SQLContext(
                        analysis_md="长" * (m._SQL_DIGEST_MAX_CHARS + 500))})
    assert len(seen["user"]) < m._SQL_DIGEST_MAX_CHARS + 2000


def test_build_claims_llm_failure_returns_empty(monkeypatch):
    import unified_agent.agents.merge as m

    monkeypatch.setattr(m, "ask_llm_json",
                        lambda s, u, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    assert m.build_claims({}) == []


def test_render_llm_text_lands_and_mode_legal(monkeypatch):
    import unified_agent.agents.merge as m
    from unified_agent.core.state import SQLContext

    monkeypatch.setattr(m, "ask_llm_text",
                        lambda s, u, **kw: "# 结论\n本月成交 1200 元（执行 SQL 见下）")
    report = m.render({"question": "q", "task_type": "hybrid",
                       "sql_ctx": SQLContext(executed_sql="SELECT 1"),
                       "rag_ctx": None}, claims=[])
    assert report.answer_md.startswith("# 结论") and report.mode == "hybrid"
    assert "车道失败备忘" in report.answer_md               
    assert [p.lane for p in report.partial_failures] == ["rag"]


def test_render_failure_yields_number_free_fallback(monkeypatch):
    import unified_agent.agents.merge as m
    from unified_agent.core.state import RAGContext

    def boom(s, u, **kw):
        raise RuntimeError("down")
    monkeypatch.setattr(m, "ask_llm_text", boom)
    report = m.render({"question": "q", "task_type": "rag",
                       "rag_ctx": RAGContext()}, claims=[])
    assert "LLM 渲染不可用" in report.answer_md and report.claims == []


def test_render_empty_llm_text_yields_lane_fallback(monkeypatch):
    import unified_agent.agents.merge as m
    from unified_agent.core.state import SQLContext

    monkeypatch.setattr(m, "ask_llm_json", lambda s, u, **kw: {"claims": []})
    monkeypatch.setattr(m, "ask_llm_text", lambda s, u, **kw: "   ")
    sql = SQLContext(status="ok", outcome="answered", executed_sql="SELECT sum(x) FROM t",
                     analysis_md="本月成交 1200 元。")
    report = m.render({"question": "q", "task_type": "hybrid",
                       "rag_ctx": None, "sql_ctx": sql}, claims=[])
    assert "本月成交 1200 元" in report.answer_md          
    assert "车道失败备忘" in report.answer_md               
    assert report.answer_md.strip().startswith("本月成交")   


def test_merge_total_failure_refusal_skips_llm(monkeypatch):
    import unified_agent.agents.merge as m
    from unified_agent.core.contracts import validate
    from unified_agent.core.state import SQLContext

    def no_llm(*a, **kw):
        raise AssertionError("全军覆没不得调 LLM")
    monkeypatch.setattr(m, "ask_llm_json", no_llm)
    monkeypatch.setattr(m, "ask_llm_text", no_llm)
    c = SQLContext(status="error", outcome="refused", retriable=True,
                   error_code="runtime: Code: 63. DB::Exception: neighbor() OVER",
                   db_error="Code: 63. DB::Exception: Syntax error: neighbor() OVER ...",
                   sql="SELECT toYYYYMM(order_date) AS m, neighbor(amount, -1) OVER () FROM t")
    out = m.merge({"question": "环比", "task_type": "sql", "sql_ctx": c})
    r = out["final_report"]
    assert "本次未能给出答案" in r["answer_md"]
    assert "runtime: Code: 63" in r["answer_md"]            
    assert "数据库回执" in r["answer_md"]
    assert "未成功执行" in r["answer_md"] and "neighbor" in r["answer_md"]   
    assert r["sql"] is None and r["claims"] == []
    assert r["partial_failures"][0]["lane"] == "sql"
    assert validate(m.UnifiedFinalReport(**r), {"sql_ctx": c}) == []


def test_merge_hybrid_partial_failure_appends_memo(monkeypatch):
    import unified_agent.agents.merge as m
    from unified_agent.core.contracts import validate
    from unified_agent.core.state import RAGContext, SQLContext

    monkeypatch.setattr(m, "ask_llm_json",
                        lambda s, u, **kw: {"claims": [{"text": "文档说营收增长", "numbers": []}]})
    monkeypatch.setattr(m, "ask_llm_text",
                        lambda s, u, **kw: "# 结论\n文档口径营收保持增长。")
    rag = RAGContext(answer="营收保持增长", citations=["c1"],
                     provenance=[{"text": "营收保持增长", "chunk_id": "c1"}])
    sql = SQLContext(status="error", outcome="refused", retriable=True,
                     error_code="runtime: Code: 63. DB::Exception: ...",
                     db_error="Code: 63. ...", sql="SELECT neighbor(amount,-1) OVER () FROM t")
    out = m.merge({"question": "q", "task_type": "hybrid",
                   "rag_ctx": rag, "sql_ctx": sql})
    r = out["final_report"]
    assert "车道失败备忘" in r["answer_md"] and "库内数据（SQL）车道本次未能产出" in r["answer_md"]
    assert "runtime" in r["answer_md"]                      
    assert r["sql"] is None                                 
    assert r["citations"] and r["citations"][0]["chunk_id"] == "c1"
    assert r["citations"][0].get("excerpt") == "营收保持增长"
    assert [p["lane"] for p in r["partial_failures"]] == ["sql"]
    assert validate(m.UnifiedFinalReport(**r), {"rag_ctx": rag, "sql_ctx": sql}) == []


def test_merge_appends_degrade_notice(monkeypatch):
    import unified_agent.agents.merge as m
    from unified_agent.core.contracts import validate
    from unified_agent.core.state import SQLContext

    monkeypatch.setattr(m, "ask_llm_json",
                        lambda s, u, **kw: {"claims": [{"text": "库内营收保持增长", "numbers": []}]})
    monkeypatch.setattr(m, "ask_llm_text", lambda s, u, **kw: "# 结论\n库内营收保持增长。")
    sql = SQLContext(status="ok", outcome="answered", sql="SELECT 1",
                     checked_sql_pre_injection="SELECT 1",
                     query_result={"rows": [], "columns": []}, analysis_md="库内营收保持增长")
    out = m.merge({"question": "那2025年呢", "task_type": "sql", "sql_ctx": sql,
                   "degrade_notice": "系统多轮澄清后仍未确定您要查的是内部文档还是业务数据，"
                                     "已按默认方向「业务数据」作答；如结果不符合预期，请重新提问。"})
    r = out["final_report"]
    assert "提示（系统追加）" in r["answer_md"]
    assert "已按默认方向" in r["answer_md"]
    assert validate(m.UnifiedFinalReport(**r), {"sql_ctx": sql}) == []



def _hist(n):
    from langchain_core.messages import AIMessage, HumanMessage
    out = []
    for i in range(n):
        out.append(HumanMessage(id=f"q{i}", content=f"Q{i}"))
        out.append(AIMessage(id=f"a{i}", content=f"A{i}"))
    return out


def test_compress_skips_when_window_not_full(monkeypatch):
    import unified_agent.memory.compress as c

    monkeypatch.setattr(c, "ask_llm_json",
                        lambda s, u: (_ for _ in ()).throw(AssertionError("不该调 LLM")))
    assert c.compress_history({"history": _hist(1)}) == {}     


def test_compress_notes_land_and_prune(monkeypatch):
    import unified_agent.memory.compress as c

    seen = {}

    def fake(s, u):
        seen["history_json"] = u
        return {"digest": "压缩后摘要",
                "notes": {"preferences": ["口径=含退款"],
                          "entities": ["v_fact_order"], "open_items": []}}
    monkeypatch.setattr(c, "ask_llm_json", fake)
    monkeypatch.setattr(c, "_estimate_history_tokens", lambda h: 3000)  
    out = c.compress_history({"history": _hist(4)})            
    assert out["conversation_notes"]["preferences"] == ["口径=含退款"]
    pruned = [x for x in out["history"]]
    assert all(type(x).__name__ == "RemoveMessage" for x in pruned)
    assert {x.id for x in pruned} == {"q0", "a0"}
    assert "Q0" in seen["history_json"] and "Q3" not in seen["history_json"]  


def test_compress_merges_existing_notes(monkeypatch):
    import unified_agent.memory.compress as c

    monkeypatch.setattr(c, "ask_llm_json",
                        lambda s, u: {"digest": "d", "notes": {
                            "preferences": ["口径=含退款", "偏好表格"],
                            "entities": [], "open_items": ["Q3 未答"]}})
    monkeypatch.setattr(c, "_estimate_history_tokens", lambda h: 3000)
    out = c.compress_history({"history": _hist(4),
                              "conversation_notes": {"preferences": ["口径=含退款"],
                                                     "entities": ["v_fact_order"],
                                                     "open_items": []}})
    n = out["conversation_notes"]
    assert n["preferences"] == ["口径=含退款", "偏好表格"]       
    assert n["entities"] == ["v_fact_order"] and n["open_items"] == ["Q3 未答"]


def test_compress_failure_keeps_history(monkeypatch):
    import unified_agent.memory.compress as c

    def boom(s, u):
        raise RuntimeError("down")
    monkeypatch.setattr(c, "ask_llm_json", boom)
    monkeypatch.setattr(c, "_estimate_history_tokens", lambda h: 3000)
    assert c.compress_history({"history": _hist(4)}) == {}     


def test_compress_bad_notes_shape_ignored(monkeypatch):
    import unified_agent.memory.compress as c

    monkeypatch.setattr(c, "ask_llm_json",
                        lambda s, u: {"digest": "d", "notes": {"preferences": "不是列表"}})
    monkeypatch.setattr(c, "_estimate_history_tokens", lambda h: 3000)
    assert c.compress_history({"history": _hist(4)}) == {}     
