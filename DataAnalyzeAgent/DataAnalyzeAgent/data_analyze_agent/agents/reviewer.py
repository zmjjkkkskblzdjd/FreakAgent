
from __future__ import annotations

from data_analyze_agent.core import RetrievedCard, ReviewIssue, SQLAttempt, ReviewVerdict,ask_llm_json,build_dataclass
from data_analyze_agent.core.llm import create_llm_for_tier
from data_analyze_agent.db import check, gateway, introspect
from data_analyze_agent.agents import get_prompt
from data_analyze_agent.agents.recall_memory import hints_by_type
from config import get_app_config
from data_analyze_agent.graph.state import DataAnalyzeState


_default_schema_cache: dict[int | None, list] = {}


def get_default_schema_cache() -> list:
    key = gateway.current_datasource_id()
    cache = _default_schema_cache.get(key)
    if cache is None:
        cache = introspect()
        _default_schema_cache[key] = cache
    return cache


def _build_reviewer_user_content(
    question: str,
    sql: str,
    attempt: SQLAttempt,
    cards: dict[str, list[RetrievedCard]],
    *,
    repair_count: int = 0,
    prev_issues: list[ReviewIssue] | None = None,
    metric_hints: list[str] | None = None,
) -> str:
    parts = [
        f"【待审 SQL】（stage={attempt.stage}，version={attempt.version}）\n"
        f"{sql}\n\n"
        f"声明表：{', '.join(attempt.used_tables)}\n"
        f"理由：{attempt.rationale}",
        f"【用户问题】\n{question}",
    ]

    for kind, title in (
        ("schema", "表结构卡（只能使用这里出现的表名/列名/枚举）"),
        ("glossary", "术语卡（指标口径以此为准，禁止自创定义）"),
        ("fewshot", "例题卡（标准写法优先照抄口径与结构，只改过滤条件与维度）"),
    ):
        group = cards.get(kind, [])
        if not group:
            parts.append(f"【{title}】\n（无）")
        else:
            blocks = [f"{i}.\n{card.content}" for i, card in enumerate(group, start=1)]
            parts.append(f"【{title}】\n" + "\n\n".join(blocks))

    if metric_hints:
        lines = "\n".join(f"- {h}" for h in metric_hints)
        parts.append(
            "【指标口径参考】（用户确认过的长期记忆，口径优先级高于术语卡；"
            "SQL 按本段写口径不算错，不要因与术语卡通用定义不同而打回）\n" + lines
        )

    if repair_count > 0:
        lines = [f"已打回 {repair_count} 次，上次意见："]
        if prev_issues:
            for issue in prev_issues:
                lines.append(f"- {issue.detail} → {issue.suggestion}")
        else:
            lines.append("- （无具体意见记录）")
        lines.append("纪律：同一问题不得第 3 次原样打回，应改判 escalate_plan 或 reject_query。")
        parts.append("【打回历史】\n" + "\n".join(lines))

    return "\n\n".join(parts)

def _check_passed(result: dict) -> None:
    if result.get('passed'):
        if result.get('issues') or result.get('fix_hint') != 'pass':
            raise RuntimeError("passed=true 时 issues 应为空且 fix_hint=pass")
    else:
        if not result.get('issues'):
            raise RuntimeError("passed=false 时 issues 不应为空")


def _check_repair_count(result: dict, repair_count: int) -> None:
    if (repair_count >= get_app_config().sql_guard.max_sql_repairs
            and result.get('fix_hint') == 'fix_sql'):
        result['fix_hint'] = 'escalate_plan'


def _to_review_verdict(result: dict, repair_count: int, sql: str) -> ReviewVerdict:
    _check_passed(result)
    _check_repair_count(result, repair_count)
    return build_dataclass(ReviewVerdict, {**result, 'checked_sql': sql})

def review_sql(
    question: str,
    attempt: SQLAttempt,
    cards: dict[str, list[RetrievedCard]],
    *,
    repair_count: int = 0,
    prev_issues: list[ReviewIssue] | None = None,
    metric_hints: list[str] | None = None,
    lane: str = "ask",
    schema_cache: list,
    llm=None,
) -> ReviewVerdict:
    guard=check(sql=attempt.sql,schema_cache=schema_cache,lane=lane)
    if not guard.passed:
        return guard
    if lane == "write":
        return guard
    if not get_app_config().sql_guard.use_reviewer:
        return guard
    system_prompt=get_prompt('reviewer')
    user_content=_build_reviewer_user_content(question=question,sql=guard.checked_sql,attempt=attempt,cards=cards,repair_count=repair_count,prev_issues=prev_issues,metric_hints=metric_hints)

    result=ask_llm_json(system_prompt=system_prompt,user_content=user_content,llm=llm)
    if not result["ok"]:
        raise RuntimeError(f"查数分析 LLM 调用失败：{result['error']}")

    try:
        return _to_review_verdict(result["data"], repair_count, guard.checked_sql)
    except RuntimeError as first_error:
        retry_content = (
            f"{user_content}\n\n【上一次输出未通过校验】\n{first_error}\n"
            "请严格按系统提示词的输出格式修正，只返回 JSON 对象。"
        )
        retry = ask_llm_json(system_prompt, retry_content, llm=llm)
        if not retry["ok"]:
            raise RuntimeError(f"解读员补调仍失败：{retry['error']}") from first_error
        return _to_review_verdict(retry["data"], repair_count, guard.checked_sql)


def review_sql_node(state: DataAnalyzeState) -> dict:
    question = state.get("clarified_question") or state.get("question")
    if not question:
        raise ValueError("review_sql_node 未取到 question，无法审查")

    attempt = state.get("current_attempt")
    if attempt is None:
        raise ValueError("review_sql_node 未取到 current_attempt，无法审查")

    cards = {
        "schema": state.get("schema_docs") or [],
        "glossary": state.get("glossary_hits") or [],
        "fewshot": state.get("fewshot_hits") or [],
    }

    repair_count = state.get("repair_count", 0)
    prev_verdict = state.get("review_verdict")
    prev_issues = prev_verdict.issues if prev_verdict else None

    route = state.get("route_decision")
    lane = route.route if route and route.route in ("ask", "write") else "ask"

    verdict = review_sql(
        question, attempt, cards,
        repair_count=repair_count,
        prev_issues=prev_issues,
        metric_hints=hints_by_type(state, "metric_semantic"),
        lane=lane,
        schema_cache=get_default_schema_cache(),
        llm=create_llm_for_tier(state.get("tier") or "lite"),
    )
    return {"review_verdict": verdict}

def route_after_review(state: DataAnalyzeState) -> str:
    if state.get("node_error"):
        return "finalize"               
    v = state["review_verdict"]
    if v.passed:
        decision = state.get("route_decision")
        if decision is not None and decision.route == "write":
            return "approve_write"
        return "execute"
    if v.fix_hint == "need_clarify":
        return "finalize"
    if v.fix_hint == "escalate_plan" and state.get("tier", "lite") == "lite":
        return "plan"  
    if v.fix_hint == "fix_sql":
        if not get_app_config().sql_guard.use_auto_repair:
            return "finalize"
        max_repairs = get_app_config().sql_guard.max_sql_repairs
        if state["repair_count"] < max_repairs:
            return "write_sql"
    return "finalize"