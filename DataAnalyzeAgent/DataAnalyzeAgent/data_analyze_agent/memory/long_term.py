from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from data_analyze_agent.core import MemoryRecord
from config import get_app_config
from data_analyze_agent.knowledge.embeddings import get_embedding

_ALLFUSION_ROOT = str(Path(__file__).resolve().parents[4])
if _ALLFUSION_ROOT not in sys.path:
    sys.path.insert(0, _ALLFUSION_ROOT)

from memory_service.store import MemoryStore  # noqa: E402  共享记忆底座

_VALID_TYPES = ("rag", "sql", "general")


def _memory_user() -> str:
    return (get_app_config().memory.user_id or "local").strip() or "local"


def _wrap_embedding():
    return lambda docs: [get_embedding(d) for d in docs]


class LongTermMemory:

    def __init__(self) -> None:
        self.cfg = get_app_config()          
        self.store = MemoryStore(embedding=_wrap_embedding())

    @staticmethod
    def _meta_from_record(record: MemoryRecord) -> dict:
        return {"memory_type": record.memory_type if record.memory_type in _VALID_TYPES else "sql",
                "legacy_type": record.memory_type,   
                "user_id": record.user_id or _memory_user(),   
                "source": f"thread:{record.source_thread_id}" if record.source_thread_id else "da",
                "importance": int(record.importance),
                "access_count": int(record.access_count),
                "status": record.status}

    @staticmethod
    def _record_from_hit(hit: dict) -> MemoryRecord:
        m = hit["metadata"]
        source = m.get("source", "")
        thread_id = source.removeprefix("thread:") if source.startswith("thread:") else ""
        laa = m.get("last_accessed_at") or 0.0
        return MemoryRecord(
            memory_id=MemoryStore.doc_id(m.get("user_id", ""), m.get("tenant_id", "default"),
                                         hit["text"]),
            content=hit["text"],
            memory_type=m.get("legacy_type") or m.get("memory_type", "preference"),
            importance=int(float(m.get("importance", 3))),
            source_thread_id=thread_id,
            created_at=datetime.fromtimestamp(float(m.get("created_at") or 0),
                                              tz=timezone.utc).isoformat(),
            last_accessed_at=(datetime.fromtimestamp(float(laa), tz=timezone.utc).isoformat()
                              if laa else ""),
            access_count=int(m.get("access_count", 0)),
            status=m.get("status", "active"),
            user_id=m.get("user_id", ""),
        )

    def add(self, record: MemoryRecord) -> dict:
        try:
            rid = self.store.add(record.content, self._meta_from_record(record))
            return {"ok": True, "record_id": rid}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def recall(self, query: str, top_k: int = 3) -> list[MemoryRecord]:
        if not query.strip():
            return []
        try:
            hits = self.store.recall(query, {"user_id": _memory_user()}, top_k=top_k)
        except Exception:
            return []
        return [self._record_from_hit(h) for h in hits]

    def archive(self, record_id: str) -> dict:
        return self.store.archive(record_id)

    def close(self) -> None:
        self.store.close()
