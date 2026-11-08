import pytest

from memory_service.store import MemoryStore, _HashEmbedding
from memory_service import store as st


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    s = MemoryStore(path=str(tmp_path), embedding=_HashEmbedding())
    monkeypatch.setattr(st, "_STORE", s)
    return s


@pytest.fixture()
def extract_ok(monkeypatch):
    import unified_agent.memory.writeback as wb
    monkeypatch.setattr(wb, "ask_llm_json", lambda *a, **k: {
        "summary": "该用户成交额口径含退款", "hint_type": "metric_semantic"})



def test_recall_node_lands_hits(mem):
    from unified_agent.memory.recall import recall
    mem.add("退货率按订单维度统计", {"user_id": "u1", "tenant_id": "acme", "type": "sql"})
    out = recall({"question": "退货率怎么统计", "user_id": "u1", "tenant_id": "acme"})
    assert out["recalled_memory"] and "退货率" in out["recalled_memory"][0]["text"]


def test_recall_uses_resolved_question(mem, monkeypatch):
    import unified_agent.memory.recall as rc
    seen = {}
    real = mem.recall

    def _spy(query, where, **kw):
        seen["query"] = query
        return real(query, where, **kw)

    monkeypatch.setattr(mem, "recall", _spy)
    monkeypatch.setattr(rc, "get_store", lambda: mem)
    rc.recall({"question": "那2025年呢",
               "resolved_question": "2025 年华东区销售额是多少",
               "user_id": "u1", "tenant_id": "acme"})
    assert seen["query"] == "2025 年华东区销售额是多少"      


