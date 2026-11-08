from __future__ import annotations

import asyncio
import itertools
import json
import logging
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Optional

from . import parser
from .config import config
from .schemas import (
    ErrorCode,
    FileError,
    FileResult,
    FileState,
    ParseOptions,
    TaskResponse,
    TaskState,
    now,
)

log = logging.getLogger("doc_parser.tasks")

_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}


class QueueFullError(Exception):
    pass


class TaskNotFoundError(Exception):
    pass


class TaskGoneError(Exception):
    pass


class NotCancellableError(Exception):
    pass


@dataclass
class _FilePayload:
    file_id: str
    filename: str
    data: bytes


@dataclass
class Task:
    task_id: str
    options: ParseOptions
    files: list[_FilePayload]
    response: TaskResponse
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    gone: bool = False  


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._worker: Optional[asyncio.Task] = None
        self._reaper: Optional[asyncio.Task] = None
        self._started_at = now()


    def start(self) -> None:
        self._worker = asyncio.create_task(self._worker_loop(), name="doc_parser_worker")
        self._reaper = asyncio.create_task(self._reaper_loop(), name="doc_parser_reaper")

    async def stop(self) -> None:
        for t in (self._worker, self._reaper):
            if t:
                t.cancel()
        await asyncio.gather(
            *[t for t in (self._worker, self._reaper) if t],
            return_exceptions=True,
        )

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def running_tasks(self) -> int:
        return sum(1 for t in self._tasks.values() if t.response.status == TaskState.RUNNING)

    @property
    def uptime_s(self) -> float:
        return now() - self._started_at


    def submit(self, files: list[tuple[str, bytes]], options: ParseOptions) -> Task:
        if self._queue.qsize() >= config.MAX_QUEUE_SIZE:
            raise QueueFullError(f"队列已满（上限 {config.MAX_QUEUE_SIZE}）")

        task_id = uuid.uuid4().hex
        payloads = [
            _FilePayload(file_id=uuid.uuid4().hex, filename=name, data=data)
            for name, data in files
        ]
        response = TaskResponse(
            task_id=task_id,
            status=TaskState.PENDING,
            created_at=now(),
            total_files=len(payloads),
            results=[
                FileResult(file_id=p.file_id, filename=p.filename) for p in payloads
            ],
        )
        task = Task(task_id=task_id, options=options, files=payloads, response=response)
        self._tasks[task_id] = task
        self._queue.put_nowait(
            (_PRIORITY_RANK[options.priority], next(self._seq), task_id)
        )
        return task

    def get(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        if task.gone:
            raise TaskGoneError(task_id)
        return task

    def cancel(self, task_id: str) -> Task:
        task = self.get(task_id)
        if task.response.status not in (TaskState.PENDING, TaskState.RUNNING):
            raise NotCancellableError(task_id)
        task.cancel_event.set()
        return task


    def subscribe(self, task_id: str) -> asyncio.Queue:
        task = self.get(task_id)
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        task.subscribers.add(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.subscribers.discard(q)

    def _emit(self, task: Task, event: str, data: dict) -> None:
        payload = {"event": event, "data": data}
        for q in list(task.subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                log.warning("task %s: subscriber queue full, drop %s", task.task_id, event)


    async def _worker_loop(self) -> None:
        while True:
            _, _, task_id = await self._queue.get()
            task = self._tasks.get(task_id)
            if task is None:
                continue
            try:
                await self._run_task(task)
            except Exception:
                log.exception("task %s: worker crashed", task_id)
                task.response.status = TaskState.FAILED
                task.response.finished_at = now()
            finally:
                self._queue.task_done()

    async def _run_task(self, task: Task) -> None:
        resp = task.response

        if task.cancel_event.is_set():
            self._finalize_cancelled(task)
            return

        resp.status = TaskState.RUNNING
        resp.started_at = now()
        self._emit(task, "task_started", {
            "task_id": task.task_id,
            "total_files": resp.total_files,
        })

        for payload, file_result in zip(task.files, resp.results):
            if task.cancel_event.is_set():
                for remaining in resp.results:
                    if remaining.status == FileState.PENDING:
                        remaining.status = FileState.SKIPPED
                self._finalize_cancelled(task)
                return

            file_result.status = FileState.RUNNING
            self._emit(task, "file_started", {
                "task_id": task.task_id,
                "file_id": payload.file_id,
                "filename": payload.filename,
            })

            try:
                outcome = await parser.convert_to_markdown(
                    payload.data, payload.filename, task.options
                )
            except parser.ParseTimeoutError as exc:
                self._fail_file(task, file_result, ErrorCode.PARSE_TIMEOUT, str(exc))
                continue
            except parser.ParseFailedError as exc:
                self._fail_file(task, file_result, ErrorCode.PARSE_FAILED, str(exc))
                continue
            except Exception:
                log.exception("task %s file %s: unexpected error", task.task_id, payload.file_id)
                self._fail_file(task, file_result, ErrorCode.INTERNAL_ERROR, "内部错误")
                continue

            file_result.status = FileState.SUCCESS
            file_result.markdown = outcome.markdown
            file_result.page_count = outcome.page_count
            file_result.duration_ms = outcome.duration_ms
            resp.completed_files += 1
            self._emit(task, "file_completed", {
                "task_id": task.task_id,
                "file_id": payload.file_id,
                "filename": payload.filename,
                "page_count": outcome.page_count,
                "duration_ms": outcome.duration_ms,
                "completed_files": resp.completed_files,
                "total_files": resp.total_files,
            })

        resp.finished_at = now()
        if resp.failed_files == 0:
            resp.status = TaskState.SUCCEEDED
        elif resp.completed_files == 0:
            resp.status = TaskState.FAILED
        else:
            resp.status = TaskState.PARTIAL_SUCCESS
        resp.expires_at = now() + config.TASK_TTL
        self._emit(task, "task_completed", {
            "task_id": task.task_id,
            "status": resp.status.value,
            "completed_files": resp.completed_files,
            "failed_files": resp.failed_files,
            "total_files": resp.total_files,
        })
        self._fire_callback(task)

    def _fail_file(self, task: Task, file_result: FileResult,
                   code: ErrorCode, message: str) -> None:
        resp = task.response
        file_result.status = FileState.FAILED
        file_result.error = FileError(code=code, message=message)
        resp.failed_files += 1
        self._emit(task, "file_failed", {
            "task_id": task.task_id,
            "file_id": file_result.file_id,
            "filename": file_result.filename,
            "error": {"code": code.value, "message": message},
            "failed_files": resp.failed_files,
            "total_files": resp.total_files,
        })

    def _finalize_cancelled(self, task: Task) -> None:
        resp = task.response
        if resp.status == TaskState.RUNNING:
            for fr in resp.results:
                if fr.status == FileState.RUNNING:
                    fr.status = FileState.SKIPPED
        resp.status = TaskState.CANCELLED
        resp.finished_at = now()
        resp.expires_at = now() + config.TASK_TTL
        self._emit(task, "task_completed", {
            "task_id": task.task_id,
            "status": TaskState.CANCELLED.value,
            "completed_files": resp.completed_files,
            "failed_files": resp.failed_files,
            "total_files": resp.total_files,
        })

    def _fire_callback(self, task: Task) -> None:
        url = task.options.callback_url
        if not url:
            return
        body = json.dumps(task.response.model_dump(mode="json")).encode("utf-8")

        def _post() -> None:
            try:
                req = urllib.request.Request(
                    url, data=body, headers={"Content-Type": "application/json"}
                )
                urllib.request.urlopen(req, timeout=10).close()
            except Exception:
                log.warning("task %s: callback to %s failed", task.task_id, url)

        asyncio.get_running_loop().run_in_executor(None, _post)


    async def _reaper_loop(self) -> None:
        _TOMBSTONE_KEEP = 24 * 3600
        while True:
            await asyncio.sleep(60)
            expired, deleted = 0, 0
            for task_id in list(self._tasks.keys()):
                task = self._tasks.get(task_id)
                if task is None:
                    continue
                resp = task.response
                if task.gone:
                    if resp.finished_at and now() > resp.finished_at + _TOMBSTONE_KEEP:
                        del self._tasks[task_id]
                        deleted += 1
                    continue
                if (
                    resp.expires_at is not None
                    and now() > resp.expires_at
                    and resp.status
                    in (TaskState.SUCCEEDED, TaskState.FAILED,
                        TaskState.PARTIAL_SUCCESS, TaskState.CANCELLED)
                ):
                    task.files = []
                    for fr in resp.results:
                        fr.markdown = None
                    task.gone = True
                    expired += 1
            if expired or deleted:
                log.info("reaper: %d tasks expired (results purged), %d tombstones deleted",
                         expired, deleted)


task_manager = TaskManager()
