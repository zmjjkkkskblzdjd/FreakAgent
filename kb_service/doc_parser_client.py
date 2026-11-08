from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import httpx

DEFAULT_BASE_URL = "http://localhost:8002"
TASK_WAIT_TIMEOUT = 600.0


class DocParserError(Exception):

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.details = details or {}


def _base_url() -> str:
    return os.environ.get("DOC_PARSER_URL", DEFAULT_BASE_URL).rstrip("/")


def _raise_for_error(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    try:
        err = resp.json().get("error", {})
        raise DocParserError(
            code=err.get("code", "INTERNAL_ERROR"),
            message=err.get("message", f"HTTP {resp.status_code}"),
            details=err.get("details") or {},
        )
    except (ValueError, KeyError):
        raise DocParserError("INTERNAL_ERROR", f"HTTP {resp.status_code}: 响应非 JSON")


async def _consume_events(
        client: httpx.AsyncClient,
        events_url: str,
) -> None:
    async with client.stream("GET", events_url) as resp:
        _raise_for_error(resp)
        event_name = None
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:") and event_name == "task_completed":
                return
            elif not line.strip():
                event_name = None  


async def parse_files(
        files: list[tuple[str, bytes]],
        options: Optional[dict[str, Any]] = None,
        listen_events: bool = True,
) -> list[dict[str, Any]]:
    if not files:
        return []

    base = _base_url()
    multipart = [("files", (name, data)) for name, data in files]
    form = {"options": json.dumps(options or {}, ensure_ascii=False)}

    timeout = httpx.Timeout(connect=5.0, read=TASK_WAIT_TIMEOUT,
                            write=60.0, pool=5.0)
    async with httpx.AsyncClient(base_url=base, timeout=timeout) as client:
        resp = await client.post("/v1/tasks", files=multipart, data=form)
        _raise_for_error(resp)
        created = resp.json()
        task_id = created["task_id"]

        if listen_events:
            await _consume_events(client, created["events_url"])
        else:
            import asyncio
            while True:
                r = await client.get(created["status_url"])
                _raise_for_error(r)
                if r.json()["status"] in (
                        "succeeded", "failed", "partial_success", "cancelled"):
                    break
                await asyncio.sleep(1.0)

        result = await client.get(created["status_url"])
        _raise_for_error(result)
        body = result.json()

    if body["status"] == "cancelled":
        raise DocParserError("TASK_CANCELLED", f"任务被取消: {task_id}")
    return body["results"]


async def parse_file(path: str, options: Optional[dict[str, Any]] = None) -> str:
    p = Path(path)
    data = p.read_bytes()
    results = await parse_files([(p.name, data)], options=options)
    r = results[0]
    if r["status"] != "success":
        err = r.get("error") or {}
        raise DocParserError(
            code=err.get("code", "PARSE_FAILED"),
            message=err.get("message", f"解析失败: {p.name}"),
        )
    return r["markdown"] or ""
