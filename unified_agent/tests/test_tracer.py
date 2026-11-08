from __future__ import annotations

import json

import pytest
from langgraph.errors import GraphBubbleUp

from unified_agent.observability import tracer as tracer_mod
from unified_agent.observability.tracer import TraceEvent, trace_node


@pytest.fixture()
def tmp_tracer(tmp_path, monkeypatch):
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("UA_TRACE_PATH", str(path))
    monkeypatch.setattr(tracer_mod, "_tracer", None)
    yield path
    monkeypatch.setattr(tracer_mod, "_tracer", None)


def _lines(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_trace_node_ok_plain_fn(tmp_tracer):

    def node(state):
        return {"x": 1}

    assert trace_node("n1", node)({"session_id": "s1"}) == {"x": 1}
    evs = _lines(tmp_tracer)
    kinds = [(e["kind"], e["status"]) for e in evs]
    assert kinds == [("node_enter", "running"), ("node_exit", "ok")]
    assert evs[0]["node"] == "n1" and evs[0]["session_id"] == "s1"


def test_trace_node_ok_with_config(tmp_tracer):

    def node(state, config=None):
        return {"thread": (config or {}).get("configurable", {}).get("thread_id")}

    out = trace_node("n2", node)({}, {"configurable": {"thread_id": "t-9"}})
    assert out == {"thread": "t-9"}
    evs = _lines(tmp_tracer)
    assert all(e["thread_id"] == "t-9" for e in evs)


def test_trace_node_error_reraise(tmp_tracer):

    def node(state):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        trace_node("n3", node)({})
    evs = _lines(tmp_tracer)
    assert evs[-1]["kind"] == "node_exit" and evs[-1]["status"] == "error"
    assert "ValueError" in evs[-1]["detail"]["error"]


def test_trace_finalize_records_validation_errors(tmp_tracer):
    def bad(state):
        return {"task_status": "done", "validation_errors": ["answer_md 出现 claims 外数字：1"]}

    trace_node("finalize", bad)({})
    fin = [e for e in _lines(tmp_tracer) if e["kind"] == "finalize"]
    assert len(fin) == 1 and fin[0]["status"] == "validation_failed"
    assert fin[0]["detail"]["validation_errors"] == ["answer_md 出现 claims 外数字：1"]

    trace_node("finalize", lambda s: {"validation_errors": []})({})
    assert [e for e in _lines(tmp_tracer) if e["kind"] == "finalize"][-1]["status"] == "ok"

    trace_node("finalize", lambda s: {"validation_errors": [],
                                      "validation_exempted": ["185799.26"]})({})
    fin = [e for e in _lines(tmp_tracer) if e["kind"] == "finalize"][-1]
    assert fin["status"] == "ok"
    assert fin["detail"]["validation_exempted"] == ["185799.26"]


def test_trace_node_suspend_graph_bubble_up(tmp_tracer):
    def node(state):
        raise GraphBubbleUp()

    with pytest.raises(GraphBubbleUp):
        trace_node("preflight", node)({}, {"configurable": {"thread_id": "t-1"}})
    evs = _lines(tmp_tracer)
    assert evs[-1]["kind"] == "clarify_suspend" and evs[-1]["status"] == "suspended"

    with pytest.raises(GraphBubbleUp):
        trace_node("approve_write", node)({})
    assert _lines(tmp_tracer)[-1]["kind"] == "approval_suspend"


def test_trace_event_schema(tmp_tracer):

    def node(state):
        return {}

    trace_node("n4", node)({})
    e = _lines(tmp_tracer)[0]
    for field in ("event_id", "thread_id", "session_id", "seq", "node", "kind",
                  "started_at", "duration_ms", "status", "detail"):
        assert field in e
    assert isinstance(e["seq"], int) and e["seq"] >= 1


def test_build_graph_nodes_traced():
    from unified_agent.graph.builder import build_graph

    g = build_graph()
    nodes = g.get_graph().nodes
    for name in ("recall_memory", "compress_history", "intake", "recall_catalog",
                 "preflight", "chitchat", "rag", "sql", "approve_write",
                 "execute_write", "merge", "web_attribution", "finalize", "write_memory"):
        assert name in nodes, f"缺节点 {name}"


def test_rag_and_sql_wrappers_emit(tmp_tracer):
    from unified_agent.adapters.rag_adapter import RAGContext, _wrap_rag_node
    from unified_agent.adapters.sql_adapter import SQLContext, _wrap_data_node
    from unified_agent.core.state import UnifiedState

    def rag_fn(state):
        return {}

    wrapped = _wrap_rag_node(rag_fn)
    state = {"session_id": "s-r", "rag_ctx": RAGContext()}
    wrapped(state)
    evs = _lines(tmp_tracer)
    assert evs[-1]["kind"] == "node_exit" and evs[-1]["status"] == "ok"
    assert evs[-1]["node"] == "rag_fn"

    wrapped({"session_id": "s-r", "rag_ctx": RAGContext(status="error")})
    assert _lines(tmp_tracer)[-1]["status"] == "skipped"

    def sql_fn(da_state):
        return {}

    snode = _wrap_data_node(sql_fn)
    snode({"session_id": "s-s", "sql_ctx": SQLContext()})
    evs = _lines(tmp_tracer)
    assert evs[-1]["kind"] == "node_exit" and evs[-1]["status"] == "ok"

    snode({"session_id": "s-s", "sql_ctx": SQLContext(status="error")})
    assert _lines(tmp_tracer)[-1]["status"] == "skipped"
