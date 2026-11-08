from __future__ import annotations

import json
from typing import Any, Callable

from core.types import ToolDefinition


class ToolRegistry:

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def _normalize_tool(self, tool: ToolDefinition | Callable[..., dict[str, Any]]) -> ToolDefinition:
        if isinstance(tool, ToolDefinition):
            return tool

        tool_definition = getattr(tool, "__tool_definition__", None)
        if isinstance(tool_definition, ToolDefinition):
            return tool_definition

        raise TypeError(
            "register(...) 只接受 ToolDefinition 或使用 @tool 装饰过的函数。"
        )

    def register(self, tool: ToolDefinition | Callable[..., dict[str, Any]]) -> None:
        tool_definition = self._normalize_tool(tool)
        self._tools[tool_definition.name] = tool_definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def register_many(self, *tools: ToolDefinition | Callable[..., dict[str, Any]]) -> None:
        for tool in tools:
            self.register(tool)

    def build_tools_payload(self) -> list[dict[str, Any]]:
        _RISK_LABEL = {
            "read": "",
            "write": "[WRITE·需确认] ",
            "destructive": "[DESTRUCTIVE·默认禁止] ",
        }
        payload: list[dict[str, Any]] = []

        for tool in self._tools.values():
            label = _RISK_LABEL.get(tool.risk, "")
            payload.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": f"{label}{tool.description}",
                        "parameters": tool.parameters,
                    },
                }
            )

        return payload

    def execute_tool_call(self, tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        function_info = tool_call.get("function", {})
        tool_name = function_info.get("name", "unknown_tool")
        raw_arguments = function_info.get("arguments", "{}")

        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            return tool_name, {
                "ok": False,
                "error": f"工具参数不是合法 JSON：{exc}",
                "raw_arguments": raw_arguments,
            }

        tool = self._tools.get(tool_name)
        if tool is None:
            return tool_name, {
                "ok": False,
                "error": f"未知工具：{tool_name}",
                "arguments": arguments,
            }


        if tool.requires_confirmation and arguments.pop("__confirmed__", False) is not True:
            return tool_name, {
                "ok": False,
                "error": "confirmation_required",
                "risk": tool.risk,
                "message": ("该工具会修改数据，必须先向用户展示结果并由用户确认"
                            "（__confirmed__=true 重放）后才能执行。"),
            }

        try:
            return tool_name, tool.handler(**arguments)
        except KeyError as exc:
            return tool_name, {
                "ok": False,
                "error": f"缺少必要参数：{exc}",
                "arguments": arguments,
            }
        except TypeError as exc:
            return tool_name, {
                "ok": False,
                "error": f"工具参数不匹配：{exc}",
                "arguments": arguments,
            }
