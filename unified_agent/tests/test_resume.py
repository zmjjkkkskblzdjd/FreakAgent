import pytest

from unified_agent.core.contracts import PendingInterrupt, ResumeCommand
from unified_agent.graph.checkpoint import SchemaVersionError, _check_version


def test_pending_interrupt_single_definition():
    p = PendingInterrupt(interrupt_id="i1", kind="route", lane=None, schema={})
    assert p.kind == "route" and p.lane is None


def test_resume_route_action_enum():
    r = ResumeCommand(type="clarify", kind="route", action="choose_hybrid")
    assert r.action == "choose_hybrid"


def test_version_gate_fail_loud():
    with pytest.raises(SchemaVersionError):
        _check_version({"schema_version": 0})
    _check_version({"schema_version": 1})  


def test_route_clarify_count_in_state():
    from unified_agent.core.state import UnifiedState
    assert "route_clarify_count" in UnifiedState.__annotations__


def test_apply_route_resume_increments_count():
    from unified_agent.agents.preflight import apply_route_resume
    base = {"route_clarify_count": 0}
    for action in ("new_question", "choose_sql", "give_up"):
        cmd = ResumeCommand(type="clarify", kind="route", action=action)
        out = apply_route_resume(base, cmd)
        assert out["route_clarify_count"] == 1


def test_route_clarify_capped_after_two_rounds():
    from unified_agent.agents import preflight as pf
    state = {"task_type": "hybrid", "confidence": 0.3, "route_clarify_count": 2}
    assert pf.route_clarify_payload(state) is None
    out = pf.preflight(state)          
    assert out["task_type"] in ("rag", "sql")
    assert out["degraded_from"] == "hybrid" and out["next_step"] == "dispatch"