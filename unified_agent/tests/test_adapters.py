from types import SimpleNamespace
import json

from unified_agent.adapters.rag_adapter import build_rag_subgraph
from unified_agent.adapters.sql_adapter import build_sql_subgraph, _wrap_data_node
from unified_agent.core.state import RAGContext, SQLContext


def test_retriever_tool_loop_is_capped(monkeypatch):
    import unified_agent.adapters.rag_adapter as ra

    class _FakeRetriever:
        def __init__(self, **kw):
            self.max_loops = 6                     

    monkeypatch.setattr(ra, "RetrieverAgent", _FakeRetriever)
    monkeypatch.setattr(ra, "_shared_registry", lambda: None)
    r = ra._build_retriever()
    assert r.max_loops == int(ra.os.getenv("UA_RAG_RETRIEVER_LOOPS", "4")) == 4


def test_budgeted_registry_blocks_after_low_score_streak():
    import unified_agent.adapters.rag_adapter as ra
    from core import ToolDefinition

    scores = {"a": 0.006, "b": 0.006, "hit": 0.62}

    def _handler(query="", **_):
        return {"ok": True, "data": {"evidences": [{"score": scores.get(query, 0.0)}]}}

    class _Inner:
        def get(self, name):
            return ToolDefinition(name=name, description="d", parameters={},
                                  handler=_handler)

    budget = ra._BudgetedRegistry(_Inner(), low_limit=2, floor=0.05)
    assert budget.get("list_sources") is not None        
    tool = budget.get("retrieve_knowledge")
    tool.handler(query="a")
    assert not budget.blocked
    tool.handler(query="b")                              
    assert budget.blocked and budget.best_score < 0.05
    out = tool.handler(query="hit")
    assert out["ok"] is False and "额度已用尽" in out["error"]

    budget2 = ra._BudgetedRegistry(_Inner(), low_limit=2, floor=0.05)
    tool2 = budget2.get("retrieve_knowledge")
    tool2.handler(query="a")
    tool2.handler(query="hit")
    tool2.handler(query="b")
    assert not budget2.blocked and budget2.best_score == 0.62


def test_both_subgraphs_compile():
    assert build_rag_subgraph() is not None
    assert build_sql_subgraph() is not None


def test_sql_ctx_roundtrip():
    from unified_agent.adapters.sql_adapter import _sync_from_ctx, _sync_to_ctx
    state = {"sql_ctx": SQLContext(question="成交额")}
    flat = _sync_from_ctx(state)
    assert flat["question"] == "成交额"
    back = _sync_to_ctx(state, {"schema": {"t": "v_fact"}})
    assert back["sql_ctx"].schema == {"t": "v_fact"}


def test_sync_to_ctx_keeps_output_fields():
    from unified_agent.adapters.sql_adapter import _sync_to_ctx
    state = {"sql_ctx": SQLContext()}
    out = _sync_to_ctx(state, {"executed_sql": "SELECT 1",
                               "query_result": {"row_count": 0, "rows": []},
                               "checked_sql_pre_injection": "SELECT 1"})
    c = out["sql_ctx"]
    assert c.executed_sql == "SELECT 1"
    assert c.query_result == {"row_count": 0, "rows": []}
    assert c.checked_sql_pre_injection == "SELECT 1"



def _cfg(**overrides):
    cfg = SimpleNamespace(
        gateway=SimpleNamespace(database="sales_dw", catalog_ttl_sec=300),
        sql_guard=SimpleNamespace(use_reviewer=False, use_auto_repair=True, max_sql_repairs=2),
        graph=SimpleNamespace(empty_result_preview_rows=50, max_escalations=2),
    )
    for path, val in overrides.items():
        obj = cfg
        parts = path.split(".")
        for p in parts[:-1]:
            obj = getattr(obj, p)
        setattr(obj, parts[-1], val)
    return cfg


def test_retrieve_schema_smoke(monkeypatch):
    from data_analyze_agent.agents import retriever
    from data_analyze_agent.agents.retriever import make_retrieve_schema_node

    monkeypatch.setattr(retriever, "get_app_config", lambda: _cfg())
    monkeypatch.setattr(retriever.gateway, "list_tables", lambda: [{"name": "orders", "comment": "订单表", "is_view": False}])
    monkeypatch.setattr(retriever.gateway, "is_business_table", lambda n: True)
    monkeypatch.setattr(retriever.gateway, "get_table_schema", lambda n: {"columns": [{"name": "id", "type": "bigint"}]})
    monkeypatch.setattr(retriever, "match_metrics", lambda q: [])
    monkeypatch.setattr(retriever, "render_metric_cards", lambda m: [])
    monkeypatch.setattr(retriever, "match_fewshots", lambda q: [])
    monkeypatch.setattr(retriever, "render_fewshot_cards", lambda f: [])
    monkeypatch.setattr(retriever, "match_fewshots_semantic", lambda q: [])

    node = _wrap_data_node(make_retrieve_schema_node())
    out = node({"sql_ctx": SQLContext(question="查订单")})
    schema = out["sql_ctx"].schema
    assert isinstance(schema, list) and len(schema) == 1
    assert "orders" in schema[0]["content"]


