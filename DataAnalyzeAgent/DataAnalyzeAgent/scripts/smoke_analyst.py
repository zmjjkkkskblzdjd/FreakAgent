import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("GLM_API_KEY", "FAKE")

from decimal import Decimal
from datetime import datetime
from unittest.mock import patch

from data_analyze_agent.agents.analyst import (
    analyze_result, analyze_result_node, after_analysis,
    _build_analyst_user_content, _render_cell,
    _is_empty, _check_referenced_cells, _to_data_analysis,
    REFERENCED_CELL_RE,
)
from data_analyze_agent.core.types import QueryResult, DataAnalysis
from data_analyze_agent.graph.state import DataAnalyzeState



def _fake_llm_ok(data):
    return {"ok": True, "data": data}


def _fake_llm_fail(msg):
    return {"ok": False, "error": msg}



def test_normal_single_value():
    qr = QueryResult(
        columns=["GMV"],
        rows=[(Decimal("802100.00"),)],
        row_count=1,
        executed_sql="SELECT SUM(amount) FROM payments WHERE status='成功'",
    )
    good = {
        "answer_md": "2026年9月GMV为802100元（支付口径）",
        "referenced_cells": ["row[0].GMV"],
        "confidence": "high",
        "is_empty": False,
        "empty_is_suspicious": False,
        "escalate": False,
        "escalate_reason": "",
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(good)):
        result = analyze_result("2026年9月GMV", qr)
        assert isinstance(result, DataAnalysis)
        assert result.is_empty is False  
        assert result.confidence == "high"
        print("✓ 场景1 正常单值")



def test_multi_row_mixed_types():
    qr = QueryResult(
        columns=["渠道", "GMV", "成交时间"],
        rows=[
            ("微信", Decimal("120000.00"), datetime(2026, 9, 1, 12, 0, 0)),
            ("支付宝", Decimal("80000.50"), datetime(2026, 9, 2, 10, 30, 0)),
        ],
        row_count=2,
        executed_sql="SELECT channel, SUM(amount), MAX(paid_at) FROM payments GROUP BY channel",
    )
    good = {
        "answer_md": "微信GMV最高120000元，支付宝80000.5元",
        "referenced_cells": ["row[0].GMV", "row[1].GMV"],
        "confidence": "high",
        "is_empty": False,
        "empty_is_suspicious": False,
        "escalate": False,
        "escalate_reason": "",
        "chart_suggestion": "pie",
        "refused": False,
        "refuse_reason": "",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(good)):
        result = analyze_result("各渠道GMV", qr)
        assert result.chart_suggestion == "pie"
        print("✓ 场景2 多行混合类型")



def test_empty_suspicious_escalate():
    qr = QueryResult(
        columns=["渠道", "GMV"],
        rows=[],
        row_count=0,
        executed_sql="SELECT channel, SUM(amount) FROM payments WHERE created_at > '2026-10-01' GROUP BY channel",
    )
    good = {
        "answer_md": "查询结果为空，时间范围可能过严",
        "referenced_cells": [],
        "confidence": "low",
        "is_empty": True,
        "empty_is_suspicious": True,
        "escalate": True,
        "escalate_reason": "10月数据可能尚未入库，建议扩大时间范围",
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(good)):
        result = analyze_result("2026年10月GMV", qr)
        assert result.is_empty is True
        assert result.escalate is True
        assert result.escalate_reason != ""
        print("✓ 场景3 空结果可疑升级")



def test_lie_about_is_empty():
    qr = QueryResult(
        columns=["a"], rows=[(1,)], row_count=1, executed_sql="SELECT 1",
    )
    bad = {
        "answer_md": "结果为空",
        "referenced_cells": ["row[0].a"],
        "confidence": "high",
        "is_empty": True,  
        "empty_is_suspicious": False,
        "escalate": False,
        "escalate_reason": "",
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(bad)):
        result = analyze_result("问", qr)
        assert result.is_empty is False  
        print("✓ 场景4 谎报is_empty被纠正")



def test_nonempty_suspicious_blocked():
    qr = QueryResult(
        columns=["a"], rows=[(1,)], row_count=1, executed_sql="SELECT 1",
    )
    bad = {
        "answer_md": "结果可疑",
        "referenced_cells": ["row[0].a"],
        "confidence": "high",
        "is_empty": False,
        "empty_is_suspicious": True,  
        "escalate": False,
        "escalate_reason": "",
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(bad)):
        try:
            analyze_result("问", qr)
            assert False, "应该抛 RuntimeError"
        except RuntimeError as e:
            assert "非空却声称可疑" in str(e)
            print("✓ 场景5 非空可疑被拦截")



