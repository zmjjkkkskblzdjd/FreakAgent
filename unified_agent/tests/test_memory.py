from memory_service.field_dict import MEMORY_FIELDS
from unified_agent.memory.recall import _where, recall
from unified_agent.core.state import UnifiedState


def test_field_dict_marks_schema_version():
    assert "schema_version" in MEMORY_FIELDS
    assert MEMORY_FIELDS["schema_version"][2] is True   


def test_preference_and_profile_are_distinct():
    assert MEMORY_FIELDS["preference"][1] != MEMORY_FIELDS["user_profile"][1]


def test_where_filters_type_user_tenant():
    w = _where(UnifiedState(user_id="u1", tenant_id="t1"))
    assert list(w.keys()) == ["$and"]
    clauses = w["$and"]
    assert {"user_id": "u1"} in clauses and {"tenant_id": "t1"} in clauses
    assert {"status": "active"} in clauses and {"schema_version": 1} in clauses
    or_clause = [c for c in clauses if "$or" in c]
    assert or_clause and all("memory_type" in t for t in or_clause[0]["$or"])


def test_recall_returns_state_known_keys():
    out = recall(UnifiedState(user_id="u1", tenant_id="t1"))
    assert set(out.keys()) <= set(UnifiedState.__annotations__)
    assert "recalled_memory" in out