def test_plan_smoke(monkeypatch):
    from data_analyze_agent.agents import planner

    plan_data = {"goal": "查订单", "metrics": [{"name": "订单数", "expression_hint": "COUNT(*)", "related_tables": []}],
                 "dimensions": [], "time_range": "", "filters": [], "joins": [],
                 "success_criteria": ["返回订单数"], "need_chart": False, "notes": ""}
    monkeypatch.setattr(planner, "ask_llm_json", lambda *a, **k: {"ok": True, "data": plan_data})
    monkeypatch.setattr(planner, "create_llm_for_tier", lambda t: None)
    monkeypatch.setattr(planner, "get_prompt", lambda r: "sys")

    node = _wrap_data_node(planner.plan_node)
    out = node({"sql_ctx": SQLContext(question="查订单")})
    assert out["sql_ctx"].question == "查订单"


def test_write_sql_smoke(monkeypatch):
    from data_analyze_agent.agents import sql_writer

    attempt_data = {"sql": "SELECT COUNT(*) FROM orders", "used_tables": ["orders"], "rationale": "统计订单"}
    monkeypatch.setattr(sql_writer, "ask_llm_json", lambda *a, **k: {"ok": True, "data": attempt_data})
    monkeypatch.setattr(sql_writer, "create_llm_for_tier", lambda t: None)
    monkeypatch.setattr(sql_writer, "get_prompt", lambda r: "sys")
    monkeypatch.setattr(sql_writer, "hints_by_type", lambda s, *t: None)

    node = _wrap_data_node(sql_writer.write_sql_node)
    out = node({"sql_ctx": SQLContext(question="查订单数")})
    assert out["sql_ctx"].sql == "SELECT COUNT(*) FROM orders"


def test_review_sql_smoke(monkeypatch):
    from data_analyze_agent.agents import reviewer
    from data_analyze_agent.core import ReviewVerdict

    guard = ReviewVerdict(passed=True, issues=[], fix_hint="pass", risk_level="low", checked_sql="SELECT COUNT(*) FROM orders")
    monkeypatch.setattr(reviewer, "check", lambda **k: guard)
    monkeypatch.setattr(reviewer, "get_default_schema_cache", lambda: [])
    monkeypatch.setattr(reviewer, "get_app_config", lambda: _cfg())

    node = _wrap_data_node(reviewer.review_sql_node)
    out = node({"sql_ctx": SQLContext(question="查订单数", sql="SELECT COUNT(*) FROM orders")})
    assert out["sql_ctx"].checked_sql_pre_injection == "SELECT COUNT(*) FROM orders"


def test_execute_sql_smoke(monkeypatch):
    from data_analyze_agent.agents import executor

    qr = {"columns": ["cnt"], "rows": [(42,)], "row_count": 1, "truncated": False, "executed_sql": "SELECT COUNT(*) FROM orders"}
    monkeypatch.setattr(executor, "run_select", lambda sql: {"ok": True, "data": qr})

    node = _wrap_data_node(executor.execute_sql_node)
    out = node({"sql_ctx": SQLContext(question="查订单数", checked_sql_pre_injection="SELECT COUNT(*) FROM orders")})
    c = out["sql_ctx"]
    assert c.query_result["row_count"] == 1
    assert c.executed_sql == "SELECT COUNT(*) FROM orders"
    assert c.status == "ok"


def test_analyze_smoke(monkeypatch):
    from data_analyze_agent.agents import analyst

    analysis = {"answer_md": "共有 42 笔订单", "referenced_cells": ["row[0].cnt"],
                "confidence": "high", "is_empty": False, "empty_is_suspicious": False,
                "escalate": False, "escalate_reason": "", "chart_suggestion": "none",
                "refused": False, "refuse_reason": ""}
    monkeypatch.setattr(analyst, "ask_llm_json", lambda *a, **k: {"ok": True, "data": analysis})
    monkeypatch.setattr(analyst, "create_llm_for_tier", lambda t: None)
    monkeypatch.setattr(analyst, "get_prompt", lambda r: "sys")
    monkeypatch.setattr(analyst, "hints_by_type", lambda s, *t: None)
    monkeypatch.setattr(analyst, "numbers_backing_errors", lambda *a, **k: [])
    monkeypatch.setattr(analyst, "get_app_config", lambda: _cfg())

    node = _wrap_data_node(analyst.analyze_result_node)
    out = node({"sql_ctx": SQLContext(question="查订单数",
                                       query_result={"columns": ["cnt"], "rows": [(42,)], "row_count": 1,
                                                     "truncated": False, "executed_sql": "SELECT COUNT(*) FROM orders"})})
    assert out["sql_ctx"].analysis_md == "共有 42 笔订单"



_CHUNK = {
    "chunk_id": "c1", "content": "re.search 用于在字符串里做正则匹配，返回首个匹配对象。",
    "source": "re.md", "doc_type": "note", "chunk_index": 0,
    "score": 0.91, "match_type": "hybrid",
}


