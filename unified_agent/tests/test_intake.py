from __future__ import annotations

from types import SimpleNamespace

from unified_agent.agents import intake as it
from unified_agent.agents import recall_catalog as rc


def _patch_env(monkeypatch, snapshot="【知识库】默认库｜3 篇\n【数据源】#5｜12 张表"):
    monkeypatch.setattr(rc, "snapshot_text", lambda s: snapshot)
    monkeypatch.setattr(rc, "_tenant_datasources", lambda tid: [])
    return snapshot


def _hist(text="格力电器2020年一季度营业收入是多少"):
    return [SimpleNamespace(content=text)]


def test_intake_lands_resolved_question_and_task_type(monkeypatch):
    snap = _patch_env(monkeypatch)
    seen = {}

    def fake(system, user, **kw):
        seen["user"] = user
        return {"resolved_question": "格力电器2020年一季度的营业收入是多少",
                "task_type": "rag", "rag_tier": "deep", "sql_tier": "lite",
                "confidence": 0.85, "rationale": "季报文档事实",
                "need_attribution": False,
                "lane_questions": {"rag": "多余", "sql": "多余"}}

    monkeypatch.setattr(it, "ask_llm_json", fake)
    out = it.intake({"question": "那它的营收呢", "history": _hist(),
                     "tenant_id": 12, "datasource_id": 5})

    assert out["resolved_question"] == "格力电器2020年一季度的营业收入是多少"
    assert out["task_type"] == "rag" and out["rag_tier"] == "deep"
    assert out["confidence"] == 0.85 and out["rationale"] == "季报文档事实"
    assert out["need_attribution"] is False
    assert "lane_questions" not in out                  
    assert out["catalog_hints"] == {"snapshot": snap}   
    assert isinstance(out["_intake_ms"], int) and out["_intake_ms"] >= 0
    assert "那它的营收呢" in seen["user"]
    assert "格力电器2020年一季度营业收入是多少" in seen["user"]
    assert snap in seen["user"]


def test_intake_llm_failure_falls_back(monkeypatch):
    _patch_env(monkeypatch)

    def boom(s, u):
        raise RuntimeError("network down")
    monkeypatch.setattr(it, "ask_llm_json", boom)
    out = it.intake({"question": "本轮原话", "task_type": "sql"})
    assert out["task_type"] == "sql"            
    assert out["confidence"] == 0.3
    assert out["next_step"] == "preflight_clarify"
    assert out["resolved_question"] == "本轮原话"


