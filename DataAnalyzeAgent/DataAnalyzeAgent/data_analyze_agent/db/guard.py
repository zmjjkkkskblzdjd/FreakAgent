
from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from config import get_app_config
from data_analyze_agent.db.gateway import current_datasource_id
from data_analyze_agent.core import (
    FORBIDDEN_FUNCTIONS,
    READ_ONLY_STATEMENTS,
    WRITE_MYSQL_BIZ_TABLES,
    WRITE_MYSQL_TABLES,
    WRITE_STATEMENTS,
    ReviewIssue,
    ReviewVerdict,
)

__all__ = ["check"]

_BANNED_TABLE_FUNCS = {
    "remote", "remotesecure", "url", "file", "s3", "s3cluster", "hdfs",
    "jdbc", "odbc", "mysql", "postgresql", "sqlite", "mongodb", "redis",
    "cluster", "clusterallreplicas", "numbers", "executable", "input",
    "generateurlostream", "fuzzquery",
}
_LIMIT_INJECT = 200     
_SECURITY_CODES = {"multi_statement", "not_read_only", "forbidden_function",
                   "forbidden_table_function", "not_write_dml",
                   "write_table_forbidden", "cross_database"}
_MEDIUM_RISK_CODES = {"unknown_table", "unknown_column"}
_REJECT_CODES = _SECURITY_CODES | {"other"}


def check(sql: str, schema_cache: list, *, lane: str = "ask", engine=None) -> ReviewVerdict:
    if lane not in ("ask", "write"):
        raise ValueError(f"lane 只支持 ask/write，收到 {lane!r}")
    if schema_cache is None:
        raise ValueError("schema_cache 不能为空：guard 不允许在没有表结构底账时放行任何 SQL")

    issues: list[ReviewIssue] = []
    root = _parse(sql, issues, lane)
    auto_fixed = None
    if root is not None:
        if lane == "ask":
            _check_root_type(root, issues)
            _check_forbidden_functions(root, issues)
            _check_tables_columns(root, schema_cache, issues)
            auto_fixed = _maybe_inject_limit(root, issues)
        else:
            _check_write_root_type(root, issues)
            _check_forbidden_functions(root, issues)
            _check_write_tables(root, schema_cache, issues)
            _check_write_where(root, issues)

    issues = _dedupe(issues)
    return ReviewVerdict(
        passed=not any(i.code != "missing_limit" for i in issues),
        issues=issues,
        fix_hint=_fix_hint(issues),
        risk_level=_risk_level(issues),
        checked_sql=auto_fixed or sql,
    )



def _parse(sql: str, issues: list[ReviewIssue], lane: str = "ask"):
    dialect = "clickhouse" if lane == "ask" else "mysql"
    if not sql or not sql.strip():
        issues.append(ReviewIssue(code="other", detail="SQL 为空，无法审查"))
        return None
    try:
        stmts = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as exc:
        issues.append(ReviewIssue(
            code="other",
            detail=f"sqlglot 无法解析该语句（fail-closed 默认拒绝）：{str(exc)[:200]}",
            suggestion="语句语法不被支持时一律不放行；请改写为常规 SELECT",
        ))
        return None
    except Exception as exc:  # noqa: BLE001 —— 解析期任何意外都按拒绝处理
        issues.append(ReviewIssue(
            code="other",
            detail=f"SQL 解析出现意外错误（fail-closed 默认拒绝）：{type(exc).__name__}：{exc}",
        ))
        return None
    if len(stmts) > 1:
        shapes = "；".join(f"第{i + 1}条 {type(s).__name__}" for i, s in enumerate(stmts))
        issues.append(ReviewIssue(
            code="multi_statement",
            detail=f"检测到 {len(stmts)} 条语句（{shapes}），存在注入嫌疑",
            suggestion="一次只允许一条语句",
        ))
        return None
    return stmts[0]



def _check_root_type(root, issues: list[ReviewIssue]) -> None:
    root_name = type(root).__name__
    if root_name not in READ_ONLY_STATEMENTS:
        issues.append(ReviewIssue(
            code="not_read_only",
            detail=f"ask 车道收到 {root_name} 语句：{root.sql(dialect='clickhouse')[:100]}",
            suggestion="只允许 SELECT/WITH 查询；写操作请走业务系统，本环境不支持",
        ))



