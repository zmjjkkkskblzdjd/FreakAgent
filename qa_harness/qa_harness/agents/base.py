from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from core import DEFAULT_MODEL_NAME, ask_llm_json, call_llm, emit_trace, get_api_key
from tools import PermissionLevel, ToolRegistry
from agents.prompts import get_prompt

_TOOL_LOOP_MAX_TOKENS = int(os.getenv("QA_TOOL_LOOP_MAX_TOKENS", "8000"))

_TOOL_PREVIEW_CHARS = int(os.getenv("QA_TOOL_PREVIEW_CHARS", "800"))
_TOOL_MAX_ITEMS = int(os.getenv("QA_TOOL_MAX_ITEMS", "10"))
_TOOL_SOURCES_MAX = int(os.getenv("QA_TOOL_SOURCES_MAX", "50"))
_TOOL_RESULT_MAX_CHARS = int(os.getenv("QA_TOOL_RESULT_MAX_CHARS", "8000"))
_TRUNCATE_HINT = "…（正文共 {n} 字，需要全文请调 get_chunk）"

_CHUNK_MAX_CHARS = int(os.getenv("QA_CHUNK_MAX_CHARS", "3000"))

_GET_CHUNK_PER_TURN = int(os.getenv("QA_GET_CHUNK_PER_TURN", "4"))

_EMPTY_RETRY_TOKENS = int(os.getenv("QA_EMPTY_RETRY_TOKENS", "16000"))


def _slim_evidences(evidences: list[Any], preview_chars: int = _TOOL_PREVIEW_CHARS,
                    max_items: int = _TOOL_MAX_ITEMS) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ch in evidences[:max_items]:
        if not isinstance(ch, dict):
            continue
        item = {k: ch[k] for k in
                ("chunk_id", "source", "doc_type", "chunk_index", "score", "match_type")
                if k in ch}
        content = str(ch.get("content") or "")
        if len(content) > preview_chars:
            content = content[:preview_chars] + _TRUNCATE_HINT.format(n=len(content))
        item["content"] = content
        out.append(item)
    return out

_TOOL_LOOP_SUFFIX = (
    "\n\n【本轮工作方式】\n"
    "1. 你可以多轮调用工具白名单内的工具收集信息，每一步都必须基于真实工具结果，禁止编造\n"
    "2. 信息收集完成后，你的最后一条回复必须且只能是本角色提示词规定的 JSON 对象\n"
    "3. 最后一条回复不要带 markdown 代码块或任何解释性文字"
)

_JSON_ONLY_SUFFIX = (
    "\n\n【系统】工具调用已结束，禁止再调用任何工具。\n"
    "现在只输出一个合法 JSON 对象：不要解释、不要 markdown 代码块、不要说\"以下是\"，\n"
    "第一个字符必须是 {，最后一个字符必须是 }；字符串内换行用 \\n 转义。\n"
    "JSON 内容必须严格符合上面系统提示里规定的字段结构。"
)


@dataclass
class AgentProfile:
    role: str                            
    role_description: str                
    allowed_tools: tuple[str, ...]       
    permission: PermissionLevel          


def _parse_json_object(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.splitlines()
        if len(lines) >= 3:
            clean = "\n".join(lines[1:-1]).strip()
        else:
            clean = clean.replace("```", "").strip()
    try:
        data = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"JSON 根节点不是对象，而是 {type(data).__name__}")
    return data