def test_intake_low_confidence_goes_clarify(monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setattr(it, "ask_llm_json",
                        lambda s, u, **kw: {"resolved_question": "q", "task_type": "sql",
                                      "confidence": 0.3, "rationale": "模糊"})
    out = it.intake({"question": "q", "tenant_id": 12, "datasource_id": 5})
    assert out["task_type"] == "sql" and out["next_step"] == "preflight_clarify"


def test_intake_hybrid_keeps_only_lane_questions(monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setattr(it, "ask_llm_json",
                        lambda s, u, **kw: {"resolved_question": "q", "task_type": "hybrid",
                                      "confidence": 0.9,
                                      "lane_questions": {"rag": " 文档部分 ",
                                                         "sql": "数据部分",
                                                         "web": "多余键",
                                                         "rag2": ""}})
    out = it.intake({"question": "q", "tenant_id": 12, "datasource_id": 5})
    assert out["lane_questions"] == {"rag": "文档部分", "sql": "数据部分"}


def test_sql_without_datasource_downgrades_to_rag(monkeypatch):
    _patch_env(monkeypatch)
    events = []
    monkeypatch.setattr(it, "emit_event",
                        lambda kind, node, status, detail, *a, **k:
                        events.append((kind, detail)))
    monkeypatch.setattr(it, "ask_llm_json",
                        lambda s, u, **kw: {"resolved_question": "上月销售额",
                                      "task_type": "sql", "confidence": 0.9})

    out = it.intake({"question": "上月销售额", "tenant_id": 12})
    assert out["task_type"] == "rag"
    assert out["confidence"] == 0.55                    
    assert out["next_step"] == "preflight_clarify"
    assert events[0][0] == "intake.recheck"
    assert events[0][1]["reason"] == "no_datasource"


def test_hybrid_without_datasource_downgrades_to_rag(monkeypatch):
    _patch_env(monkeypatch)
    events = []
    monkeypatch.setattr(it, "emit_event",
                        lambda kind, node, status, detail, *a, **k:
                        events.append((kind, detail)))
    monkeypatch.setattr(it, "ask_llm_json",
                        lambda s, u, **kw: {"resolved_question": "q", "task_type": "hybrid",
                                      "confidence": 0.9,
                                      "lane_questions": {"rag": "文档", "sql": "数据"}})

    out = it.intake({"question": "q", "tenant_id": 12, "kb_ids": ["default"]})
    assert out["task_type"] == "rag"
    assert events[0][1]["reason"] == "hybrid_no_sql"


def test_rag_with_explicit_empty_kb_goes_clarify(monkeypatch):
    _patch_env(monkeypatch)
    events = []
    monkeypatch.setattr(it, "emit_event",
                        lambda kind, node, status, detail, *a, **k:
                        events.append((kind, detail)))
    monkeypatch.setattr(it, "ask_llm_json",
                        lambda s, u, **kw: {"resolved_question": "q", "task_type": "rag",
                                      "confidence": 0.9})

    out = it.intake({"question": "q", "kb_ids": []})
    assert out["task_type"] == "rag"
    assert out["next_step"] == "preflight_clarify"
    assert out["confidence"] <= 0.55
    assert events[0][1]["reason"] == "no_kb"


def test_web_intent_does_not_trigger_no_kb_clarify(monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setattr(it, "ask_llm_json",
                        lambda s, u, **kw: {"resolved_question": "q", "task_type": "rag",
                                      "confidence": 0.9, "web_intent": True})

    out = it.intake({"question": "q", "kb_ids": []})
    assert out["task_type"] == "rag" and out["web_intent"] is True
    assert "next_step" not in out


def test_legacy_web_task_type_maps_to_rag_with_web_intent(monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setattr(it, "ask_llm_json",
                        lambda s, u, **kw: {"resolved_question": "q", "task_type": "web",
                                      "confidence": 0.9})

    out = it.intake({"question": "q", "kb_ids": ["default"]})
    assert out["task_type"] == "rag" and out["web_intent"] is True


def test_web_intent_only_applies_to_rag(monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setattr(it, "ask_llm_json",
                        lambda s, u, **kw: {"resolved_question": "q", "task_type": "chitchat",
                                      "confidence": 0.9, "web_intent": True})

    out = it.intake({"question": "q", "kb_ids": ["default"]})
    assert out["web_intent"] is False


def test_hybrid_missing_both_sides_keeps_rag_with_clarify(monkeypatch):
    _patch_env(monkeypatch)
    events = []
    monkeypatch.setattr(it, "emit_event",
                        lambda kind, node, status, detail, *a, **k:
                        events.append((kind, detail)))
    monkeypatch.setattr(it, "ask_llm_json",
                        lambda s, u, **kw: {"resolved_question": "q", "task_type": "hybrid",
                                      "confidence": 0.9})

    out = it.intake({"question": "q", "tenant_id": 12, "kb_ids": []})
    assert out["task_type"] == "rag"
    assert out["next_step"] == "preflight_clarify"
    assert [d["reason"] for _, d in events] == ["hybrid_no_sql", "hybrid_no_rag"]


def test_chitchat_with_data_keywords_escalates_to_clarify(monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setattr(it, "ask_llm_json",
                        lambda s, u, **kw: {"resolved_question": "那个报表的金额是多少",
                                      "task_type": "chitchat", "confidence": 0.8})

    out = it.intake({"question": "那个报表的金额是多少", "tenant_id": 12})
    assert out["task_type"] == "chitchat"               
    assert out["confidence"] == 0.5
    assert out["next_step"] == "preflight_clarify"


def test_intake_prompt_file_loads():
    from unified_agent.prompts import load_prompt
    system, user_tpl = load_prompt("intake")
    assert "指代消解" in system and "分诊" in system
    assert "目录快照" in system and "存在性裁决" in system
    for ph in ("{question}", "{history_digest}", "{snapshot}"):
        assert ph in user_tpl
