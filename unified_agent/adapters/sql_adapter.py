from __future__ import annotations

import sys
import hashlib
import logging
import os
import re
import time

from unified_agent.core import policy
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from unified_agent.core.state import SQLContext, UnifiedState
from unified_agent.memory.recall import lesson_lines
from unified_agent.observability.tracer import emit_event  

logger = logging.getLogger(__name__)

_DA_ROOT = Path(__file__).resolve().parents[2] / "DataAnalyzeAgent" / "DataAnalyzeAgent"
if str(_DA_ROOT) not in sys.path:
    sys.path.insert(0, str(_DA_ROOT))

from data_analyze_agent.agents.analyst import analyze_result_node        # noqa: E402
from data_analyze_agent.agents.executor import execute_sql_node          # noqa: E402
from data_analyze_agent.agents.planner import plan_node                  # noqa: E402
from data_analyze_agent.agents.retriever import make_retrieve_schema_node  # noqa: E402
from data_analyze_agent.agents.reviewer import review_sql_node           # noqa: E402
from data_analyze_agent.agents.sql_writer import write_sql_node          # noqa: E402
from data_analyze_agent.core import (                                   # noqa: E402
    DataAnalysis, DBError, QueryResult, RetrievedCard, ReviewIssue, ReviewVerdict, RouteDecision, SQLAttempt,
)
from data_analyze_agent.db import use_datasource                      # noqa: E402
from data_analyze_agent.metrics.fewshot import bind_fewshot_user     # noqa: E402
from data_analyze_agent.core.llm import set_route_resolver as _da_set_route_resolver  # noqa: E402
from data_analyze_agent.core.llm import (                              # noqa: E402
    bind_llm_budget, reset_llm_budget,
)
from config import get_app_config                                       # noqa: E402

from unified_agent.core.llm import qa_route as _ua_qa_route          # noqa: E402


def _da_route_resolver() -> dict[str, Any] | None:
    route = _ua_qa_route()
    if route is None:
        return None
    return {
        "api_url": (route["api_url"].rsplit("/chat/completions", 1)[0]
                    if route["api_url"].endswith("/chat/completions")
                    else route["api_url"]),
        "model": route.get("model"),
        "api_key": route["api_key"],
        "temperature": route.get("temperature"),
        "max_tokens": route.get("max_tokens"),
    }


_da_set_route_resolver(_da_route_resolver)

_CTX_FIELDS = (
    "question", "schema", "metrics", "example_sql",
    "executed_sql", "query_result", "checked_sql_pre_injection",
    "policy_injected", "injection_hash", "is_write",
    "analysis_md", "sql",
    "review_passed", "review_fix_hint", "repair_count", "db_error",
    "tier", "escalation_count",
    "status", "outcome", "error_code", "retriable",
)


def _sync_from_ctx(state: UnifiedState) -> dict:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    return {
        "question": c.question,
        "schema": c.schema,
        "metrics": c.metrics,
        "example_sql": c.example_sql,
    }


def _sync_to_ctx(state: UnifiedState, patch: dict) -> dict:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    updates = {k: patch[k] for k in _CTX_FIELDS if k in patch}
    if not updates:
        return {"sql_ctx": c}
    return {"sql_ctx": SQLContext.model_validate({**c.model_dump(), **updates})}


def _wrap(fn):
    def node(state: UnifiedState) -> dict:
        inputs = _sync_from_ctx(state)
        patch = fn(inputs) or {}
        return _sync_to_ctx(state, patch)
    return node



def _cards(raw: Any) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        out = []
        for item in raw:
            if isinstance(item, RetrievedCard):
                out.append(item)
            elif isinstance(item, dict):
                out.append(RetrievedCard(**item))
        return out
    return []


_DA_HINT_TYPES = ("metric_semantic", "preference", "profile")   


def _sql_hints(state: UnifiedState) -> list[dict]:
    hints = []
    if state.get("task_type") == "hybrid":
        hints.append({
            "content": ("车道边界：本业务库只含销售明细与维度表（sales_dw）；"
                        "上市公司财报等外部实体不在库中，不要用 brand/product_name "
                        "等字段臆造匹配，只回答业务库可覆盖的指标与维度部分。"),
            "memory_type": "sql",
            "importance": 1.0,
        })
    for line in lesson_lines(state, ("sql", "hybrid")):
        hints.append({"content": line, "memory_type": "metric_semantic",
                      "importance": 1.0})
    for h in state.get("recalled_memory") or []:
        meta = h.get("metadata") or {}
        hint = str(meta.get("hint_type") or "")
        if hint in _DA_HINT_TYPES:
            content = str(meta.get("summary") or h.get("text") or "")
        elif meta.get("memory_type") == "sql":
            content, hint = str(h.get("text") or ""), "sql"   
        else:
            continue
        if not content:
            continue
        hints.append({"content": content,
                      "memory_type": hint,
                      "importance": 1.0 - float(h.get("distance") or 0.0)})
    return hints


