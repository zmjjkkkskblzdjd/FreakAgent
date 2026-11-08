
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph

from data_analyze_agent.agents.recall_memory import recall_memory_node
from data_analyze_agent.context import compress_history_node
from data_analyze_agent.core.llm import new_usage_acc, snapshot_usage, usage_scope
from config import get_app_config
from data_analyze_agent.agents.retriever import (
    after_retrieval,
    make_retrieve_schema_node,
)
from data_analyze_agent.agents.clarify import after_clarify, clarify_wait_node
from data_analyze_agent.agents.router import route_after_router, router_node
from data_analyze_agent.agents.sql_writer import write_sql_node
from data_analyze_agent.core import (
    AnalysisPlan,
    CallLogEntry,
    ChartArtifact,
    ChartSpec,
    DBError,
    DataAnalysis,
    FinalReport,
    MetricSpec,
    QueryResult,
    RetrievedCard,
    ReviewVerdict,
    RouteDecision,
    SQLAttempt,
)

from data_analyze_agent.agents.reviewer import review_sql_node, route_after_review
from data_analyze_agent.agents.executor import execute_sql_node, route_after_execute
from data_analyze_agent.agents.approve_write import approve_write_node, route_after_approval
from data_analyze_agent.agents.execute_write import execute_write_node
from data_analyze_agent.agents.analyst import analyze_result_node, after_analysis
from data_analyze_agent.agents.make_chart import make_chart_node
from data_analyze_agent.agents.finalize import finalize_node
from data_analyze_agent.agents.planner import plan_node,route_after_plan
from data_analyze_agent.graph.state import DataAnalyzeState
from data_analyze_agent.observability.tracer import TraceEvent, get_tracer
_SERIALIZER = JsonPlusSerializer(allowed_msgpack_modules=[
    RouteDecision, SQLAttempt, ReviewVerdict, QueryResult, DBError,
    DataAnalysis, FinalReport, CallLogEntry, RetrievedCard,
    AnalysisPlan, MetricSpec,
    ChartSpec, ChartArtifact,
])

__all__ = ["build_graph", "memory_checkpointer"]


def _derive_events(name: str, patch: dict) -> list[tuple[str, str, dict]]:
    events: list[tuple[str, str, dict]] = []
    if name == "write_sql":
        attempt = patch.get("current_attempt")
        if attempt is not None and attempt.stage in ("review_fix", "runtime_fix"):
            events.append(("repair", "ok",
                           {"stage": attempt.stage, "version": attempt.version}))
    elif name == "review_sql":
        verdict = patch.get("review_verdict")
        if verdict is not None and not verdict.passed:
            events.append(("review_block", "blocked",
                           {"fix_hint": verdict.fix_hint, "issues": len(verdict.issues)}))
    elif name == "plan":
        if patch.get("tier") == "deep":
            events.append(("escalation", "ok",
                           {"tier": "deep",
                            "escalation_count": patch.get("escalation_count")}))
    elif name == "execute_sql":
        err = patch.get("db_error")
        if err is not None:
            events.append(("runtime_error", "error", {"error": err.message}))
    elif name == "clarify":
        outcome = patch.get("clarify_outcome")
        if outcome and outcome != "exhausted":
            events.append(("clarify_resume", "ok", {"outcome": outcome}))
    elif name == "approve_write":
        if "approval_granted" in patch:
            events.append(("approval_resume", "ok",
                           {"granted": patch["approval_granted"],
                            "estimated_rows": patch.get("estimated_rows")}))
    elif name == "execute_write":
        wr = patch.get("write_result") or {}
        events.append(("write_executed", "ok" if wr.get("ok") else "error",
                       {"audit_id": wr.get("audit_id", ""),
                        "mutation_status": wr.get("mutation_status", "")}))
    elif name == "finalize":
        report = patch.get("final_report")
        events.append(("finalize", "ok",
                       {"status": getattr(report, "status", "")}))
    return events


