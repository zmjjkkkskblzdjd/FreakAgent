
from __future__ import annotations

import re

from data_analyze_agent.agents.prompts import get_prompt
from data_analyze_agent.agents.recall_memory import hints_by_type
from data_analyze_agent.core import RetrievedCard, SQLAttempt, build_dataclass
from data_analyze_agent.core.llm import ask_llm_json, create_llm_for_tier
from data_analyze_agent.graph.state import DataAnalyzeState

__all__ = ["write_sql", "write_sql_node"]

_CARD_GROUP_TITLES = (
    ("schema", "表结构卡（只能使用这里出现的表名/列名/枚举）"),
    ("glossary", "术语卡（指标口径以此为准，禁止自创定义）"),
    ("fewshot", "例题卡（标准写法优先照抄口径与结构，只改过滤条件与维度）"),
)


def _render_card_group(title: str, cards: list[RetrievedCard]) -> str:
    if not cards:
        return f"【{title}】\n（无）"
    blocks = [f"{i}.\n{card.content}" for i, card in enumerate(cards, start=1)]
    return f"【{title}】\n" + "\n\n".join(blocks)


def _render_plan(plan) -> str:
    lines = [f"目标：{plan.goal}"]
    metrics = "\n".join(
        f"{i}. {m.name} = {m.expression_hint}"
        f"（相关表：{'、'.join(m.related_tables) if m.related_tables else '（未声明）'}）"
        for i, m in enumerate(plan.metrics, start=1)
    )
    lines.append("指标口径：\n" + (metrics or "（无）"))
    lines.append("分组维度：" + ("、".join(plan.dimensions) if plan.dimensions else "（无）"))
    lines.append(f"时间范围：{plan.time_range or '（未明确，以备注为准）'}")
    lines.append("过滤条件：" + ("；".join(plan.filters) if plan.filters else "（无）"))
    lines.append("表关联：" + ("；".join(plan.joins) if plan.joins else "（无）"))
    criteria = "\n".join(
        f"{i}. {c}" for i, c in enumerate(plan.success_criteria, start=1)
    )
    lines.append("验收标准（SQL 结果必须能逐条支撑）：\n" + (criteria or "（无）"))
    if plan.notes.strip():
        lines.append(f"备注：{plan.notes}")
    header = "【分析提纲】（deep 立项产物，指标口径与相关表必须遵守，禁止自创口径）"
    return header + "\n" + "\n".join(lines)


def _build_writer_user_content(
    question: str,
    cards: dict[str, list[RetrievedCard]],
    feedback: str = "",
    plan=None,
    *,
    metric_hints: list[str] | None = None,
) -> str:
    parts: list[str] = []
    parts.extend(
        _render_card_group(title, cards.get(kind, []))
        for kind, title in _CARD_GROUP_TITLES
    )
    if metric_hints:
        lines = "\n".join(f"- {h}" for h in metric_hints)
        parts.append(
            "【指标口径参考】（用户确认过的长期记忆，口径优先级高于术语卡；"
            "与术语卡冲突时以本段为准，并在 rationale 说明取舍）\n" + lines
        )
    if plan is not None:
        parts.append(_render_plan(plan))
    if feedback.strip():
        parts.append(f"【上一轮反馈】（必须针对其原因改写，禁止提交相同 SQL）\n{feedback.strip()}")
    parts.append(f"【本轮问题】\n{question}")
    return "\n\n".join(parts)


def _check_used_tables(attempt: SQLAttempt) -> None:
    errors: list[str] = []
    sql = attempt.sql
    if not sql.strip():
        errors.append("sql 不能为空字符串")
    if not attempt.used_tables:
        errors.append("used_tables 不能为空：至少列出 SQL 中真实出现的表")
    sql_text = sql.replace("`", "")
    for table in attempt.used_tables:
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(table.strip('`'))}(?![A-Za-z0-9_])"
        if not re.search(pattern, sql_text, flags=re.IGNORECASE):
            errors.append(f"used_tables 声明了 {table!r}，但 SQL 文本中找不到该表名")
    if errors:
        raise ValueError("SQLAttempt 语义校验失败：\n- " + "\n- ".join(errors))


def _to_sql_attempt(data: dict, *, stage: str, version: int, feedback: str) -> SQLAttempt:
    attempt = build_dataclass(SQLAttempt, {
        **data, "version": version, "stage": stage, "feedback": feedback,
    })
    attempt.sql = attempt.sql.strip().rstrip(";").strip()  
    _check_used_tables(attempt)
    return attempt


def write_sql(
    question: str,                            
    cards: dict[str, list[RetrievedCard]],    
    feedback: str = "",                       
    *,
    stage: str = "draft",                     
    version: int = 1,
    plan=None,                                
    metric_hints: list[str] | None = None,    
    llm=None,
    lane: str = "ask",
) -> SQLAttempt:
    system_prompt = get_prompt("sql_writer_write") if lane == "write" else get_prompt("sql_writer")
    user_content = _build_writer_user_content(
        question, cards, feedback, plan=plan, metric_hints=metric_hints,
    )

    result = ask_llm_json(system_prompt, user_content, llm=llm)
    if not result["ok"]:
        raise RuntimeError(f"查数分析 LLM 调用失败：{result['error']}")

    try:
        return _to_sql_attempt(result["data"], stage=stage, version=version, feedback=feedback)
    except ValueError as first_error:
        retry_content = (
            f"{user_content}\n\n【上一次输出未通过校验】\n{first_error}\n"
            "请严格按系统提示词的输出格式修正，只返回 JSON 对象。"
        )
        retry = ask_llm_json(system_prompt, retry_content, llm=llm)
        if not retry["ok"]:
            raise RuntimeError(f"SQL 写作补调仍失败：{retry['error']}") from first_error
        return _to_sql_attempt(retry["data"], stage=stage, version=version, feedback=feedback)


def write_sql_node(state: DataAnalyzeState) -> dict:
    question = state.get("clarified_question")
    if not question:
        raise ValueError("write_sql_node 未取到 clarified_question，无法写 SQL")

    cards = {
        "schema": state.get("schema_docs") or [],
        "glossary": state.get("glossary_hits") or [],
        "fewshot": state.get("fewshot_hits") or [],
    }
    version = len(state.get("sql_attempts") or []) + 1
    repair_count = state.get("repair_count", 0)
    db_error = state.get("db_error")
    verdict = state.get("review_verdict")

    if db_error:
        stage, feedback = "runtime_fix", db_error.message
        repair_count += 1
    elif verdict is not None and not verdict.passed and verdict.fix_hint == "escalate_plan":
        stage, feedback = "write_draft", ""
    elif verdict is not None and not verdict.passed:
        stage = "review_fix"
        feedback = "\n".join(
            f"问题：{issue.detail}\n建议：{issue.suggestion}"
            for issue in verdict.issues
        )
        repair_count += 1
    elif state.get("analysis_plan") is not None and state.get("tier") == "deep":
        stage, feedback = "write_draft", ""
    else:
        stage, feedback = "draft", ""

    route = state.get("route_decision")
    lane = "write" if route and route.route == "write" else "ask"
    attempt = write_sql(
        question, cards, feedback, stage=stage, version=version,
        plan=state.get("analysis_plan"),
        metric_hints=hints_by_type(state, "metric_semantic"),
        llm=create_llm_for_tier(state.get("tier") or "lite"),
        lane=lane,
    )
    return {
        "current_attempt": attempt,       
        "sql_attempts": [attempt],        
        "repair_count" : repair_count
    }