def test_empty_nonsuspicious_escalate_blocked():
    qr = QueryResult(
        columns=["a"], rows=[], row_count=0, executed_sql="SELECT 1 WHERE 1=0",
    )
    bad = {
        "answer_md": "空但不可疑却想升级",
        "referenced_cells": [],
        "confidence": "high",
        "is_empty": True,
        "empty_is_suspicious": False,
        "escalate": True,
        "escalate_reason": "想升级",
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(bad)):
        try:
            analyze_result("问", qr)
            assert False, "应该抛 RuntimeError"
        except RuntimeError as e:
            assert "空但不可疑却升级" in str(e)
            print("✓ 场景6 空不可疑升级被拦截")



def test_escalate_no_reason_blocked():
    qr = QueryResult(
        columns=["a"], rows=[], row_count=0, executed_sql="SELECT 1 WHERE 1=0",
    )
    bad = {
        "answer_md": "空",
        "referenced_cells": [],
        "confidence": "high",
        "is_empty": True,
        "empty_is_suspicious": True,
        "escalate": True,
        "escalate_reason": "",  
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(bad)):
        try:
            analyze_result("问", qr)
            assert False, "应该抛 RuntimeError"
        except RuntimeError as e:
            assert "必须有 escalate_reason" in str(e)
            print("✓ 场景7 无reason升级被拦截")



def test_escalate_refused_mutex_blocked():
    qr = QueryResult(
        columns=["a"], rows=[], row_count=0, executed_sql="SELECT 1 WHERE 1=0",
    )
    bad = {
        "answer_md": "空",
        "referenced_cells": [],
        "confidence": "high",
        "is_empty": True,
        "empty_is_suspicious": True,
        "escalate": True,
        "escalate_reason": "想升级",
        "chart_suggestion": "none",
        "refused": True,
        "refuse_reason": "同时拒答",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(bad)):
        try:
            analyze_result("问", qr)
            assert False, "应该抛 RuntimeError"
        except RuntimeError as e:
            assert "互斥" in str(e)
            print("✓ 场景8 互斥被拦截")



def test_bad_ref_format_blocked():
    qr = QueryResult(
        columns=["a"], rows=[(1,)], row_count=1, executed_sql="SELECT 1",
    )
    bad = {
        "answer_md": "有数",
        "referenced_cells": ["row.a"],  
        "confidence": "high",
        "is_empty": False,
        "empty_is_suspicious": False,
        "escalate": False,
        "escalate_reason": "",
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(bad)):
        try:
            analyze_result("问", qr)
            assert False, "应该抛 RuntimeError"
        except RuntimeError as e:
            assert "格式非法" in str(e)
            print("✓ 场景9 出处格式非法被拦截")



def test_ref_out_of_bounds_blocked():
    qr = QueryResult(
        columns=["a"], rows=[(1,)], row_count=1, executed_sql="SELECT 1",
    )
    bad = {
        "answer_md": "有数",
        "referenced_cells": ["row[99].a"],  
        "confidence": "high",
        "is_empty": False,
        "empty_is_suspicious": False,
        "escalate": False,
        "escalate_reason": "",
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(bad)):
        try:
            analyze_result("问", qr)
            assert False, "应该抛 RuntimeError"
        except RuntimeError as e:
            assert "越界" in str(e)
            print("✓ 场景10 出处越界被拦截")



def test_ref_unknown_col_blocked():
    qr = QueryResult(
        columns=["a"], rows=[(1,)], row_count=1, executed_sql="SELECT 1",
    )
    bad = {
        "answer_md": "有数",
        "referenced_cells": ["row[0].b"],  
        "confidence": "high",
        "is_empty": False,
        "empty_is_suspicious": False,
        "escalate": False,
        "escalate_reason": "",
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(bad)):
        try:
            analyze_result("问", qr)
            assert False, "应该抛 RuntimeError"
        except RuntimeError as e:
            assert "不存在的列" in str(e)
            print("✓ 场景11 未知列被拦截")



def test_nonempty_no_refs_blocked():
    qr = QueryResult(
        columns=["a"], rows=[(1,)], row_count=1, executed_sql="SELECT 1",
    )
    bad = {
        "answer_md": "有数",
        "referenced_cells": [],  
        "confidence": "high",
        "is_empty": False,
        "empty_is_suspicious": False,
        "escalate": False,
        "escalate_reason": "",
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(bad)):
        try:
            analyze_result("问", qr)
            assert False, "应该抛 RuntimeError"
        except RuntimeError as e:
            assert "至少登记 1 条" in str(e)
            print("✓ 场景12 非空无出处被拦截")