def _safe_node(name: str, fn, *, reraise: bool = False):
    def wrapped(state: DataAnalyzeState, config=None):
        started = datetime.now().isoformat(timespec="seconds")
        t0 = time.perf_counter()
        thread_id = (config or {}).get("configurable", {}).get("thread_id", "")
        tracer = get_tracer()
        acc = new_usage_acc()

        def _emit(kind: str, status: str, detail: dict, duration: int = 0,
                  tokens_in: int = 0, tokens_out: int = 0) -> None:
            tracer.emit(TraceEvent(
                event_id=uuid.uuid4().hex, thread_id=thread_id, session_id=thread_id,
                seq=tracer.next_seq(), node=name, kind=kind,
                started_at=started, duration_ms=duration,
                status=status, detail=detail,
                tokens_in=tokens_in, tokens_out=tokens_out))

        try:
            with usage_scope(acc):
                patch = fn(state)
        except GraphBubbleUp:
            if name in ("clarify", "approve_write"):
                _emit({"clarify": "clarify_suspend",
                       "approve_write": "approval_suspend"}[name], "suspended", {})
            raise
        except Exception as exc:  # noqa: BLE001 —— Day 8 铁律：任何节点 raise 不炸图
            duration = int((time.perf_counter() - t0) * 1000)
            usage = snapshot_usage(acc)
            tin = sum(v["in"] for v in usage.values())
            tout = sum(v["out"] for v in usage.values())
            detail = {"error": f"{type(exc).__name__}: {exc}"}
            seq = tracer.next_seq()
            tracer.emit(TraceEvent(
                event_id=uuid.uuid4().hex, thread_id=thread_id, session_id=thread_id,
                seq=seq, node=name, kind="node_exit",
                started_at=started, duration_ms=duration, status="error", detail=detail,
                tokens_in=tin, tokens_out=tout))
            entry = CallLogEntry(seq=seq, node=name,
                                 started_at=started, duration_ms=duration,
                                 ok=False, status="error", detail=detail)
            if reraise:
                raise
            return {"call_log": [entry],
                    "node_error": f"节点 {name} 异常：{type(exc).__name__}：{exc}",
                    "llm_tokens_in": tin, "llm_tokens_out": tout,
                    "llm_usage_by_model": usage, "active_ms": duration}
        duration = int((time.perf_counter() - t0) * 1000)
        usage = snapshot_usage(acc)
        tin = sum(v["in"] for v in usage.values())
        tout = sum(v["out"] for v in usage.values())
        patch = patch or {}
        patch["llm_tokens_in"] = tin
        patch["llm_tokens_out"] = tout
        patch["llm_usage_by_model"] = usage
        patch["active_ms"] = duration
        _emit("node_exit", "ok", {"usage_by_model": usage} if usage else {},
              duration, tokens_in=tin, tokens_out=tout)
        for kind, status, detail in _derive_events(name, patch):
            _emit(kind, status, detail)
        return patch

    return wrapped


def build_graph(checkpointer=None, store=None, retrievers=None):
    if checkpointer is None:
        cp_dir = Path(get_app_config().paths.checkpoint_dir)
        cp_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(cp_dir / "checkpoints.db", check_same_thread=False)
        checkpointer = SqliteSaver(conn, serde=_SERIALIZER)

    graph = StateGraph(DataAnalyzeState)
    graph.add_node("recall_memory", _safe_node("recall_memory", recall_memory_node))
    graph.add_node("compress_history", _safe_node("compress_history", compress_history_node))
    graph.add_node("router", _safe_node("router", router_node))
    graph.add_node("clarify", _safe_node("clarify", clarify_wait_node))
    graph.add_node("retrieve_schema", _safe_node("retrieve_schema",
                                                 make_retrieve_schema_node(retrievers)))
    graph.add_node('plan',_safe_node("plan",plan_node))
    graph.add_node("write_sql", _safe_node("write_sql", write_sql_node))
    graph.add_node("review_sql", _safe_node("review_sql", review_sql_node))
    graph.add_node("execute_sql", _safe_node("execute_sql", execute_sql_node))
    graph.add_node("approve_write", _safe_node("approve_write", approve_write_node))
    graph.add_node("execute_write", _safe_node("execute_write", execute_write_node))
    graph.add_node("analyze_result", _safe_node("analyze_result", analyze_result_node))
    graph.add_node("make_chart", _safe_node("make_chart", make_chart_node))
    graph.add_node("finalize", _safe_node("finalize", finalize_node, reraise=True))

    graph.add_edge(START, "recall_memory")
    graph.add_edge("recall_memory", "compress_history")
    graph.add_edge("compress_history", "router")
    graph.add_conditional_edges(
        "router",
        route_after_router,
        {"end": "finalize", "clarify": "clarify",
         "retrieve_schema": "retrieve_schema", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "clarify",
        after_clarify,
        {"retrieve_schema": "retrieve_schema", "router": "router", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "retrieve_schema",
        after_retrieval,
        {
            "end": "finalize",            
            "write_sql": "write_sql",     
            "plan": "plan",               
        },
    )
    graph.add_conditional_edges(
        'plan',route_after_plan,{'finalize':'finalize',"write_sql": "write_sql"})
    graph.add_edge("write_sql", "review_sql")
    graph.add_conditional_edges(
        "review_sql",
        route_after_review,
        {
            "execute": "execute_sql",
            "approve_write": "approve_write",  
            "write_sql": "write_sql",     
            "finalize": "finalize",       
            "plan": "plan",               
        },
    )

    graph.add_conditional_edges(
        "approve_write",
        route_after_approval,
        {"execute_write": "execute_write", "finalize": "finalize"},
    )
    graph.add_edge("execute_write", "finalize")   

    graph.add_conditional_edges(
        "execute_sql",
        route_after_execute,
        {
            "analyze_result": "analyze_result",
            "write_sql": "write_sql",     
            "finalize": "finalize",       
        },
    )
    graph.add_conditional_edges(
        "analyze_result",
        after_analysis,
        {
            "plan": "plan",               
            "make_chart": "make_chart",   
        },
    )
    graph.add_edge("make_chart", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer, store=store)


def memory_checkpointer() -> MemorySaver:
    return MemorySaver(serde=_SERIALIZER)