class BaseAgent:

    profile: AgentProfile

    def __init__(
        self,
        profile: AgentProfile,
        tool_registry: ToolRegistry | None = None,
        max_loops: int = 6,
        model_name: str = DEFAULT_MODEL_NAME,
        chunk_max_chars: int = _CHUNK_MAX_CHARS,
    ) -> None:
        self.profile = profile
        self.max_loops = max_loops
        self.model_name = model_name
        self._chunk_max_chars = chunk_max_chars
        self.tool_trace: list[dict[str, Any]] = []

        if profile.allowed_tools and profile.permission == PermissionLevel.NONE:
            raise ValueError(
                f"角色 {profile.role} 岗位配置矛盾：allowed_tools 非空（{profile.allowed_tools}）"
                "但权限为 NONE"
            )

        self.tool_registry = self._scope_registry(tool_registry)

    def _scope_registry(self, registry: ToolRegistry | None) -> ToolRegistry | None:
        if not self.profile.allowed_tools:
            return None
        if registry is None:
            raise ValueError(
                f"角色 {self.profile.role} 需要工具 {self.profile.allowed_tools}，"
                "但构造时没有传入 tool_registry"
            )

        missing = [name for name in self.profile.allowed_tools if registry.get(name) is None]
        if missing:
            raise ValueError(
                f"工具注册表缺少 {self.profile.role} 白名单内的工具：{missing}"
            )

        scoped = ToolRegistry()
        for name in self.profile.allowed_tools:
            scoped.register(registry.get(name))
        return scoped

    def run(self, task_summary: str) -> dict[str, Any]:
        self.tool_trace = []  
        try:
            if self.tool_registry is None:
                raw = ask_llm_json(get_prompt(self.profile.role), task_summary,
                                   model_name=self.model_name)
            else:
                raw = self._run_tool_loop(task_summary)
        except Exception as e:
            return {"ok": False, "error": f"LLM 调用失败：{e}"}

        if not isinstance(raw, dict):
            return {"ok": False, "error": f"LLM 输出不是 JSON 对象：{raw!r}"}

        return self._validate_and_build(raw)

    def _trace_turn(self, turn: int, started: float, outcome: str,
                    assistant: dict[str, Any],
                    tool_calls: list[dict[str, Any]],
                    error: str = "", status: str = "") -> None:
        ok = status or ("ok" if outcome in ("tool_call", "json_ok") else "error")
        emit_trace({
            "kind": "node", "node": "qa_turn",
            "status": ok,
            "error_code": "" if ok == "ok" else outcome,
            "model": self.model_name,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "attrs": {
                "role": self.profile.role,
                "turn": turn + 1,
                "max_loops": self.max_loops,
                "outcome": outcome,
                "tools": [str((tc.get("function") or {}).get("name") or "")
                          for tc in tool_calls],
                "content_len": len(str(assistant.get("content") or "")),
                "error": str(error)[:200],
            },
        })

    def _slim_tool_message(self, name: str, result: Any) -> str:
        slim: Any = result
        if isinstance(result, dict):
            if name in ("retrieve_knowledge", "retrieve_batch", "web_search"):
                evidences = result.get("evidences")
                if isinstance(evidences, list):
                    slim = {**result, "evidences": _slim_evidences(evidences),
                            "evidences_total": len(evidences)}
            elif name == "get_chunk":
                single = result.get("evidence")
                if isinstance(single, dict):
                    content = str(single.get("content") or "")
                    if len(content) > self._chunk_max_chars:
                        slim = {**result, "evidence": {
                            **single,
                            "content": content[:self._chunk_max_chars]
                            + f"…（片段共 {len(content)} 字，已截断）",
                        }}
            elif name == "list_sources":
                sources = result.get("sources")
                if isinstance(sources, list) and len(sources) > _TOOL_SOURCES_MAX:
                    slim = {**result,
                            "sources": sources[:_TOOL_SOURCES_MAX],
                            "sources_total": len(sources),
                            "truncated": f"仅列前 {_TOOL_SOURCES_MAX} 个，共 {len(sources)} 个"}
        text = json.dumps(slim, ensure_ascii=False)
        if len(text) > _TOOL_RESULT_MAX_CHARS:
            text = (text[:_TOOL_RESULT_MAX_CHARS]
                    + "…（结果过长已截断；按 chunk_id 调 get_chunk 取全文）")
        return text

    def _run_tool_loop(self, task_summary: str) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": get_prompt(self.profile.role)},
            {"role": "user", "content": task_summary + _TOOL_LOOP_SUFFIX},
        ]
        tools_payload = self.tool_registry.build_tools_payload()

        for turn in range(self.max_loops):
            _turn_t0 = time.perf_counter()
            final_turn = turn == self.max_loops - 1
            if final_turn:
                messages.append({
                    "role": "user",
                    "content": "【系统】工具调用额度即将用尽：本轮必须直接输出最终 JSON，禁止再调用任何工具。",
                })
            assistant = call_llm(
                api_key=get_api_key(),
                messages=messages,
                tools=tools_payload,
                model_name=self.model_name,
                tool_choice="none" if final_turn else "auto",
                max_completion_tokens=_TOOL_LOOP_MAX_TOKENS,
            )
            tool_calls = assistant.get("tool_calls") or []

            if tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": assistant.get("content"),
                    "tool_calls": tool_calls,
                })
                chunk_calls = 0
                for tool_call in tool_calls:
                    call_name = str((tool_call.get("function") or {}).get("name") or "")
                    if call_name == "get_chunk":
                        chunk_calls += 1
                        if chunk_calls > _GET_CHUNK_PER_TURN:
                            name = call_name
                            result = {
                                "ok": False,
                                "error": (f"本轮最多取 {_GET_CHUNK_PER_TURN} 个片段全文，"
                                          "剩余 chunk_id 请下一轮再取"),
                            }
                        else:
                            name, result = self.tool_registry.execute_tool_call(tool_call)
                    else:
                        name, result = self.tool_registry.execute_tool_call(tool_call)
                    try:
                        arguments = json.loads(
                            tool_call.get("function", {}).get("arguments", "{}")
                        )
                    except json.JSONDecodeError:
                        arguments = {}
                    self.tool_trace.append({"name": name, "arguments": arguments, "result": result})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "content": self._slim_tool_message(name, result),
                    })
                self._trace_turn(turn, _turn_t0, "tool_call", assistant, tool_calls)
                continue

            text = (assistant.get("content") or "").strip()
            if not text:
                self._trace_turn(turn, _turn_t0, "empty_content", assistant, [],
                                 "empty_content")
                assistant = call_llm(
                    api_key=get_api_key(),
                    messages=messages,
                    tools=tools_payload,
                    model_name=self.model_name,
                    tool_choice="none",
                    max_completion_tokens=_EMPTY_RETRY_TOKENS,
                )
                text = (assistant.get("content") or "").strip()
                self._trace_turn(turn, _turn_t0, "empty_retry",
                                 assistant, [], "" if text else "empty_content",
                                 status="ok" if text else "error")
                if not text:
                    if self.tool_trace:
                        return self._final_json_only(task_summary)
                    continue
            messages.append({"role": "assistant", "content": text})
            try:
                parsed = _parse_json_object(text)
                self._trace_turn(turn, _turn_t0, "json_ok", assistant, [])
                return parsed
            except ValueError as exc:
                self._trace_turn(turn, _turn_t0, "json_parse_fail", assistant, [], str(exc))
                messages.append({
                    "role": "user",
                    "content": (
                        f"你上一条回复无法解析为约定的 JSON 对象：{exc}。\n"
                        "重要：你已经完成了所有必要的工具调用，现在必须输出最终结果。\n"
                        "请严格按照以下要求输出：\n"
                        "1. 只输出一个合法的 JSON 对象，不要有任何其他文字\n"
                        "2. 不要用 ```json 或 ``` 代码块包裹\n"
                        "3. 不要加解释、不要加备注、不要说\"以下是...\"\n"
                        "4. JSON 的第一个字符必须是 {，最后一个字符必须是 }\n"
                        "5. 字符串内的换行符请用 \\n 转义，双引号用 \\\" 转义"
                    ),
                })

        return self._final_json_only(task_summary)


    def _final_json_only(self, task_summary: str) -> dict[str, Any]:
        if not self.tool_trace:
            raise RuntimeError(
                f"{self.profile.role} 工具循环未产出任何结果，无法生成最终 JSON"
            )
        _final_t0 = time.perf_counter()
        assistant = call_llm(
            api_key=get_api_key(),
            messages=[
                {"role": "system", "content": get_prompt(self.profile.role)},
                {"role": "user", "content": (
                    task_summary
                    + "\n\n【本轮工具真实返回的片段（据此输出最终 JSON）】\n"
                    + self._tool_digest()
                    + _JSON_ONLY_SUFFIX
                )},
            ],
            tools=[],
            model_name=self.model_name,
            tool_choice="none",
            max_completion_tokens=8000,   
        )
        text = (assistant.get("content") or "").strip()
        emit_trace({
            "kind": "node", "node": "qa_turn.final_json",
            "status": "ok" if text else "error",
            "error_code": "" if text else "empty_content",
            "model": self.model_name,
            "completion_tokens": len(text) // 4,
            "duration_ms": int((time.perf_counter() - _final_t0) * 1000),
            "attrs": {"role": self.profile.role,
                      "content_len": len(text),
                      "tool_trace_items": len(self.tool_trace)},
        })
        if not text:
            raise RuntimeError(f"{self.profile.role} 最终 JSON 输出为空（纯 JSON 兜底仍失败）")
        return _parse_json_object(text)

    def _tool_digest(self, per_content: int = 600, max_items: int = 8) -> str:
        lines: list[str] = []
        seen: set[str] = set()
        for entry in self.tool_trace:
            result = entry.get("result")
            if not isinstance(result, dict) or not result.get("ok"):
                continue
            chunks = result.get("evidences")
            if not isinstance(chunks, list):
                single = result.get("evidence")
                chunks = [single] if isinstance(single, dict) else []
            for ch in chunks:
                if not isinstance(ch, dict):
                    continue
                cid = ch.get("chunk_id")
                if not isinstance(cid, str) or not cid or cid in seen:
                    continue
                seen.add(cid)
                lines.append(
                    f"- chunk_id={cid} | source={ch.get('source', 'unknown')} "
                    f"| score={ch.get('score', 0.0)}\n"
                    f"  片段：{str(ch.get('content', ''))[:per_content]}"
                )
                if len(lines) >= max_items:
                    return "\n".join(lines)
        return "\n".join(lines) if lines else "（本轮无可用片段）"

    def _validate_and_build(self, raw: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(f"{self.profile.role} 未实现 _validate_and_build")
