import unified_agent.agents.fact_check as fc
from unified_agent.agents.fact_check import (
    attach_fact_check, classify_sentence, evidence_pool, split_sentences,
)
from unified_agent.core.contracts import FactCheck, UnifiedFinalReport, validate
from unified_agent.core.state import RAGContext, SQLContext

EVIDENCE = "数据显示本月退货率为3.2%，环比下降0.5个百分点，整体退货情况明显改善。"


def _state():
    return {
        "rag_ctx": RAGContext(provenance=[{"chunk_id": "c3", "text": EVIDENCE}]),
        "sql_ctx": SQLContext(),
    }


def _no_llm(*_a, **_k):
    return {}


def test_split_sentences_skips_code_and_tables():
    md = "## 结论\n本月销售额为 100 万元。环比增长 10%！\n```python\nprint(1)\n```\n| a | b |"
    assert split_sentences(md) == ["本月销售额为 100 万元。", "环比增长 10%！"]


def test_split_sentences_skips_citation_footnote_lines():
    md = "结论如下。\n[6] sub_r006843\n[4] sub_r004143\n金额为 55 万元。"
    assert split_sentences(md) == ["结论如下。", "金额为 55 万元。"]


def test_classify_sentence():
    assert classify_sentence("谢谢你的提问") == "social"
    assert classify_sentence("不建议在高峰期操作") == "opinion"
    assert classify_sentence("首先打开配置页面") == "procedural"
    assert classify_sentence("本月退货率为 3.2%") == "factual"
    assert classify_sentence("预计本月退货率为 3.2%") == "factual"   


def test_evidence_pool_includes_rag_and_sql_rows():
    state = _state()
    state["sql_ctx"] = SQLContext(query_result={"rows": [["华东", 100]]})
    pool = evidence_pool(state)
    ids = {e["id"] for e in pool}
    assert "c3" in ids and "sql-row-0" in ids


def test_number_without_evidence_is_unsupported():
    st = _state()
    status, issue = fc._deterministic_check("本月销售额为 12345 元。", evidence_pool(st))
    assert status == "unsupported"
    assert "12345" in issue["reason"]


def test_number_present_in_evidence_is_supported():
    st = _state()
    status, _ = fc._deterministic_check("本月退货率为 3.2%，环比下降 0.5 个百分点。",
                                        evidence_pool(st))
    assert status == "supported"


def test_text_overlap_supports_sentence():
    st = _state()
    status, _ = fc._deterministic_check("本月退货率环比下降", evidence_pool(st))
    assert status == "supported"


def test_unrelated_sentence_goes_gray():
    st = _state()
    status, _ = fc._deterministic_check("公司未来三年的战略规划是什么", evidence_pool(st))
    assert status == "gray"


def test_5b_entailed_requires_verbatim_quote(monkeypatch):
    pool = [{"id": "c3", "text": "本月退货情况明显改善"}]
    gray = [("整体退货表现有所好转", {})]
    monkeypatch.setattr(fc, "ask_llm_json", lambda *a, **k: {"verdicts": [
        {"index": 0, "verdict": "entailed", "evidence_id": "c3",
         "quote": "退货情况明显改善"}]})
    out = fc._verify_gray(gray, pool)
    assert out["整体退货表现有所好转"]["verdict"] == "entailed"
    assert out["整体退货表现有所好转"]["evidence_id"] == "c3"


def test_5b_fabricated_quote_downgrades_to_unknown(monkeypatch):
    pool = [{"id": "c3", "text": "本月退货情况明显改善"}]
    gray = [("整体退货表现有所好转", {})]
    monkeypatch.setattr(fc, "ask_llm_json", lambda *a, **k: {"verdicts": [
        {"index": 0, "verdict": "entailed", "evidence_id": "c3",
         "quote": "退货率下降了 20%"}]})          
    out = fc._verify_gray(gray, pool)
    assert out["整体退货表现有所好转"]["verdict"] == "unknown"


def test_attach_summary_and_coverage(monkeypatch):
    monkeypatch.setattr(fc, "ask_llm_json", _no_llm)
    report = UnifiedFinalReport(answer_md="本月退货率为 3.2%。谢谢你的提问。", mode="rag")
    out = attach_fact_check(report, _state())
    summary = out.fact_check
    assert summary.sentences == 2 and summary.factual == 1
    assert summary.coverage == 0.5
    assert summary.supported == 1 and summary.unsupported == 0


def test_attach_reports_unsupported_number(monkeypatch):
    monkeypatch.setattr(fc, "ask_llm_json", _no_llm)
    report = UnifiedFinalReport(answer_md="本月销售额为 12345 元。", mode="rag")
    out = attach_fact_check(report, _state())
    assert out.fact_check.unsupported == 1


def test_chitchat_and_refusals_are_not_checked():
    assert attach_fact_check(UnifiedFinalReport(answer_md="你好", mode="chitchat"),
                             _state()).fact_check is None
    assert attach_fact_check(UnifiedFinalReport(
        answer_md="拒绝", mode="rag", refusal_reason="policy_denied"),
        _state()).fact_check is None