def _qa_registry(*names_and_handlers):
    from core import ToolDefinition
    from tools import ToolRegistry
    registry = ToolRegistry()
    for name, handler in names_and_handlers:
        registry.register(ToolDefinition(
            name=name, description=name,
            parameters={"type": "object", "properties": {}}, handler=handler,
        ))
    return registry


def _patch_qa_llm(monkeypatch, responses):
    import agents.base as base

    calls = {"n": 0, "msgs": []}

    def fake_call_llm(**kwargs):
        calls["n"] += 1
        calls["msgs"].append(str(kwargs.get("messages") or kwargs))
        return responses[calls["n"] - 1]

    monkeypatch.setattr(base, "call_llm", fake_call_llm)
    monkeypatch.setattr(base, "get_api_key", lambda: "test-key")
    return calls


def test_rag_retrieve_smoke(monkeypatch):
    from agents import RETRIEVER_KB_ONLY_PROFILE, RetrieverAgent
    from unified_agent.adapters import rag_adapter
    from unified_agent.adapters.rag_adapter import _retrieve

    registry = _qa_registry(
        ("retrieve_knowledge", lambda **kw: {"ok": True, "evidences": [_CHUNK]}),
        ("retrieve_batch", lambda **kw: {"ok": True, "evidences": [_CHUNK]}),
        ("get_chunk", lambda **kw: {"ok": True, "evidence": _CHUNK}),
        ("list_sources", lambda **kw: {"ok": True, "sources": ["re.md"]}),
    )
    agent = RetrieverAgent(tool_registry=registry, profile=RETRIEVER_KB_ONLY_PROFILE)
    monkeypatch.setattr(rag_adapter, "_build_retriever", lambda: agent)

    tool_call = {"id": "t1", "function": {
        "name": "retrieve_knowledge", "arguments": json.dumps({"query": "re.search"})}}
    final = {"content": json.dumps(
        {"evidences": [{"chunk_id": "c1"}], "gaps": [], "escalate": False, "escalate_reason": ""})}
    calls = _patch_qa_llm(monkeypatch, [{"tool_calls": [tool_call]}, final])

    out = _retrieve({"rag_ctx": RAGContext(query="re.search 是做什么的")})
    c = out["rag_ctx"]
    assert calls["n"] == 2                       
    assert c.status == "ok"
    assert len(c.snippets) == 1 and c.snippets[0]["chunk_id"] == "c1"
    assert c.snippets[0]["content"].startswith("re.search")
    prov = c.provenance[0]
    assert prov["source"] == "re.md" and prov["score"] == 0.91
    assert "content" not in prov                


def test_rag_retrieve_persists_gaps(monkeypatch):
    from agents import RETRIEVER_KB_ONLY_PROFILE, RetrieverAgent
    from unified_agent.adapters import rag_adapter

    registry = _qa_registry(
        ("retrieve_knowledge", lambda **kw: {"ok": True, "evidences": [_CHUNK]}),
        ("retrieve_batch", lambda **kw: {"ok": True, "evidences": [_CHUNK]}),
        ("get_chunk", lambda **kw: {"ok": True, "evidence": _CHUNK}),
        ("list_sources", lambda **kw: {"ok": True, "sources": ["re.md"]}),
    )
    agent = RetrieverAgent(tool_registry=registry, profile=RETRIEVER_KB_ONLY_PROFILE)
    monkeypatch.setattr(rag_adapter, "_build_retriever", lambda: agent)

    final = {"content": json.dumps(
        {"evidences": [{"chunk_id": "c1"}],
         "gaps": ["香港新资本投资者入境计划：两轮检索均无知识库证据"],
         "escalate": False, "escalate_reason": ""})}
    tool_call = {"id": "t1", "function": {
        "name": "retrieve_knowledge", "arguments": json.dumps({"query": "香港投资计划"})}}
    _patch_qa_llm(monkeypatch, [{"tool_calls": [tool_call]}, final])

    out = rag_adapter._retrieve({"rag_ctx": RAGContext(query="香港投资计划")})
    assert out["rag_ctx"].gaps == ["香港新资本投资者入境计划：两轮检索均无知识库证据"]


def test_rag_answer_lite_smoke(monkeypatch):
    from agents import AnswererAgent
    from unified_agent.adapters import rag_adapter
    from unified_agent.adapters.rag_adapter import _answer

    registry = _qa_registry(("get_chunk", lambda **kw: {"ok": True, "evidence": _CHUNK}))
    monkeypatch.setattr(rag_adapter, "_build_answerer", lambda lane="lite": AnswererAgent(lane=lane, tool_registry=registry))

    draft = {"content": json.dumps(
        {"answer": "re.search 用于做正则匹配。", "citations": ["c1"], "confidence": "high"})}
    _patch_qa_llm(monkeypatch, [draft])

    out = _answer({"rag_ctx": RAGContext(query="re.search 是做什么的", snippets=[_CHUNK])})
    c = out["rag_ctx"]
    assert c.status == "ok" and c.outcome == "answered"
    assert c.citations == ["c1"]
    assert "re.search" in c.answer and "参考来源" in c.answer   