def _to_data_state(state: UnifiedState) -> dict:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    q = c.question or state.get("resolved_question") or state.get("question", "")
    da: dict = {
        "question": q,
        "clarified_question": q,
        "schema_docs": _cards(c.schema),
        "glossary_hits": _cards(c.metrics),
        "fewshot_hits": _cards(c.example_sql),
        "tier": c.tier,                      
        "repair_count": c.repair_count,      
        "escalation_count": c.escalation_count,
        "sql_attempts": [],
        "memory_hints": _sql_hints(state),
        "route_decision": RouteDecision(
            route="write" if c.is_write else "ask", tier=c.tier, reason="",
        ),
    }
    if c.sql:
        da["current_attempt"] = SQLAttempt(
            sql=c.sql, version=1, stage="draft", used_tables=[], rationale="",
        )
    if c.checked_sql_pre_injection:
        da["review_verdict"] = ReviewVerdict(
            passed=True, issues=[], fix_hint="pass", risk_level="low",
            checked_sql=c.checked_sql_pre_injection,
        )
    elif c.review_passed is False and c.sql:
        da["review_verdict"] = ReviewVerdict(
            passed=False,
            issues=[ReviewIssue(**d) for d in (c.review_issues or [])],
            fix_hint=c.review_fix_hint or "fix_sql",
            risk_level="low", checked_sql=c.sql,
        )
    if c.db_error:
        da["db_error"] = DBError(kind="runtime", message=c.db_error)
    if c.query_result:
        qr = c.query_result
        if isinstance(qr, dict):
            da["query_result"] = QueryResult(**qr)
        elif isinstance(qr, QueryResult):
            da["query_result"] = qr
    return da


