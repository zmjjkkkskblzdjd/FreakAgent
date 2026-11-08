from data_analyze_agent.agents import approve_write as aw
from data_analyze_agent.core.types import DBError, ReviewVerdict


def _verdict() -> ReviewVerdict:
    return ReviewVerdict(passed=True, issues=[], fix_hint="pass", risk_level="low",
                         checked_sql="UPDATE sales_dw.fact_sales_order_line "
                                     "SET order_status='cancelled' WHERE 1=1")


def test_precheck_too_many_rows_refused(monkeypatch):
    monkeypatch.setattr(aw, "execute_write", lambda *a, **k: {
        "ok": False, "data": DBError(
            kind="other",
            message="[too_many_rows] 预估影响 342290 行，超过单次上限 1000，"
                    "已拒绝执行；建议缩小 WHERE 范围分批操作")})
    out = aw.approve_write_node({"review_verdict": _verdict(), "approval_asks": 0})
    assert out["approval_granted"] is False
    wr = out["write_result"]
    assert wr["error_code"] == "too_many_rows"
    assert "342290" in wr["error_message"]


def test_precheck_guard_rejected_stays_precheck_failed(monkeypatch):
    monkeypatch.setattr(aw, "execute_write", lambda *a, **k: {
        "ok": False, "data": DBError(
            kind="other", message="[guard_rejected] 网关安检拒绝：表不在写白名单")})
    out = aw.approve_write_node({"review_verdict": _verdict(), "approval_asks": 0})
    assert out["approval_granted"] is False
    assert out["write_result"]["error_code"] == "precheck_failed"


def test_precheck_local_over_limit(monkeypatch):
    monkeypatch.setattr(aw, "execute_write", lambda *a, **k: {
        "ok": True, "data": {"estimated_rows": 5000, "target": "clickhouse",
                             "database": "sales_dw", "table": "fact_sales_order_line",
                             "operation": "UPDATE"}})
    out = aw.approve_write_node({"review_verdict": _verdict(), "approval_asks": 0})
    assert out["approval_granted"] is False
    assert out["write_result"]["error_code"] == "too_many_rows"
