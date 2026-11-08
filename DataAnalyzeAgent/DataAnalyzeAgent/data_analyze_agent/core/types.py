
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

__all__ = [
    "Route", "Tier", "Confidence", "FixHint", "RiskLevel",
    "DBErrorKind", "ChartType", "MemoryType", "IssueCode",
    "AttemptStage", "ReportStatus", "CallStatus",
    "ROUTES", "TIERS", "TIER_ORDER", "ISSUE_CODES", "SQL_STAGES",
    "REPORT_STATUSES", "CALL_STATUSES",
    "READ_ONLY_STATEMENTS", "FORBIDDEN_FUNCTIONS",
    "WRITE_STATEMENTS", "WRITE_MYSQL_BIZ_TABLES", "WRITE_MYSQL_TABLES",
    "RouteDecision", "SQLAttempt", "ReviewIssue", "ReviewVerdict",
    "QueryResult", "DBError", "DataAnalysis", "FinalReport", "CallLogEntry",
    "ColumnMeta", "TableMeta",
    'RetrievedCard','MetricSpec','AnalysisPlan',
    "ChartSpec", "ChartArtifact","MemoryRecord"
]


Route = Literal["chitchat", "ask", "write", "clarify", "reject"]
Tier = Literal["lite", "deep"]
Confidence = Literal["high", "medium", "low"]
FixHint = Literal["fix_sql", "need_clarify", "escalate_plan", "reject_query", "pass"]
RiskLevel = Literal["none", "low", "medium", "high"]
DBErrorKind = Literal["syntax", "unknown_column", "unknown_table", "timeout", "lock_wait", "other"]
ChartType = Literal["line", "bar", "pie", "table", "none"]
MemoryType = Literal["profile", "metric_semantic", "preference"]

TIER_ORDER = {"lite": 0, "deep": 1}
ROUTES = ("chitchat", "ask", "write", "clarify", "reject")
TIERS = ("lite", "deep")

READ_ONLY_STATEMENTS = ("Select", "Union", "UnionAll", "With")
WRITE_STATEMENTS = ("Insert", "Update", "Delete")
WRITE_MYSQL_BIZ_TABLES = ("fact_sales_order_line", "dim_region", "dim_customer", "dim_product")
WRITE_MYSQL_TABLES = ("kb_glossary", "kb_fewshot")
FORBIDDEN_FUNCTIONS = ("sleep", "sleepeachrow", "joinget", "version",
                       "current_user", "currentuser")


IssueCode = Literal[
    "multi_statement",         
    "not_read_only",           
    "forbidden_function",      
    "forbidden_table_function",  
    "unknown_table",           
    "unknown_column",          
    "missing_limit",           
    "not_write_dml",           
    "missing_where",           
    "write_table_forbidden",   
    "cross_database",          
    "other",
]
ISSUE_CODES = (
    "multi_statement", "not_read_only", "forbidden_function",
    "forbidden_table_function", "unknown_table",
    "unknown_column", "missing_limit",
    "not_write_dml", "missing_where", "write_table_forbidden",
    "cross_database", "other",
)

AttemptStage = Literal["draft", "review_fix", "runtime_fix", "write_draft"]
SQL_STAGES = ("draft", "review_fix", "runtime_fix", "write_draft")

ReportStatus = Literal[
    "answered", "refused", "failed", "cancelled", "chitchat", "clarified"
]
REPORT_STATUSES = (
    "answered", "refused", "failed", "cancelled", "chitchat", "clarified",
)

CallStatus = Literal["ok", "error", "blocked", "interrupt"]   
CALL_STATUSES = ("ok", "error", "blocked", "interrupt")



@dataclass
class RouteDecision:

    route: Route
    tier: Tier
    reason: str                            
    rewritten_question: str = ""           
    clarifying_question: str = ""          
    ambiguous_slots: list[str] = field(default_factory=list)


@dataclass
class MetricSpec:

    name: str                              
    expression_hint: str                   
    related_tables: list[str]              


