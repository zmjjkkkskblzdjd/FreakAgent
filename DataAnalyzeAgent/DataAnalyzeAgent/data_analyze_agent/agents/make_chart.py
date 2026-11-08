
from __future__ import annotations

from data_analyze_agent.graph.state import DataAnalyzeState
from data_analyze_agent.visualization import build_chart_artifact, decide_chart

__all__ = ["make_chart_node"]


def make_chart_node(state: DataAnalyzeState) -> dict:
    if state.get("node_error"):
        return {"chart": None}
    analysis = state.get("data_analysis")
    query_result = state.get("query_result")
    if analysis is None or query_result is None or analysis.refused:
        return {"chart": None}

    question = state.get("clarified_question") or state.get("question") or ""
    spec = decide_chart(query_result, analysis.chart_suggestion, question=question)
    if spec.chart_type not in ("line", "bar", "pie"):
        return {"chart": None}

    thread_id = state.get("thread_id") or "session"
    try:
        artifact = build_chart_artifact(spec, query_result, thread_id=thread_id)
    except Exception:  # noqa: BLE001 —— 渲染失败只降级为无图，不拖垮整条 answered
        return {"chart": None}
    return {"chart": artifact}