def test_rag_answer_falls_back_to_extractive(monkeypatch):
    from unified_agent.adapters import rag_adapter
    from unified_agent.adapters.rag_adapter import _answer

    class _DeadAnswerer:
        tool_trace = []

        def run(self, *_a, **_k):
            return {"ok": False, "error": "answerer 最终 JSON 输出为空（纯 JSON 兜底仍失败）"}

    monkeypatch.setattr(rag_adapter, "_build_answerer", lambda lane="lite": _DeadAnswerer())
    out = _answer({"rag_ctx": RAGContext(query="re.search 是做什么的", snippets=[_CHUNK])})
    c = out["rag_ctx"]
    assert c.status == "ok" and c.outcome == "answered"
    assert c.citations == ["c1"]
    assert "系统自动抽取" in c.answer and "re.search" in c.answer


def test_rag_answer_deep_critic_loop_smoke(monkeypatch):
    from agents import AnswererAgent, CriticAgent
    from unified_agent.adapters import rag_adapter
    from unified_agent.adapters.rag_adapter import _answer

    registry = _qa_registry(("get_chunk", lambda **kw: {"ok": True, "evidence": _CHUNK}))
    monkeypatch.setattr(rag_adapter, "_build_answerer",
                        lambda lane="lite": AnswererAgent(lane=lane, tool_registry=registry))
    monkeypatch.setattr(rag_adapter, "_build_critic", lambda: CriticAgent(tool_registry=registry))

    draft1 = {"content": json.dumps(
        {"answer": "初版答案。", "citations": ["c1"], "confidence": "medium"})}
    reject = {"content": json.dumps({"passed": False, "fix_hint": "fix_draft", "issues": [
        {"issue_type": "unsupported_claim", "detail": "某论断在引用片段中找不到原文依据",
         "related_chunk_id": "c1"}]})}
    draft2 = {"content": json.dumps(
        {"answer": "修正版答案。", "citations": ["c1"], "confidence": "high"})}
    passed = {"content": json.dumps({"passed": True, "fix_hint": "pass", "issues": []})}
    calls = _patch_qa_llm(monkeypatch, [draft1, reject, draft2, passed])

    out = _answer({"rag_ctx": RAGContext(query="re.search 是做什么的", tier="deep", snippets=[_CHUNK])})
    c = out["rag_ctx"]
    assert calls["n"] == 4                       
    assert "修正版答案" in c.answer
    assert any("上一版草稿" in m for m in calls["msgs"])   
    assert c.citations == ["c1"] and c.outcome == "answered"


def test_get_chunk_capped_per_turn(monkeypatch):
    from agents import AnswererAgent
    from agents.base import _GET_CHUNK_PER_TURN

    executed = {"n": 0}

    def _get_chunk(**kw):
        executed["n"] += 1
        return {"ok": True, "evidence": _CHUNK}

    registry = _qa_registry(("get_chunk", _get_chunk))
    agent = AnswererAgent(lane="lite", tool_registry=registry)
    calls = [{"id": f"t{i}", "type": "function",
              "function": {"name": "get_chunk",
                           "arguments": json.dumps({"chunk_id": f"c{i}"})}}
             for i in range(6)]
    final = {"content": json.dumps({"answer": "x"})}
    trace = _patch_qa_llm(monkeypatch, [{"tool_calls": calls}, final])

    out = agent._run_tool_loop("写答案")
    assert out["answer"] == "x"
    assert executed["n"] == _GET_CHUNK_PER_TURN == 4
    assert any(f"本轮最多取 {_GET_CHUNK_PER_TURN} 个" in m for m in trace["msgs"])



def test_write_detect_flags_root_verbs():
    from unified_agent.adapters.sql_adapter import _write_detect
    from langgraph.graph import END as _  # noqa: F401  （占位对齐 import 风格）

    def _run(sql):
        out = _write_detect({"sql_ctx": SQLContext(sql=sql, status="ok")})
        return out["sql_ctx"].is_write

    assert _run("INSERT INTO sales_biz.orders VALUES (1)")
    assert _run("UPDATE sales_biz.orders SET a=1 WHERE id=2")
    assert _run("DELETE FROM sales_biz.orders WHERE id=3")
    assert _run("-- 备注\n  insert into sales_biz.t values (1)")   
    assert _run("-- 更新操作\nUPDATE t SET a=1\nWHERE id=2")       
    assert _run("WITH x AS (SELECT 1) INSERT INTO sales_biz.t SELECT * FROM x")
    assert not _run("SELECT * FROM sales_biz.orders")
    assert not _run("WITH x AS (SELECT 1) SELECT * FROM x")        
    assert not _run("SELECT * FROM t WHERE note = 'WITH x) DELETE'")
    assert _run("-- c\nINSERT INTO t VALUES (1);\n")
    assert _run("EXPLAIN SELECT 1")
    assert _run("SHOW TABLES")
    assert _run("SELECT * FROM t WHERE id=1 FOR UPDATE")
    assert _run("REPLACE INTO sales_biz.t VALUES (1)")
    assert not _run("SELECT replace(name, 'a', 'b') FROM t")