def _simplify(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _simplify(v) for k, v in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [_simplify(v) for v in value]
    return value


def _from_data_patch(state: UnifiedState, patch: dict) -> dict:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    out: dict = {}

    if "schema_docs" in patch:
        out["schema"] = _simplify(patch["schema_docs"])
    if "glossary_hits" in patch:
        out["metrics"] = _simplify(patch["glossary_hits"])
    if "fewshot_hits" in patch:
        out["example_sql"] = _simplify(patch["fewshot_hits"])

    attempt = patch.get("current_attempt")
    if attempt is not None:
        out["sql"] = getattr(attempt, "sql", None)

    if "repair_count" in patch:
        out["repair_count"] = int(patch["repair_count"] or 0)

    prev_count = int((state.get("sql_ctx") or SQLContext()).escalation_count or 0)
    if "tier" in patch and patch["tier"]:
        out["tier"] = str(patch["tier"])
        if out["tier"] == "deep" and "escalation_count" in patch \
                and int(patch["escalation_count"] or 0) > prev_count:
            emit_event("escalation", "sql_plan", "escalate",
                       {"reason": "escalate_plan", "tier": "lite->deep",
                        "escalation_count": int(patch["escalation_count"] or 0)})
    if "escalation_count" in patch:
        out["escalation_count"] = int(patch["escalation_count"] or 0)

    verdict = patch.get("review_verdict")
    if verdict is not None:
        if getattr(verdict, "passed", False):
            out["checked_sql_pre_injection"] = getattr(verdict, "checked_sql", None)
            out["review_passed"] = True
            out["review_fix_hint"] = ""
            out["review_issues"] = []
            out["status"] = "ok"
            out["outcome"] = "answered"
        else:
            out["review_passed"] = False
            out["review_fix_hint"] = getattr(verdict, "fix_hint", "") or ""
            out["review_issues"] = [
                asdict(i) if is_dataclass(i)
                else dict(i) if isinstance(i, dict)
                else {"code": "other", "detail": str(i),
                      "suggestion": "", "auto_fixed_sql": None}
                for i in (getattr(verdict, "issues", None) or [])
            ]

    qr = patch.get("query_result")
    if qr is not None:
        out["query_result"] = _simplify(qr)
        out["executed_sql"] = qr.executed_sql if isinstance(qr, QueryResult) else qr.get("executed_sql")
        out["status"] = "ok"
        out["outcome"] = "answered"
        out["db_error"] = ""   
        out["error_code"] = None
        out["retriable"] = False

    dbe = patch.get("db_error")
    if dbe is not None:
        msg = getattr(dbe, "message", "") or str(dbe)
        m = re.match(r"^\[([a-z_]+)\]", msg)
        code = m.group(1) if m else getattr(dbe, "kind", "")
        out["error_code"] = f"{code}: {msg}"
        out["db_error"] = msg
        out["status"] = "error"
        out["retriable"] = True

    analysis = patch.get("data_analysis")
    if analysis is not None:
        out["analysis_md"] = getattr(analysis, "answer_md", "")
        if getattr(analysis, "refused", False):
            out["outcome"] = "refused"

    route = patch.get("route_decision")
    if route is not None:
        out["is_write"] = getattr(route, "route", "") == "write"

    return {"sql_ctx": SQLContext.model_validate({**c.model_dump(), **out})}


def _node_budget(name: str, remaining: float | None) -> float:
    cap = _LLM_BUDGET_BY_NODE.get(name, _LLM_BUDGET_SEC)
    if remaining is None:
        return cap
    return max(_LLM_MIN_BUDGET_SEC, min(cap, remaining))


def _wrap_data_node(fn):
    name = getattr(fn, "__name__", "sql_node")

    def node(state: UnifiedState, config=None) -> dict:
        thread = ((config or {}).get("configurable") or {}).get("thread_id", "") \
            if isinstance(config, dict) else ""
        session = state.get("session_id", "") or ""
        c: SQLContext = state.get("sql_ctx") or SQLContext()
        emit_event("node_enter", name, "running", {}, thread, session)
        t0 = time.perf_counter()

        def _dur() -> int:
            return int((time.perf_counter() - t0) * 1000)

        if c.status == "error":
            emit_event("node_exit", name, "skipped",
                       {"reason": "upstream_error"}, thread, session, _dur())
            return {}                    
        remaining = lane_remaining(c)
        if remaining is not None and remaining <= 0:
            emit_event("node_exit", name, "error",
                       {"error": "lane_deadline"}, thread, session, _dur())
            return _abort(c, "lane_deadline", retriable=False)

        budget = _node_budget(name, remaining)
        token = bind_llm_budget(deadline=time.monotonic() + budget, max_retries=0)
        try:
            da_state = _to_data_state(state)
            with use_datasource(state.get("datasource_id"),
                                state.get("tenant_id"), state.get("user_id"),
                                state.get("role") or "member"), \
                    bind_fewshot_user(state.get("user_id")):
                patch = fn(da_state) or {}
            out = _from_data_patch(state, patch)
        except Exception as e:           # noqa: BLE001 车道内降级，不留炸穿路径
            timed_out = _is_timeout(str(e))
            code = f"{'llm_timeout' if timed_out else 'node_crash'}: {e}"
            emit_event("node_exit", name, "error", {"error": code},
                       thread, session, _dur())
            return _abort(c, code, retriable=True)
        finally:
            reset_llm_budget(token)
        emit_event("node_exit", name, "ok", {}, thread, session, _dur())
        return out
    return node



def _inject_read(state: UnifiedState) -> dict:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    if c.status == "error":
        return {}                        
    scope = policy.resolve_scope(state.get("tenant_id", "default"))
    filters = scope["read_filters"]
    base = c.checked_sql_pre_injection or c.sql
    if filters and base:
        c.executed_sql = policy.inject_read_filters(base, filters)
        c.policy_injected = True
        c.injection_hash = hashlib.sha256(c.executed_sql.encode("utf-8")).hexdigest()[:16]
    elif base:
        c.executed_sql = base
    return {"sql_ctx": c}




_MAX_REPAIR_ROUNDS = 1


_LANE_DEADLINE_SEC = float(os.getenv("UA_SQL_LANE_DEADLINE_SEC", "180"))
_LANE_DEGRADE_SEC = float(os.getenv("UA_SQL_LANE_DEGRADE_SEC", "60"))
_LLM_BUDGET_SEC = float(os.getenv("UA_SQL_LLM_TIMEOUT_SEC", "90"))
_LLM_MIN_BUDGET_SEC = 5.0
_LLM_BUDGET_BY_NODE = {
    "write_sql_node": _LLM_BUDGET_SEC,
    "review_sql_node": float(os.getenv("UA_SQL_REVIEW_TIMEOUT_SEC", "50")),
    "analyze_result_node": _LLM_BUDGET_SEC,
    "plan_node": _LLM_BUDGET_SEC,
}

_DEGRADE_PREFIXES = ("lane_deadline", "llm_timeout", "node_crash", "review_escalated")

_TIMEOUT_MARKERS = ("timed out", "timeout", "time out")


def lane_deadline_sec() -> float:
    return _LANE_DEADLINE_SEC


def lane_degrade_sec() -> float:
    return _LANE_DEGRADE_SEC


def _is_timeout(msg: str) -> bool:
    low = (msg or "").lower()
    return any(m in low for m in _TIMEOUT_MARKERS)


def is_fatal_code(code: str | None) -> bool:
    return (code or "").startswith(("node_crash", "lane_deadline", "llm_timeout"))


def lane_remaining(c: SQLContext) -> float | None:
    if not c.lane_deadline_ts:
        return None
    return float(c.lane_deadline_ts) - time.time()


def should_degrade_retry(c: SQLContext) -> bool:
    if c.degrade_retried or c.status != "error":
        return False
    code = c.error_code or ""
    return any(code.startswith(p) for p in _DEGRADE_PREFIXES) or _is_timeout(code)


def _abort(c: SQLContext, code: str, *, retriable: bool) -> dict:
    return {"sql_ctx": SQLContext.model_validate({
        **c.model_dump(), "status": "error", "outcome": "refused",
        "error_code": code, "retriable": retriable,
    })}


def _auto_repair_on() -> bool:
    return bool(get_app_config().sql_guard.use_auto_repair)


def _max_escalations() -> int:
    return max(0, int(get_app_config().graph.max_escalations))



_COMMENT_PREFIX_RE = re.compile(r"^(?:\s*(?:--[^\n]*\n|/\*.*?\*/))*\s*", re.DOTALL)
_WORD_RE = re.compile(r"[A-Za-z]+")
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")          
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_RE = re.compile(r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.)*\"", re.DOTALL)
_WRITE_KW_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|MERGE|GRANT|REVOKE"
    r"|PREPARE|EXECUTE|CALL|SET|LOCK|UNLOCK|RENAME)\b"
    r"|\bREPLACE\s+INTO\b",          
    re.IGNORECASE)                   