@dataclass
class AnalysisPlan:

    goal: str                              
    metrics: list[MetricSpec]              
    dimensions: list[str]                  
    time_range: str                        
    filters: list[str]                     
    joins: list[str]                       
    success_criteria: list[str]            
    need_chart: bool = False
    notes: str = ""                        



@dataclass
class SQLAttempt:

    sql: str                               
    version: int                           
    stage: AttemptStage
    used_tables: list[str]                 
    rationale: str                         
    feedback: str = ""                     


@dataclass
class DataAnalysis:

    answer_md: str                         
    referenced_cells: list[str]            
    confidence: Confidence
    is_empty: bool                         
    empty_is_suspicious: bool              
    escalate: bool = False                 
    escalate_reason: str = ""
    chart_suggestion: ChartType = "none"
    refused: bool = False                  
    refuse_reason: str = ""




@dataclass
class ChartSpec:

    chart_type: ChartType                  
    title: str                             
    x_field: str                           
    y_fields: list[str]                    
    series_from_rows: bool = True          
    reason: str = ""                       


@dataclass
class ChartArtifact:

    spec: ChartSpec
    path: str




@dataclass
class ColumnMeta:

    name: str
    data_type: str                 
    is_nullable: bool
    column_key: str                
    column_comment: str            
    extra: str = ""                


@dataclass
class TableMeta:

    table_name: str
    table_comment: str
    ddl: str                       
    columns: list[ColumnMeta]
    schema_hash: str               


@dataclass
class ReviewIssue:

    code: IssueCode
    detail: str                            
    suggestion: str = ""                   
    auto_fixed_sql: str | None = None      


@dataclass
class ReviewVerdict:

    passed: bool
    issues: list[ReviewIssue]
    fix_hint: FixHint                      
    risk_level: RiskLevel
    checked_sql: str                       
    explain_rows: int = 0                  


@dataclass
class QueryResult:

    columns: list[str]                     
    rows: list[tuple]                      
    row_count: int                         
    total_found: int | None = None         
    truncated: bool = False                
    duration_ms: int = 0
    executed_sql: str = ""                 
    used_read_only_account: bool = True
    source: str = ""                       
    mirror_note: str = ""                  
    lag_ms: int | None = None              


@dataclass
class DBError:

    kind: DBErrorKind
    message: str                           
    errno: int | None = None               




def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return value


@dataclass
class FinalReport:

    answer_md: str                         
    sql: str                               
    status: ReportStatus
    confidence: Confidence
    columns: list[str]                     
    preview_rows: list[tuple]              
    row_count: int
    truncated: bool
    chart_path: str | None                 
    tables_used: list[str]
    tier_used: Tier
    escalated: bool                        
    repairs_used: int
    duration_ms: int
    tokens_in: int = 0                     
    tokens_out: int = 0
    usage_by_model: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer_md": self.answer_md,
            "sql": self.sql,
            "status": self.status,
            "confidence": self.confidence,
            "columns": [str(c) for c in self.columns],
            "preview_rows": [
                [_json_safe(cell) for cell in row] for row in self.preview_rows
            ],
            "row_count": self.row_count,
            "truncated": self.truncated,
            "chart_path": self.chart_path,
            "tables_used": list(self.tables_used),
            "tier_used": self.tier_used,
            "escalated": self.escalated,
            "repairs_used": self.repairs_used,
            "duration_ms": self.duration_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "usage_by_model": dict(self.usage_by_model or {}),
        }




@dataclass
class CallLogEntry:

    seq: int
    node: str                              
    started_at: str                        
    duration_ms: int
    ok: bool
    status: CallStatus
    detail: dict = field(default_factory=dict)   

@dataclass
class RetrievedCard:            
    card_id: str
    kind: Literal["schema", "glossary", "fewshot"]
    content: str                
    score: float
    match_type: Literal["vector", "keyword", "hybrid"]

@dataclass
class MemoryRecord:
    memory_id: str                  
    content: str                    
    memory_type: MemoryType         
    importance: int                 
    source_thread_id: str           
    created_at: str
    last_accessed_at: str
    access_count: int = 0
    status: Literal["active", "archived"] = "active"
    user_id: str = ""