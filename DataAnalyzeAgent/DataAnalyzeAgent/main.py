
import time

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from config import get_app_config
from data_analyze_agent.graph.builder import build_graph
from data_analyze_agent.memory.long_term import LongTermMemory
from data_analyze_agent.agents.recall_memory import extract_memory_candidates, build_memory_record
from data_analyze_agent.metrics.fewshot_collect import maybe_collect_fewshot

_PREVIEW_ROWS = 5  

_MEMORY_TYPE_LABELS = {
    "profile": "背景",
    "metric_semantic": "指标口径",
    "preference": "常用偏好",
}


def _print_report(report) -> None:
    print("\n—— 结论 " + "—" * 40)
    print(report.answer_md)
    print("—— 实际执行 SQL（可复制到 ClickHouse 客户端复查）" + "—" * 8)
    print(report.sql or "（无 SQL：闲聊/拒答/失败路径）")
    if report.preview_rows:
        shown = min(len(report.preview_rows), _PREVIEW_ROWS)
        print(f"—— 结果预览（前 {shown}/{report.row_count} 行）" + "—" * 14)
        for row in report.preview_rows[:_PREVIEW_ROWS]:
            print("  ", tuple(row))
    if report.chart_path:
        print("—— 图表（D12）" + "—" * 34)
        print("  ", report.chart_path)
    print(f"—— 终态 {report.status}｜修复 {report.repairs_used} 次｜"
          f"执行 {report.duration_ms}ms " + "—" * 10)




def main() -> None:
    app = build_graph()
    thread = f"cli-{time.time_ns()}"
    print("DataAnalyzeAgent 问数 REPL（/reset 新话题，/quit 退出）")
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
            print(f"[已重置] 新线程 {thread}")
            continue

        cfg = {
            "configurable": {"thread_id": thread},
            "recursion_limit": get_app_config().graph.recursion_limit,
        }
        started = time.perf_counter()
        try:
            out = app.invoke(
                {"question": question,
                 "thread_id": thread,
                 "messages": [HumanMessage(content=question)]},
                cfg,
            )
            while out.get("__interrupt__"):
                print("\n—— 待确认 " + "—" * 40)
                print(out["__interrupt__"][0].value)
                reply = ""
                while not reply:
                    try:
                        reply = input("你> ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print("\n再见。（挂起已存档，重开后可继续本线程）")
                        return
                    if reply == "/quit":
                        print("再见。（挂起已存档，重开后可继续本线程）")
                        return
                    if not reply:
                        print("（答复不能为空；/quit 退出）")
                out = app.invoke(Command(resume=reply), cfg)
        except Exception as exc:  # noqa: BLE001 —— REPL 不许崩：打印后继续接客
            print(f"[运行异常] {type(exc).__name__}: {exc}")
            continue

        report = out.get("final_report")
        if report is None:
            print(f"[无交活单] 流程未走完 finalize（罕见），state keys: {sorted(out.keys())}")
            continue
        _print_report(report)
        print(f"（本轮墙钟 {time.perf_counter() - started:.1f}s，含 LLM 调用）")

        if report.status == "answered":
            _maybe_save_memory(question, report, thread)
            if maybe_collect_fewshot(question, report):
                print("[已提名为例题候选，审核发布后生效]")


def _maybe_save_memory(question: str, report, thread: str) -> None:
    candidates = extract_memory_candidates(question, report)
    if not candidates:
        return
    print("\n—— 可保存的长期记忆 " + "—" * 30)
    print("从本轮对话识别到以下可复用信息；存入后，以后问数会自动参考。")
    for i, cand in enumerate(candidates, 1):
        label = _MEMORY_TYPE_LABELS.get(cand["memory_type"], cand["memory_type"])
        print(f"  {i}. 【{label}】{cand['content']}")
    try:
        reply = input("是否存入记忆库？输入 y 保存，其他键跳过 (y/N) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if reply != "y":
        return
    lt = LongTermMemory()   
    saved = 0
    try:
        for cand in candidates:
            record = build_memory_record(
                cand["content"], cand["memory_type"], cand["importance"],
                thread_id=thread,
            )
            ok = lt.add(record)
            if ok["ok"]:
                saved += 1
            else:
                print(f"[写入失败] {cand['content']}：{ok['error']}")
        if saved:
            print(f"[已保存 {saved}/{len(candidates)} 条长期记忆]")
    finally:
        lt.close()


if __name__ == "__main__":
    main()
