
from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.types import interrupt
from langchain_core.messages import HumanMessage

from config import get_app_config
from data_analyze_agent.agents.prompts import get_prompt
from data_analyze_agent.agents.router import render_history
from data_analyze_agent.core.llm import ask_llm_json, create_llm_for_tier
from data_analyze_agent.graph.state import DataAnalyzeState

if TYPE_CHECKING:  
    from langchain_openai import ChatOpenAI

__all__ = ["classify_clarify_reply", "clarify_wait_node", "after_clarify"]

_CLARIFY_OUTCOMES = ("supplement", "give_up", "new_question")


def classify_clarify_reply(
    reply: str, history_text: str, *, llm: ChatOpenAI | None = None
) -> str:
    system_prompt = get_prompt("clarify_classify")
    user_content = (
        f"【对话历史】\n{history_text or '（无）'}\n\n"
        f"【用户对最新反问的答复】\n{reply}"
    )

    result = ask_llm_json(system_prompt, user_content, llm=llm)
    if not result["ok"]:
        return "supplement"
    outcome = str(result["data"].get("outcome", "")).strip()
    if outcome in _CLARIFY_OUTCOMES:
        return outcome

    retry_content = (
        f"{user_content}\n\n【上一次输出未通过校验】\n"
        f"outcome 只允许 {list(_CLARIFY_OUTCOMES)} 之一，收到的是：{outcome!r}。"
        "请严格按系统提示词的输出格式修正，只返回 JSON 对象。"
    )
    retry = ask_llm_json(system_prompt, retry_content, llm=llm)
    if retry["ok"]:
        outcome = str(retry["data"].get("outcome", "")).strip()
        if outcome in _CLARIFY_OUTCOMES:
            return outcome
    return "supplement"


def clarify_wait_node(state: DataAnalyzeState) -> dict:
    decision = state.get("route_decision")
    if decision is None or not decision.clarifying_question.strip():
        raise ValueError("clarify_wait_node 未取到有效反问话术，无法挂起")

    rounds = state.get("clarify_rounds", 0)
    max_rounds = get_app_config().graph.max_clarify_rounds
    if rounds >= max_rounds:
        return {"clarify_outcome": "exhausted"}

    reply = interrupt(decision.clarifying_question)   
    reply_text = str(reply).strip()
    base = decision.rewritten_question or state.get("question", "")
    patch: dict = {"clarify_rounds": rounds + 1, "clarify_outcome": "supplement"}

    if not reply_text:
        patch["clarified_question"] = base
        return patch

    patch["messages"] = [HumanMessage(content=reply_text)]
    history_text = render_history(state.get("messages") or [])
    outcome = classify_clarify_reply(
        reply_text, history_text, llm=create_llm_for_tier("clarify"))
    patch["clarify_outcome"] = outcome

    if outcome == "new_question":
        return patch
    if outcome == "give_up":
        return patch
    patch["clarified_question"] = f"{base}（用户补充：{reply_text}）"
    return patch


def after_clarify(state: DataAnalyzeState) -> str:
    if state.get("node_error"):
        return "finalize"
    outcome = state.get("clarify_outcome")
    if outcome == "new_question":
        return "router"
    if outcome in ("exhausted", "give_up"):
        return "finalize"
    return "retrieve_schema"
