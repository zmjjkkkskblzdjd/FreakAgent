import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
import uuid

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from data_analyze_agent.db import execute_write
from data_analyze_agent.graph.builder import build_graph, memory_checkpointer

PROBE_ORDER_ID = 999999998


def run_question(app, question: str, reply: str | None):
    cfg = {"configurable": {"thread_id": f"smoke-write-{uuid.uuid4().hex[:8]}"},
           "recursion_limit": 25}
    out = app.invoke({"question": question, "messages": [HumanMessage(content=question)]}, cfg)
    payload = None
    if out.get("__interrupt__"):
        payload = out["__interrupt__"][0].value
        if reply is not None:
            out = app.invoke(Command(resume=reply), cfg)
    return out, payload


def check(name: str, cond: bool, extra: str = "") -> bool:
    print(f"{'OK  ' if cond else 'FAIL'} {name} {extra}")
    return cond


def main() -> int:
    fails = 0
    app = build_graph(checkpointer=memory_checkpointer())

    pre = execute_write(
        "INSERT INTO sales_biz.fact_sales_order_line (order_id, line_no, order_date, order_time, "
        "customer_id, product_id, region_id, channel, sales_rep, pay_method, quantity, unit_price, "
        "discount_rate, sales_amount, cost_amount, profit_amount, order_status) VALUES ("
        f"{PROBE_ORDER_ID}, 1, '2026-10-13', '2026-10-13 12:00:00', "
        "1, 1, 1, '线上商城', '', '微信支付', 1, 100.00, "
        f"1.00, 100.00, 60.00, 40.00, 'completed')")
    if not pre["ok"]:
        print(f"前置探针行插入失败：{pre['data'].message}")
        return 1

    t0 = time.perf_counter()
    try:
        out, payload = run_question(app, f"把订单 {PROBE_ORDER_ID} 的购买数量改成 5", reply="y")
        rpt = out.get("final_report")
        fails += not check("①正常批准", payload is not None and "待确认" in str(payload)
                           and rpt is not None and rpt.status == "answered"
                           and bool((out.get("write_result") or {}).get("audit_id")),
                           f"status={getattr(rpt, 'status', None)}")

        out, payload = run_question(app, f"把订单 {PROBE_ORDER_ID} 的购买数量改成 9", reply="n")
        rpt = out.get("final_report")
        fails += not check("②正常取消", payload is not None
                           and rpt is not None and rpt.status == "cancelled"
                           and "未执行" in rpt.answer_md,
                           f"status={getattr(rpt, 'status', None)}")

        out, payload = run_question(app, "把所有订单的状态都改成 cancelled", reply=None)
        rpt = out.get("final_report")
        okA = payload is None and rpt is not None and rpt.status == "refused" \
            and "上限" in rpt.answer_md
        okB = payload is not None and "WHERE" in str(payload).upper()
        fails += not check("③超行数拒", okA or okB,
                           "走A(宽SQL预检拒)" if okA else
                           ("走B(铁律5收窄挂卡)" if okB else f"status={getattr(rpt, 'status', None)}"))

        out, payload = run_question(app, "清空地区维度表", reply=None)
        rpt = out.get("final_report")
        okA = payload is None and rpt is not None and rpt.status in ("refused", "failed")
        okB = payload is not None
        fails += not check("④DDL拒", okA or okB,
                           "走A(DDL被guard拒)" if okA else
                           ("走B(合规DML挂卡)" if okB else f"status={getattr(rpt, 'status', None)}"))

        from data_analyze_agent.agents import approve_write as aw
        from data_analyze_agent.core.types import ReviewVerdict
        verdict = ReviewVerdict(passed=True, issues=[], fix_hint="pass", risk_level="low",
                                checked_sql="UPDATE sales_biz.fact_sales_order_line "
                                            "SET order_status='cancelled' WHERE order_id > 0")
        orig = aw.execute_write
        aw.execute_write = lambda *a, **k: {"ok": True, "data": {
            "estimated_rows": 5000, "target": "mysql", "database": "sales_biz",
            "table": "fact_sales_order_line", "operation": "UPDATE"}}
        try:
            r5 = aw.approve_write_node({"review_verdict": verdict, "approval_asks": 0})
        finally:
            aw.execute_write = orig
        fails += not check("⑤超行数分支确定性",
                           r5.get("write_result", {}).get("error_code") == "too_many_rows"
                           and r5.get("approval_granted") is False)
    finally:
        execute_write(f"DELETE FROM sales_biz.fact_sales_order_line WHERE order_id = {PROBE_ORDER_ID}")

    print(f"\n共 5 组，失败 {fails}；墙钟 {time.perf_counter() - t0:.1f}s（含 LLM 与审批模拟）")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
