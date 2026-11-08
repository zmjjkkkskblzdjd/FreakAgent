from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from langgraph.types import Command

_ALLFUSION_ROOT = Path(__file__).resolve().parents[1]
if Path.cwd() != _ALLFUSION_ROOT:
    print(f"[提示] 当前 cwd={Path.cwd()}，建议先 cd 到 AllFusion 根再运行：")
    print(f"       cd {_ALLFUSION_ROOT} && python -m unified_agent.main")
if str(_ALLFUSION_ROOT) not in sys.path:
    sys.path.insert(0, str(_ALLFUSION_ROOT))

from unified_agent.core.contracts import UnifiedFinalReport  # noqa: E402
from unified_agent.core.state import SCHEMA_VERSION  # noqa: E402
from unified_agent.graph.builder import build_graph  # noqa: E402
from unified_agent.graph.checkpoint import (  # noqa: E402
    SchemaVersionError, get_checkpointer, load_state,
)

_USER_ID = os.getenv("UNIFIED_USER_ID", "cli_user")
_TENANT_ID = os.getenv("UNIFIED_TENANT_ID", "local")



def _ask_route(schema: dict) -> dict:
    print("\n—— 路由澄清 " + "—" * 40)
    print(f"您刚才的问法：{schema.get('question', '')}")
    rationale = schema.get("rationale") or ""
    if rationale:
        print(f"系统拿不准的原因：{rationale}")
    options = schema.get("options") or []
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o.get('label', o.get('action', ''))}")
    while True:
        sel = input("选编号（或 /quit）> ").strip()
        if sel == "/quit":
            raise KeyboardInterrupt
        if sel.isdigit() and 1 <= int(sel) <= len(options):
            action = options[int(sel) - 1]["action"]
            return {"type": "clarify", "kind": "route", "action": action}
        print(f"无效输入，请输入 1~{len(options)} 的编号。")


def _ask_content(schema: dict) -> dict:
    print("\n—— 内容澄清 " + "—" * 40)
    for k, v in schema.items():
        print(f"  {k}: {v}")
    while True:
        ans = input("你> ").strip()
        if ans == "/quit":
            raise KeyboardInterrupt
        if ans:
            return {"type": "clarify", "kind": "content", "answer": ans}
        print("（答复不能为空；/quit 退出）")


def _ask_lane(schema: dict) -> dict:
    lane = schema.get("lane") or "rag"
    name = schema.get("lane_name") or lane
    idx, total = schema.get("index", 1), schema.get("total", 2)
    print(f"\n—— 车道子问题确认 {idx}/{total}：" + "—" * 30)
    print(f"整体问题：{schema.get('original', '')}")
    if schema.get("missing"):
        print(f"未识别出「{name}」部分的内容——可以补充一条该车道的问题，或不查这一路。")
    else:
        print(f"「{name}」子问题：{schema.get('question', '')}")
    options = schema.get("options") or []
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o.get('label', o.get('action', ''))}")
    while True:
        sel = input("选编号（或 /quit）> ").strip()
        if sel == "/quit":
            raise KeyboardInterrupt
        if not (sel.isdigit() and 1 <= int(sel) <= len(options)):
            print(f"无效输入，请输入 1~{len(options)} 的编号。")
            continue
        action = options[int(sel) - 1]["action"]
        cmd = {"type": "lane_confirm", "kind": "lane", "lane": lane,
               "lane_action": action}
        if action in ("edit", "add"):
            text = input("新问题> ").strip()
            if not text:
                print("（输入为空，视为不查这一路）")
                cmd["lane_action"] = "reject"
            else:
                cmd["lane_question"] = text
        return cmd


def _ask_approve(schema: dict) -> dict:
    print("\n—— 写操作审批 " + "—" * 38)
    print(schema.get("sql", "") or "（无 SQL 文本）")
    est = schema.get("est_rows")
    if est is not None:
        print(f"预计影响行数：{est}")
    ans = input("批准执行？输入 y 批准，其他键拒绝 (y/N) ").strip().lower()
    return {"type": "approve_write", "granted": ans == "y"}


def _resolve_interrupt(payload: dict) -> dict:
    kind = payload.get("kind")
    schema = payload.get("schema") or {}
    if kind == "route":
        return _ask_route(schema)
    if kind == "content":
        return _ask_content(schema)
    if kind == "approve_write":
        return _ask_approve(schema)
    if kind == "lane_confirm":
        return _ask_lane(schema)
    print(f"\n—— 未识别 interrupt kind={kind}，原始 payload ——")
    print(payload)
    ans = input("你> ").strip()
    return {"type": "clarify", "kind": "content", "answer": ans}



