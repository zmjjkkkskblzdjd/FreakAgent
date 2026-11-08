
from __future__ import annotations

import uuid
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from data_analyze_agent.core.types import FinalReport
from data_analyze_agent.graph.builder import build_graph

__all__ = ["build_graph", "ask", "FinalReport"]

_RECURSION_LIMIT = 25

_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _interrupted_envelope(out: dict, graph, cfg: dict, thread_id: str) -> dict[str, Any]:
    interrupts = out.get("__interrupt__") or ()
    prompt = str(interrupts[0].value) if interrupts else ""
    return {
        "status": "interrupted",
        "interrupt_kind": "clarify",
        "prompt": prompt,
        "thread_id": thread_id,
    }


def ask(
    question: str | None = None,
    *,
    thread_id: str | None = None,
    reply: str | None = None,
) -> dict[str, Any]:
    tid = (thread_id or "").strip() or uuid.uuid4().hex
    cfg: dict = {
        "configurable": {"thread_id": tid},
        "recursion_limit": _RECURSION_LIMIT,
    }
    is_resume = reply is not None
    try:
        graph = _get_graph()
        if is_resume:
            out = graph.invoke(Command(resume=str(reply)), cfg)
        else:
            text = (question or "").strip()
            if not text:
                return {
                    "status": "failed",
                    "error": "question 为空：新问数必须提供非空问题；恢复挂起请用 reply=",
                    "thread_id": tid,
                }
            out = graph.invoke(
                {"question": text, "messages": [HumanMessage(content=text)]}, cfg
            )

        if out.get("__interrupt__"):
            return _interrupted_envelope(out, graph, cfg, tid)

        report = out.get("final_report")
        if report is None:
            return {
                "status": "failed",
                "error": "图运行已结束但未产出 final_report（状态机异常收口）",
                "thread_id": tid,
            }
        result = report.to_dict()
        result["thread_id"] = tid
        return result
    except Exception as exc:  # noqa: BLE001 —— 融合铁律：错误也是结构化单据
        return {
            "status": "failed",
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            "thread_id": tid,
        }