_WRITE_INTENT_RE = re.compile(
    r"把[^。；;]{0,60}?(改成|改为|更新为|设为|置为|调整为|删除|清空)"
    r"|(插入|新增|添加|写入|录入)"
)


def has_write_intent(question: str) -> bool:
    return bool(_WRITE_INTENT_RE.search(question or ""))


def _strip_literals(sql: str) -> str:
    s = _BLOCK_COMMENT_RE.sub(" ", sql)
    s = _LINE_COMMENT_RE.sub(" ", s)
    return _STRING_RE.sub(" ", s)


def _cte_tail_verb(stripped: str) -> str | None:
    depth = 0
    i, n = 0, len(stripped)
    while i < n:
        ch = stripped[i]
        if ch == ";":
            return None                      
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return None                  
            if depth == 0:
                j = i + 1
                while j < n and stripped[j].isspace():
                    j += 1
                if j < n and stripped[j] == ",":
                    i = j + 1                
                    continue
                m = _WORD_RE.match(stripped, j)
                return m.group(0).upper() if m else None
        i += 1
    return None


def _write_detect(state: UnifiedState) -> dict:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    if c.status == "error":
        return {}                        
    sql = (c.checked_sql_pre_injection or c.sql or "").strip()
    s = _COMMENT_PREFIX_RE.sub("", sql, count=1).lstrip()
    root_m = _WORD_RE.match(s)
    root = root_m.group(0).upper() if root_m else "UNKNOWN"
    if root == "SELECT":
        verb = "SELECT"
        is_write = bool(_WRITE_KW_RE.search(_strip_literals(s)))
    elif root == "WITH":
        stripped = _strip_literals(s)    
        tail_verb = _cte_tail_verb(stripped)
        if tail_verb is not None:
            verb = tail_verb
            is_write = verb != "SELECT"
        else:
            verb = "UNKNOWN"
            is_write = bool(_WRITE_KW_RE.search(stripped))
    else:
        is_write, verb = True, root      
    logger.info("write_detect: root=%s verb=%s is_write=%s sql=%.80r",
                root, verb, is_write, sql)
    return {"sql_ctx": SQLContext.model_validate({**c.model_dump(), "is_write": is_write})}


def _retrieval_gate(state: UnifiedState) -> dict:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    if c.status == "error":
        return {}                        
    if _cards(c.schema):
        return {}
    return {"sql_ctx": SQLContext.model_validate({
        **c.model_dump(), "status": "ok", "outcome": "refused",
        "error_code": "schema_no_hit",
    })}