def test_range_hyphen_is_not_negative_number():
    st = {"rag_ctx": RAGContext(provenance=[{"chunk_id": "c1", "text": "罚款20万元，最高50万元。"}])}
    status, issue = fc._deterministic_check("区间为20万-50万元。", evidence_pool(st))
    assert status == "supported", issue


def test_citation_marker_is_not_number():
    st = {"rag_ctx": RAGContext(provenance=[{"chunk_id": "c1", "text": "该公司被罚款30万元。"}])}
    status, issue = fc._deterministic_check("该公司被罚款[5]。", evidence_pool(st))
    assert status == "supported", issue


def test_split_sentences_skips_section_headings():
    md = ("一、信贷业务违规\n二、内控失效与整改落实不到位\n"
          "违规类型与处罚轻重的对应关系：\n本月退货率为 3.2%。")
    assert split_sentences(md) == ["本月退货率为 3.2%。"]


def test_split_sentences_skips_markdown_numbered_headings():
    md = "**1. 夜间检查覆盖行业**\n**2. 新业态关注**\n本月退货率为 3.2%。"
    assert split_sentences(md) == ["本月退货率为 3.2%。"]


def test_chinese_numeral_range_matches_arabic():
    st = {"rag_ctx": RAGContext(provenance=[
        {"chunk_id": "c1", "text": "警告，并处五万元以上五十万元以下罚款。"}])}
    status, issue = fc._deterministic_check("警告，并处 5 万元至 50 万元罚款。", evidence_pool(st))
    assert status == "supported", issue


def test_chinese_wan_mantissa_matches():
    st = {"rag_ctx": RAGContext(provenance=[
        {"chunk_id": "c1", "text": "当年开出近九千万元罚单。"}])}
    status, issue = fc._deterministic_check("当年开出近 9000 万元罚单。", evidence_pool(st))
    assert status == "supported", issue


def test_chinese_percent_matches():
    st = {"rag_ctx": RAGContext(provenance=[
        {"chunk_id": "c1", "text": "同比下降百分之五十。"}])}
    status, issue = fc._deterministic_check("同比下降 50%。", evidence_pool(st))
    assert status == "supported", issue


def test_arabic_wan_matches_scaled_evidence():
    st = {"rag_ctx": RAGContext(provenance=[
        {"chunk_id": "c1", "text": "处罚金额合计1000000元。"}])}
    status, issue = fc._deterministic_check("罚款 100 万元。", evidence_pool(st))
    assert status == "supported", issue


def test_bracket_heading_is_skipped():
    md = "【收费与客户权益类】\n【财务真实性类（保险机构）】\n本月退货率为 3.2%。"
    assert split_sentences(md) == ["本月退货率为 3.2%。"]


def test_meta_statements_are_procedural():
    assert classify_sentence("以下结论全部来自知识库文档，未联网、未查库。") == "procedural"
    assert classify_sentence("覆盖限制声明：以上清单限于知识库收录文档所载案例；") == "procedural"
    assert classify_sentence("本书共三章。") == "factual"      


def test_reference_section_with_urls_is_not_checked():
    md = ("截至 2025 年 6 月起减免[1]。\n\n参考来源：\n"
          "[1] https://www.gov.cn/zhengce/2025-06/01/content.htm")
    assert split_sentences(md) == ["截至 2025 年 6 月起减免[1]。"]


def test_reference_section_heading_variants_stop_splitting():
    for heading in ("参考来源：", "参考来源", "**参考来源**："):
        md = f"结论为 3.2%。\n\n{heading}\n[1] 2025年度报告.pdf"
        assert split_sentences(md) == ["结论为 3.2%。"], heading


def test_web_answer_with_url_footer_passes_validation(monkeypatch):
    st = {"rag_ctx": RAGContext(
        status="ok", outcome="answered", citations=["w1"],
        provenance=[{"chunk_id": "w1", "doc_type": "web",
                     "text": "政策要点：2025 年 6 月起减免。"}])}
    monkeypatch.setattr(fc, "ask_llm_json", _no_llm)
    report = UnifiedFinalReport(
        answer_md=("截至 2025 年 6 月起减免[1]。\n\n参考来源：\n"
                   "[1] https://www.gov.cn/zhengce/2025-06/01/content.htm"),
        mode="rag", passthrough=True, citations=[{"chunk_id": "w1", "source_type": "web"}])
    out = attach_fact_check(report, st)
    assert out.fact_check.unsupported == 0
    assert validate(out, st) == []


def test_validate_surfaces_unsupported_count():
    report = UnifiedFinalReport(
        answer_md="x", mode="chitchat",
        fact_check=FactCheck(sentences=1, factual=1, unsupported=1))
    errs = validate(report, {})
    assert any("fact_check" in e for e in errs)
