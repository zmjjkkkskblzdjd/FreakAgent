from unified_agent.agents.finalize import validate_and_set
from unified_agent.core.contracts import UnifiedFinalReport
from unified_agent.core.output_guard import sanitize_answer


def test_api_key_is_masked():
    out, hits = sanitize_answer("临时密钥 sk-abcdefgh12345678 已写入配置")
    assert "sk-abcdefgh12345678" not in out and "***" in out
    assert any("api_key" in h for h in hits)


def test_connection_string_credential_is_masked():
    out, hits = sanitize_answer("连接串 mysql://root:pwd@db:3306/sales")
    assert "root:pwd" not in out
    assert "mysql://***@" in out
    assert any("url_credential" in h for h in hits)


def test_phone_is_masked():
    out, hits = sanitize_answer("联系电话 13812345678")
    assert "13812345678" not in out
    assert any("phone" in h for h in hits)


def test_regular_metric_number_is_not_masked():
    text = "本月销售额 12345678 元，同比 3.2%"
    out, hits = sanitize_answer(text)
    assert out == text
    assert hits == []


def test_finalize_redacts_answer_and_reports_hits():
    report = UnifiedFinalReport(
        answer_md="回答正文：sk-abcdefgh12345678 与 mysql://root:pwd@db/x",
        mode="chitchat")
    out = validate_and_set({"final_report": report.model_dump()})
    answer = out["final_report"]["answer_md"]
    assert "sk-abcdefgh12345678" not in answer
    assert "root:pwd" not in answer
    assert out["redactions"]
    assert out["final_report"]["redactions"] == out["redactions"]
