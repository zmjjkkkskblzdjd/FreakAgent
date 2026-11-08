from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from config import MemoryConfig
from core import QATaskContext
from core.llm import ask_llm_json
from core.types import MemoryRecord
from memory.long_term import LongTermMemory

_ARCHIVE_PROMPT = """你是一个记忆抽取器。从下面的问答中抽取值得长期记住的用户事实/偏好/项目信息。

规则：
1. 只抽取关于用户的稳定事实（如"用户在做 RAG 项目"、"用户偏好简洁回答"），不要抽取一次性的问题细节
2. 每条记忆打 importance 分（0.0-1.0）：核心项目信息 >0.7，偏好 0.5-0.7，边缘事实 <0.5
3. type 取值：fact（事实）/ preference（偏好）/ project（项目）/ other
4. 没有值得记住的内容就返回空列表

输出 JSON 格式（只输出 JSON，不要其他文字）：
{"memories": [{"content": "用户在做 RAG 项目", "type": "project", "importance": 0.8}]}
"""

_VALID_TYPES = ("fact", "preference", "project", "other")


def archive_session(
    ctx: QATaskContext,
    memory: LongTermMemory,
    *,
    user_id: str = "default",
) -> dict:
    answer = ctx.draft.answer if ctx.draft is not None else ""
    if not ctx.question or not answer:
        return {"ok": True, "records": []}

    user_content = f"问题：{ctx.question}\n回答：{answer}"

    try:
        result = ask_llm_json(
            system_prompt=_ARCHIVE_PROMPT,
            user_content=user_content,
        )
    except Exception as e:
        return {"ok": False, "error": f"LLM 抽取失败：{e}"}

    memories = result.get("memories", [])
    if not isinstance(memories, list):
        return {"ok": False, "error": "LLM 返回的 memories 不是列表"}

    config = memory.config or MemoryConfig()
    now = datetime.now(timezone.utc).isoformat()
    records: list[MemoryRecord] = []

    for item in memories:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        try:
            importance = float(item.get("importance", 0.5))
        except (TypeError, ValueError):
            importance = 0.5
        if importance < config.min_importance:
            continue
        record_type = item.get("type", "other")
        if record_type not in _VALID_TYPES:
            record_type = "other"
        record_id = hashlib.md5(
            f"{content}{user_id}".encode("utf-8")
        ).hexdigest()
        records.append(MemoryRecord(
            id=record_id,
            user_id=user_id,
            session_id=ctx.session_id,
            content=content,
            type=record_type,
            importance=importance,
            created_at=now,
            last_accessed_at=now,
            access_count=0,
            status="active",
        ))

    return {"ok": True, "records": records}