def test_refused_exempt_from_refs():
    qr = QueryResult(
        columns=["a"], rows=[(1,)], row_count=1, executed_sql="SELECT 1",
    )
    good = {
        "answer_md": "无法回答：数据不足",
        "referenced_cells": [],  
        "confidence": "low",
        "is_empty": False,
        "empty_is_suspicious": False,
        "escalate": False,
        "escalate_reason": "",
        "chart_suggestion": "none",
        "refused": True,
        "refuse_reason": "现有数据无法支撑该结论",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(good)):
        result = analyze_result("问", qr)
        assert result.refused is True
        print("✓ 场景13 refused豁免出处")



def test_retry_success():
    qr = QueryResult(
        columns=["a"], rows=[(1,)], row_count=1, executed_sql="SELECT 1",
    )
    bad = {
        "answer_md": "有数",
        "referenced_cells": ["row[99].a"],  
        "confidence": "high",
        "is_empty": False,
        "empty_is_suspicious": False,
        "escalate": False,
        "escalate_reason": "",
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    good = {
        "answer_md": "有数",
        "referenced_cells": ["row[0].a"],
        "confidence": "high",
        "is_empty": False,
        "empty_is_suspicious": False,
        "escalate": False,
        "escalate_reason": "",
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    call_count = [0]
    def fake_ask(*args, **kwargs):
        call_count[0] += 1
        return _fake_llm_ok(bad if call_count[0] == 1 else good)

    with patch("data_analyze_agent.agents.analyst.ask_llm_json", side_effect=fake_ask):
        result = analyze_result("问", qr)
        assert call_count[0] == 2
        assert result.referenced_cells == ["row[0].a"]
        print("✓ 场景14 补调成功")



def test_retry_still_fails():
    qr = QueryResult(
        columns=["a"], rows=[(1,)], row_count=1, executed_sql="SELECT 1",
    )
    bad = {
        "answer_md": "有数",
        "referenced_cells": ["row[99].a"],
        "confidence": "high",
        "is_empty": False,
        "empty_is_suspicious": False,
        "escalate": False,
        "escalate_reason": "",
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(bad)):
        try:
            analyze_result("问", qr)
            assert False, "应该抛 RuntimeError"
        except RuntimeError as e:
            assert "越界" in str(e)
            print("✓ 场景15 补调仍失败抛错")



def test_node():
    qr = QueryResult(
        columns=["a"], rows=[(1,)], row_count=1, executed_sql="SELECT 1",
    )
    state: DataAnalyzeState = {
        "clarified_question": "问",
        "query_result": qr,
    }
    good = {
        "answer_md": "有数",
        "referenced_cells": ["row[0].a"],
        "confidence": "high",
        "is_empty": False,
        "empty_is_suspicious": False,
        "escalate": False,
        "escalate_reason": "",
        "chart_suggestion": "none",
        "refused": False,
        "refuse_reason": "",
    }
    with patch("data_analyze_agent.agents.analyst.ask_llm_json", return_value=_fake_llm_ok(good)):
        patch_result = analyze_result_node(state)
        assert "data_analysis" in patch_result
        assert isinstance(patch_result["data_analysis"], DataAnalysis)
        print("✓ 场景16 节点薄适配")



def test_after_analysis():
    s1: DataAnalyzeState = {
        "data_analysis": DataAnalysis(
            answer_md="", referenced_cells=[], confidence="high",
            is_empty=False, empty_is_suspicious=False,
            escalate=False, escalate_reason="",
            chart_suggestion="none", refused=False, refuse_reason="",
        ),
        "tier": "lite",
        "escalation_count": 0,
    }
    assert after_analysis(s1) == "finalize"

    s2: DataAnalyzeState = {
        "data_analysis": DataAnalysis(
            answer_md="", referenced_cells=[], confidence="high",
            is_empty=True, empty_is_suspicious=True,
            escalate=True, escalate_reason="可疑",
            chart_suggestion="none", refused=False, refuse_reason="",
        ),
        "tier": "deep",
        "escalation_count": 0,
    }
    assert after_analysis(s2) == "finalize"

    s3: DataAnalyzeState = {
        "data_analysis": DataAnalysis(
            answer_md="", referenced_cells=[], confidence="high",
            is_empty=True, empty_is_suspicious=True,
            escalate=True, escalate_reason="可疑",
            chart_suggestion="none", refused=False, refuse_reason="",
        ),
        "tier": "lite",
        "escalation_count": 1,  
    }
    assert after_analysis(s3) == "finalize"

    s4: DataAnalyzeState = {
        "data_analysis": DataAnalysis(
            answer_md="", referenced_cells=[], confidence="high",
            is_empty=True, empty_is_suspicious=True,
            escalate=True, escalate_reason="可疑",
            chart_suggestion="none", refused=False, refuse_reason="",
        ),
        "tier": "lite",
        "escalation_count": 0,
    }
    assert after_analysis(s4) == "plan"

    print("✓ 场景17 条件边三分叉")



def test_number_backing_retry_recovery():
    qr = QueryResult(
        columns=["sales"],
        rows=[(Decimal("12239585.78"),)],
        row_count=1,
        executed_sql="SELECT SUM(total_amount) AS sales FROM orders WHERE ...",
    )
    bad = {
        "answer_md": "销售额为223.96元",  
        "referenced_cells": ["row[0].sales"],
        "confidence": "high",
        "is_empty": False, "empty_is_suspicious": False,
        "escalate": False, "escalate_reason": "",
        "chart_suggestion": "none", "refused": False, "refuse_reason": "",
    }
    good = dict(bad, answer_md="2025年上半年销售额为12239585.78元")
    calls = [0]
    def fake_ask(*args, **kwargs):
        calls[0] += 1
        return _fake_llm_ok(bad if calls[0] == 1 else good)

    with patch("data_analyze_agent.agents.analyst.ask_llm_json", side_effect=fake_ask):
        result = analyze_result("2025年上半年销售额", qr)
        assert calls[0] == 2
        assert "12239585.78" in result.answer_md
        print("✓ 场景18 数字软闸回喂后长数字误抄被救回")



def test_number_backing_retry_still_bad_returns():
    qr = QueryResult(
        columns=["sales"], rows=[(Decimal("12239585.78"),)], row_count=1,
        executed_sql="SELECT SUM(total_amount) AS sales FROM orders",
    )
    bad = {
        "answer_md": "销售额为223.96元",
        "referenced_cells": ["row[0].sales"],
        "confidence": "high",
        "is_empty": False, "empty_is_suspicious": False,
        "escalate": False, "escalate_reason": "",
        "chart_suggestion": "none", "refused": False, "refuse_reason": "",
    }
    calls = [0]
    def fake_ask(*args, **kwargs):
        calls[0] += 1
        return _fake_llm_ok(bad)

    with patch("data_analyze_agent.agents.analyst.ask_llm_json", side_effect=fake_ask):
        result = analyze_result("销售额", qr)
        assert calls[0] == 2
        assert isinstance(result, DataAnalysis) and "223.96" in result.answer_md
        print("✓ 场景19 软闸补调仍失败返回产物（不炸图，交 finalize 降级）")



def test_point_lookup_input_id_backed():
    qr = QueryResult(
        columns=["total_amount"], rows=[(Decimal("4395.58"),)], row_count=1,
        executed_sql="SELECT total_amount FROM orders WHERE order_id = 100 LIMIT 1",
    )
    good = {
        "answer_md": "订单号为100的订单金额是4395.58元",
        "referenced_cells": ["row[0].total_amount"],
        "confidence": "high",
        "is_empty": False, "empty_is_suspicious": False,
        "escalate": False, "escalate_reason": "",
        "chart_suggestion": "none", "refused": False, "refuse_reason": "",
    }
    calls = [0]
    def fake_ask(*args, **kwargs):
        calls[0] += 1
        return _fake_llm_ok(good)

    with patch("data_analyze_agent.agents.analyst.ask_llm_json", side_effect=fake_ask):
        result = analyze_result("订单号为100的订单金额", qr)
        assert calls[0] == 1  
        assert "4395.58" in result.answer_md
        print("✓ 场景20 点查复述订单号不触发软闸（SQL 背书）")



if __name__ == "__main__":
    test_normal_single_value()
    test_multi_row_mixed_types()
    test_empty_suspicious_escalate()
    test_lie_about_is_empty()
    test_nonempty_suspicious_blocked()
    test_empty_nonsuspicious_escalate_blocked()
    test_escalate_no_reason_blocked()
    test_escalate_refused_mutex_blocked()
    test_bad_ref_format_blocked()
    test_ref_out_of_bounds_blocked()
    test_ref_unknown_col_blocked()
    test_nonempty_no_refs_blocked()
    test_refused_exempt_from_refs()
    test_retry_success()
    test_retry_still_fails()
    test_number_backing_retry_recovery()
    test_number_backing_retry_still_bad_returns()
    test_point_lookup_input_id_backed()
    test_node()
    test_after_analysis()
    print("\n✅ 全部 20 组场景通过")
