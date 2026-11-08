from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages
from pydantic import BaseModel

from memory_service import SCHEMA_VERSION  # noqa: E402,F401  # 独立共享包


class RAGContext(BaseModel):
    query: str = ""
    filters: dict | None = None          
    tier: str = "lite"                   
    top_k: int = 5
    snippets: list[dict] = []            
    provenance: list[dict] = []          
    answer: str = ""                     
    citations: list[str] = []            
    plan_md: str = ""                    
    gaps: list[str] = []                 
    escalate_requested: bool = False     
    escalate_reason: str = ""            
    draft_escalated: bool = False        
    draft_escalate_reason: str = ""
    fix_rounds: int = 0                  
    last_fix_hint: str = ""              
    status: str = "ok"                   
    outcome: str = "answered"            
    error_code: str | None = None
    retriable: bool = False
    lane_deadline_ts: float = 0.0        


class SQLContext(BaseModel):
    question: str = ""
    schema: dict | list | None = None    
    tier: str = "lite"                   
    escalation_count: int = 0            
    metrics: list[dict] = []
    example_sql: list[dict] = []
    is_write: bool = False               
    read_filters: dict | None = None     
    sql: str | None = None               
    checked_sql_pre_injection: str | None = None   
    review_passed: bool | None = None    
    review_fix_hint: str = ""            
    review_issues: list[dict] = []       
    repair_count: int = 0                
    db_error: str = ""                   
    executed_sql: str | None = None      
    write_precheck: dict | None = None   
    policy_injected: bool = False        
    injection_hash: str | None = None
    query_result: dict | None = None
    analysis_md: str = ""                
    web_sources: list[dict] = []         
    lane_deadline_ts: float = 0.0        
    degrade_retried: bool = False        
    status: str = "ok"                   
    outcome: str = "answered"            
    error_code: str | None = None
    retriable: bool = False


class UnifiedState(TypedDict, total=False):
    session_id: str
    tenant_id: str
    user_id: str
    role: str                        
    datasource_id: int | None        
    sql_enabled: bool                
    web_enabled: bool                
    history: Annotated[list, add_messages]
    entity_ids: list
    permissions: dict
    kb_ids: list[str]                
    kb_scope: dict                   
    task_status: str                 
    schema_version: int              
    route_clarify_count: int         
    pending_interrupt: dict          
    validation_errors: list          
    validation_exempted: list        
    redactions: list                 

    question: str                    
    resolved_question: str           
    lane_questions: dict             
    lane_decisions: list             
    task_type: str                   
    rag_tier: str                    
    sql_tier: str                    
    confidence: float                
    safety: str                      
    policy_denied: bool              
    policy_rule: str                 
    policy_message: str              
    refusal_reason: str              
    refusal_level: str               
    rationale: str | None            
    next_step: str
    degraded_from: str | None        
    degrade_notice: str              
    web_intent: bool                 
    need_attribution: bool           
    recalled_memory: list            
    catalog_hints: dict              
    _intake_ms: int                  

    conversation_notes: dict         

    tool_calls_rag: Annotated[list, add]     
    tool_calls_sql: Annotated[list, add]     
    cost_ledger: Annotated[list, add]        

    rag_ctx: RAGContext
    sql_ctx: SQLContext

    final_report: object | None      