def test_recall_node_fail_soft(monkeypatch):
    from unified_agent.memory.recall import recall
    import unified_agent.memory.recall as rc

    monkeypatch.setattr(rc, "get_store",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    out = recall({"question": "q"})
    assert out["recalled_memory"] == []                              
    assert "question" not in out                                     



def test_write_memory_writes_notes_and_qa(mem, extract_ok):
    from unified_agent.memory.writeback import write_memory
    write_memory({
        "user_id": "u1", "tenant_id": "acme", "question": "上月成交额",
        "task_type": "sql",
        "conversation_notes": {"preferences": ["口径=含退款"], "entities": [], "open_items": []},
        "final_report": {"answer_md": "上月成交额 1200 元。"},
    })
    n = mem._col.count()
    assert n == 2                                                    
    by_src = {m["source"]: m for m in mem._col.get()["metadatas"]}
    assert by_src["conversation_notes"]["memory_type"] == "general"
    assert by_src["final_report"]["memory_type"] == "sql"            
    assert by_src["final_report"]["hint_type"] == "metric_semantic"
    assert by_src["final_report"]["summary"] == "该用户成交额口径含退款"


def test_write_memory_skips_empty_notes(mem, extract_ok):
    from unified_agent.memory.writeback import write_memory
    write_memory({"user_id": "u1", "tenant_id": "acme", "question": "q",
                  "conversation_notes": {"preferences": [], "entities": [], "open_items": []},
                  "final_report": {"answer_md": "结论。"}})
    assert mem._col.count() == 1                                     


def test_write_memory_fail_soft_on_store_down(monkeypatch):
    from unified_agent.memory.writeback import write_memory
    import unified_agent.memory.writeback as wb

    monkeypatch.setattr(wb, "get_store",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    out = write_memory({"question": "q", "final_report": {"answer_md": "x"}})
    assert [m.type for m in out["history"]] == ["human", "ai"]   


def test_write_memory_appends_thread_history(mem, extract_ok):
    from unified_agent.memory.writeback import write_memory
    out = write_memory({"user_id": "u1", "tenant_id": "acme", "question": "上月成交额",
                        "task_type": "sql",
                        "conversation_notes": {"preferences": ["口径=含退款"],
                                               "entities": [], "open_items": []},
                        "final_report": {"answer_md": "上月成交额 1200 元。"}})
    assert len(out["history"]) == 2                               
    assert getattr(out["history"][0], "content") == "上月成交额"
    assert "1200" in getattr(out["history"][1], "content")
    assert mem._col.count() == 2                                  


def test_write_memory_skips_refused_qa(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    from unified_agent.core.state import SQLContext
    monkeypatch.setattr(wb, "ask_llm_text", lambda *a, **k: "执行前先确认函数兼容性")
    out = wb.write_memory({
        "user_id": "u1", "tenant_id": "acme", "question": "各区域销售额环比",
        "task_type": "sql",
        "sql_ctx": SQLContext(status="error", outcome="refused",
                              error_code="runtime: Code: 63"),
        "conversation_notes": {"preferences": ["口径=含退款"], "entities": [], "open_items": []},
        "final_report": {"answer_md": "**本次未能给出答案。**"},
    })
    metas = mem._col.get(include=["metadatas"])["metadatas"]
    assert not any(m["source"] == "final_report" for m in metas)   
    assert any(m["source"] == "conversation_notes" for m in metas)  
    assert [m["memory_type"] for m in metas if m["source"] == "reflexion"] == ["lesson"]
    assert len(out["history"]) == 2                               


def test_write_memory_skips_validation_failed_qa(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    monkeypatch.setattr(wb, "ask_llm_text", lambda *a, **k: "渲染前逐个核对 claims 数字")
    out = wb.write_memory({
        "user_id": "u1", "tenant_id": "acme", "question": "华东2024年8月销售额",
        "task_type": "hybrid",
        "validation_errors": ["answer_md 出现 claims 外数字：8"],
        "conversation_notes": {"preferences": ["口径=含退款"], "entities": [], "open_items": []},
        "final_report": {"answer_md": "华东 2024 年 8 月销售额 11,327,151.01 元。"},
    })
    metas = mem._col.get(include=["metadatas"])["metadatas"]
    assert not any(m["source"] == "final_report" for m in metas)   
    assert any(m["source"] == "conversation_notes" for m in metas)  
    assert [m["memory_type"] for m in metas if m["source"] == "reflexion"] == ["lesson"]
    assert len(out["history"]) == 2                               


def test_write_memory_skips_error_code_qa(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    from unified_agent.core.state import SQLContext
    monkeypatch.setattr(wb, "ask_llm_text", lambda *a, **k: "写操作前先确认网关可达")
    wb.write_memory({
        "user_id": "u1", "tenant_id": "acme", "question": "把昨天订单删了",
        "task_type": "sql",
        "sql_ctx": SQLContext(is_write=True, status="ok", outcome="answered",
                              error_code="write_execute_failed"),
        "final_report": {"answer_md": "写操作执行失败：网关不可达。"},
    })
    metas = mem._col.get(include=["metadatas"])["metadatas"]
    assert not any(m["source"] == "final_report" for m in metas)   
    assert [m["memory_type"] for m in metas if m["source"] == "reflexion"] == ["lesson"]


def test_write_memory_qa_uses_resolved_history_keeps_raw(mem, extract_ok):
    from unified_agent.memory.writeback import write_memory
    out = write_memory({
        "user_id": "u1", "tenant_id": "acme",
        "question": "那2025年呢", "resolved_question": "2025 年华东区销售额是多少",
        "task_type": "sql",
        "final_report": {"answer_md": "2025 年华东区销售额 1.5 亿元。"},
    })
    assert getattr(out["history"][0], "content") == "那2025年呢"        
    docs = mem._col.get()["documents"]
    long_doc = next(d for d in docs if "Q: " in d)                      
    assert "Q: 2025 年华东区销售额是多少" in long_doc                    



def test_write_memory_skips_qa_when_extract_empty(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    monkeypatch.setattr(wb, "ask_llm_json", lambda *a, **k: {"summary": "", "hint_type": ""})
    wb.write_memory({"user_id": "u1", "tenant_id": "acme", "question": "q",
                     "task_type": "rag",
                     "final_report": {"answer_md": "一段可以入库的答案。"}})
    assert mem._col.count() == 0                                     


def test_write_memory_skips_qa_when_extract_raises(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    monkeypatch.setattr(wb, "ask_llm_json",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = wb.write_memory({"user_id": "u1", "tenant_id": "acme", "question": "q",
                           "task_type": "sql", "final_report": {"answer_md": "答案。"}})
    assert mem._col.count() == 0
    assert len(out["history"]) == 2                                  


@pytest.mark.parametrize("hint,mtype", [
    ("metric_semantic", "sql"), ("preference", "general"),
    ("profile", "general"), ("fact", "rag"),
])
def test_write_memory_hint_type_maps_to_pool(mem, monkeypatch, hint, mtype):
    import unified_agent.memory.writeback as wb
    monkeypatch.setattr(wb, "ask_llm_json",
                        lambda *a, **k: {"summary": "一句话结论", "hint_type": hint})
    wb.write_memory({"user_id": "u1", "tenant_id": "acme", "question": "q",
                     "task_type": "rag", "final_report": {"answer_md": "答案。"}})
    meta = mem._col.get(include=["metadatas"])["metadatas"][0]
    assert meta["hint_type"] == hint and meta["memory_type"] == mtype
    expected = "candidate" if hint in ("preference", "profile") else "formal"
    assert meta["stage"] == expected
    assert meta["confidence"] == 0.5                 


def test_write_memory_writes_multiple_atomic_memories(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    monkeypatch.setattr(wb, "ask_llm_json", lambda *a, **k: {"memories": [
        {"summary": "该用户默认货币单位为万元", "hint_type": "preference",
         "confidence": 0.8, "subject_dim": "默认货币单位", "subject_value": "万元"},
        {"summary": "成交额口径含退款", "hint_type": "metric_semantic",
         "confidence": 0.9, "subject_dim": "成交额口径", "subject_value": "含退款"}]})
    wb.write_memory({"user_id": "u1", "tenant_id": "acme", "question": "q",
                     "task_type": "sql", "final_report": {"answer_md": "答案。"}})
    metas = mem._col.get(include=["metadatas"])["metadatas"]
    assert len(metas) == 2                                    
    by_hint = {m["hint_type"]: m for m in metas}
    assert by_hint["preference"]["stage"] == "candidate"       
    assert by_hint["metric_semantic"]["stage"] == "formal"     


def test_write_memory_skips_empty_memories_array(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    monkeypatch.setattr(wb, "ask_llm_json", lambda *a, **k: {"memories": []})
    wb.write_memory({"user_id": "u1", "tenant_id": "acme", "question": "q",
                     "task_type": "rag", "final_report": {"answer_md": "答案。"}})
    assert mem._col.count() == 0


def test_write_memory_caps_atomic_memories(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    monkeypatch.setattr(wb, "ask_llm_json", lambda *a, **k: {"memories": [
        {"summary": f"结论{i}", "hint_type": "fact", "confidence": 0.9}
        for i in range(5)]})
    wb.write_memory({"user_id": "u1", "tenant_id": "acme", "question": "q",
                     "task_type": "rag", "final_report": {"answer_md": "答案。"}})
    metas = mem._col.get(include=["metadatas"])["metadatas"]
    assert len(metas) == wb._EXT_MAX_ITEMS
    assert {m["summary"] for m in metas} == {"结论0", "结论1", "结论2"}


def test_write_memory_keeps_confidence_and_subject(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    monkeypatch.setattr(wb, "ask_llm_json", lambda *a, **k: {
        "summary": "该用户默认货币单位为万元", "hint_type": "preference",
        "confidence": 0.9, "subject_dim": "默认货币单位", "subject_value": "万元"})
    wb.write_memory({"user_id": "u1", "tenant_id": "acme", "question": "q",
                     "task_type": "rag", "final_report": {"answer_md": "答案。"}})
    meta = mem._col.get(include=["metadatas"])["metadatas"][0]
    assert meta["stage"] == "candidate" and meta["confidence"] == 0.9
    assert meta["subject_dim"] == "默认货币单位" and meta["subject_value"] == "万元"


def test_write_memory_partial_subject_is_dropped(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    monkeypatch.setattr(wb, "ask_llm_json", lambda *a, **k: {
        "summary": "一句话结论", "hint_type": "preference",
        "confidence": 0.9, "subject_dim": "默认货币单位"})
    wb.write_memory({"user_id": "u1", "tenant_id": "acme", "question": "q",
                     "task_type": "rag", "final_report": {"answer_md": "答案。"}})
    meta = mem._col.get(include=["metadatas"])["metadatas"][0]
    assert meta["subject_dim"] == "" and meta["subject_value"] == ""


def test_confidence_normalized():
    import unified_agent.memory.writeback as wb
    assert wb._confidence(85) == 0.85
    assert wb._confidence(-1) == 0.0
    assert wb._confidence("bad") == wb._EXT_CONF_DEFAULT


def test_write_memory_invalid_hint_falls_back_by_lane(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    monkeypatch.setattr(wb, "ask_llm_json",
                        lambda *a, **k: {"summary": "一句话结论", "hint_type": "乱写"})
    wb.write_memory({"user_id": "u1", "tenant_id": "acme", "question": "q",
                     "task_type": "sql", "final_report": {"answer_md": "答案。"}})
    meta = mem._col.get(include=["metadatas"])["metadatas"][0]
    assert meta["hint_type"] == "metric_semantic" and meta["memory_type"] == "sql"


def test_sql_hints_consume_summary_by_hint_type():
    import unified_agent.adapters.sql_adapter as sa
    hints = sa._sql_hints({"recalled_memory": [
        {"text": "摘要\\nQ: …\\nA: …",
         "metadata": {"memory_type": "sql", "hint_type": "metric_semantic",
                      "summary": "成交额口径含退款"}}]})
    assert [h for h in hints if h["content"] == "成交额口径含退款"] == [
        {"content": "成交额口径含退款", "memory_type": "metric_semantic", "importance": 1.0}]


def test_rag_brief_prefers_summary_over_raw_text():
    import unified_agent.adapters.rag_adapter as ra
    from unified_agent.core.state import RAGContext
    st = {"recalled_memory": [{
        "text": "Q: 上月成交额怎么算\\nA: " + "很长的答案" * 80,
        "metadata": {"memory_type": "general", "hint_type": "preference",
                     "summary": "该用户成交额口径含退款"}}]}
    out = ra._task_summary(RAGContext(query="q"), st)
    assert "该用户成交额口径含退款" in out and "很长的答案" not in out



def _lesson_meta(lane="sql", fail_sig="sig1", **kw):
    return {"user_id": "u1", "tenant_id": "acme", "memory_type": "lesson",
            "lane": lane, "fail_sig": fail_sig, "source": "reflexion", **kw}


def test_recall_node_lands_lessons(mem):
    from unified_agent.memory.recall import recall
    mem.add("执行前先核对列名与枚举口径", _lesson_meta())
    out = recall({"question": "列名怎么核对", "user_id": "u1", "tenant_id": "acme"})
    assert any((h.get("metadata") or {}).get("memory_type") == "lesson"
               for h in out["recalled_memory"])


def test_lesson_lines_filter_by_lane():
    from unified_agent.memory.recall import lesson_block, lesson_lines
    state = {"recalled_memory": [
        {"text": "SQL 教训", "metadata": {"memory_type": "lesson", "lane": "sql"}},
        {"text": "RAG 教训", "metadata": {"memory_type": "lesson", "lane": "rag"}},
        {"text": "QA 记忆", "metadata": {"memory_type": "rag"}},
    ]}
    assert lesson_lines(state, ("rag", "hybrid")) == ["RAG 教训"]
    assert lesson_block(state, ("sql",)) == "【历史教训（上次同类失败）】\n- SQL 教训"
    assert lesson_block({"recalled_memory": []}, ("sql",)) == ""
    assert lesson_lines({"recalled_memory": [
        {"text": f"教训{i}", "metadata": {"memory_type": "lesson", "lane": "sql"}}
        for i in range(3)]}, ("sql",)) == ["教训0", "教训1"]          


def test_sql_hints_map_lesson_to_metric_semantic():
    import unified_agent.adapters.sql_adapter as sa
    hints = sa._sql_hints({"recalled_memory": [
        {"text": "先核对列名", "metadata": {"memory_type": "lesson", "lane": "sql"}}]})
    assert {"content": "先核对列名", "memory_type": "metric_semantic",
            "importance": 1.0} in hints


def test_rag_task_summary_lessons_opt_in():
    import unified_agent.adapters.rag_adapter as ra
    from unified_agent.core.state import RAGContext
    st = {"recalled_memory": [{"text": "只用台账里的数字", "metadata": {
        "memory_type": "lesson", "lane": "rag"}}]}
    c = RAGContext(query="q")
    assert "历史教训" not in ra._task_summary(c, st)
    assert "只用台账里的数字" in ra._task_summary(c, st, with_lessons=True)


def test_write_memory_writes_lesson_on_lane_failure(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    from unified_agent.core.state import SQLContext
    monkeypatch.setattr(wb, "ask_llm_text", lambda *a, **k: "执行前先核对列名与函数兼容性")
    wb.write_memory({
        "user_id": "u1", "tenant_id": "acme", "question": "各区域销售额",
        "task_type": "sql",
        "sql_ctx": SQLContext(status="error", outcome="refused",
                              error_code="runtime: Code: 63"),
        "final_report": {"answer_md": "本次未能给出答案。"},
    })
    metas = mem._col.get(include=["metadatas"])["metadatas"]
    lessons = [m for m in metas if m["memory_type"] == "lesson"]
    assert len(lessons) == 1
    assert lessons[0]["lane"] == "sql" and lessons[0]["status"] == "active"
    assert lessons[0]["fail_sig"] and lessons[0]["importance"] == 3.0
    assert not any(m["memory_type"] == "sql" for m in metas)          


def test_write_memory_lesson_dedup_and_reinforce(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    from unified_agent.core.state import SQLContext
    calls = []

    def _fake_text(*a, **k):
        calls.append(1)
        return "执行前先核对列名"

    monkeypatch.setattr(wb, "ask_llm_text", _fake_text)
    state = {"user_id": "u1", "tenant_id": "acme", "question": "各区域销售额",
             "task_type": "sql",
             "sql_ctx": SQLContext(status="ok", outcome="answered", db_error="Unknown column"),
             "final_report": {"answer_md": "本次未能给出答案。"}}
    wb.write_memory(state)
    wb.write_memory(state)
    metas = [m for m in mem._col.get(include=["metadatas"])["metadatas"]
             if m["memory_type"] == "lesson"]
    assert len(metas) == 1                                            
    assert metas[0]["importance"] == 4.0                              
    assert len(calls) == 1                                            


def test_write_memory_lesson_archived_when_ineffective(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    from unified_agent.core.state import SQLContext
    monkeypatch.setattr(wb, "ask_llm_text", lambda *a, **k: "执行前先核对列名")

    def _state():
        st = {"user_id": "u1", "tenant_id": "acme", "question": "各区域销售额",
              "task_type": "sql",
              "sql_ctx": SQLContext(status="error", outcome="refused",
                                    error_code="runtime: Code: 63"),
              "final_report": {"answer_md": "本次未能给出答案。"}}
        lane, kind, ev = wb._failure_signal(st)
        sig = wb._fail_sig(lane, kind, ev)
        st["recalled_memory"] = [{"text": "其对账口径", "metadata": {
            "memory_type": "lesson", "lane": lane, "fail_sig": sig}}]
        return st, sig

    st, sig = _state()
    mem.add("执行前先核对列名", _lesson_meta(fail_sig=sig, importance=3.0, ineffective=0))
    wb.write_memory(st)            
    wb.write_memory(st)            
    metas = [m for m in mem._col.get(include=["metadatas"])["metadatas"]
             if m["memory_type"] == "lesson"]
    assert len(metas) == 1
    assert metas[0]["status"] == "archived"
    assert metas[0]["status_reason"] == "ineffective"
    assert metas[0]["ineffective"] == 2 and metas[0]["importance"] == 1.0


def test_write_memory_lesson_template_fallback(mem, monkeypatch):
    import unified_agent.memory.writeback as wb
    from unified_agent.core.state import SQLContext
    monkeypatch.setattr(wb, "ask_llm_text", lambda *a, **k: "   ")   
    wb.write_memory({
        "user_id": "u1", "tenant_id": "acme", "question": "各区域销售额",
        "task_type": "sql",
        "sql_ctx": SQLContext(status="error", outcome="refused",
                              error_code="runtime: Code: 63"),
        "final_report": {"answer_md": "本次未能给出答案。"},
    })
    texts = [d for d in mem._col.get()["documents"] if d.startswith("[sql/")]
    assert texts and "runtime: Code: 63" in texts[0]                  