def _check_forbidden_functions(root, issues: list[ReviewIssue]) -> None:
    for fn in root.find_all(exp.Func):
        raw = fn.this if isinstance(fn, exp.Anonymous) else fn.sql_name()
        name = (raw or "").lower()
        candidates = {name, name.removeprefix("current_")}
        hit = candidates & set(FORBIDDEN_FUNCTIONS)
        if hit:
            issues.append(ReviewIssue(
                code="forbidden_function",
                detail=f"命中危险函数 {sorted(hit)[0].upper()}（位置：{fn.sql(dialect='clickhouse')[:80]}）",
                suggestion="该函数被安全策略禁止，请改写查询",
            ))
            return  

    for table in root.find_all(exp.Table):
        if table.name:
            continue
        anon = table.find(exp.Anonymous)
        if anon is not None:
            func_name = (anon.name or "").lower()
            issues.append(ReviewIssue(
                code="forbidden_table_function",
                detail=f"命中被禁的 ClickHouse 表函数 {func_name or '(未识别)'}",
                suggestion="只允许查询 sales_dw 库中的真实表与 v_ 视图，禁止表函数",
            ))
            return



def _check_tables_columns(root, schema_cache: list, issues: list[ReviewIssue]) -> None:
    real = {t.table_name.lower(): t for t in schema_cache}
    allowed_db = get_app_config().gateway.database.lower()
    ds_mode = current_datasource_id() is not None
    cte_names = {c.alias.lower() for c in root.find_all(exp.CTE) if c.alias}
    output_aliases = {a.alias.lower() for a in root.find_all(exp.Alias) if a.alias}

    alias_map: dict[str, str] = {}
    for t in root.find_all(exp.Table):
        tname = (t.name or "").lower()
        if not tname or tname in cte_names:
            continue  
        if t.db and t.db.lower() != allowed_db and not (ds_mode and tname in real):
            issues.append(ReviewIssue(
                code="unknown_table",
                detail=f"表 `{t.db}.{t.name}` 的库名前缀不是 {allowed_db}（本环境只允许查该库）",
                suggestion="表名写作 sales_dw.表名 或裸表名",
            ))
            continue
        if tname not in real:
            issues.append(ReviewIssue(
                code="unknown_table",
                detail=f"表 `{tname}` 不在 live schema 中（现有表：{', '.join(sorted(real))}）",
                suggestion="只允许查询检索到的表卡里的表；请核对表名或申请澄清",
            ))
            continue
        alias_map[(t.alias or t.name).lower()] = tname

    known_cols = {c.name.lower() for t in schema_cache for c in t.columns}
    for col in root.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            continue  
        cname = (col.name or "").lower()
        owner = (col.table or "").lower()
        if owner:
            table_name = alias_map.get(owner)
            if table_name is None:
                continue  
            if cname not in {c.name.lower() for c in real[table_name].columns}:
                issues.append(ReviewIssue(
                    code="unknown_column",
                    detail=f"列 `{cname}` 不属于表 `{table_name}`",
                    suggestion="请对照表卡的列清单核对列名",
                ))
        elif cname not in known_cols and cname not in output_aliases:
            issues.append(ReviewIssue(
                code="unknown_column",
                detail=f"列 `{cname}` 不属于任何已知表或输出别名",
                suggestion="请核对列名拼写，或给列名补上表名前缀",
            ))



def _maybe_inject_limit(root, issues: list[ReviewIssue]) -> str | None:
    if root is None or type(root).__name__ not in READ_ONLY_STATEMENTS:
        return None
    if root.find(exp.Limit) is not None or root.find(exp.AggFunc) is not None:
        return None
    try:
        fixed = root.limit(_LIMIT_INJECT).sql(dialect="clickhouse")
    except Exception:  # noqa: BLE001 —— 改写失败就维持原样，不因注入器而拒
        return None
    issues.append(ReviewIssue(
        code="missing_limit",
        detail=f"非聚合查询未带 LIMIT，已自动注入 LIMIT {_LIMIT_INJECT}（零风险改写，留痕备查）",
        suggestion="建议查询显式写 LIMIT",
        auto_fixed_sql=fixed,
    ))
    return fixed



