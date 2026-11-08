from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

from .config import config


def format_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def format_heartbeat() -> str:
    return ": heartbeat\n\n"


async def event_stream(
    task_id: str,
    manager,
    terminal_states: tuple = ("succeeded", "failed", "partial_success", "cancelled"),
) -> AsyncGenerator[str, None]:
    queue = manager.subscribe(task_id)
    try:
        task = manager.get(task_id)
        resp = task.response
        yield format_event("task_snapshot", {
            "task_id": task_id,
            "status": resp.status.value,
            "completed_files": resp.completed_files,
            "failed_files": resp.failed_files,
            "total_files": resp.total_files,
        })
        if resp.status.value in terminal_states:
            return

        while True:
            try:
                payload = await asyncio.wait_for(
                    queue.get(), timeout=config.SSE_HEARTBEAT
                )
            except asyncio.TimeoutError:
                yield format_heartbeat()
                continue
            yield format_event(payload["event"], payload["data"])
            if (
                payload["event"] == "task_completed"
                and payload["data"].get("status") in terminal_states
            ):
                return
    finally:
        manager.unsubscribe(task_id, queue)
