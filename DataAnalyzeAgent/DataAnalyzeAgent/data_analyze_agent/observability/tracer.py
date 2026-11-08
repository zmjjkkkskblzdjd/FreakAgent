
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from config import PROJECT_ROOT, get_app_config
from data_analyze_agent.core import CallLogEntry

__all__ = ["TraceEvent", "Tracer", "get_tracer"]


@dataclass
class TraceEvent:                
    event_id: str
    thread_id: str
    session_id: str
    seq: int
    node: str
    kind: Literal["node_enter", "node_exit", "llm_call", "tool_call",
                  "review_block", "runtime_error", "repair", "escalation",
                  "clarify_suspend", "approval_suspend", "approval_resume", "finalize"]
    started_at: str
    duration_ms: int
    status: str
    detail: dict = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0


class Tracer:

    def __init__(self, path: str | Path | None = None):
        p = Path(path) if path else Path(get_app_config().paths.log_path)
        self.path = p if p.is_absolute() else PROJECT_ROOT / p
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def emit(self, event: TraceEvent) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 —— 旁路纪律：写不进就放弃这一条
            pass

    @staticmethod
    def now() -> str:
        return datetime.now().isoformat(timespec="seconds")


_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    global _tracer
    if _tracer is None:
        _tracer = Tracer()
    return _tracer
