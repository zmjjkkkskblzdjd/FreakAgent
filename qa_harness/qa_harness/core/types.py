from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal


Route = Literal["kb", "web", "clarify", "chitchat", "reject"]
Tier = Literal["direct", "lite", "deep"]
Confidence = Literal["high", "medium", "low"]
MatchType = Literal["vector", "bm25", "hybrid", "web"]

FixHint = Literal["pass", "need_more_evidence", "fix_draft"]

ROUTES: tuple[str, ...] = ("kb", "web", "clarify", "chitchat", "reject")
TIERS: tuple[str, ...] = ("direct", "lite", "deep")
TIER_ORDER: dict[str, int] = {"direct": 0, "lite": 1, "deep": 2}
FIX_HINTS: tuple[str, ...] = ("pass", "need_more_evidence", "fix_draft")
ToolRisk = Literal["read", "write", "destructive"]
ISSUE_TYPES: tuple[str, ...] = (
    "unsupported_claim", "fake_citation", "missed_criterion", "incomplete_coverage",
    "dishonest_refusal",
)
MAX_FIX_ROUNDS: int = 2

ToolHandler = Callable[..., dict[str, Any]]


@dataclass
class ToolDefinition:

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    risk: ToolRisk = "read"
    requires_confirmation: bool = False



@dataclass
class Evidence:

    chunk_id: str         
    content: str          
    source: str           
    doc_type: str         
    chunk_index: int      
    score: float          
    match_type: MatchType  



@dataclass
class RouteDecision:

    route: Route                            
    tier: Tier                              
    reason: str                             
    clarifying_question: str | None = None  


@dataclass
class EvidenceReport:

    evidences: list[Evidence]   
    gaps: list[str]             
    escalate: bool = False      
    escalate_reason: str = ""   

@dataclass
class SubQuestion:
    question: str                  
    search_queries: list[str]      
    criterion: str                 

@dataclass
class Plan:
    sub_questions: list[SubQuestion]   
    success_criteria: list[str]        
    need_web: bool = False             
    notes: str = ""                    

@dataclass
class Draft:

    answer: str                       
    citations: list[str]              
    confidence: Confidence            
    is_refusal: bool = False          
    escalate: bool = False            
    escalate_reason: str = ""        

@dataclass
class VerdictIssue:
    issue_type: str                
    detail: str                    
    related_chunk_id: str | None = None  

@dataclass
class Verdict:
    passed: bool                   
    issues: list[VerdictIssue]     
    fix_hint: FixHint              

@dataclass
class MemoryRecord:
    id: str               
    user_id: str          
    session_id: str       
    content: str          
    type: str             
    importance: float     
    created_at: str       
    last_accessed_at: str 
    access_count: int     
    status: str           

@dataclass
class CallLogEntry:

    seq: int                    
    delegate: str               
    actor: str                  
    started_at: str             
    duration_ms: int            
    ok: bool                    
    error: str = ""             
    args_digest: str = ""       
    tokens_in: int = 0          
    tokens_out: int = 0         


@dataclass
class QATaskContext:

    session_id: str             
    question: str               
    created_at: str             

    route_decision: RouteDecision | None = None
    evidence_report: EvidenceReport | None = None
    draft: Draft | None = None
    plan: Plan | None = None  
    verdict: Verdict | None = None

    attempts: dict[str, int] = field(default_factory=dict)  

    call_log: list[CallLogEntry] = field(default_factory=list)