def test_write_detect_cte_tail_depth_aware():
    from unified_agent.adapters.sql_adapter import _write_detect

    def _run(sql):
        return _write_detect({"sql_ctx": SQLContext(sql=sql, status="ok")}
                             )["sql_ctx"].is_write

    assert not _run(
        "WITH monthly_sales AS (\n"
        "  SELECT toYYYYMM(order_date) AS month, region, SUM(amount) AS sales\n"
        "  FROM sales_dw.v_fact_sales_order_line GROUP BY month, region\n"
        ") SELECT region, sales, lag(sales) OVER (PARTITION BY region ORDER BY month)\n"
        "FROM monthly_sales")
    assert not _run("WITH a AS (SELECT f(1) AS x), b AS (SELECT 2) SELECT * FROM a, b")
    assert not _run("WITH x AS (SELECT * FROM (SELECT 1) t) SELECT * FROM x")
    assert not _run("WITH x AS (SELECT ')' AS p) SELECT * FROM x")
    assert _run("WITH x AS (SELECT f(1)) DELETE FROM t WHERE id IN (SELECT * FROM x)")


def test_write_detect_fail_closed_on_unparseable():
    from unified_agent.adapters.sql_adapter import _write_detect
    assert not _write_detect({"sql_ctx": SQLContext(sql="WITH x AS (SELECT 1", status="ok")}
                             )["sql_ctx"].is_write
    assert _write_detect({"sql_ctx": SQLContext(sql="WITH x AS (SELECT 1 DELETE FROM t", status="ok")}
                         )["sql_ctx"].is_write


def test_write_detect_short_circuit_on_error():
    from unified_agent.adapters.sql_adapter import _write_detect
    c = SQLContext(sql="INSERT INTO t VALUES (1)", status="error", error_code="x")
    assert _write_detect({"sql_ctx": c}) == {}      


def test_node_budget_is_capped_per_node():
    from unified_agent.adapters.sql_adapter import _LLM_BUDGET_SEC, _node_budget
    assert _node_budget("review_sql_node", None) == 50
    assert _node_budget("write_sql_node", None) == _LLM_BUDGET_SEC == 90
    assert _node_budget("retrieve_schema_node", None) == _LLM_BUDGET_SEC
    assert _node_budget("review_sql_node", 30) == 30    
    assert _node_budget("review_sql_node", 1) == 5      


def test_after_execute_route_skips_analyze_in_hybrid():
    from langgraph.graph import END
    from unified_agent.adapters.sql_adapter import _after_execute_route
    ok = SQLContext(status="ok", outcome="answered")
    assert _after_execute_route({"sql_ctx": ok, "task_type": "sql"}) == "analyze"
    assert _after_execute_route({"sql_ctx": ok, "task_type": "hybrid"}) is END
    assert _after_execute_route({"sql_ctx": SQLContext(status="error"),
                                 "task_type": "hybrid"}) is END
    assert _after_execute_route({"sql_ctx": SQLContext(status="ok", db_error="x"),
                                 "task_type": "hybrid"}) == "write_sql"


def test_after_review_route_write_exits_subgraph():
    from langgraph.graph import END
    from unified_agent.adapters.sql_adapter import _after_review_route
    w = SQLContext(review_passed=True, is_write=True, status="ok")
    assert _after_review_route({"sql_ctx": w}) is END
    r = SQLContext(review_passed=True, is_write=False, status="ok")
    assert _after_review_route({"sql_ctx": r}) == "policy_inject"


def test_after_sql_failed_write_skips_approval():
    from unified_agent.graph.builder import _after_sql
    bad = SQLContext(is_write=True, status="error", outcome="refused",
                     error_code="review_need_clarify")
    assert _after_sql({"sql_ctx": bad}) == "merge"
    ok = SQLContext(is_write=True, status="ok", executed_sql="INSERT INTO t VALUES (1)")
    assert _after_sql({"sql_ctx": ok}) == "approve_write"


def test_execute_write_success(monkeypatch):
    from unified_agent.agents import approve
    monkeypatch.setattr(approve, "da_execute_write", lambda sql, **kw: {
        "ok": True,
        "data": {"audit_id": "w-123", "actual_rows": 3,
                 "mutation_status": "done", "elapsed_ms": 42},
    })
    c = SQLContext(is_write=True, status="ok", executed_sql="INSERT INTO t VALUES (1)")
    out = approve.execute_write({"sql_ctx": c})
    c2 = out["sql_ctx"]
    assert "w-123" in c2.analysis_md and "3" in c2.analysis_md
    assert out["task_status"] == "running" and c2.error_code in ("", None)


