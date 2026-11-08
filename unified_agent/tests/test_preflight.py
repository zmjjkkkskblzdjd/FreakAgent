import pytest

from unified_agent.agents import preflight as pf
from unified_agent.core.contracts import ResumeCommand
from unified_agent.core.state import UnifiedState


@pytest.fixture(autouse=True)
def _fresh_bucket(monkeypatch):
    monkeypatch.setattr(pf, "_BUCKET", pf.TokenBucket(capacity=100, refill_per_sec=10))


def test_low_confidence_yields_route_payload():
    s = UnifiedState(task_type="rag", confidence=0.3, route_clarify_count=0,
                     question="看下那个数据", rationale="问题模糊，车道特征矛盾")
    p = pf.route_clarify_payload(s)
    assert p is not None and p["kind"] == "route" and p["lane"] is None
    opts = p["schema"]["options"]
    by_action = {o["action"]: o["label"] for o in opts}
    assert "choose_hybrid" in by_action and "give_up" in by_action
    assert "new_question" in by_action
    assert all(o["label"] for o in opts)
    assert by_action["choose_sql"].startswith("查业务数据库")
    assert p["schema"]["question"] == "看下那个数据"
    assert p["schema"]["rationale"] == "问题模糊，车道特征矛盾"


def test_route_clarify_count_caps_at_n(monkeypatch):
    monkeypatch.setenv("UNIFIED_DEGRADE_DIRECTION", "sql")
    s = UnifiedState(task_type="hybrid", confidence=0.3, route_clarify_count=2)
    assert pf.route_clarify_payload(s) is None
    out = pf.route_clarify_exhausted(s)
    assert out["task_type"] == "sql" and out["degraded_from"] == "hybrid"   
    assert out["task_status"] == "running" and out["next_step"] == "dispatch"
    assert "业务数据" in out["degrade_notice"]
    assert not any(ch.isdigit() for ch in out["degrade_notice"])   


def test_apply_route_resume_branches():
    s = UnifiedState(task_type="rag", confidence=0.3)
    assert pf.apply_route_resume(s, ResumeCommand(type="clarify", kind="route",
                                                  action="give_up"))["task_status"] == "cancelled"
    assert pf.apply_route_resume(s, ResumeCommand(type="clarify", kind="route",
                                                  action="new_question"))["next_step"] == "router"
    out = pf.apply_route_resume(s, ResumeCommand(type="clarify", kind="route",
                                                 action="choose_hybrid"))
    assert out["task_type"] == "hybrid" and out["next_step"] == "dispatch"


def test_degrade_records_direction_and_origin(monkeypatch):
    monkeypatch.setenv("UNIFIED_DEGRADE_DIRECTION", "rag")
    out = pf.degrade(UnifiedState(task_type="hybrid"))
    assert out["task_type"] == "rag" and out["degraded_from"] == "hybrid"


def test_hybrid_degrades_when_parallel_disabled(monkeypatch):
    monkeypatch.setenv("UNIFIED_HYBRID_PARALLEL", "false")
    assert pf._hybrid_parallel_allowed() is False
    monkeypatch.setenv("UNIFIED_HYBRID_PARALLEL", "true")
    monkeypatch.setattr(pf, "_BUCKET", pf.TokenBucket(capacity=0, refill_per_sec=0))
    assert pf._hybrid_parallel_allowed() is False


def test_exhausted_notice_lands_through_graph_node(monkeypatch):
    from unified_agent.graph.builder import build_graph
    g = build_graph()
    ch = g.builder.channels
    assert "degrade_notice" in ch and "resolved_question" in ch

    monkeypatch.setenv("UNIFIED_DEGRADE_DIRECTION", "sql")
    r = g.builder.nodes["preflight"].runnable
    out = r.invoke({"task_type": "hybrid", "confidence": 0.3,
                    "route_clarify_count": 2, "question": "q"})
    assert out["task_type"] == "sql"
    assert "系统" in out["degrade_notice"]


def test_intake_sends_all_non_chitchat_through_catalog_then_preflight():
    from unified_agent.graph.builder import _after_intake
    for t in ("rag", "sql", "hybrid"):
        assert _after_intake({"task_type": t}) == "recall_memory"
    assert _after_intake({"task_type": "chitchat"}) == "chitchat"  



