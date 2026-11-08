import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("GLM_API_KEY", "FAKE")

from decimal import Decimal
from langchain_core.messages import AIMessage

from data_analyze_agent.agents.finalize import (
    determine_status, build_final_report, finalize_node,
    _extract_unguarded_numbers,
)
from data_analyze_agent.core.types import (
    RouteDecision, SQLAttempt, ReviewIssue, ReviewVerdict,
    QueryResult, DBError, DataAnalysis,
)

SQL = "SELECT channel, SUM(amount) AS GMV FROM payments WHERE status='成功' GROUP BY channel"

def qr(rows, sql=SQL, row_count=None):
    return QueryResult(
        columns=["渠道", "GMV"],
        rows=rows,
        row_count=row_count if row_count is not None else len(rows),
        executed_sql=sql,
        duration_ms=42,
    )

def attempt(sql=SQL):
    return SQLAttempt(sql=sql, version=1, stage="draft",
                      used_tables=["payments"], rationale="按渠道聚合")

def analysis(answer, refused=False, reason="", confidence="high"):
    return DataAnalysis(
        answer_md=answer,
        referenced_cells=["row[0].GMV"] if not refused else [],
        confidence=confidence,
        is_empty=False, empty_is_suspicious=False,
        escalate=False, escalate_reason="",
        chart_suggestion="none", refused=refused, refuse_reason=reason,
    )

nums = _extract_unguarded_numbers("2026年9月GMV为120000元，占比60%，共3条")
assert "120000" in nums and "3" in nums
assert "2026" not in nums and "9" not in nums and "60" not in nums
print("✓ 数字豁免：年份/月/% 放行，裸金额/行数保留")

state = {
    "route_decision": RouteDecision(route="ask", tier="lite", reason=""),
    "current_attempt": attempt(),
    "query_result": qr([("微信", Decimal("120000.00")), ("支付宝", Decimal("80000.00"))]),
    "data_analysis": analysis("微信GMV为120000元，支付宝80000元，共2条"),
    "tier": "lite",
}
assert determine_status(state) == "answered"
rep = build_final_report(state)
assert rep.status == "answered" and rep.sql == SQL and rep.confidence == "high"
assert rep.row_count == 2 and len(rep.preview_rows) == 2
assert rep.tables_used == ["payments"] and rep.duration_ms == 42
out = finalize_node(state)["final_report"]
assert out.status == "answered"
print("✓ 场景1 answered 正常")

state2 = {
    "route_decision": RouteDecision(route="ask", tier="lite", reason=""),
    "current_attempt": attempt(),
    "query_result": qr([(i, Decimal("1.00")) for i in range(200)], row_count=200),
    "data_analysis": DataAnalysis(
        answer_md="共200条", referenced_cells=["row[0].GMV"], confidence="medium",
        is_empty=False, empty_is_suspicious=False, chart_suggestion="none"),
}
rep2 = build_final_report(state2)
assert len(rep2.preview_rows) == 50
assert finalize_node(state2)["final_report"].status == "answered"
print("✓ 场景2 预览截 50 行 + row_count 背书")

state3 = {
    "route_decision": RouteDecision(route="ask", tier="lite", reason=""),
    "current_attempt": attempt(),
    "query_result": qr([("微信", Decimal("120000.00"))]),
    "data_analysis": analysis("微信GMV为999999元（编造）"),
}
rep3 = finalize_node(state3)["final_report"]
assert rep3.status == "failed", rep3.status
assert "999999" in rep3.answer_md and rep3.sql == SQL
print("✓ 场景3 脑补数字降级 failed（带原因+SQL）")

state4 = {
    "route_decision": RouteDecision(route="ask", tier="lite", reason=""),
    "current_attempt": attempt(),
    "query_result": qr([("微信", Decimal("120000.00"))], sql=""),
    "data_analysis": analysis("微信GMV为120000元"),
}
rep4 = finalize_node(state4)["final_report"]
assert rep4.status == "failed" and "SQL" in rep4.answer_md
print("✓ 场景4 缺执行SQL降级 failed")

state5 = {
    "route_decision": RouteDecision(route="chitchat", tier="lite", reason=""),
    "messages": [AIMessage(content="你好呀，我只能帮你查数哦")],
}
assert determine_status(state5) == "chitchat"
rep5 = build_final_report(state5)
assert rep5.status == "chitchat" and rep5.sql == ""
assert rep5.answer_md == "你好呀，我只能帮你查数哦"
assert rep5.columns == [] and rep5.confidence == "low"
assert finalize_node(state5)["final_report"].status == "chitchat"
print("✓ 场景5 chitchat")

state6 = {
    "route_decision": RouteDecision(route="reject", tier="lite", reason=""),
    "messages": [AIMessage(content="抱歉，这个问题与当前数据库无关")],
}
assert determine_status(state6) == "refused"
rep6 = build_final_report(state6)
assert rep6.status == "refused" and "无关" in rep6.answer_md and rep6.sql == ""
print("✓ 场景6 reject 拒答")

