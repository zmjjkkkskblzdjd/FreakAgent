from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    capacity: float
    refill_per_sec: float
    tokens: float = field(init=False)
    last: float = field(init=False)

    def __post_init__(self):
        self.tokens = self.capacity
        self.last = time.monotonic()

    def acquire(self, n: float = 1.0) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill_per_sec)
        self.last = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


def backoff_after_429(attempt: int) -> float:
    import random
    return min(60.0, (2 ** attempt) + random.uniform(0, 0.5))


def cost_ledger_entry(lane: str, node: str, tokens_in: int, tokens_out: int, ts: float | None = None):
    return {"lane": lane, "node": node, "tokens_in": tokens_in,
            "tokens_out": tokens_out, "ts": ts or time.time()}