def test_execute_write_passes_preview_token(monkeypatch):
    from unified_agent.agents import approve
    seen = {}

    def _fake(sql, **kw):
        seen.update(kw)
        return {"ok": True, "data": {"audit_id": "w-tok", "actual_rows": 1,
                                     "mutation_status": "done", "elapsed_ms": 5}}

    monkeypatch.setattr(approve, "da_execute_write", _fake)
    c = SQLContext(is_write=True, status="ok", executed_sql="UPDATE t SET a=1",
                   write_precheck={"status": "ok", "preview_token": "pt-1"})
    approve.execute_write({"sql_ctx": c, "datasource_id": 1,
                           "tenant_id": "0", "user_id": "1", "role": "member"})
    assert seen.get("preview_token") == "pt-1"


def test_execute_write_gateway_failure(monkeypatch):
    from unified_agent.agents import approve
    monkeypatch.setattr(approve, "da_execute_write",
                        lambda sql, **kw: {"ok": False, "data": SimpleNamespace(message="写表白名单拒绝")})
    c = SQLContext(is_write=True, status="ok", executed_sql="INSERT INTO t VALUES (1)")
    out = approve.execute_write({"sql_ctx": c})
    c2 = out["sql_ctx"]
    assert "写操作执行失败" in c2.analysis_md
    assert c2.error_code == "write_execute_failed"


def _ok_precheck(state, sql):
    return {"status": "ok", "target": "mysql", "database": "sales_biz",
            "table": "fact_sales_order_line", "operation": "update",
            "estimated_rows": 3, "max_write_rows": 1000, "audit_id": "w-pre",
            "preview_token": "pt-pre"}


def test_approve_write_refusal_sets_analysis_md(monkeypatch):
    from unified_agent.agents import approve
    monkeypatch.setattr(approve, "_precheck_write", _ok_precheck)
    monkeypatch.setattr(approve, "interrupt", lambda payload: {"granted": False})
    c = SQLContext(is_write=True, status="ok", sql="INSERT INTO t VALUES (1)")
    out = approve.approve_write({"sql_ctx": c, "session_id": "s1"}, {"configurable": {"thread_id": "t1"}})
    c2 = out["sql_ctx"]
    assert c2.outcome == "refused" and c2.error_code == "write_not_approved"
    assert "拒绝" in c2.analysis_md          


def test_approve_write_card_carries_precheck(monkeypatch):
    from unified_agent.agents import approve
    monkeypatch.setattr(approve, "_precheck_write", _ok_precheck)
    seen = {}
    monkeypatch.setattr(approve, "interrupt", lambda p: seen.update(p) or {"granted": True})
    c = SQLContext(is_write=True, status="ok", sql="UPDATE t SET a=1")
    out = approve.approve_write({"sql_ctx": c, "session_id": "s1"},
                                {"configurable": {"thread_id": "t1"}})
    schema = seen["schema"]
    assert schema["preview"] == "dry_run" and schema["sql"] == "UPDATE t SET a=1"
    assert schema["database"] == "sales_biz"
    assert schema["table"] == "fact_sales_order_line"
    assert schema["operation"] == "update" and schema["est_rows"] == 3
    assert schema["max_write_rows"] == 1000
    assert "preview_token" not in schema          
    assert c.write_precheck["status"] == "ok"    
    assert c.write_precheck["preview_token"] == "pt-pre"
    assert out["task_status"] == "running"
    assert out["sql_ctx"].write_precheck["preview_token"] == "pt-pre"  


def test_approve_write_precheck_blocked_skips_interrupt(monkeypatch):
    from unified_agent.agents import approve
    monkeypatch.setattr(approve, "_precheck_write",
                        lambda state, sql: {"status": "blocked",
                                            "error": "[too_many_rows] 预估影响 5000 行，超过单次上限 1000"})
    called = []
    monkeypatch.setattr(approve, "interrupt", lambda p: called.append(p))
    c = SQLContext(is_write=True, status="ok", sql="DELETE FROM t")
    out = approve.approve_write({"sql_ctx": c, "session_id": "s1"},
                                {"configurable": {"thread_id": "t1"}})
    c2 = out["sql_ctx"]
    assert called == []                      
    assert c2.outcome == "refused" and c2.error_code == "write_precheck_failed"
    assert "too_many_rows" in c2.analysis_md and "未做任何变更" in c2.analysis_md


def test_approve_write_precheck_unavailable_skips_interrupt(monkeypatch):
    from unified_agent.agents import approve
    monkeypatch.setattr(approve, "_precheck_write",
                        lambda state, sql: {"status": "unavailable",
                                            "error": "预检不可用：网关不可达（refused）"})
    monkeypatch.setattr(approve, "interrupt", lambda p: (_ for _ in ()).throw(AssertionError("不应挂起")))
    c = SQLContext(is_write=True, status="ok", sql="INSERT INTO t VALUES (1)")
    out = approve.approve_write({"sql_ctx": c}, {"configurable": {"thread_id": "t1"}})
    assert out["sql_ctx"].error_code == "write_precheck_failed"
    assert "网关不可达" in out["sql_ctx"].analysis_md


