from __future__ import annotations

import re

from unified_agent.core.llm import ask_llm_text
from unified_agent.core.state import SQLContext, UnifiedState
from unified_agent.prompts import load_prompt
from unified_agent.tools.web_search import web_search as _web_search

_ATTR_SYSTEM, _ATTR_USER = load_prompt("web_attribution")
_EXTRACT_SYSTEM, _EXTRACT_USER = load_prompt("web_attribution_extract")

_ATTRIBUTION_KEYWORDS = ("为什么", "原因", "为何", "归因", "导致", "造成", "怎么回事", "什么情况")


def _sql_ctx(state: UnifiedState) -> SQLContext:
    c = state.get("sql_ctx")
    return c if isinstance(c, SQLContext) else SQLContext.model_validate(c or {})


def needs_external_attribution(state: UnifiedState) -> bool:
    if not state.get("need_attribution"):
        return False
    sc = _sql_ctx(state)
    if sc.status == "error" or sc.outcome == "refused":
        return False
    text = (state.get("question", "") + " " + (sc.analysis_md or ""))
    return any(kw in text for kw in _ATTRIBUTION_KEYWORDS)


def build_attribution_query(question: str, analysis_md: str) -> str:
    try:
        user = _EXTRACT_USER.format(question=question, analysis=analysis_md[:500])
        extracted = ask_llm_text(_EXTRACT_SYSTEM, user, max_tokens=150)
        parts = [p.strip() for p in (extracted or "").split("\n") if p.strip()]
        if parts:
            seen = []
            for p in parts:
                clean = re.sub(r"^[-*\d.\s]+", "", p).strip()
                if clean and clean not in seen:
                    seen.append(clean)
            return " ".join(seen) + " 原因 政策 影响"
    except Exception:
        pass
    first_sentence = (analysis_md or "").split("。")[0][:80]
    return f"{question[:30]} {first_sentence} 原因 政策 影响"


def web_attribution(state: UnifiedState) -> dict:
    sc = _sql_ctx(state)
    question = state.get("question", "")
    analysis_md = sc.analysis_md or ""

    query = build_attribution_query(question, analysis_md)
    web_evidences = _web_search(query, top_k=5)
    if not web_evidences:
        return {"sql_ctx": sc}            

    web_lines = []
    for i, e in enumerate(web_evidences, 1):
        web_lines.append(f"[{i}] {(e.get('content') or '')[:200]}\n    来源：{e.get('source', '')}")
    web_text = "\n\n".join(web_lines)

    user = _ATTR_USER.format(
        question=question,
        sql_conclusion=analysis_md,
        web_context=web_text,
    )
    try:
        new_analysis = ask_llm_text(_ATTR_SYSTEM, user, max_tokens=600)
    except Exception:
        new_analysis = ""
    if not new_analysis.strip():
        return {"sql_ctx": sc}            

    web_sources = [{
        "chunk_id": e.get("chunk_id") or "",
        "source": e.get("source") or "",
        "excerpt": (e.get("content") or "")[:300],
        "source_type": "web",
    } for e in web_evidences]
    updated = SQLContext.model_validate({
        **sc.model_dump(), "analysis_md": new_analysis.strip(),
        "web_sources": web_sources,
    })
    return {"sql_ctx": updated}
