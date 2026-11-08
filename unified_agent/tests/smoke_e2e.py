import os

from unified_agent.graph.builder import build_graph

os.environ.setdefault("HYBRID_CONFIRM_MODE", "off")


def test_graph_runs_rag_single_lane():
    app = build_graph()
    out = app.invoke({"task_type": "rag", "message": "re.search 是做什么的",
                      "schema_version": 1})
    assert out.get("task_status") != "cancelled"


def test_graph_runs_hybrid_fanout_and_merges():
    app = build_graph()
    out = app.invoke({"task_type": "hybrid", "message": "文档口径与实际数据各是多少",
                      "schema_version": 1, "tool_calls_rag": [], "tool_calls_sql": []})
    assert isinstance(out.get("tool_calls_rag"), list)
    assert isinstance(out.get("tool_calls_sql"), list)