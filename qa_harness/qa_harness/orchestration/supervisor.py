from __future__ import annotations

import sys
from typing import Any

from config import TierModelConfig, TokenBudgetConfig
from context import ContextCompressor, ConversationNotes
from core import (
    AgentRuntime,
    Draft,
    EvidenceReport,
    QATaskContext,
    RouteDecision,
    get_api_key,
    Plan,
    Verdict
)
from core.messages import MessageStore
from tools import ToolRegistry
from tools.answer_tool import submit_answer_tool
from agents import AnswererAgent, RetrieverAgent, RouterAgent,PlannerAgent,CriticAgent, RETRIEVER_PROFILE, RETRIEVER_KB_ONLY_PROFILE
from agents.prompts import get_prompt

from memory import LongTermMemory
from orchestration.delegate import as_delegate_tool
from orchestration.guardrails import check_tier_change
from observability import Tracer




class SupervisorRuntime(AgentRuntime):

    def __init__(
        self,
        *args: Any,
        compressor: ContextCompressor | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.compressor = compressor
        self.compress_actions: list[str] = []

    def build_runtime_messages(
        self,
        message_store: MessageStore,
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if self.compressor is not None:
            action = self.compressor.run()
            if action != "no_action":
                self.compress_actions.append(action)
                print(f"[compressor] {action}", file=sys.stderr)
        return super().build_runtime_messages(message_store, state)





def _summarize_route(rd: RouteDecision) -> dict:
    return {
        "route": rd.route,
        "tier": rd.tier,
        "reason": rd.reason,
        "clarifying_question": rd.clarifying_question,
    }

def _summarize_plan(p: Plan) -> dict:
    return {
        "sub_questions": [
            {"question": sq.question, "search_queries": sq.search_queries, "criterion": sq.criterion}
            for sq in p.sub_questions
        ],
        "success_criteria": p.success_criteria,
        "need_web": p.need_web,
        "notes": p.notes,
    }



def _summarize_evidence_report(er: EvidenceReport) -> dict:
    return {
        "evidences": [
            {
                "chunk_id": e.chunk_id,
                "source": e.source,
                "content_preview": e.content[:100],
                "score": e.score,
            }
            for e in er.evidences
        ],
        "gaps": er.gaps,
        "escalate": er.escalate,
        "escalate_reason": er.escalate_reason,
    }


def _summarize_draft(d: Draft) -> dict:
    return {
        "answer_preview": d.answer[:200],
        "citations": d.citations,
        "confidence": d.confidence,
        "is_refusal": d.is_refusal,
        "escalate": d.escalate,
        "escalate_reason": d.escalate_reason,
    }

def _summarize_critic(v: Verdict) -> dict:
    return {
        "passed": v.passed,
        "issues": [
            {
                "issue_type": issue.issue_type,
                "detail": issue.detail,
                "related_chunk_id": issue.related_chunk_id,
            }
            for issue in v.issues
        ],
        "fix_hint": v.fix_hint,
    }


def _write_route(ctx: QATaskContext, rd: RouteDecision) -> None:
    ok, reason = check_tier_change(ctx, rd.tier)
    if not ok:
        raise RuntimeError(reason)
    ctx.route_decision = rd

def _write_plan(ctx: QATaskContext, p: Plan) -> None:
    ctx.plan = p

def _write_evidence(ctx: QATaskContext, er: EvidenceReport) -> None:
    ctx.evidence_report = er


def _write_draft(ctx: QATaskContext, d: Draft) -> None:
    ctx.draft = d

def _write_critic(ctx: QATaskContext, v:Verdict) -> None:
        ctx.verdict = v




def _build_bound_answerer(
    ctx: QATaskContext,
    kb_tool_registry: ToolRegistry,
    model_name: str,
) -> AnswererAgent:
    agent = AnswererAgent(
        lane=ctx.route_decision.tier,  # type: ignore[union-attr]  # check_precondition 已确保非 None
        tool_registry=kb_tool_registry,
        model_name=model_name,
    )
    if ctx.route_decision.tier == "deep":
        agent.max_loops = 12
    else:
        agent.max_loops = 8
    agent.bind_evidence(ctx.evidence_report)
    return agent


def _build_bound_critic(
    ctx: QATaskContext,
    kb_tool_registry: ToolRegistry,
    model_name: str,
) -> CriticAgent:
    agent = CriticAgent(tool_registry=kb_tool_registry, model_name=model_name)
    agent.max_loops = 10
    agent.bind_plan(ctx.plan)
    agent.bind_draft(ctx.draft)
    return agent




def build_supervisor(
    ctx: QATaskContext,
    kb_tool_registry: ToolRegistry,
    max_loops: int = 10,
    message_store: MessageStore | None = None,
    budget_config: TokenBudgetConfig | None = None,
    memory: LongTermMemory | None = None,
    user_id: str = "default",
    tier_models: TierModelConfig | None = None,
    notes: ConversationNotes | None = None,
) -> AgentRuntime:
    registry = ToolRegistry()
    tracer = Tracer()  
    tm = tier_models or TierModelConfig()

    registry.register(as_delegate_tool(
        ctx=ctx,
        name="delegate_route",
        description=(
            "委派 router 判定用户问题的意图 route 与难度档位 tier。"
            "任务开始必调；返回 route(kb/web/clarify/chitchat/reject) + tier(direct/lite/deep)。"
        ),
        agent_factory=lambda: RouterAgent(model_name=tm.direct),
        action="delegate_route",
        summarize=_summarize_route,
        write_back=_write_route,
        max_loops=max_loops,
        tracer=tracer,
    ))

    registry.register(as_delegate_tool(
        ctx=ctx,
        name="delegate_plan",
        description="委派 planner 把复杂问题拆解成子问题和检索词。仅 tier=deep 时调用。",
        agent_factory=lambda: PlannerAgent(model_name=tm.deep),
        action="delegate_plan",
        summarize=_summarize_plan,
        write_back=_write_plan,
        max_loops=max_loops,
        tracer=tracer,
    ))

    registry.register(as_delegate_tool(
        ctx=ctx,
        name="delegate_retrieve",
        description=(
            "委派 retriever 检索知识库或联网证据。仅 route=kb 或 route=web 时调用；"
            "lite 档自行生成检索词，deep 档按 Plan 的 search_queries 一次性批量检索；"
            "返回证据摘要（chunk_id/source/前100字/score）+ gaps + escalate。"
        ),
        agent_factory=lambda: RetrieverAgent(
            tool_registry=kb_tool_registry,
            max_loops=12 if ctx.route_decision and ctx.route_decision.tier == "deep" else 8,
            model_name=getattr(tm, ctx.route_decision.tier) if ctx.route_decision else tm.lite,
            profile=(
                RETRIEVER_KB_ONLY_PROFILE
                if ctx.route_decision and ctx.route_decision.route == "kb"
                else RETRIEVER_PROFILE
            ),
        ),
        action="delegate_retrieve",
        summarize=_summarize_evidence_report,
        write_back=_write_evidence,
        max_loops=max_loops,
        tracer=tracer,
    ))

    registry.register(as_delegate_tool(
        ctx=ctx,
        name="delegate_answer",
        description=(
            "委派 answerer 撰写带引用的回答或拒答草稿。"
            "direct 档无证据；lite/deep 档需先调 delegate_retrieve 拿证据。"
        ),
        agent_factory=lambda: _build_bound_answerer(
            ctx, kb_tool_registry,
            model_name=getattr(tm, ctx.route_decision.tier) if ctx.route_decision else tm.lite,
        ),
        action="delegate_answer",
        summarize=_summarize_draft,
        write_back=_write_draft,
        max_loops=max_loops,
        tracer=tracer,
    ))

    registry.register(as_delegate_tool(
        ctx=ctx,
        name="delegate_critic",
        description=(
            "委派 critic 按五条检查清单核实草稿。仅 tier=deep 时调用，且必须已调 "
            "delegate_answer 写好草稿。返回 passed（true=通过）/ issues（不通过的具体问题）"
            "/ fix_hint（pass=通过、need_more_evidence=回 retrieve 补证据、"
            "fix_draft=回 answer 修草稿）。"
        ),
        agent_factory=lambda: _build_bound_critic(
            ctx, kb_tool_registry,
            model_name=tm.deep,  
        ),
        action="delegate_critic",
        summarize=_summarize_critic,
        write_back=_write_critic,
        max_loops=max_loops,
        tracer=tracer,
    ))

    registry.register(submit_answer_tool(ctx))

    compressor: ContextCompressor | None = None
    if message_store is not None:
        budget = budget_config or TokenBudgetConfig()
        compressor = ContextCompressor(store=message_store, budget=budget,
                                       model_name=tm.deep)

    system_content = get_prompt("supervisor")
    if memory is not None:
        try:
            hits = memory.recall(ctx.question, user_id=user_id, top_k=3)
            if hits:
                summary = "\n".join(
                    f"- {h.content}（{h.type}，{h.importance:.1f}）"
                    for h in hits
                )
                system_content += f"\n\n【用户档案摘要】\n{summary}"
        except Exception:
            pass

    if notes is not None:
        try:
            notes_md = notes.to_markdown()
            if notes_md.strip():
                system_content += f"\n\n【对话笔记】\n{notes_md}"
        except Exception:
            pass

    return SupervisorRuntime(
        api_key=get_api_key(),
        tool_registry=registry,
        max_loops=max_loops,
        system_message={"role": "system", "content": system_content},
        compressor=compressor,
        model_name=tm.deep,  
    )
