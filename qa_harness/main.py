from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

from core import MessageStore, QATaskContext, get_api_key
from context import ConversationNotes
from knowledge import HybridRetriever, VectorStore
from memory import LongTermMemory, archive_session
from tools import ToolRegistry
from tools.kb_tools import create_kb_tools
from tools.web_tools import create_web_tools
from orchestration.supervisor import build_supervisor


def build_readonly_tool_registry() -> ToolRegistry:
    store = VectorStore()                       
    reranker = None
    if os.getenv("QA_USE_RERANKER", "").strip() in ("1", "true", "True"):
        from knowledge import LocalReranker
        model_path = os.getenv("RERANKER_MODEL_PATH", "")
        reranker = LocalReranker(model_path)
    retriever = HybridRetriever(store, reranker=reranker)
    registry = ToolRegistry()
    for t in create_kb_tools(store, retriever):
        registry.register(t)
    for t in create_web_tools():
        registry.register(t)
    return registry


def run_one_question(question: str, kb_registry: ToolRegistry) -> str:
    answer, _, _ = _run_one_get_ctx(question, kb_registry, memory=None, notes=None)
    return answer


def _run_one_get_ctx(
    question: str,
    kb_registry: ToolRegistry,
    memory: LongTermMemory | None = None,
    notes: ConversationNotes | None = None,
) -> tuple[str, QATaskContext, MessageStore]:
    ctx = QATaskContext(
        session_id=uuid.uuid4().hex[:8],
        question=question,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    message_store = MessageStore(max_turns=20)
    runtime = build_supervisor(
        ctx=ctx,
        kb_tool_registry=kb_registry,
        message_store=message_store,
        memory=memory,
        notes=notes,
    )

    try:
        final_text = runtime.run(question, message_store)
    except RuntimeError as e:
        return f"[系统] 任务未完成：{e}", ctx, message_store

    if ctx.draft is not None:
        return ctx.draft.answer, ctx, message_store

    if final_text:
        return final_text, ctx, message_store
    return "[系统] Supervisor 未调 submit_answer 就结束且无文本回复", ctx, message_store


MAX_CLARIFY_ROUNDS = 2  


def _maybe_archive(
    ctx: QATaskContext,
    memory: LongTermMemory,
    *,
    input_fn=input,
    print_fn=print,
) -> None:
    try:
        choice = input_fn(
            "是否归档本次会话的关键信息到长期记忆？(y/N) "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if choice != "y":
        return
    result = archive_session(ctx, memory)
    if not result.get("ok"):
        print_fn(f"[归档失败] {result.get('error', '未知错误')}")
        return
    records = result.get("records", [])
    for record in records:
        memory.add(record)
    if records:
        print_fn(f"[归档] 已保存 {len(records)} 条记忆。")


def run_with_clarify(
    question: str,
    kb_registry: ToolRegistry,
    *,
    input_fn=input,
    print_fn=print,
    enable_archive: bool = True,
    notes: ConversationNotes | None = None,
) -> str:
    memory = LongTermMemory()
    try:
        current_question = question
        original_question = question
        answer = ""
        ctx = None

        for round_num in range(MAX_CLARIFY_ROUNDS):
            answer, ctx, _msg_store = _run_one_get_ctx(
                current_question, kb_registry, memory=memory, notes=notes
            )

            if notes is not None and ctx.route_decision is not None:
                rd = ctx.route_decision
                areas = _derive_knowledge_areas(ctx)
                notes.light_update(
                    route=rd.route,
                    tier=rd.tier,
                    question_summary=current_question,
                    knowledge_areas=areas or None,
                )
                notes.add_dialogue_turn(_format_dialogue_turn(current_question, answer))
                if notes.needs_full_update():
                    if notes.pending_event_reason:
                        trigger_tag = f"事件：{notes.pending_event_reason}"
                    elif notes.last_full_update_turn == 0:
                        trigger_tag = "冷启动建档"
                    else:
                        trigger_tag = "周期维护"
                    print(f"[notes] 第 {notes.turn_count} 轮触发完整更新（{trigger_tag}）",
                          file=sys.stderr)
                    notes.full_update()

            rd = ctx.route_decision
            if rd is None or rd.route != "clarify":
                break  

            if round_num >= MAX_CLARIFY_ROUNDS - 1:
                break

            clarifying_q = rd.clarifying_question or answer
            print_fn(f"\n[追问] {clarifying_q}")

            try:
                user_answer = input_fn("补充 > ").strip()
            except (EOFError, KeyboardInterrupt):
                break  

            if not user_answer:
                break  

            if round_num == 0:
                current_question = (
                    f"{original_question}\n\n"
                    f"【用户补充说明】{user_answer}"
                )
            else:
                current_question = (
                    f"{current_question}\n"
                    f"【用户补充说明{round_num + 1}】{user_answer}"
                )

        if enable_archive and ctx is not None:
            _maybe_archive(ctx, memory, input_fn=input_fn, print_fn=print_fn)

        return answer
    finally:
        memory.close()


def _web_domain_label(url: str) -> str:
    from urllib.parse import urlparse
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return f"联网·{host}" if host else ""


def _derive_knowledge_areas(ctx: QATaskContext) -> list[str]:
    areas: list[str] = []
    er = ctx.evidence_report
    if er is None:
        return areas
    for ev in er.evidences:
        src = getattr(ev, "source", "") or ""
        is_web = (
            getattr(ev, "doc_type", "") == "web"
            or getattr(ev, "match_type", "") == "web"
        )
        if is_web:
            label = _web_domain_label(src)
        elif src.lower().endswith(".md"):
            label = os.path.basename(src)[:-3]
        else:
            label = ""
        if label and label not in areas:
            areas.append(label)
    return areas


def _format_dialogue_turn(question: str, answer: str) -> str:
    q = (question or "").strip()[:500]
    a = (answer or "").strip()[:500]
    return f"[user] {q}\n\n[assistant] {a}"


def main() -> int:
    try:
        get_api_key()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    print("[系统] 正在加载知识库...", file=sys.stderr)
    kb_registry = build_readonly_tool_registry()
    from knowledge.embeddings import _get_embedding_model
    _get_embedding_model()

    notes = ConversationNotes.create()

    print("qa_harness 已就绪。输入问题开始问答，输入 'exit' 退出。", file=sys.stderr)
    while True:
        try:
            question = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[系统] 再见。", file=sys.stderr)
            break
        if not question:
            continue
        if question.lower() in ("exit", "quit", "退出"):
            break

        answer = run_with_clarify(question, kb_registry, notes=notes)
        print(f"\n答 > {answer}\n")


if __name__ == "__main__":
    sys.exit(main())
