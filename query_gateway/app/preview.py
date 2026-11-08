# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass

from . import config


def sql_hash(sql: str) -> str:
    return hashlib.sha256((sql or "").encode("utf-8")).hexdigest()[:32]


@dataclass
class Preview:
    token: str
    tenant_id: int
    user_id: int
    datasource_id: int
    sql_hash: str
    estimated_rows: int
    expires_at: float
    used: bool = False
    data: dict | None = None


class PreviewError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class PreviewStore:
    def __init__(self, ttl: int | None = None, rate_per_min: int | None = None) -> None:
        self._ttl = int(config.PREVIEW_TTL_SEC if ttl is None else ttl)
        self._rate = int(config.PREVIEW_RATE_PER_MIN if rate_per_min is None else rate_per_min)
        self._items: dict[str, Preview] = {}
        self._issued: dict[int, deque[float]] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for token in [t for t, p in self._items.items() if p.expires_at < now or p.used]:
            self._items.pop(token, None)

    def issue(self, *, tenant_id: int, user_id: int, datasource_id: int,
              sql: str, estimated_rows: int, data: dict | None = None) -> str:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            marks = self._issued.setdefault(user_id, deque())
            while marks and now - marks[0] >= 60:
                marks.popleft()
            if len(marks) >= self._rate:
                raise PreviewError("preview_rate_limited",
                                   f"预检过于频繁（上限 {self._rate} 次/分钟）", 429)
            marks.append(now)
            token = uuid.uuid4().hex
            self._items[token] = Preview(
                token=token, tenant_id=tenant_id, user_id=user_id,
                datasource_id=datasource_id, sql_hash=sql_hash(sql),
                estimated_rows=int(estimated_rows),
                expires_at=now + self._ttl, data=data)
            return token

    def consume(self, token: str, *, tenant_id: int, user_id: int,
                datasource_id: int, sql: str) -> Preview:
        now = time.monotonic()
        with self._lock:
            entry = self._items.get(token)
            if entry is None or entry.used or entry.expires_at < now:
                raise PreviewError("preview_token_used_or_expired",
                                   "预检令牌不存在、已被使用或已过期，请重新预检")
            entry.used = True
            self._items.pop(token, None)
        if (entry.tenant_id != tenant_id or entry.user_id != user_id
                or entry.datasource_id != datasource_id):
            raise PreviewError("preview_token_mismatch", "预检令牌与调用方/数据源不匹配")
        if entry.sql_hash != sql_hash(sql):
            raise PreviewError("preview_token_mismatch", "执行 SQL 与预检时不一致，请重新预检")
        return entry

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._issued.clear()


previews = PreviewStore()


class WriteLocks:

    def __init__(self, timeout: float | None = None) -> None:
        self._timeout = float(config.WRITE_LOCK_TIMEOUT_SEC if timeout is None else timeout)
        self._locks: dict[tuple[int, str], threading.Lock] = {}
        self._guard = threading.Lock()

    def acquire(self, datasource_id: int, table: str) -> threading.Lock | None:
        key = (datasource_id, table)
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        if lock.acquire(timeout=self._timeout):
            return lock
        return None

    @staticmethod
    def release(lock: threading.Lock) -> None:
        if lock.locked():
            lock.release()


write_locks = WriteLocks()