def _after_retrieval_route(state: UnifiedState) -> str:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    if c.status == "error":
        return END                       
    if c.error_code == "schema_no_hit":
        return END
    if c.tier == "deep":
        return "plan"
    return "write_sql"


def _review_gate(state: UnifiedState) -> dict:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    if c.status == "error":
        return {}                        
    if c.review_passed is not False:
        return {}                        
    if c.review_fix_hint == "fix_sql" and c.repair_count < _MAX_REPAIR_ROUNDS \
            and _auto_repair_on():
        return {}   
    if c.review_fix_hint == "need_clarify":
        return {"sql_ctx": SQLContext.model_validate({
            **c.model_dump(), "status": "ok", "outcome": "refused",
            "error_code": "review_need_clarify",
        })}
    if c.review_fix_hint == "escalate_plan" and c.tier == "lite" \
            and c.escalation_count < _max_escalations():
        return {}   
    code = ("review_escalated" if c.review_fix_hint == "escalate_plan"
            else f"review_{c.review_fix_hint or 'failed'}")
    return {"sql_ctx": SQLContext.model_validate({
        **c.model_dump(), "status": "error", "outcome": "refused",
        "error_code": code, "retriable": True,
    })}


def _after_review_route(state: UnifiedState) -> str:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    if c.status == "error":
        return END                       
    if c.review_passed is not False:
        return END if c.is_write else "policy_inject"   
    if c.review_fix_hint == "escalate_plan" and c.tier == "lite" \
            and c.escalation_count < _max_escalations():
        return "plan"
    if c.review_fix_hint == "fix_sql" and c.repair_count < _MAX_REPAIR_ROUNDS \
            and _auto_repair_on():
        return "write_sql"
    return END


def _execute_gate(state: UnifiedState) -> dict:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    if is_fatal_code(c.error_code):
        return {}   
    if c.status != "error" or not c.retriable:
        return {}   
    if _auto_repair_on() and c.repair_count < _MAX_REPAIR_ROUNDS:
        return {"sql_ctx": SQLContext.model_validate({
            **c.model_dump(), "status": "ok", "error_code": None})}
    return {"sql_ctx": SQLContext.model_validate({
        **c.model_dump(), "status": "error", "outcome": "refused",
        "error_code": c.error_code or "execute_failed",
    })}


def _after_execute_route(state: UnifiedState) -> str:
    c: SQLContext = state.get("sql_ctx") or SQLContext()
    if is_fatal_code(c.error_code):
        return END                       
    if c.status == "error":
        return END                       
    if c.db_error:
        return "write_sql"               
    if state.get("task_type") == "hybrid":
        return END                       
    return "analyze"


def build_sql_subgraph():
    g = StateGraph(UnifiedState)
    g.add_node("retrieve_schema", _wrap_data_node(make_retrieve_schema_node()))
    g.add_node("retrieval_gate", _retrieval_gate)
    g.add_node("plan", _wrap_data_node(plan_node))
    g.add_node("write_sql", _wrap_data_node(write_sql_node))
    g.add_node("write_detect", _write_detect)      
    g.add_node("review_sql", _wrap_data_node(review_sql_node))
    g.add_node("review_gate", _review_gate)
    g.add_node('policy_inject',_inject_read)
    g.add_node("execute_sql", _wrap_data_node(execute_sql_node))
    g.add_node("execute_gate", _execute_gate)
    g.add_node("analyze", _wrap_data_node(analyze_result_node))
    g.add_edge(START, "retrieve_schema")
    g.add_edge("retrieve_schema", "retrieval_gate")
    g.add_conditional_edges("retrieval_gate", _after_retrieval_route,
                            {"plan": "plan", "write_sql": "write_sql", END: END})
    g.add_edge("plan", "write_sql")
    g.add_edge("write_sql", "write_detect")        
    g.add_edge("write_detect", "review_sql")
    g.add_edge("review_sql", "review_gate")
    g.add_conditional_edges("review_gate", _after_review_route,
                            {"policy_inject": "policy_inject",
                             "write_sql": "write_sql",
                             "plan": "plan",
                             END: END})
    g.add_edge('policy_inject','execute_sql')
    g.add_edge("execute_sql", "execute_gate")
    g.add_conditional_edges("execute_gate", _after_execute_route,
                            {"analyze": "analyze",
                             "write_sql": "write_sql",
                             END: END})
    g.add_edge("analyze", END)
    return g.compile()
