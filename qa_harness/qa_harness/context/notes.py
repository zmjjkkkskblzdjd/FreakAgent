from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from core import TIER_ORDER
from core.llm import DEFAULT_MODEL_NAME, ask_llm_json


_FULL_UPDATE_SYSTEM_PROMPT = """你是对话笔记管理员。根据新增的对话内容，更新结构化笔记。

规则：
1. 只输出需要修改的字段，不需要修改的字段不要输出
2. 列表类型的字段（topics_discussed、completed_actions、open_questions、
   sub_questions_done、sub_questions_pending），输出完整的新列表（去重后），
   而不是追加项
3. dict 类型的字段（user_preferences），输出完整的新 dict
4. 字符串字段直接输出新值
5. 如果某个字段不需要更新，就不要包含它
6. 输出必须是纯 JSON，不要任何解释文字、不要 markdown 代码块
7. knowledge_areas 和 questions_answered 由代码自动维护，禁止输出这两个字段：
   knowledge_areas 中是机器标签（知识库文档名，或 "联网·域名" 形式的网页指纹，
   如 "联网·blog.csdn.net"），用于系统内部的话题切换检测，不要翻译、改写或删除；
   话题的语义概括请写入 topics_discussed

字段说明：
- user_goal：用户整体目标，一句话概括
- user_intent：主要意图类型（学习/调试/查询/闲聊/其他）
- user_preferences：用户偏好字典（如 {"detail_level": "high", "language": "chinese"}）
- topics_discussed：讨论过的主题列表（语义化概括，去重）
- completed_actions：已完成的关键动作列表
- open_questions：未解决/遗留问题列表
- next_action：预期下一步
- sub_questions_done：已完成的子问题列表（deep 档）
- sub_questions_pending：待处理的子问题列表（deep 档）
"""


