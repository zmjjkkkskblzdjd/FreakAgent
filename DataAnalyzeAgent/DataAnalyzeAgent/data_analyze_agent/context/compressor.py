from __future__ import annotations

import sys

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from config import TokenBudgetConfig
from data_analyze_agent.context.budget import measure
from data_analyze_agent.core.llm import ask_llm_text
from data_analyze_agent.graph.state import DataAnalyzeState


_CLEANUP_KEEP_RECENT = 8   
_COMPRESS_KEEP_RECENT = 6  


_COMPRESS_SYSTEM_PROMPT = """请将以下对话历史压缩为结构化摘要。

必须保留：
1. 用户的核心目标和当前状态
2. 已完成的关键操作（含具体指标口径、时间范围、筛选条件）
3. 已发现的问题和对应的解决决策
4. 当前的待办事项
5. 已确认的指标口径/常用偏好（用于下次直接复用，避免重复反问）

可以丢弃：
- 重复的尝试记录
- 冗长的工具输出原文（保留结论即可）
- 探索性但无结论的讨论

格式：使用层次化列表，确保关键细节不丢失。控制在 800 字以内。"""




def _msg_to_dict(m) -> dict:
    if isinstance(m, BaseMessage):
        role = getattr(m, "type", "unknown")  
        role = {"human": "user", "ai": "assistant"}.get(role, role)
        return {"role": role, "content": m.content}
    if isinstance(m, dict):
        return m
    return {"role": "unknown", "content": str(m)}


def _dict_to_msg(d: dict) -> BaseMessage:
    role = d.get("role", "user")
    content = d.get("content", "")
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        return AIMessage(content=content)
    if role == "system":
        return SystemMessage(content=content)
    if role == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=d.get("tool_call_id", ""),
            name=d.get("name"),
        )
    return HumanMessage(content=str(content))


class ContextCompressor:

    def __init__(
        self,
        messages: list,
        budget: TokenBudgetConfig,
        *,
        cleanup_keep_recent: int = _CLEANUP_KEEP_RECENT,
        compress_keep_recent: int = _COMPRESS_KEEP_RECENT,
    ) -> None:
        self._views = [_msg_to_dict(m) for m in messages]
        self.budget = budget
        self.cleanup_keep_recent = cleanup_keep_recent
        self.compress_keep_recent = compress_keep_recent


    def run(self) -> tuple[list, str]:
        try:
            usage = measure(self._views, self.budget)
            if usage.recommendation == "no_action":
                return self._views, "no_action"
            self._cleanup_old_tool_results()
            usage2 = measure(self._views, self.budget)
            if usage2.recommendation == "compress":
                if self._full_compress():
                    return self._views, "compressed"
                return self._views, "cleaned_up"  
            return self._views, "cleaned_up"
        except Exception as e:
            print(f"[compressor] run 异常，messages 原样保留：{e}", file=sys.stderr)
            return self._views, "no_action"


    def _cleanup_old_tool_results(self) -> int:
        msgs = self._views
        cutoff = len(msgs) - self.cleanup_keep_recent
        if cutoff <= 0:
            return 0
        replaced = 0
        for i in range(min(cutoff, len(msgs))):
            if msgs[i].get("role") == "tool":
                name = msgs[i].get("name") or msgs[i].get("tool_call_id") or "tool"
                msgs[i] = {
                    "role": "tool",
                    "tool_call_id": msgs[i].get("tool_call_id"),
                    "name": name,
                    "content": f"[已执行：{name} → 结果已归档以节省空间]",
                }
                replaced += 1
        return replaced


    def _full_compress(self) -> bool:
        msgs = self._views
        real_system_idxs = [
            i for i, m in enumerate(msgs)
            if m.get("role") == "system"
            and "【对话历史摘要】" not in (m.get("content") or "")
        ]
        last_real_system = real_system_idxs[-1] if real_system_idxs else -1
        sys_segment = msgs[: last_real_system + 1] if last_real_system >= 0 else []
        middle_start = last_real_system + 1
        recent_start = max(middle_start, len(msgs) - self.compress_keep_recent)
        middle = msgs[middle_start:recent_start]
        recent = msgs[recent_start:]
        if not middle:
            return False  

        text = _format_messages_as_text(middle)
        result = ask_llm_text(
            system_prompt=_COMPRESS_SYSTEM_PROMPT,
            user_content=text,
            max_tokens=800,
        )
        if not result["ok"]:
            print(f"[compressor] LLM 压缩失败：{result['error']}", file=sys.stderr)
            return False

        summary = result["data"]["content"]
        if not summary or not summary.strip():
            return False

        summary_msg = {
            "role": "system",
            "content": f"【对话历史摘要】\n{summary.strip()}",
        }
        self._views = sys_segment + [summary_msg] + recent
        return True




def _format_messages_as_text(messages: list) -> str:
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "unknown")
        content = m.get("content")
        if content is None:
            tool_calls = m.get("tool_calls")
            if tool_calls:
                calls = "; ".join(
                    f"{tc.get('function', {}).get('name', 'tool')}"
                    f"({tc.get('function', {}).get('arguments', '')})"
                    for tc in tool_calls
                )
                lines.append(f"[{role}] 调用工具: {calls}")
            else:
                lines.append(f"[{role}] (空)")
        elif isinstance(content, str):
            lines.append(f"[{role}] {content}")
        elif isinstance(content, list):
            text = " ".join(
                str(c.get("text", "")) if isinstance(c, dict) else str(c)
                for c in content
            )
            lines.append(f"[{role}] {text}")
        else:
            lines.append(f"[{role}] {content}")
    return "\n\n".join(lines)




def compress_history_node(state: DataAnalyzeState) -> dict:
    messages = state.get("messages") or []
    if not messages:
        return {}

    try:
        from config import get_app_config
        budget = get_app_config().token_budget
        compressor = ContextCompressor(messages, budget)
        new_views, action = compressor.run()
        if action == "no_action":
            return {}
        return {"messages": [_dict_to_msg(v) for v in new_views]}
    except Exception as e:
        print(f"[compress_history] 压缩失败，messages 原样保留：{e}", file=sys.stderr)
        return {}



