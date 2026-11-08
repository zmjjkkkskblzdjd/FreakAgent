
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from langchain_core.messages import AIMessage

from config import get_app_config
from data_analyze_agent.core import FinalReport
from data_analyze_agent.graph.state import DataAnalyzeState

__all__ = [
    "determine_status",
    "build_final_report",
    "finalize_node",
    "numbers_backing_errors",
]

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_EXEMPT_NUMBER_SUFFIX = ("年", "月", "日", "%", "％")


def determine_status(state: DataAnalyzeState) -> str:
    if state.get("node_error"):
        return "failed"
    route_decision = state.get("route_decision")
    route = route_decision.route if route_decision else None

    analysis = state.get("data_analysis")
    docs = state.get("schema_docs")
    db_error = state.get("db_error")
    verdict = state.get("review_verdict")

    if route == "chitchat":
        return "chitchat"
    if route == "clarify" and state.get("clarify_outcome") == "give_up":
        return "cancelled"
    if route == "clarify" and docs is None:
        return "clarified"
    if analysis is not None and analysis.refused:
        return "refused"
    if route == "reject":
        return "refused"
    if docs is not None and len(docs) == 0:
        return "refused"
    if route == "write":
        wr = state.get("write_result")
        if wr is None:
            return "failed"                      
        if wr.get("ok"):
            return "answered"
        code = wr.get("error_code")
        if code == "user_cancelled":
            return "cancelled"
        if code in ("too_many_rows", "approval_exhausted"):
            return "refused"                     
        return "failed"                          
    if db_error is not None:
        return "failed"
    if verdict is not None and not verdict.passed:
        return "failed"
    if analysis is not None:
        return "answered"
    return "failed"