def _check_write_root_type(root, issues: list[ReviewIssue]) -> None:
    root_name = type(root).__name__
    if root_name not in WRITE_STATEMENTS:
        issues.append(ReviewIssue(
            code="not_write_dml",
            detail=f"写车道收到 {root_name} 语句：只允许 INSERT/UPDATE/DELETE，DDL 一律拒",
            suggestion="请改写为单条 INSERT/UPDATE/DELETE",
        ))


def _check_write_where(root, issues: list[ReviewIssue]) -> None:
    root_name = type(root).__name__
    if root_name in ("Update", "Delete") and root.args.get("where") is None:
        issues.append(ReviewIssue(
            code="missing_where",
            detail=f"{root_name.upper()} 必须带 WHERE（禁裸全表写）",
            suggestion="补上明确的 WHERE 条件；确需大批量写请分批并走管理员通道",
        ))


def _check_write_tables(root, schema_cache: list, issues: list[ReviewIssue]) -> None:
    if current_datasource_id() is not None:
        return
    tables = [t for t in root.find_all(exp.Table) if t.name]
    dbs = {(t.db or "").lower() for t in tables}
    if any(not d for d in dbs):
        issues.append(ReviewIssue(
            code="cross_database",
            detail="写语句的表必须带库名前缀（sales_biz. 或 sales_meta.）",
            suggestion="表名写作 sales_biz.表名 或 sales_meta.表名",
        ))
        return
    if len(dbs) > 1:
        issues.append(ReviewIssue(
            code="cross_database",
            detail=f"一条写语句只能写一个库，检测到 {sorted(dbs)}",
            suggestion="拆成多条写，逐条审批执行",
        ))
        return
    db = next(iter(dbs))
    for t in tables:
        tname = t.name.lower()
        if db == "sales_biz":
            if tname not in WRITE_MYSQL_BIZ_TABLES:
                issues.append(ReviewIssue(
                    code="write_table_forbidden",
                    detail=f"sales_biz.{tname} 不在写白名单 {list(WRITE_MYSQL_BIZ_TABLES)}",
                    suggestion="写目标只能是 4 张业务表；查询请走读车道",
                ))
        elif db == "sales_meta":
            if tname not in WRITE_MYSQL_TABLES:
                issues.append(ReviewIssue(
                    code="write_table_forbidden",
                    detail=f"sales_meta.{tname} 不在写白名单 {list(WRITE_MYSQL_TABLES)}（kb_meta 锁死只读）",
                    suggestion="知识维护只开放 kb_glossary / kb_fewshot",
                ))
        else:
            issues.append(ReviewIssue(
                code="write_table_forbidden",
                detail=f"库 {db} 不可写（只允许 sales_biz / sales_meta；sales_dw 为 CDC 只读副本）",
                suggestion="写目标必须落在 sales_biz 或 sales_meta",
            ))



def _dedupe(issues: list[ReviewIssue]) -> list[ReviewIssue]:
    seen: set[tuple[str, str]] = set()
    unique = []
    for i in issues:
        key = (i.code, i.detail)
        if key not in seen:
            seen.add(key)
            unique.append(i)
    return unique


def _fix_hint(issues: list[ReviewIssue]) -> str:
    if not issues or all(i.code == "missing_limit" for i in issues):
        return "pass"
    hard = [i for i in issues if i.code in _REJECT_CODES and i.code != "write_table_forbidden"]
    if hard:
        return "reject_query"
    return "fix_sql"


def _risk_level(issues: list[ReviewIssue]) -> str:
    if any(i.code in _SECURITY_CODES for i in issues):
        return "high"
    if any(i.code in _MEDIUM_RISK_CODES or i.code == "other" for i in issues):
        return "medium"
    return "low"


_LIMIT_INJECT = get_app_config().sql_guard.inject_limit
