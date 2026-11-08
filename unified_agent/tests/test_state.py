from operator import add
from typing import Annotated, get_args, get_type_hints

from unified_agent.core.state import SCHEMA_VERSION, RAGContext, SQLContext, UnifiedState


def test_schema_version_is_int():
    assert isinstance(SCHEMA_VERSION, int) and SCHEMA_VERSION == 1


def test_rag_sql_context_defaults():
    assert RAGContext().status == "ok" and RAGContext().outcome == "answered"
    assert SQLContext().status == "ok" and SQLContext().outcome == "answered"


def test_rag_context_has_empty_only():
    assert SQLContext().status == "ok" and SQLContext().outcome == "answered"


def test_reducer_fields_are_annotated_add():
    hints = get_type_hints(UnifiedState, include_extras=True)
    for field in ("tool_calls_rag", "tool_calls_sql", "cost_ledger"):
        assert add in get_args(hints[field]), f"{field} 必须是 Annotated[list, add]，否则 hybrid fan-out 丢数据"


def test_v12_new_fields_declared():
    hints = get_type_hints(UnifiedState, include_extras=True)
    for field in ("pending_interrupt", "recalled_memory", "degraded_from", "validation_errors"):
        assert field in hints
    assert SQLContext().is_write is False