def _last_ai_reply(state: DataAnalyzeState) -> str:
    for message in reversed(state.get("messages") or []):
        if isinstance(message, AIMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


def _extract_unguarded_numbers(text: str) -> list[str]:
    numbers: list[str] = []
    for match in _NUMBER_RE.finditer(text):
        token = match.group()
        suffix = text[match.end():match.end() + 1]
        if suffix in _EXEMPT_NUMBER_SUFFIX:
            continue
        if len(token.split(".")[0]) == 4 and 1900 <= int(token[:4]) <= 2099:
            continue
        numbers.append(token)
    return numbers


def _backing_text(query_result, preview_limit: int) -> str:
    parts = [str(query_result.row_count)]
    for row in query_result.rows[:preview_limit]:
        for cell in row:
            if cell is not None:
                parts.append(str(cell))
    if query_result.executed_sql:
        parts.append(query_result.executed_sql)
    return "\n".join(parts)


def _answer_number_values(text: str) -> list[tuple[str, Decimal]]:
    out: list[tuple[str, Decimal]] = []
    for match in _NUMBER_RE.finditer(text):
        token = match.group()
        suffix = text[match.end():match.end() + 1]
        if suffix in _EXEMPT_NUMBER_SUFFIX:
            continue
        if len(token.split(".")[0]) == 4 and 1900 <= int(token[:4]) <= 2099:
            continue
        value = Decimal(token)
        if text[match.end():match.end() + 2].lstrip().startswith("万"):
            value *= 10000
        out.append((token, value))
    return out


def numbers_backing_errors(
    answer_md: str, query_result, preview_limit: int
) -> list[str]:
    text = _backing_text(query_result, preview_limit)
    backing_numbers: set[Decimal] = set()
    for raw in _NUMBER_RE.findall(text):
        try:
            backing_numbers.add(Decimal(raw))
        except InvalidOperation:
            pass

    errors: list[str] = []
    for token, value in _answer_number_values(answer_md):
        if token in text:
            continue
        if value in backing_numbers:
            continue
        tol = max(Decimal("0.011"), abs(value) * Decimal("0.001"))
        if any(abs(value - backing) <= tol for backing in backing_numbers):
            continue
        errors.append(f"结论中的数字 {token} 在结果预览中找不到出处（疑似脑补）")
    return errors


def _check_hard_rules(status: str, report: FinalReport, state: DataAnalyzeState) -> list[str]:
    errors: list[str] = []
    query_result = state.get("query_result")

    if status == "answered":
        if not report.sql:
            errors.append("answered 报告缺少实际执行的 SQL")
        elif query_result is not None and report.sql != query_result.executed_sql:
            errors.append("报告 SQL 与 query_result.executed_sql 不一致")
        if query_result is not None and report.answer_md.strip():
            preview_limit = get_app_config().graph.empty_result_preview_rows
            errors.extend(numbers_backing_errors(
                report.answer_md, query_result, preview_limit
            ))

    elif status == "failed":
        if not report.answer_md.strip():
            errors.append("failed 报告必须说明已尝试什么与失败原因")
        if not report.sql:
            errors.append("failed 报告必须附上已尝试的 SQL")

    elif status == "refused":
        if not report.answer_md.strip():
            errors.append("refused 报告必须说明缺什么数据/为什么拒答")

    elif status == "clarified":
        if not report.answer_md.strip():
            errors.append("clarified 报告必须带面向用户的反问话术")

    elif status == "cancelled":
        if not report.answer_md.strip():
            errors.append("cancelled 报告必须说明取消原因或零命中原因")

    return errors


def _humanize_error(msg: str | None) -> str | None:
    return re.sub(r"^\[\w+\]\s*", "", msg) if msg else msg


def _build_failed_answer(
    state: DataAnalyzeState, reason: str = "", *, downgraded: bool = False
) -> str:
    repair_count = state.get("repair_count", 0)
    if downgraded:
        lines = ["结论未通过收口校验，未向你硬交答案。"]
    else:
        lines = [f"SQL 修复/重试已达上限（{repair_count} 次），本次查询未能完成。"]
    db_error = state.get("db_error")
    verdict = state.get("review_verdict")
    if db_error is not None:
        lines.append(f"最后一次数据库报错：{db_error.message}")
    elif verdict is not None and verdict.issues:
        lines.append("最后一次审查意见：")
        lines.extend(f"- {issue.detail}" for issue in verdict.issues)
    wr = state.get("write_result")
    if wr is not None and not wr.get("ok"):
        lines.append(f"写操作未完成：{_humanize_error(wr.get('error_message')) or '未知原因'}")
    node_error = state.get("node_error")
    if node_error:
        lines.append(f"内部异常：{node_error}")
    if reason:
        lines.append(f"收口校验未通过：{reason}")
    return "\n".join(lines)


def build_final_report(state: DataAnalyzeState) -> FinalReport:
    status = determine_status(state)
    attempt = state.get("current_attempt")
    query_result = state.get("query_result")
    analysis = state.get("data_analysis")
    route_decision = state.get("route_decision")
    route = route_decision.route if route_decision else None
    preview_limit = get_app_config().graph.empty_result_preview_rows

    chart_artifact = state.get("chart")
    chart_path = (
        chart_artifact.path
        if status == "answered" and analysis is not None and chart_artifact is not None
        else None
    )

    tables_used = list(attempt.used_tables) if attempt else []
    attempted_sql = attempt.sql if attempt else ""
    tier_used = state.get("tier", "lite")
    escalated = state.get("escalation_count", 0) > 0
    repairs_used = state.get("repair_count", 0)

    if status == "answered":
        if route == "write":
            wr = state.get("write_result") or {}
            answer_md = _last_ai_reply(state)
            confidence = "high"
            sql = state.get("write_sql", "")
            columns, preview_rows = [], []
            row_count = int(wr.get("actual_rows") or 0)
            truncated = False
            duration_ms = int(wr.get("elapsed_ms") or 0)
        else:
            if analysis is not None:
                answer_md = analysis.answer_md
                confidence = analysis.confidence
            else:
                answer_md = _last_ai_reply(state)
                confidence = "high"
            sql = query_result.executed_sql if query_result else ""
            columns = list(query_result.columns) if query_result else []
            preview_rows = list(query_result.rows[:preview_limit]) if query_result else []
            row_count = query_result.row_count if query_result else 0
            truncated = query_result.truncated if query_result else False
            duration_ms = query_result.duration_ms if query_result else 0

    elif status == "chitchat":
        answer_md = _last_ai_reply(state)
        sql, confidence = "", "low"
        columns, preview_rows, row_count, truncated, duration_ms = [], [], 0, False, 0

    elif status == "clarified":
        if state.get("clarify_outcome") == "exhausted":
            last_question = (
                route_decision.clarifying_question if route_decision is not None else ""
            )
            answer_md = (
                f"已进行 {state.get('clarify_rounds', 0)} 轮澄清仍未明确需求，本次先收口。"
                "你可以换个问法再试。"
                + (f"最新待确认：{last_question}" if last_question.strip() else "")
            )
        else:
            answer_md = (
                route_decision.clarifying_question if route_decision is not None
                and route_decision.clarifying_question.strip()
                else _last_ai_reply(state)
            )
        sql, confidence = "", "low"
        columns, preview_rows, row_count, truncated, duration_ms = [], [], 0, False, 0

    elif status == "refused":
        wr = state.get("write_result") or {}
        answer_md = (
            analysis.refuse_reason if analysis is not None and analysis.refused
            else _humanize_error(wr.get("error_message")) or _last_ai_reply(state)
        )
        sql = attempted_sql or state.get("write_sql", "")
        confidence = "low"
        columns, preview_rows, row_count, truncated, duration_ms = [], [], 0, False, 0

    elif status == "cancelled":
        if (state.get("write_result") or {}).get("error_code") == "user_cancelled":
            answer_md = ("已按你的决定取消，未执行任何写入。\n"
                         f"未执行的 SQL：{state.get('write_sql', '')}")
            sql = state.get("write_sql", "")
        elif route == "clarify" and state.get("clarify_outcome") == "give_up":
            sql = ""
            answer_md = "好的，本次查询已取消，未访问数据库。需要时换个问法再告诉我。"
        else:  
            answer_md, sql = _last_ai_reply(state), attempted_sql
        confidence = "low"
        columns, preview_rows, row_count, truncated, duration_ms = [], [], 0, False, 0

    else:  
        answer_md = _build_failed_answer(state)
        sql = attempted_sql
        confidence = "low"
        columns, preview_rows, row_count, truncated, duration_ms = [], [], 0, False, 0
        if query_result is not None:
            duration_ms = query_result.duration_ms

    active_ms = state.get("active_ms", 0) or 0
    usage_by_model = dict(state.get("llm_usage_by_model") or {})

    return FinalReport(
        answer_md=answer_md,
        sql=sql,
        status=status,
        confidence=confidence,
        columns=columns,
        preview_rows=preview_rows,
        row_count=row_count,
        truncated=truncated,
        chart_path=chart_path,               
        tables_used=tables_used,
        tier_used=tier_used,
        escalated=escalated,
        repairs_used=repairs_used,
        duration_ms=active_ms or duration_ms,  
        tokens_in=state.get("llm_tokens_in", 0) or 0,
        tokens_out=state.get("llm_tokens_out", 0) or 0,
        usage_by_model=usage_by_model,
    )


def finalize_node(state: DataAnalyzeState) -> dict:
    report = build_final_report(state)
    errors = _check_hard_rules(report.status, report, state)

    if errors and report.status != "failed":
        reason = "；".join(errors)
        report = FinalReport(
            answer_md=_build_failed_answer(state, reason=reason, downgraded=True),
            sql=report.sql or (state.get("current_attempt").sql if state.get("current_attempt") else ""),
            status="failed",
            confidence="low",
            columns=[],
            preview_rows=[],
            row_count=0,
            truncated=False,
            chart_path=None,
            tables_used=report.tables_used,
            tier_used=report.tier_used,
            escalated=report.escalated,
            repairs_used=report.repairs_used,
            duration_ms=report.duration_ms,
            tokens_in=report.tokens_in,
            tokens_out=report.tokens_out,
            usage_by_model=dict(report.usage_by_model or {}),
        )
        fatal = _check_hard_rules("failed", report, state)
        if fatal:
            raise RuntimeError("finalize 无法组装出合法报告：" + "；".join(fatal))

    return {"final_report": report, "node_error": None}