def test_lane_confirm_payload_present_and_missing():
    p = pf.lane_confirm_payload("sql", "查一下华东区的销售额", "华东区销售额和政策", 1)
    assert p["kind"] == "lane_confirm" and p["lane"] == "sql"
    assert p["schema"]["missing"] is False
    actions = [o["action"] for o in p["schema"]["options"]]
    assert {"approve", "edit", "reject", "cancel"} <= set(actions) and "add" not in actions
    m = pf.lane_confirm_payload("rag", "", "华东区销售额和政策", 2)
    actions = [o["action"] for o in m["schema"]["options"]]
    assert "add" in actions and "approve" not in actions
    assert m["schema"]["missing"] is True and m["schema"]["original"]


def test_apply_lane_decision_edit_inherits_context(monkeypatch):
    import unified_agent.agents.lane_question as lq

    def boom(system, user):
        raise RuntimeError("network down")
    monkeypatch.setattr(lq, "ask_llm_json", boom)
    cmd = ResumeCommand(type="lane_confirm", kind="lane", lane="sql",
                        lane_action="edit", lane_question="它的销售额是多少")
    fq, dec = pf.apply_lane_decision("sql", cmd, "华东区销售额",
                                     "2024年华东区的销售额是多少")
    assert fq and "2024年" in fq                     
    assert dec["action"] == "edit" and dec["user_input"] == "它的销售额是多少"


def test_apply_lane_decision_edit_uses_valid_llm_output(monkeypatch):
    import unified_agent.agents.lane_question as lq
    monkeypatch.setattr(lq, "ask_llm_json",
                        lambda s, u: {"question": "2024年华东区的营业额是多少"})
    cmd = ResumeCommand(type="lane_confirm", kind="lane", lane="sql",
                        lane_action="edit", lane_question="它的营业额是多少")
    fq, dec = pf.apply_lane_decision("sql", cmd, "华东区营业额",
                                     "2024年华东区的营业额是多少")
    assert fq == "2024年华东区的营业额是多少" and dec["normalized"] == "llm"


def test_apply_lane_decision_rejects_and_missing_approve():
    reject = ResumeCommand(type="lane_confirm", kind="lane", lane="sql",
                           lane_action="reject")
    fq, dec = pf.apply_lane_decision("sql", reject, "华东区销售额", "华东区销售额和政策")
    assert fq is None and dec["action"] == "reject"
    approve = ResumeCommand(type="lane_confirm", kind="lane", lane="rag",
                            lane_action="approve")
    fq, dec = pf.apply_lane_decision("rag", approve, "", "华东区销售额和政策")
    assert fq is None and dec["action"] == "reject"


def test_lane_confirm_off_mode_auto_decisions(monkeypatch):
    monkeypatch.setenv("HYBRID_CONFIRM_MODE", "off")
    final, decisions = pf._auto_lane_decisions(
        "original", {"rag": "文档部分", "sql": "数据部分"})
    assert final == {"rag": "文档部分", "sql": "数据部分"}
    assert [d["action"] for d in decisions] == ["approve", "approve"]
    final, decisions = pf._auto_lane_decisions("original", {"sql": "数据部分"})
    assert final == {"sql": "数据部分"}
    assert decisions[0]["action"] == "skip_missing"
    final, decisions = pf._auto_lane_decisions("original", {})
    assert final == {"rag": "original", "sql": "original"}
    assert all(d["action"] == "fallback_full" for d in decisions)


