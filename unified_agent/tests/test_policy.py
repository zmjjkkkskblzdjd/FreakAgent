import pytest

from unified_agent.core.policy import PolicyInjectionError, inject_read_filters, resolve_scope


def test_resolve_scope_shape():
    s = resolve_scope("t1")
    assert set(s.keys()) == {"read_filters", "write_constraints"}  
    assert s["read_filters"] is None  


def test_inject_read_filters_ands_where():
    sql = "SELECT name FROM v_fact WHERE date = '2026-01-01'"
    out = inject_read_filters(sql, {"where": "tenant_id = 't1'"})
    assert "tenant_id = 't1'" in out and "date = '2026-01-01'" in out


def test_inject_skips_when_no_filters():
    sql = "SELECT 1"
    assert inject_read_filters(sql, None) == sql


def test_inject_fails_on_non_select():
    with pytest.raises(PolicyInjectionError):
        inject_read_filters("DELETE FROM x WHERE 1=1", {"where": "tenant_id='t1'"})


def test_passthrough_fallback_sets_executed_sql():
    from unified_agent.adapters.sql_adapter import _inject_read
    from unified_agent.core.state import SQLContext
    state = {"sql_ctx": SQLContext(sql="SELECT 1", checked_sql_pre_injection="SELECT 1"),
             "tenant_id": "t1"}
    out = _inject_read(state)
    assert out["sql_ctx"].executed_sql == "SELECT 1"
    assert out["sql_ctx"].policy_injected is False


def test_injected_sql_sets_policy_trace():
    import unified_agent.adapters.sql_adapter as sa
    from unified_agent.core.state import SQLContext

    class _FakeScope(dict):
        pass

    orig = sa.policy.resolve_scope
    sa.policy.resolve_scope = lambda tid: {"read_filters": {"where": "tenant_id = 't1'"},
                                           "write_constraints": None}
    try:
        state = {"sql_ctx": SQLContext(sql="SELECT name FROM v_fact",
                                       checked_sql_pre_injection="SELECT name FROM v_fact"),
                 "tenant_id": "t1"}
        out = sa._inject_read(state)
        c = out["sql_ctx"]
        assert c.policy_injected is True and c.injection_hash
        assert "tenant_id = 't1'" in c.executed_sql
    finally:
        sa.policy.resolve_scope = orig