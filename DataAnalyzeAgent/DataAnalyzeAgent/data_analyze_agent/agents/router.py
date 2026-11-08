
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from config import get_app_config
from data_analyze_agent.agents.prompts import get_prompt
from data_analyze_agent.agents.retriever import get_glossary_terms
from data_analyze_agent.core import RouteDecision
from data_analyze_agent.core.json_utils import build_dataclass
from data_analyze_agent.core.llm import ask_llm_json, call_llm, create_llm_for_tier
from data_analyze_agent.graph.state import DataAnalyzeState

if TYPE_CHECKING:  
    from langchain_openai import ChatOpenAI

__all__ = [
    "render_history", "route_question", "reply_chitchat",
    "router_node", "route_after_router",
]

_ROUTER_TERMINAL_ROUTES = frozenset({"chitchat", "clarify", "reject"})

_HISTORY_MAX_MESSAGES = 6
_CLARIFY_SLOTS = ("time_range", "metric_semantic", "dimension")
_CHITCHAT_FALLBACK = "抱歉，我这边暂时出了点问题，可以稍后再问我一次。"
_REJECT_REPLY = (
    "抱歉，这个问题与当前数据仓库的销售经营业务无关，我无法处理。"
    "你可以问我成交额、订单量、毛利、客单价、渠道/品类/区域销售等经营数据相关的问题。"
)
_WRITE_DISABLED_REPLY = (
    "当前环境未开启写操作功能（enable_write_lane=off），我只能查询数据，"
    "不能执行新增、修改或删除。需要写操作时请联系管理员开启写车道。"
)


def render_history(messages: list[BaseMessage]) -> str:
    dialogue = [m for m in messages if isinstance(m, (HumanMessage, AIMessage))]
    kept = dialogue[-_HISTORY_MAX_MESSAGES:]
    if kept and isinstance(kept[0], AIMessage):
        kept = kept[1:]
    lines: list[str] = []
    for m in kept:
        speaker = "用户" if isinstance(m, HumanMessage) else "助手"
        lines.append(f"{speaker}：{m.content}")
    return "\n".join(lines)


def _build_router_user_content(
    question: str,
    history_text: str,
    memory_hints: list[dict] | None,
    glossary_terms: list[str] | None = None,
) -> str:
    parts: list[str] = []
    if glossary_terms:
        parts.append("【本库指标清单】\n" + "、".join(glossary_terms))
    if memory_hints:
        hints = "\n".join(
            f"- [{h['memory_type']}] {h['content']}" for h in memory_hints
        )
        parts.append(f"【长期记忆参考】\n{hints}")
    parts.append(f"【对话历史】\n{history_text or '（无）'}")
    parts.append(f"【本轮问题】\n{question}")
    return "\n\n".join(parts)


def _to_route_decision(data: dict, question: str) -> RouteDecision:
    normalized = dict(data)
    if normalized.get("clarifying_question") is None:
        normalized["clarifying_question"] = ""
    if normalized.get("ambiguous_slots") is None:
        normalized["ambiguous_slots"] = []

    decision = build_dataclass(RouteDecision, normalized)

    errors: list[str] = []
    if decision.route == "clarify":
        if not decision.clarifying_question.strip():
            errors.append("route=clarify 时 clarifying_question 必填（反问话术不能为空）")
        bad_slots = [s for s in decision.ambiguous_slots if s not in _CLARIFY_SLOTS]
        if bad_slots:
            errors.append(
                f"ambiguous_slots 含非法值 {bad_slots}，只允许 {list(_CLARIFY_SLOTS)}"
            )
        if not decision.ambiguous_slots:
            errors.append("route=clarify 时 ambiguous_slots 至少列出一个缺失槽位")
    else:
        decision.clarifying_question = ""
        decision.ambiguous_slots = []

    if not decision.rewritten_question.strip():
        decision.rewritten_question = question

    if errors:
        raise ValueError("RouteDecision 语义校验失败：\n- " + "\n- ".join(errors))
    return decision


def route_question(
    question: str,
    history_text: str = "",
    memory_hints: list[dict] | None = None,
    *,
    llm: ChatOpenAI | None = None,
    glossary_terms: list[str] | None = None,
) -> RouteDecision:
    if glossary_terms is None:
        glossary_terms = get_glossary_terms()
    system_prompt = get_prompt("router")
    user_content = _build_router_user_content(question, history_text, memory_hints, glossary_terms)

    result = ask_llm_json(system_prompt, user_content, llm=llm)
    if not result["ok"]:
        raise RuntimeError(f"分诊 LLM 调用失败：{result['error']}")

    try:
        return _to_route_decision(result["data"], question)
    except ValueError as first_error:
        retry_content = (
            f"{user_content}\n\n【上一次输出未通过校验】\n{first_error}\n"
            "请严格按系统提示词的输出格式修正，只返回 JSON 对象。"
        )
        retry = ask_llm_json(system_prompt, retry_content, llm=llm)
        if not retry["ok"]:
            raise RuntimeError(f"分诊补调仍失败：{retry['error']}") from first_error
        return _to_route_decision(retry["data"], question)


def reply_chitchat(
    question: str, history_text: str = "", *, llm: ChatOpenAI | None = None
) -> str:
    messages = [
        SystemMessage(content=get_prompt("chitchat")),
        HumanMessage(content=(
            f"【对话历史】\n{history_text or '（无）'}\n\n【用户消息】\n{question}"
        )),
    ]
    result = call_llm(messages, llm=llm, temperature=0.7)
    if not result["ok"]:
        return _CHITCHAT_FALLBACK
    content = result["data"]["content"].strip()
    return content or _CHITCHAT_FALLBACK


def router_node(state: DataAnalyzeState) -> dict:
    messages = state.get("messages") or []
    human_indexes = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    if not human_indexes:
        raise ValueError("router_node 在 state.messages 中未找到 HumanMessage，无法分诊")
    last_human = human_indexes[-1]
    question = str(messages[last_human].content)
    history_text = render_history(messages[:last_human])

    decision = route_question(
        question, history_text, state.get("memory_hints"),
        llm=create_llm_for_tier("lite"),
    )

    write_disabled = (
        decision.route == "write"
        and not get_app_config().sql_guard.enable_write_lane
    )
    if write_disabled:
        decision = replace(
            decision, route="reject",
            reason=f"写车道开关关闭，写意图被代码层改判拒答；LLM 原始理由：{decision.reason}",
        )

    patch: dict = {
        "question": question,
        "clarified_question": decision.rewritten_question,
        "route_decision": decision,
        "tier": decision.tier,  
        "approval_asks": 0,
        "approval_granted": False,
        "write_result": None,
    }

    if decision.route == "chitchat":
        reply = reply_chitchat(
            question, history_text, llm=create_llm_for_tier("chitchat"))
        patch["messages"] = [AIMessage(content=reply)]
    elif decision.route == "clarify":
        patch["messages"] = [AIMessage(content=decision.clarifying_question)]
    elif decision.route == "reject":
        reply = _WRITE_DISABLED_REPLY if write_disabled else _REJECT_REPLY
        patch["messages"] = [AIMessage(content=reply)]
    return patch


def route_after_router(state):
    if state.get("node_error"):
        return "finalize"               
    decision = state.get("route_decision")
    if decision is None:
        return "end"
    if decision.route == "clarify":
        return "clarify"
    if decision.route in ("chitchat", "reject"):
        return "finalize"

    return "retrieve_schema"            