def test_hybrid_two_cards_then_dispatch(monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    from unified_agent.core.state import UnifiedState

    monkeypatch.delenv("HYBRID_CONFIRM_MODE", raising=False)
    g = StateGraph(UnifiedState)
    g.add_node("preflight", pf.preflight)
    g.add_edge(START, "preflight")
    g.add_edge("preflight", END)
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-lane"}}

    out = app.invoke({"task_type": "hybrid", "confidence": 0.9, "question": "整体问题",
                      "lane_questions": {"rag": "文档部分", "sql": "数据部分"}}, cfg)
    assert out["__interrupt__"][0].value["lane"] == "rag"
    out = app.invoke(Command(resume={"type": "lane_confirm", "kind": "lane",
                                     "lane": "rag", "lane_action": "approve"}), cfg)
    assert out["__interrupt__"][0].value["lane"] == "sql"
    out = app.invoke(Command(resume={"type": "lane_confirm", "kind": "lane",
                                     "lane": "sql", "lane_action": "reject"}), cfg)
    assert out["lane_questions"] == {"rag": "文档部分"}
    assert out["next_step"] == "dispatch" and out["task_status"] == "running"
    assert out["sql_ctx"] is None                    
    assert [d["action"] for d in out["lane_decisions"]] == ["approve", "reject"]


def test_hybrid_cancel_on_first_card_stops_task(monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    from unified_agent.core.state import UnifiedState

    monkeypatch.delenv("HYBRID_CONFIRM_MODE", raising=False)
    g = StateGraph(UnifiedState)
    g.add_node("preflight", pf.preflight)
    g.add_edge(START, "preflight")
    g.add_edge("preflight", END)
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-lane-cancel"}}
    app.invoke({"task_type": "hybrid", "confidence": 0.9, "question": "q",
                "lane_questions": {"rag": "文档部分", "sql": "数据部分"}}, cfg)
    out = app.invoke(Command(resume={"type": "lane_confirm", "kind": "lane",
                                     "lane": "rag", "lane_action": "cancel"}), cfg)
    assert "__interrupt__" not in out
    assert out["task_status"] == "cancelled" and out["next_step"] == "end"


def test_route_clarify_choose_hybrid_then_lane_cards(monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    from unified_agent.core.state import UnifiedState

    monkeypatch.delenv("HYBRID_CONFIRM_MODE", raising=False)
    g = StateGraph(UnifiedState)
    g.add_node("preflight", pf.preflight)
    g.add_edge(START, "preflight")
    g.add_edge("preflight", END)
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-route-then-lane"}}
    out = app.invoke({"task_type": "hybrid", "confidence": 0.3, "question": "q",
                      "lane_questions": {"rag": "文档部分", "sql": "数据部分"}}, cfg)
    assert out["__interrupt__"][0].value["kind"] == "route"
    out = app.invoke(Command(resume={"type": "clarify", "kind": "route",
                                     "action": "choose_hybrid"}), cfg)
    p = out["__interrupt__"][0].value
    assert p["kind"] == "lane_confirm" and p["lane"] == "rag"


def test_single_lane_clears_hybrid_residue(monkeypatch):
    out = pf.preflight({"task_type": "sql", "confidence": 0.9,
                        "lane_questions": {"sql": "旧子问题"}, "lane_decisions": [{"lane": "sql"}]})
    assert out["lane_questions"] == {} and out["lane_decisions"] == []


def test_dispatch_hybrid_uses_lane_whitelist():
    from unified_agent.graph.builder import _dispatch
    from unified_agent.graph.reducers import RAG_LANE, SQL_LANE
    assert _dispatch({"task_type": "hybrid", "lane_questions": {"sql": "q"}}) == [SQL_LANE]
    assert _dispatch({"task_type": "hybrid", "lane_questions": {}}) == [RAG_LANE, SQL_LANE]


def test_dispatch_returns_keys_and_pathmap_fans_out():
    from unified_agent.graph.builder import _DISPATCH_MAP, _dispatch
    from unified_agent.graph.reducers import RAG_LANE, SQL_LANE
    assert _dispatch({"task_type": "hybrid"}) == [RAG_LANE, SQL_LANE]  
    assert _dispatch({"task_type": "sql"}) == "sql"
    assert _dispatch({"next_step": "router"}) == "router"
    assert _dispatch({"task_status": "cancelled"}) == "finalize"
    assert _DISPATCH_MAP["rag"] == RAG_LANE and _DISPATCH_MAP["sql"] == SQL_LANE
    assert all(isinstance(v, str) for v in _DISPATCH_MAP.values())     


def test_preflight_ignores_web_intent():
    out = pf.preflight({"task_type": "rag", "confidence": 0.9, "web_intent": True,
                        "question": "最近的政策有哪些", "kb_ids": ["default"],
                        "web_enabled": False})
    assert "task_type" not in out


def test_web_intent_keeps_rag_when_kb_disabled():
    out = pf.preflight({"task_type": "rag", "confidence": 0.9, "web_intent": True,
                        "question": "最近的政策有哪些", "kb_ids": [],
                        "sql_enabled": True, "web_enabled": True})
    assert "task_type" not in out
    assert "degraded_from" not in out


def test_web_intent_degrades_when_web_disabled():
    out = pf.preflight({"task_type": "rag", "confidence": 0.9, "web_intent": True,
                        "question": "最近的政策有哪些", "kb_ids": [],
                        "sql_enabled": True, "web_enabled": False})
    assert out["task_type"] == "sql" and out["degraded_from"] == "rag"