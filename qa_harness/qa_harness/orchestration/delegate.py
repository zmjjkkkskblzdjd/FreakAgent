import hashlib
import time
from datetime import datetime
from typing import Any, Callable

from core import CallLogEntry, QATaskContext
from orchestration.guardrails import apply_escalation, check_precondition, check_repeat_call
from orchestration.message_bus import safe_invoke,validate_output,sanitize
from tools import tool
from observability.tracer import Tracer,TraceEvent

_ACTOR_BY_ACTION: dict[str, str] = {
    "delegate_route": "router",
    "delegate_plan": "planner",
    "delegate_retrieve": "retriever",
    "delegate_answer": "answerer",
    "delegate_critic": "critic",
}


def as_delegate_tool(
    *,
    ctx: QATaskContext,
    name: str,
    description: str,
    agent_factory: Callable[[], Any],
    action: str,
    summarize: Callable[[Any], dict],
    write_back: Callable[[QATaskContext, Any], None],
    max_loops: int = 10,
    tracer: Tracer | None = None,
    parameter_descriptions: dict[str, str] | None = None,
) -> Callable[..., dict]:
    param_desc = parameter_descriptions or {"task_summary": "本轮任务摘要"}
    repeat_threshold = max(2, max_loops // 3)

    def _append_log(
        seq: int,
        started: float,
        ok: bool,
        error: str,
        args_digest: str,
    ) -> None:
        actor = _ACTOR_BY_ACTION.get(action, action)
        duration_ms = int((time.time() - started) * 1000)
        ctx.call_log.append(CallLogEntry(
            seq=seq,
            delegate=action,
            actor=actor,
            started_at=datetime.now().isoformat(),
            duration_ms=duration_ms,
            ok=ok,
            error=error,
            args_digest=args_digest,
        ))
        if tracer is not None:
            tracer.emit(TraceEvent(
                session_id=ctx.session_id,
                seq=seq,
                timestamp=datetime.now().isoformat(),
                event_type="delegate_end",
                action=action,
                actor=actor,
                ok=ok,
                error=error,
                duration_ms=duration_ms,
                args_digest=args_digest,
                tier=ctx.route_decision.tier if ctx.route_decision else "",
            ))

    @tool(name=name, description=description, parameter_descriptions=param_desc)
    def delegate_handler(task_summary: str) -> dict:
        started = time.time()
        args_digest = hashlib.md5(task_summary.encode("utf-8")).hexdigest()[:8]
        seq = len(ctx.call_log) + 1
        try:
            ok, reason = check_precondition(ctx, action)
            if not ok:
                _append_log(seq, started, False, reason, args_digest)
                return {"ok": False, "error": f"前置条件不满足：{reason}"}

            ok, reason = check_repeat_call(ctx, action, args_digest, repeat_threshold)
            if not ok:
                _append_log(seq, started, False, reason, args_digest)
                return {"ok": False, "error": reason}

            agent = agent_factory()
            result = safe_invoke(agent,task_summary)
            if not result.get("ok"):
                err = result.get("error", "子 Agent 失败")
                _append_log(seq, started, False, err, args_digest)
                return {"ok": False, "error": err}


            data = result["data"]
            v = validate_output(action, data)
            if not v["ok"]:
                _append_log(seq, started, False, v["reason"], args_digest)
                return {"ok": False, "error": f"schema 校验失败：{v['reason']}"}
            write_back(ctx, data)

            brief = summarize(data)
            brief = sanitize(brief)
            if getattr(data, "escalate", False):
                new_tier = apply_escalation(ctx)
                if new_tier is not None:
                    brief["escalation_applied"] = new_tier
                    brief["escalate"] = False  

            _append_log(seq, started, True, "", args_digest)
            return {"ok": True, "brief": brief}
        except Exception as e:
            err = f"委派工具 {name!r} 执行异常：{type(e).__name__}: {e}"
            _append_log(seq, started, False, err, args_digest)
            return {"ok": False, "error": err}

    return delegate_handler