def _print_route(out: dict) -> None:
    task_type = out.get("task_type") or "?"
    confidence = out.get("confidence")
    rationale = out.get("rationale") or ""
    degraded_from = out.get("degraded_from")
    rag_tier = out.get("rag_tier") or ""
    sql_tier = out.get("sql_tier") or ""
    parts = [f"task_type={task_type}"]
    if confidence is not None:
        parts.append(f"confidence={confidence:.2f}" if isinstance(confidence, float)
                     else f"confidence={confidence}")
    if degraded_from:
        parts.append(f"degraded_from={degraded_from}")
    tier_bits = []
    if rag_tier:
        tier_bits.append(f"rag_tier={rag_tier}")
    if sql_tier:
        tier_bits.append(f"sql_tier={sql_tier}")
    if tier_bits:
        parts.append(" ".join(tier_bits))
    print("\n—— 路由判定 " + "—" * 40)
    print("  " + " ｜ ".join(parts))
    if rationale:
        print(f"  rationale: {rationale}")



def _print_report(report: UnifiedFinalReport, validation_errors: list, task_status: str) -> None:
    print(f"\n—— 模式 {report.mode} ｜ 任务 {task_status} " + "—" * 30)
    print(report.answer_md)

    if report.sql:
        print("\n—— 实际执行 SQL（可复制到客户端复查）" + "—" * 10)
        print(report.sql.sql)
        if report.sql.injected:
            print(f"（policy 已注入 read_filters，hash={report.sql.injection_hash}）")

    if report.citations:
        print(f"\n—— 引用（{len(report.citations)} 条）" + "—" * 26)
        for c in report.citations:
            src = c.get("source", "")
            excerpt = (c.get("excerpt") or "")[:80].replace("\n", " ")
            print(f"  · [{src}] {excerpt}")

    if report.partial_failures:
        print(f"\n—— 部分失败车道（{len(report.partial_failures)}）" + "—" * 20)
        for p in report.partial_failures:
            reason = f" ｜ {p.reason}" if p.reason else ""
            print(f"  · {p.lane}: status={p.status} outcome={p.outcome} "
                  f"code={p.error_code}{reason}")

    if report.claims:
        print(f"\n—— claims 台账：{len(report.claims)} 条（数字均已挂 evidence_id）")

    if validation_errors:
        print(f"\n—— 校验错误（{len(validation_errors)}）" + "—" * 24)
        for e in validation_errors:
            print(f"  ! {e}")



def main() -> None:
    checkpointer = get_checkpointer()
    app = build_graph(checkpointer=checkpointer)
    thread = f"cli-{time.time_ns()}"
    session = f"sess-{time.time_ns()}"
    print(f"unified_agent 融合 REPL（schema v{SCHEMA_VERSION}，user={_USER_ID} tenant={_TENANT_ID}）")
    print(f"thread: {thread} ｜ /reset 新话题，/quit 退出")

    while True:
        try:
            question = input("\n问> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return
        if not question:
            continue
        if question == "/quit":
            print("再见。")
            return
        if question == "/reset":
            thread = f"cli-{time.time_ns()}"
            session = f"sess-{time.time_ns()}"
            print(f"[已重置] 新 thread {thread}")
            continue

        cfg = {"configurable": {"thread_id": thread}}
        started = time.perf_counter()
        try:
            load_state(checkpointer, thread)
            out = app.invoke({
                "session_id": session,
                "user_id": _USER_ID,
                "tenant_id": _TENANT_ID,
                "message": question,
                "question": question,
                "schema_version": SCHEMA_VERSION,
                "route_clarify_count": 0,
            }, cfg)

            while out.get("__interrupt__"):
                payload = out["__interrupt__"][0].value
                try:
                    cmd = _resolve_interrupt(payload)
                except KeyboardInterrupt:
                    print("\n（挂起已存档，重开后同 thread 可续）再见。")
                    return
                out = app.invoke(Command(resume=cmd), cfg)

        except SchemaVersionError as e:
            print(f"[版本门拦截] {e}")
            print("提示：本 thread 是旧版 schema。/reset 开新 thread，"
                  "或用 scripts/cleanup_threads.py 清理旧快照。")
            continue
        except Exception as exc:  # noqa: BLE001 —— REPL 不许崩：打印后继续接客
            print(f"[运行异常] {type(exc).__name__}: {exc}")
            continue

        _print_route(out)

        report_raw = out.get("final_report")
        if report_raw is None:
            print(f"[无 final_report] task_status={out.get('task_status')} ｜ "
                  f"state keys={sorted(out.keys())}")
            continue
        report = report_raw if isinstance(report_raw, UnifiedFinalReport) \
            else UnifiedFinalReport(**report_raw)
        _print_report(report, out.get("validation_errors") or [],
                      out.get("task_status", ""))
        print(f"\n（本轮墙钟 {time.perf_counter() - started:.1f}s，含 LLM 调用）")


if __name__ == "__main__":
    main()
