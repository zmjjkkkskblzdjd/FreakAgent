
from __future__ import annotations

import re

from data_analyze_agent.agents.prompts import get_prompt
from data_analyze_agent.core import AnalysisPlan, RetrievedCard, build_dataclass
from data_analyze_agent.core.llm import ask_llm_json, create_llm_for_tier
from data_analyze_agent.graph.state import DataAnalyzeState

__all__ = ["make_analysis_plan", "plan_node", "route_after_plan"]

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


def _build_planner_user_content(
    question: str,
    cards: dict[str, list[RetrievedCard]],
    feedback: str = "",
) -> str:
    parts = [
        _render_card_group(title, cards.get(kind, []))
        for kind, title in _CARD_GROUP_TITLES
    ]
    if feedback.strip():
        parts.append(
            f"【升级原因】（deep 立项的缘由，提纲必须正面回应它）\n{feedback.strip()}"
        )
    parts.append(f"【本轮问题】\n{question}")
    return "\n\n".join(parts)


def _check_plan_shape(
    plan: AnalysisPlan, cards: dict[str, list[RetrievedCard]]
) -> None:
    errors: list[str] = []
    if not plan.goal.strip():
        errors.append("goal 不能为空：给一句话任务目标")
    if not plan.metrics:
        errors.append("metrics 不能为空：至少给出 1 条指标口径")
    if not plan.success_criteria:
        errors.append("success_criteria 不能为空：至少 1 条可独立核对的验收标准")

    if plan.metrics:
        card_text = "\n".join(c.content for group in cards.values() for c in group)
        for i, metric in enumerate(plan.metrics, start=1):
            if not metric.name.strip() or not metric.expression_hint.strip():
                errors.append(f"metrics[{i}] 的 name / expression_hint 不能为空")
            for table in metric.related_tables:
                pattern = rf"(?<![A-Za-z0-9_]){re.escape(table.strip('`'))}(?![A-Za-z0-9_])"
                if not re.search(pattern, card_text, flags=re.IGNORECASE):
                    errors.append(
                        f"metrics[{i}].related_tables 声明了 {table!r}，"
                        "但三卡材料中没有该表（只能使用检索到的表）"
                    )
    if errors:
        raise ValueError("AnalysisPlan 形状校验失败：\n- " + "\n- ".join(errors))


def _to_analysis_plan(
    data: dict, cards: dict[str, list[RetrievedCard]]
) -> AnalysisPlan:
    data = dict(data or {})
    for key in ("metrics", "dimensions", "filters", "joins", "success_criteria"):
        data[key] = data.get(key) or []
    for key in ("goal", "time_range", "notes"):
        data[key] = data.get(key) or ""
    if data.get("need_chart") is None:
        data["need_chart"] = False
    for metric in data["metrics"]:
        if isinstance(metric, dict):
            metric["related_tables"] = metric.get("related_tables") or []
            metric["name"] = metric.get("name") or ""
            metric["expression_hint"] = metric.get("expression_hint") or ""

    plan = build_dataclass(AnalysisPlan, data)
    _check_plan_shape(plan, cards)
    return plan


def make_analysis_plan(
    question: str,                            
    cards: dict[str, list[RetrievedCard]],    
    feedback: str = "",                       
    *,
    llm=None,
) -> AnalysisPlan:
    system_prompt = get_prompt("planner")
    user_content = _build_planner_user_content(question, cards, feedback)

    result = ask_llm_json(system_prompt, user_content, llm=llm)
    if not result["ok"]:
        raise RuntimeError(f"分析规划 LLM 调用失败：{result['error']}")

    try:
        return _to_analysis_plan(result["data"], cards)
    except ValueError as first_error:
        retry_content = (
            f"{user_content}\n\n【上一次输出未通过校验】\n{first_error}\n"
            "请严格按系统提示词的输出格式修正，只返回 JSON 对象。"
        )
        retry = ask_llm_json(system_prompt, retry_content, llm=llm)
        if not retry["ok"]:
            raise RuntimeError(f"分析规划补调仍失败：{retry['error']}") from first_error
        return _to_analysis_plan(retry["data"], cards)


def plan_node(state: DataAnalyzeState) -> dict:
    question = state.get("clarified_question")
    if not question:
        raise ValueError("plan_node 未取到 clarified_question，无法立项")

    cards = {
        "schema": state.get("schema_docs") or [],
        "glossary": state.get("glossary_hits") or [],
        "fewshot": state.get("fewshot_hits") or [],
    }

    analysis = state.get("data_analysis")
    verdict = state.get("review_verdict")
    if analysis is not None and analysis.escalate and analysis.escalate_reason.strip():
        feedback = analysis.escalate_reason
    elif verdict is not None and not verdict.passed:
        feedback = "\n".join(
            f"问题：{issue.detail}\n建议：{issue.suggestion}"
            for issue in verdict.issues
        )
    else:
        feedback = ""

    patch: dict = {"analysis_plan": make_analysis_plan(
        question, cards, feedback, llm=create_llm_for_tier("deep"))}
    if state.get("tier") == "lite":
        patch["tier"] = "deep"
        patch["escalation_count"] = state.get("escalation_count", 0) + 1
        patch["repair_count"] = 0
    return patch


def route_after_plan(state: DataAnalyzeState) -> str:
    if state.get("node_error"):
        return "finalize"               
    return "write_sql"