state7 = {
    "route_decision": RouteDecision(route="ask", tier="lite", reason=""),
    "schema_docs": [],
    "messages": [AIMessage(content="没有找到相关表")],
}
assert determine_status(state7) == "refused"
state7b = {"route_decision": RouteDecision(route="ask", tier="lite", reason="")}
assert determine_status(state7b) == "failed"
print("✓ 场景7 零命中拒答 / 键缺失不误判")

state8 = {
    "route_decision": RouteDecision(route="ask", tier="lite", reason=""),
    "current_attempt": attempt(),
    "query_result": qr([]),
    "data_analysis": analysis("", refused=True, reason="现有表无库存字段"),
    "tier": "lite",
}
rep8 = build_final_report(state8)
assert rep8.status == "refused"
assert rep8.answer_md == "现有表无库存字段" and rep8.sql == SQL
print("✓ 场景8 analyst refused 带已尝试 SQL")

state9 = {
    "route_decision": RouteDecision(route="ask", tier="lite", reason=""),
    "current_attempt": attempt(),
    "db_error": DBError(kind="unknown_column", message="1054 Unknown column 'statu'", errno=1054),
    "repair_count": 2,
}
assert determine_status(state9) == "failed"
rep9 = finalize_node(state9)["final_report"]
assert rep9.status == "failed"
assert "1054" in rep9.answer_md and rep9.sql == SQL and rep9.repairs_used == 2
print("✓ 场景9 执行失败超限 failed（含 MySQL 原文）")

state10 = {
    "route_decision": RouteDecision(route="ask", tier="lite", reason=""),
    "current_attempt": attempt(),
    "review_verdict": ReviewVerdict(
        passed=False,
        issues=[ReviewIssue(code="unknown_column", detail="列 statu 不存在")],
        fix_hint="escalate_plan", risk_level="medium", checked_sql=SQL),
    "repair_count": 2,
}
rep10 = finalize_node(state10)["final_report"]
assert rep10.status == "failed" and "列 statu 不存在" in rep10.answer_md
print("✓ 场景10 review 打回超限 failed")

state11 = {
    "route_decision": RouteDecision(route="ask", tier="deep", reason=""),
    "current_attempt": attempt(),
    "query_result": qr([("微信", Decimal("120000.00"))]),
    "data_analysis": analysis("微信GMV为120000元"),
    "tier": "deep", "escalation_count": 1,
}
rep11 = build_final_report(state11)
assert rep11.tier_used == "deep" and rep11.escalated is True
print("✓ 场景11 升级元信息透传")

d = build_final_report(state).to_dict()
assert d["preview_rows"][0][1] == "120000.00"
assert isinstance(d["preview_rows"], list) and isinstance(d["preview_rows"][0], list)
print("✓ 场景12 to_dict JSON-safe")

SQL13 = "SELECT total_amount FROM orders WHERE order_id = 100 LIMIT 1"
state13 = {
    "route_decision": RouteDecision(route="ask", tier="lite", reason=""),
    "current_attempt": attempt(SQL13),
    "query_result": qr([(Decimal("4395.58"),)], sql=SQL13),
    "data_analysis": analysis("订单号为100的订单金额是4395.58元"),
}
rep13 = finalize_node(state13)["final_report"]
assert rep13.status == "answered", rep13.answer_md
print("✓ 场景13 点查复述订单号被 SQL 字面量背书（L10 回归）")

SQL14 = "SELECT SUM(amount) AS s FROM refunds WHERE 1=0"
state14 = {
    "route_decision": RouteDecision(route="ask", tier="lite", reason=""),
    "current_attempt": attempt(SQL14),
    "query_result": qr([], sql=SQL14),
    "data_analysis": DataAnalysis(
        answer_md="退款总金额为 0.00 元", referenced_cells=[], confidence="high",
        is_empty=True, empty_is_suspicious=False, chart_suggestion="none"),
}
rep14 = finalize_node(state14)["final_report"]
assert rep14.status == "answered", rep14.answer_md
assert rep14.row_count == 0
print("✓ 场景14 空结果如实报 0.00 不被误伤（数值归一）")

state15 = {
    "route_decision": RouteDecision(
        route="clarify", tier="lite", reason="缺时间范围",
        clarifying_question="请问你想统计哪个时间范围的支付笔数？",
        ambiguous_slots=["time_range"]),
    "messages": [AIMessage(content="请问你想统计哪个时间范围的支付笔数？")],
}
assert determine_status(state15) == "clarified"
rep15 = finalize_node(state15)["final_report"]
assert rep15.status == "clarified"
assert rep15.answer_md == "请问你想统计哪个时间范围的支付笔数？"
assert rep15.sql == "" and rep15.row_count == 0
print("✓ 场景15 clarify 收口为 clarified（无 SQL 但有完整报告）")

rep16 = finalize_node(state3)["final_report"]
assert rep16.status == "failed"
assert "已达上限（0 次）" not in rep16.answer_md
assert "收口校验" in rep16.answer_md and "999999" in rep16.answer_md
assert "已达上限（2 次）" in finalize_node(state9)["final_report"].answer_md
print("✓ 场景16 降级文案区分收口校验失败与修复超限")

print("\n✅ finalize 全部 16 组场景通过")