@dataclass
class ConversationNotes:
    session_id: str
    created_at: str
    updated_at: str
    turn_count: int = 0                   
    full_update_count: int = 0            
    last_full_update_turn: int = 0        
    pending_event_reason: str = ""        
    version: int = 1                      

    user_goal: str = ""                   
    user_intent: str = ""                 
    user_preferences: dict[str, str] = field(default_factory=dict)  
    topics_discussed: list[str] = field(default_factory=list)       

    last_route: str = ""                  
    last_tier: str = ""                   
    knowledge_areas: list[str] = field(default_factory=list)  
    questions_answered: list[str] = field(default_factory=list) 

    completed_actions: list[str] = field(default_factory=list)  
    open_questions: list[str] = field(default_factory=list)     
    next_action: str = ""                                        

    sub_questions_done: list[str] = field(default_factory=list)     
    sub_questions_pending: list[str] = field(default_factory=list)  

    def __post_init__(self) -> None:
        self._dialogue_buffer: list[str] = []


    @classmethod
    def create(cls, session_id: str | None = None) -> "ConversationNotes":
        import uuid
        sid = session_id or str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        return cls(session_id=sid, created_at=now, updated_at=now)


    def to_markdown(self) -> str:
        lines: list[str] = []

        lines.append(f"- 轮次：{self.turn_count}")
        lines.append(f"- 上次路线：{self.last_route}/{self.last_tier}")

        if self.user_goal:
            lines.append(f"- 用户目标：{self.user_goal}")
        if self.user_intent:
            lines.append(f"- 主要意图：{self.user_intent}")
        if self.user_preferences:
            prefs = ", ".join(f"{k}={v}" for k, v in self.user_preferences.items())
            lines.append(f"- 用户偏好：{prefs}")
        if self.topics_discussed:
            lines.append(f"- 讨论主题：{', '.join(self.topics_discussed[:10])}")
            if len(self.topics_discussed) > 10:
                lines[-1] += f" 等 {len(self.topics_discussed)} 个"
        if self.knowledge_areas:
            lines.append(f"- 知识领域：{', '.join(self.knowledge_areas[:8])}")
            if len(self.knowledge_areas) > 8:
                lines[-1] += f" 等 {len(self.knowledge_areas)} 个"
        if self.questions_answered:
            lines.append(f"- 已答问题数：{len(self.questions_answered)}")
        if self.completed_actions:
            lines.append(f"- 已完成动作：{len(self.completed_actions)} 项")
        if self.open_questions:
            lines.append(f"- 待解决问题：{len(self.open_questions)} 项")
            for q in self.open_questions[:3]:
                lines.append(f"  - {q}")
        if self.next_action:
            lines.append(f"- 下一步：{self.next_action}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


    def light_update(
        self,
        *,
        route: str,
        tier: str,
        question_summary: str,
        knowledge_areas: list[str] | None = None,
        topics: list[str] | None = None,
    ) -> None:
        prev_tier = self.last_tier

        self.turn_count += 1
        self.last_route = route
        self.last_tier = tier
        self.updated_at = datetime.now(timezone.utc).isoformat()

        summary = question_summary.strip()
        if summary:
            short = summary[:20]
            if not any(q[:20] == short for q in self.questions_answered):
                self.questions_answered.append(summary[:80])

        if knowledge_areas:
            incoming = [ka for ka in knowledge_areas if ka]
            if (
                self.knowledge_areas
                and incoming
                and not (set(incoming) & set(self.knowledge_areas))
            ):
                self._mark_event(f"知识领域切换到 {'、'.join(incoming[:3])}")
            for ka in incoming:
                if ka not in self.knowledge_areas:
                    self.knowledge_areas.append(ka)

        if topics:
            for t in topics:
                if t and t not in self.topics_discussed:
                    self.topics_discussed.append(t)

        if (
            prev_tier
            and tier == "deep"
            and TIER_ORDER.get(tier, 0) > TIER_ORDER.get(prev_tier, 99)
        ):
            self._mark_event(f"档位从 {prev_tier} 升级到 deep")

    def _mark_event(self, reason: str) -> None:
        if self.pending_event_reason:
            if reason not in self.pending_event_reason:
                self.pending_event_reason += f"；{reason}"
        else:
            self.pending_event_reason = reason

    def _consume_update_trigger(self) -> None:
        self.last_full_update_turn = self.turn_count
        self.pending_event_reason = ""

    def needs_full_update(self, interval: int = 5) -> bool:
        if self.turn_count <= 0:
            return False
        if self.last_full_update_turn == 0:
            return True
        if self.pending_event_reason:
            return True
        return self.turn_count - self.last_full_update_turn >= interval


    def add_dialogue_turn(self, turn_text: str) -> None:
        text = turn_text.strip()
        if text:
            self._dialogue_buffer.append(text)

    def full_update(
        self,
        recent_dialogue_text: str | None = None,
        *,
        model_name: str = DEFAULT_MODEL_NAME,
    ) -> bool:
        from_buffer = recent_dialogue_text is None
        if from_buffer:
            recent_dialogue_text = "\n\n".join(self._dialogue_buffer)

        if not recent_dialogue_text.strip():
            return False

        self._consume_update_trigger()
        if from_buffer:
            self._dialogue_buffer.clear()

        current_dict = self.to_dict()
        slim_dict = {
            k: v for k, v in current_dict.items()
            if k not in (
                "questions_answered",
                "last_full_update_turn",
                "pending_event_reason",
            )
        }

        user_content = (
            f"当前笔记（JSON）：\n"
            f"{json.dumps(slim_dict, ensure_ascii=False, indent=2)}\n\n"
            f"新增对话内容：\n"
            f"{recent_dialogue_text}\n\n"
            f"请输出需要更新的字段（纯 JSON）："
        )

        try:
            updates = ask_llm_json(
                system_prompt=_FULL_UPDATE_SYSTEM_PROMPT,
                user_content=user_content,
                model_name=model_name,
            )
        except Exception as e:
            print(f"[notes] full_update LLM 调用失败：{e}", file=sys.stderr)
            return False

        if not isinstance(updates, dict):
            print(f"[notes] full_update 返回不是 dict：{type(updates)}", file=sys.stderr)
            return False

        applied = self._apply_updates(updates)
        if applied:
            self.full_update_count += 1
            self.updated_at = datetime.now(timezone.utc).isoformat()
        return applied

    def _apply_updates(self, updates: dict[str, Any]) -> bool:
        changed = False
        for key, value in updates.items():
            if not hasattr(self, key):
                continue
            current = getattr(self, key)
            if isinstance(current, list) and not isinstance(value, list):
                continue
            if isinstance(current, dict) and not isinstance(value, dict):
                continue
            if isinstance(current, str) and not isinstance(value, str):
                continue
            if isinstance(current, int) and not isinstance(value, int):
                continue
            if key in (
                "session_id", "created_at", "version",
                "turn_count", "full_update_count",
                "last_full_update_turn", "pending_event_reason",
            ):
                continue
            if key in ("questions_answered", "knowledge_areas"):
                continue
            if current == value:
                continue
            setattr(self, key, value)
            changed = True
        return changed
