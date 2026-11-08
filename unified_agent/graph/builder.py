from __future__ import annotations

import time

from langgraph.graph import END, START, StateGraph

from unified_agent.adapters.rag_adapter import build_rag_subgraph, rag_lane_deadline_sec
from unified_agent.adapters.sql_adapter import (
    build_sql_subgraph, has_write_intent, lane_deadline_sec, lane_degrade_sec,
    should_degrade_retry,
)
from unified_agent.agents.approve import approve_write, execute_write
from unified_agent.agents.chitchat import answer as chitchat_answer
from unified_agent.graph.reducers import RAG_LANE, SQL_LANE
from unified_agent.memory.compress import compress_history
from unified_agent.memory.recall import recall
from unified_agent.memory.writeback import write_memory
from unified_agent.core.state import UnifiedState, SQLContext, RAGContext
from unified_agent.core.policy_gate import policy_gate

from unified_agent.agents.preflight import preflight
from unified_agent.agents.recall_catalog import recall as recall_catalog
from unified_agent.agents.intake import intake
from unified_agent.agents.finalize import validate_and_set
from unified_agent.agents.merge import merge
from unified_agent.agents.attribution import needs_external_attribution, web_attribution
from unified_agent.observability.tracer import emit_event, trace_node   

_MIN_DEGRADE_RETRY_SEC = 45.0

def _after_intake(state: UnifiedState) -> str:
    if state.get("task_type") == "chitchat":
        return "chitchat"
    return "recall_memory"


def _after_policy(state: UnifiedState) -> str:
    if state.get("policy_denied"):
        return "denied"
    return _after_intake(state)


def _dispatch(state: UnifiedState) -> str | list[str]:
    if state.get("next_step") == "router":
        return "router"
    if state.get("task_status") == "cancelled":
        return "finalize"
    t = state.get("task_type", "rag")
    if t == "hybrid":
        lq = state.get("lane_questions") or {}
        lanes = [lane for lane in (RAG_LANE, SQL_LANE) if lane in lq]
        return lanes or [RAG_LANE, SQL_LANE]
    return t


_DISPATCH_MAP = {
    "router": "intake", "finalize": "finalize",   
    "rag": RAG_LANE, "sql": SQL_LANE,
}

def _after_sql(state: UnifiedState) -> str:
    sc = state.get("sql_ctx") or SQLContext()
    if sc.is_write:
        if sc.status == "error" or sc.outcome == "refused":
            return "merge"
        return "approve_write"
    if needs_external_attribution(state):
        return "web_attribution"
    return "merge"


def _after_approve(state: UnifiedState) -> str:
    sc = state.get("sql_ctx") or SQLContext()
    return "merge" if sc.outcome == "refused" else "execute_write"


def build_graph(checkpointer=None):   
    g = StateGraph(UnifiedState)
    g.add_node("recall_memory", trace_node("recall_memory", recall))          
    g.add_node("compress_history", trace_node("compress_history", compress_history))  
    g.add_node("intake", trace_node("intake", intake))                 
    g.add_node("policy_gate", trace_node("policy_gate", policy_gate))  
    g.add_node("recall_catalog", trace_node("recall_catalog", recall_catalog))  
    g.add_node("preflight", trace_node("preflight", preflight))        
    g.add_node("chitchat", trace_node("chitchat", chitchat_answer))    
    rag_sub, sql_sub = build_rag_subgraph(), build_sql_subgraph()

    def _rag_lane(state, config=None):
        lane_q = ((state.get("lane_questions") or {}).get("rag")
                  if state.get("task_type") == "hybrid" else None)
        sub_state = {**state, "resolved_question": lane_q} if lane_q else state
        rc = state.get("rag_ctx")
        rc = rc if isinstance(rc, RAGContext) else RAGContext.model_validate(rc or {})
        sub_state = {**sub_state, "rag_ctx": RAGContext.model_validate({
            **rc.model_dump(),
            "lane_deadline_ts": time.time() + rag_lane_deadline_sec(),
        })}
        out = rag_sub.invoke(sub_state, config)
        return {"rag_ctx": out["rag_ctx"]} if "rag_ctx" in out else {}

    def _sql_lane(state, config=None):
        sc = state.get("sql_ctx") or SQLContext()
        q = (((state.get("lane_questions") or {}).get("sql")
              if state.get("task_type") == "hybrid" else None)
             or state.get("resolved_question") or state.get("question", ""))

        def _entry(budget_sec: float, *, degrade_retried: bool) -> dict:
            return {**state, "sql_ctx": SQLContext(
                question=q,
                is_write=has_write_intent(q),
                schema=sc.schema,
                metrics=sc.metrics,
                example_sql=sc.example_sql,
                read_filters=sc.read_filters,
                lane_deadline_ts=time.time() + budget_sec,
                degrade_retried=degrade_retried,
            )}

        budget = lane_deadline_sec()
        deadline_ts = time.time() + budget
        out = sql_sub.invoke(_entry(budget, degrade_retried=False), config)
        res = out.get("sql_ctx")
        if res is not None and should_degrade_retry(res):
            thread = ((config or {}).get("configurable") or {}).get("thread_id", "") \
                if isinstance(config, dict) else ""
            remaining = deadline_ts - time.time()
            if remaining >= _MIN_DEGRADE_RETRY_SEC:
                emit_event("node_enter", "sql_degrade_retry", "running",
                           {"error": res.error_code, "remaining_s": round(remaining, 1)},
                           thread, state.get("session_id", ""))
                out = sql_sub.invoke(
                    _entry(min(lane_degrade_sec(), remaining), degrade_retried=True),
                    config)
                res = out.get("sql_ctx")
            else:
                emit_event("node_exit", "sql_degrade_retry", "skipped",
                           {"error": res.error_code, "remaining_s": round(remaining, 1)},
                           thread, state.get("session_id", ""))
        return {"sql_ctx": res} if res is not None else {}

    g.add_node(RAG_LANE, trace_node("rag", _rag_lane))
    g.add_node(SQL_LANE, trace_node("sql", _sql_lane))
    g.add_node("approve_write", trace_node("approve_write", approve_write))  
    g.add_node("execute_write", trace_node("execute_write", execute_write))  
    g.add_node("merge", trace_node("merge", merge))                    
    g.add_node("web_attribution", trace_node("web_attribution", web_attribution))  
    g.add_node("finalize", trace_node("finalize", validate_and_set))   
    g.add_node("write_memory", trace_node("write_memory", write_memory))  

    g.add_edge(START, "compress_history")
    g.add_edge("compress_history", "intake")
    g.add_edge("intake", "policy_gate")
    g.add_conditional_edges("policy_gate", _after_policy,
                            {"denied": "merge", "chitchat": "chitchat",
                             "recall_memory": "recall_memory"})
    g.add_edge("recall_memory", "recall_catalog")
    g.add_edge("recall_catalog", "preflight")
    g.add_edge("chitchat", "finalize")
    g.add_conditional_edges("preflight", _dispatch, _DISPATCH_MAP)
    g.add_conditional_edges(SQL_LANE, _after_sql,
                            {"approve_write": "approve_write", "web_attribution": "web_attribution", "merge": "merge"})
    g.add_edge(RAG_LANE, "merge")
    g.add_edge("web_attribution", "merge")
    g.add_conditional_edges("approve_write", _after_approve,
                            {"execute_write": "execute_write", "merge": "merge"})
    g.add_edge("execute_write", "merge")
    g.add_edge("merge", "finalize")
    g.add_edge("finalize", "write_memory")
    g.add_edge("write_memory", END)
    return g.compile(checkpointer=checkpointer)   