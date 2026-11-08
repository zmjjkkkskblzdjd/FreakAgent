
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from data_analyze_agent.core.result import err, ok
from data_analyze_agent.db import (
    check,
    gateway,
    get_read_engine,
    introspect,
    run_select as db_run_select,
)

__all__ = [
    "RunSelectInput",
    "RunSelectTool",
    "make_sql_tools",
    "get_default_schema_cache",
]

_default_schema_cache: dict[int | None, list] = {}


def get_default_schema_cache() -> list:
    key = gateway.current_datasource_id()
    cache = _default_schema_cache.get(key)
    if cache is None:
        cache = introspect()
        _default_schema_cache[key] = cache
    return cache


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return value


def _query_result_to_dict(result: Any) -> dict[str, Any]:
    data = asdict(result)
    data["rows"] = [[_json_safe(cell) for cell in row] for row in data["rows"]]
    return data


_NEXT_STEP = {
    "syntax": "检查 SQL 语法（关键字、括号、逗号）后改写重试",
    "unknown_column": "用 get_table_ddl 核对列名与枚举口径后改写",
    "unknown_table": "用 search_schema 确认真实表名后改写，禁止猜测表名",
    "timeout": "缩小扫描范围（补时间/分区过滤或先聚合）后重试，必要时申请升级",
    "lock_wait": "稍后重试；若持续锁等待则停止本路试探",
    "other": "核对语句与表结构后重试；反复失败应停止试探并向上反馈",
}
_SECURITY_CODES = {"multi_statement", "not_read_only", "forbidden_function"}


def _guard_reject_receipt(verdict: Any) -> dict[str, Any]:
    blocking = [i for i in verdict.issues if i.code != "missing_limit"]
    first = blocking[0]
    lines = [f"SQL 未通过安全闸门（共 {len(blocking)} 项问题）："]
    for issue in blocking:
        line = f"- [{issue.code}] {issue.detail}"
        if issue.suggestion:
            line += f" → {issue.suggestion}"
        lines.append(line)
    if first.code in _SECURITY_CODES:
        lines.append(
            "下一步：本工具只允许单条只读 SELECT/WITH，不要试图改写包装绕过，"
            "应立即停止该方向的试探。"
        )
    else:
        lines.append(
            "下一步：先用 search_schema / get_table_ddl 核对真实表名与列名，"
            "按上述意见改写后再试；反复失败应停止试探。"
        )
    return err("\n".join(lines), kind=first.code)


class RunSelectInput(BaseModel):

    sql: str = Field(
        ...,
        min_length=1,
        description="要试探执行的单条只读查询（SELECT 或 WITH-SELECT），MySQL 方言。"
        "表名/列名必须来自 search_schema / get_table_ddl 拿到的真实结构，"
        "禁止凭猜测构造；禁止 INSERT/UPDATE/DELETE/DDL、多语句与危险函数；"
        "非聚合查询不写 LIMIT 时系统会自动注入 LIMIT 200",
    )


class RunSelectTool(BaseTool):

    name: str = "run_select"
    description: str = (
        "在写正式 SQL 的过程中，用一条只读 SELECT/WITH 查询试探数据实况：验证 JOIN "
        "是否能通、过滤条件是否命中、枚举值与指标口径是否如预期。语句先过 SQL 安全"
        "闸门（AST 审查 + 自动 LIMIT 200 + EXPLAIN 预检），再经只读账号执行，最多"
        "返回 1000 行。表名/列名必须来自 search_schema、get_table_ddl 已确认的真实"
        "结构，禁止查库外表、禁止写操作或多语句，同一查询不要重复试探。成功返回 "
        "columns / rows / row_count / truncated 等字段；失败返回中文原因，请据此改写，"
        "不要把试探过程或报错写进给用户的最终答案。"
    )
    args_schema: type[BaseModel] = RunSelectInput

    model_config = ConfigDict(arbitrary_types_allowed=True)
    schema_cache: Any = Field(default=None, exclude=True)
    engine: Any = Field(default=None, exclude=True)

    def _run(self, sql: str) -> dict[str, Any]:
        sql = sql.strip()
        if not sql:
            return err(
                "sql 不能为空白字符串。下一步：提交一条具体的只读 SELECT/WITH 查询，"
                "表名列名先用 search_schema / get_table_ddl 确认。",
                kind="invalid_argument",
            )
        cache = (
            self.schema_cache
            if self.schema_cache is not None
            else get_default_schema_cache()
        )
        engine = self.engine if self.engine is not None else get_read_engine()

        try:
            verdict = check(sql, cache, lane="ask", engine=engine)
            if not verdict.passed:
                return _guard_reject_receipt(verdict)
            executed = db_run_select(verdict.checked_sql, engine=engine)
        except Exception as exc:  # noqa: BLE001 —— 工具兜底：基础设施意外转失败单
            return err(
                f"执行只读查询时发生意外错误：{type(exc).__name__}：{exc}。"
                "下一步：不要重复试探同一条语句，向用户说明暂时无法验证此查询。",
                kind="tool_failed",
            )

        if executed["ok"]:
            data = _query_result_to_dict(executed["data"])
            notes = [i.detail for i in verdict.issues if i.code == "missing_limit"]
            if notes:
                data["guard_notes"] = notes
            return ok(data)

        dberror = executed["data"]
        tip = _NEXT_STEP.get(dberror.kind, _NEXT_STEP["other"])
        extra = {"errno": dberror.errno} if dberror.errno is not None else {}
        return err(
            f"SQL 执行失败（{dberror.kind}）：{dberror.message}。下一步：{tip}。",
            kind=dberror.kind,
            **extra,
        )


def make_sql_tools(
    schema_cache: list | None = None,
    engine: Any = None,
) -> list[BaseTool]:
    return [
        RunSelectTool(schema_cache=schema_cache, engine=engine),
    ]