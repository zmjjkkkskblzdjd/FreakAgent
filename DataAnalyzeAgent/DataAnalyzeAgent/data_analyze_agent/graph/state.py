
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

from data_analyze_agent.core import (
    CallLogEntry,
    ChartArtifact,
    DBError,
    DataAnalysis,
    FinalReport,
    QueryResult,
    ReviewVerdict,
    RouteDecision,
    SQLAttempt,
    Tier, RetrievedCard, AnalysisPlan,
)


def _append_list(left: list, right: list) -> list:
    return (left or []) + (right or [])


def _add_int(left: int | None, right: int | None) -> int:
    return (left or 0) + (right or 0)


def _merge_usage(left: dict | None, right: dict | None) -> dict:
    merged: dict = {}
    for chunk in (left or {}, right or {}):
        for model, pair in chunk.items():
            slot = merged.setdefault(model, {"in": 0, "out": 0})
            slot["in"] += int(pair.get("in", 0))
            slot["out"] += int(pair.get("out", 0))
    return merged


class DataAnalyzeState(TypedDict, total=False):
    thread_id: str                            
    session_id: str                           
    question: str                             
    clarified_question: str                   

    messages: Annotated[list, add_messages]

    memory_hints: list[dict]                  
    route_decision: RouteDecision

    schema_docs: list[RetrievedCard]         
    glossary_hits: list[RetrievedCard]
    fewshot_hits: list[RetrievedCard]
    analysis_plan: AnalysisPlan              
    current_attempt: SQLAttempt               
    review_verdict: ReviewVerdict             
    query_result: QueryResult
    db_error: DBError | None
    data_analysis: DataAnalysis
    chart: ChartArtifact
    final_report: FinalReport

    sql_attempts: Annotated[list[SQLAttempt], _append_list]  
    call_log: Annotated[list[CallLogEntry], _append_list]

    repair_count: int                         
    escalation_count: int
    tier: Tier                                
    clarify_rounds: int                       

    clarify_outcome: str

    node_error: str | None                    

    pending_clarify: dict | None              

    write_sql: str                            
    write_target: str                         
    estimated_rows: int                       
    write_result: dict                        
    approval_granted: bool                    
    approval_asks: int                        

    llm_tokens_in: Annotated[int, _add_int]
    llm_tokens_out: Annotated[int, _add_int]
    llm_usage_by_model: Annotated[dict, _merge_usage]
    active_ms: Annotated[int, _add_int]
