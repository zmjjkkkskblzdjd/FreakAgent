from __future__ import annotations

from typing import Any


class MessageStore:

    def __init__(self, max_turns: int) -> None:
        self.max_turns = max_turns
        self.messages: list[dict[str, Any]] = []

    def append(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        self.trim()

    def extend(self, new_messages: list[dict[str, Any]]) -> None:
        self.messages.extend(new_messages)
        self.trim()

    def trim(self) -> None:
        max_message_count = self.max_turns * 4
        if len(self.messages) > max_message_count:
            self.messages = self.messages[-max_message_count:]

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self.messages)