def test_write_precheck_refusal_report_is_honest(monkeypatch):
    from unified_agent.agents import merge as m
    from unified_agent.core.contracts import UnifiedFinalReport, validate

    calls = []
    monkeypatch.setattr(m, "ask_llm_text", lambda *a, **kw: calls.append(a) or "不该走到渲染")
    c = SQLContext(is_write=True, status="ok", outcome="refused",
                   error_code="write_precheck_failed",
                   analysis_md="写操作未执行：[too_many_rows] 预估影响 5000 行。未做任何变更。",
                   sql="DELETE FROM sales_biz.fact_sales_order_line")
    out = m.merge({"task_type": "sql", "sql_ctx": c, "question": "删除订单"})
    report_data = out["final_report"]
    assert calls == []                      
    assert report_data["sql"] is None
    assert "预检未通过" in report_data["answer_md"]
    assert "未做任何变更" in report_data["answer_md"]
    assert validate(UnifiedFinalReport(**report_data), {"sql_ctx": c, "rag_ctx": None}) == []


def test_write_report_passes_validate(monkeypatch):
    from unified_agent.core.contracts import validate
    from unified_agent.agents import merge as m

    monkeypatch.setattr(m, "ask_llm_text", lambda *a, **kw: "写操作已完成。")
    c = SQLContext(is_write=True, status="ok", sql="INSERT INTO t VALUES (1)",
                   executed_sql="INSERT INTO t VALUES (1)", analysis_md="写操作已执行完成")
    report = m.render({"task_type": "sql", "sql_ctx": c}, [])
    assert report.mode == "sql" and report.sql is not None
    assert report.sql.sql == c.executed_sql and report.sql.injected is False
    assert validate(report, {"sql_ctx": c, "rag_ctx": None}) == []
    c2 = SQLContext(is_write=True, status="error", outcome="refused",
                    error_code="review_need_clarify", sql="INSERT INTO t VALUES (1)")
    report2 = m.render({"task_type": "sql", "sql_ctx": c2}, [])
    assert report2.sql is None and "车道失败备忘" in report2.answer_md
    assert validate(report2, {"sql_ctx": c2, "rag_ctx": None}) == []



def test_sql_node_lane_deadline_refuses_without_calling():
    import time
    from unified_agent.adapters import sql_adapter

    calls = []

    def fake(state):
        calls.append(1)
        return {}

    node = sql_adapter._wrap_data_node(fake)
    c = SQLContext(question="q", lane_deadline_ts=time.time() - 1)
    out = node({"sql_ctx": c, "question": "q"})
    assert calls == []                       
    got = out["sql_ctx"]
    assert got.status == "error" and got.outcome == "refused"
    assert got.error_code == "lane_deadline" and got.retriable is False


def test_sql_node_binds_llm_budget_and_clears_it():
    import time
    from unified_agent.adapters import sql_adapter
    from data_analyze_agent.core import llm as da_llm

    seen = {}

    def fake(state):
        seen["budget"] = da_llm._LLM_BUDGET.get()
        return {}

    node = sql_adapter._wrap_data_node(fake)
    c = SQLContext(question="q", lane_deadline_ts=time.time() + 300)
    node({"sql_ctx": c, "question": "q"})
    budget = seen["budget"]
    assert budget is not None and budget["max_retries"] == 0
    left = budget["deadline"] - time.monotonic()
    assert 0 < left <= sql_adapter._LLM_BUDGET_SEC + 1
    assert da_llm._LLM_BUDGET.get() is None  


def test_should_degrade_retry_only_once_on_timeout():
    from unified_agent.adapters.sql_adapter import should_degrade_retry

    timed_out = SQLContext(status="error", outcome="refused",
                           error_code="llm_timeout: Request timed out")
    assert should_degrade_retry(timed_out) is True
    again = SQLContext(status="error", outcome="refused", degrade_retried=True,
                       error_code="llm_timeout: Request timed out")
    assert should_degrade_retry(again) is False
    assert should_degrade_retry(
        SQLContext(status="error", outcome="refused", error_code="schema_no_hit")) is False
    assert should_degrade_retry(SQLContext(status="ok")) is False


def test_execute_gate_no_rearm_on_llm_timeout():
    from unified_agent.adapters.sql_adapter import _execute_gate

    c = SQLContext(status="error", error_code="llm_timeout: Request timed out",
                   retriable=True, repair_count=0, db_error="boom")
    assert _execute_gate({"sql_ctx": c}) == {}


def test_validate_sql_failed_lane_exempts_sql_block():
    from unified_agent.core.contracts import UnifiedFinalReport, validate
    c = SQLContext(status="error", outcome="refused",
                   error_code="schema_no_hit", sql="")
    report = UnifiedFinalReport(answer_md="抱歉，知识库中没有能回答该问题的数据表。", mode="sql")
    assert validate(report, {"sql_ctx": c}) == []


