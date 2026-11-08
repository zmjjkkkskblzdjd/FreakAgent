from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from .llm import DEFAULT_MODEL_NAME, call_llm, create_system_message
from .messages import MessageStore

if TYPE_CHECKING:
    from tools.registry import ToolRegistry


class AgentRuntime:

    def __init__(
        self,
        api_key: str,
        tool_registry: ToolRegistry,
        max_loops: int = 8,
        system_message: dict[str, str] | None = None,
        model_name: str = DEFAULT_MODEL_NAME,
    ) -> None:
        self.api_key = api_key
        self.tool_registry = tool_registry
        self.max_loops = max_loops
        self.system_message = system_message
        self.model_name = model_name

    def create_state(self, goal: str) -> dict[str, Any]:
        return {
            "goal": goal,
            "shared_context": {},
            "last_tool_name": None,
            "last_tool_result": None,
            "completed": False,
            "loop_count": 0,
        }

    def get_system_message(self) -> dict[str, str]:
        return self.system_message or create_system_message()

    def build_runtime_message(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "user",
            "content": (
                "当前运行时状态如下，请基于这些信息决定下一步：\n"
                f"- goal: {state.get('goal')!r}\n"
                f"- shared_context: {json.dumps(state.get('shared_context', {}), ensure_ascii=False)}\n"
                f"- last_tool_name: {state.get('last_tool_name')!r}\n"
                f"- last_tool_result: {json.dumps(state.get('last_tool_result'), ensure_ascii=False)}\n"
                f"- completed: {state.get('completed')!r}\n"
                f"- loop_count: {state.get('loop_count')!r}\n"
                "如果任务还没完成，就继续调用合适工具；如果任务已完成，就直接自然语言回答。"
            ),
        }

    def build_runtime_messages(
        self,
        message_store: MessageStore,
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [
            self.get_system_message(),
            *message_store.snapshot(),
            self.build_runtime_message(state),
        ]

    def update_state_from_tool_result(
        self,
        state: dict[str, Any],
        tool_name: str,
        tool_result: dict[str, Any],
    ) -> None:
        state["last_tool_name"] = tool_name
        state["last_tool_result"] = tool_result

        context_updates = tool_result.get("context_updates")
        if isinstance(context_updates, dict):
            state["shared_context"].update(context_updates)

    def on_tool_result(
        self,
        state: dict[str, Any],
        tool_name: str,
        tool_result: dict[str, Any],
        message_store: MessageStore,
    ) -> None:
        return None

    def run(self, goal: str, message_store: MessageStore) -> str:
        state = self.create_state(goal)
        tools_payload = self.tool_registry.build_tools_payload()

        for loop_index in range(1, self.max_loops + 1):
            state["loop_count"] = loop_index
            request_messages = self.build_runtime_messages(message_store, state)
            assistant_message = call_llm(
                api_key=self.api_key,
                messages=request_messages,
                tools=tools_payload,
                model_name=self.model_name,
            )
            tool_calls = assistant_message.get("tool_calls") or []

            if tool_calls:
                message_store.append(
                    {
                        "role": "assistant",
                        "content": assistant_message.get("content"),
                        "tool_calls": tool_calls,
                    }
                )

                for tool_call in tool_calls:
                    tool_name = tool_call.get("function", {}).get("name", "unknown_tool")
                    raw_arguments = tool_call.get("function", {}).get("arguments", "{}")

                    print(f"\n[循环 {loop_index}] 模型选择工具：{tool_name}")
                    print(f"[工具参数] {raw_arguments}")

                    executed_tool_name, tool_result = self.tool_registry.execute_tool_call(tool_call)
                    self.update_state_from_tool_result(
                        state=state,
                        tool_name=executed_tool_name,
                        tool_result=tool_result,
                    )
                    self.on_tool_result(
                        state=state,
                        tool_name=executed_tool_name,
                        tool_result=tool_result,
                        message_store=message_store,
                    )

                    print(f"[工具结果] {json.dumps(tool_result, ensure_ascii=False)}")

                    message_store.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": json.dumps(tool_result, ensure_ascii=False),
                        }
                    )

                continue

            final_answer = assistant_message.get("content") or "任务已处理完成。"
            state["completed"] = True
            message_store.append({"role": "assistant", "content": final_answer})
            return final_answer

        raise RuntimeError("超过最大循环次数，任务仍未完成。可以把任务描述得更具体一点再重试。")
