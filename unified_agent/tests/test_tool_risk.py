import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "qa_harness" / "qa_harness"))

from core.types import ToolDefinition  # noqa: E402
from tools import ToolRegistry  # noqa: E402


def _registry(risk="read", requires_confirmation=False, handler=None):
    calls: list[dict] = []

    def _handler(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "args": kwargs}

    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="demo_tool", description="演示工具", parameters={"type": "object"},
        handler=handler or _handler, risk=risk,
        requires_confirmation=requires_confirmation))
    return registry, calls


def test_risk_label_is_exposed_in_description():
    registry, _ = _registry(risk="write")
    payload = registry.build_tools_payload()[0]["function"]
    assert payload["description"].startswith("[WRITE·需确认] ")


def test_destructive_label_is_exposed():
    registry, _ = _registry(risk="destructive")
    assert registry.build_tools_payload()[0]["function"]["description"].startswith(
        "[DESTRUCTIVE·默认禁止] ")


def test_read_tools_keep_description_unchanged():
    registry, _ = _registry()
    assert registry.build_tools_payload()[0]["function"]["description"] == "演示工具"


def test_write_tool_without_confirmation_is_blocked():
    registry, calls = _registry(risk="write", requires_confirmation=True)
    _name, result = registry.execute_tool_call(
        {"function": {"name": "demo_tool", "arguments": "{}"}})
    assert result["error"] == "confirmation_required"
    assert calls == []                        


def test_confirmed_write_tool_executes_and_strips_flag():
    registry, calls = _registry(risk="write", requires_confirmation=True)
    _name, result = registry.execute_tool_call(
        {"function": {"name": "demo_tool", "arguments": '{"__confirmed__": true}'}})
    assert result["ok"] is True
    assert calls == [{}]                     