def test_validate_write_receipt_numbers_exempt_from_subset():
    from unified_agent.core.contracts import SqlBlock, UnifiedFinalReport, validate
    c = SQLContext(is_write=True, status="ok", sql="DELETE FROM orders WHERE d='昨天'",
                   executed_sql="DELETE FROM orders WHERE d='昨天'")
    report = UnifiedFinalReport(
        answer_md="写操作已执行完成（审计编号 w-20261018-0001），影响 5 行，耗时 30ms。",
        sql=SqlBlock(sql="DELETE FROM orders WHERE d='昨天'"),
        mode="sql")
    assert validate(report, {"sql_ctx": c}) == []
    c2 = SQLContext(is_write=False, status="ok", outcome="answered",
                    sql="SELECT 1", executed_sql="SELECT 1")
    report2 = UnifiedFinalReport(answer_md="共影响 5 行。",
                                 sql=SqlBlock(sql="SELECT 1"), mode="sql")
    assert validate(report2, {"sql_ctx": c2}) != []


def test_validate_allows_numbers_from_question():
    from unified_agent.core.contracts import (
        Claim, NumberClaim, SqlBlock, UnifiedFinalReport, validate)

    c = SQLContext(is_write=False, status="ok", outcome="answered",
                   sql="SELECT 1", executed_sql="SELECT 1")
    claims = [Claim(text="x", numbers=[
        NumberClaim(value="11327151.01", origin="sql", evidence_id="e1"),
        NumberClaim(value="1234", origin="sql", evidence_id="e2")])]
    state = {"sql_ctx": c,
             "question": "华东区2024年8月的销售额是多少？另外，苏利股份(603585)净利润？",
             "lane_questions": {"sql": "华东区2024年8月的销售额是多少？"}}
    report = UnifiedFinalReport(
        answer_md="华东区2024年8月销售额 11,327,151.01 元；苏利股份(603585)净利润 1,234 元。",
        sql=SqlBlock(sql="SELECT 1"), mode="sql", claims=claims)
    assert validate(report, state) == []
    bad = report.model_copy(update={
        "answer_md": report.answer_md + "另据 9,999,999 元数据。"})
    assert any("9999999" in e for e in validate(bad, state))


def test_validate_exempts_evidence_number_missing_from_claims():
    from unified_agent.core.contracts import (
        Claim, NumberClaim, UnifiedFinalReport, validate)

    rag = RAGContext(
        citations=["c1"],
        provenance=[
            {"chunk_id": "c1", "text": "归母净利润 15,166.06 万元，营业收入 185,799.26 万元"},
            {"chunk_id": "c9", "text": "未引用片段：7,777,777 元"},
        ])
    report = UnifiedFinalReport(
        answer_md="归母净利润 15,166.06 万元，营业收入 185,799.26 万元。",
        mode="rag", citations=[{"chunk_id": "c1"}],
        claims=[Claim(text="净利润", numbers=[
            NumberClaim(value="15166.06", origin="rag", evidence_id="c1")])])
    exempted: list[str] = []
    assert validate(report, {"rag_ctx": rag}, exempted) == []
    assert exempted == ["185799.26"]                    

    bad = report.model_copy(update={
        "answer_md": report.answer_md + "其他口径 7,777,777 元。"})
    assert any("7777777" in e for e in validate(bad, {"rag_ctx": rag}))


def test_validate_matches_unit_converted_number():
    from unified_agent.core.contracts import UnifiedFinalReport, validate

    rag = RAGContext(citations=["c1"], provenance=[
        {"chunk_id": "c1", "text": "归属于上市公司股东的净利润 1,065,713,845 元"}])
    report = UnifiedFinalReport(
        answer_md="归母净利润 10.66 亿元。", mode="rag",
        citations=[{"chunk_id": "c1"}])
    exempted: list[str] = []
    assert validate(report, {"rag_ctx": rag}, exempted) == []
    assert exempted == ["10.66"]

    bad = report.model_copy(update={"answer_md": "归母净利润 99 亿元。"})
    assert any("99" in e for e in validate(bad, {"rag_ctx": rag}))

    rag2 = RAGContext(citations=["c2"], provenance=[
        {"chunk_id": "c2", "text": "营业收入 10.66 亿元"}])
    rep2 = UnifiedFinalReport(
        answer_md="营业收入 1,065,713,845 元。", mode="rag",
        citations=[{"chunk_id": "c2"}])
    assert validate(rep2, {"rag_ctx": rag2}) == []


def test_validate_matches_sql_value_across_formats():
    from unified_agent.core.contracts import SqlBlock, UnifiedFinalReport, validate

    sql_text = "SELECT 1065713845.0 AS revenue"
    c = SQLContext(status="ok", outcome="answered", executed_sql=sql_text,
                   query_result={"columns": ["revenue"], "rows": [[1065713845.0]],
                                 "row_count": 1})
    report = UnifiedFinalReport(answer_md="营业收入 1,065,713,845 元。",
                                sql=SqlBlock(sql=sql_text), mode="sql")
    exempted: list[str] = []
    assert validate(report, {"sql_ctx": c}, exempted) == []
    assert exempted == ["1065713845"]