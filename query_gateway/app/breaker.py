# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import time

from . import config

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class _Entry:
    __slots__ = ("state", "fails", "opened_at", "open_sec", "probing")

    def __init__(self) -> None:
        self.state = CLOSED
        self.fails = 0
        self.opened_at = 0.0
        self.open_sec = 0.0
        self.probing = False


class BreakerRegistry:
    def __init__(self, fails: int | None = None, open_sec: float | None = None,
                 max_open_sec: float | None = None) -> None:
        self._threshold = int(config.BREAKER_FAILS if fails is None else fails)
        self._base_open = float(config.BREAKER_OPEN_SEC if open_sec is None else open_sec)
        self._max_open = float(config.BREAKER_MAX_OPEN_SEC if max_open_sec is None
                               else max_open_sec)
        self._entries: dict[int, _Entry] = {}
        self._lock = threading.Lock()
        self.opened_total = 0

    def _entry(self, dsid: int) -> _Entry:
        entry = self._entries.get(dsid)
        if entry is None:
            entry = _Entry()
            self._entries[dsid] = entry
        return entry

    def allow(self, dsid: int) -> tuple[bool, str]:
        with self._lock:
            entry = self._entry(dsid)
            if entry.state == OPEN:
                if time.monotonic() - entry.opened_at < entry.open_sec:
                    return False, OPEN
                entry.state = HALF_OPEN
                entry.probing = True
                return True, HALF_OPEN
            if entry.state == HALF_OPEN:
                if entry.probing:
                    return False, HALF_OPEN
                entry.probing = True
                return True, HALF_OPEN
            return True, CLOSED

    def success(self, dsid: int) -> None:
        with self._lock:
            entry = self._entry(dsid)
            entry.state = CLOSED
            entry.fails = 0
            entry.open_sec = self._base_open
            entry.probing = False

    def failure(self, dsid: int) -> None:
        with self._lock:
            entry = self._entry(dsid)
            now = time.monotonic()
            if entry.state == HALF_OPEN:
                entry.open_sec = min(max(entry.open_sec, self._base_open) * 2,
                                     self._max_open)
                entry.state = OPEN
                entry.opened_at = now
                entry.probing = False
                self.opened_total += 1
                return
            entry.fails += 1
            if entry.state == CLOSED and entry.fails >= self._threshold:
                entry.state = OPEN
                entry.open_sec = self._base_open
                entry.opened_at = now
                self.opened_total += 1

    def state_name(self, dsid: int) -> str:
        with self._lock:
            entry = self._entry(dsid)
            if entry.state == OPEN and time.monotonic() - entry.opened_at >= entry.open_sec:
                return HALF_OPEN
            return entry.state

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()
            self.opened_total = 0


breaker = BreakerRegistry()
