# -*- coding: utf-8 -*-
from typing import Any
from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    sql: str = Field(..., description="只读 SELECT SQL")
    timeout_ms: int = Field(30000, ge=1000, le=60000, description="查询超时（毫秒）")
    max_limit: int | None = Field(None, ge=1, le=50000, description="调用方自定义结果行上限")
    datasource_id: int | None = Field(None, ge=1, description="数据源 id；为空 = 旧 ClickHouse 通道")
    tenant_id: int | None = Field(None, ge=0, description="会话所属租户（跨租户按不存在处理）")
    fresh: bool = Field(False, description="true=强制读源库最新数据（P0 恒直读源库）")


class ColumnMeta(BaseModel):
    name: str
    type: str


class QueryResponse(BaseModel):
    ok: bool
    columns: list[ColumnMeta] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    executed_sql: str = ""
    elapsed_ms: int = 0
    truncated: bool = False          
    rewritten: bool = False          
    error_code: str = ""
    error_message: str = ""
    source: str = ""                 
    lag_ms: int | None = None
    degraded: bool = False
    breaker_state: str = ""
    source_table_states: dict[str, str] = {}
    mirror_note: str = ""            


class TableMeta(BaseModel):
    database: str
    name: str
    full_name: str
    engine: str
    total_rows: int | None = None
    comment: str = ""
    is_view: bool = False
    dedup_hint: str = ""             
    partition_key: str = ""
    sorting_key: str = ""


class ColumnMetaOut(BaseModel):
    name: str
    type: str
    comment: str = ""
    position: int


class TableSchema(BaseModel):
    table: TableMeta
    columns: list[ColumnMetaOut]


class WriteRequest(BaseModel):
    sql: str = Field(..., description="单条 INSERT/UPDATE/DELETE（表名必须带库名前缀）")
    estimated_rows: int | None = Field(None, ge=0, description="调用方预估行数（仅记录，不作裁决依据）")
    dry_run: bool = Field(False, description="true=只过安检+行数复核不执行（签发 preview_token）")
    datasource_id: int | None = Field(None, ge=1, description="数据源 id；为空 = 旧 sales_biz/sales_meta 通道")
    tenant_id: int | None = Field(None, ge=0, description="会话所属租户")
    user_id: int | None = Field(None, ge=0, description="会话用户 id（按表授权判定）")
    role: str = Field("member", description="会话角色：owner/admin 全量，member 需按表授权")
    preview_token: str | None = Field(None, description="dry_run 签发的一次性令牌；执行必带")
    transactional_preview: bool = Field(
        False, description="dry_run 时在事务内真实执行后立即 ROLLBACK，返回结果样例与撤回元数据")


class WriteUndo(BaseModel):
    tenant_id: int = Field(..., ge=0)
    user_id: int = Field(..., ge=0)
    datasource_id: int | None = Field(None, ge=1)


class WriteResponse(BaseModel):
    ok: bool
    audit_id: str = ""
    target: str = ""               
    database: str = ""             
    table: str = ""
    operation: str = ""            
    estimated_rows: int = 0        
    actual_rows: int = 0           
    mutation_status: str = ""      
    error_code: str = ""
    error_message: str = ""
    elapsed_ms: int = 0
    preview_token: str = ""        
    source: str = ""               
    breaker_state: str = ""
    preview_id: str = ""           
    preview_hash: str = ""         
    preview_rows: list[dict] = Field(default_factory=list)   
    before_rows: list[dict] = Field(default_factory=list)    
    affected_rows: int = 0
    auto_pk: bool = False          
    undo_deadline: str = ""        
    undo_available: bool = False
