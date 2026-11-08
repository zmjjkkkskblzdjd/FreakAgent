from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from config import MemoryConfig
from core.types import MemoryRecord
from knowledge.embeddings import get_embedding

_ALLFUSION_ROOT = str(Path(__file__).resolve().parents[3])
if _ALLFUSION_ROOT not in sys.path:
    sys.path.insert(0, _ALLFUSION_ROOT)

from memory_service.store import MemoryStore  # noqa: E402  共享记忆底座

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VALID_TYPES = ("rag", "sql", "general")


def _wrap_embedding():
    return lambda docs: [get_embedding(d) for d in docs]


class LongTermMemory:

    def __init__(self, config: MemoryConfig | None = None) -> None:
        self.config = config or MemoryConfig()
        self.store = MemoryStore(embedding=_wrap_embedding())

    @staticmethod
    def _meta_from_record(record: MemoryRecord) -> dict:
        return {"user_id": record.user_id,
                "memory_type": record.type if record.type in _VALID_TYPES else "general",
                "legacy_type": record.type,          
                "source": f"session:{record.session_id}" if record.session_id else "qa",
                "importance": record.importance,
                "access_count": record.access_count,
                "status": record.status}

    @staticmethod
    def _record_from_hit(hit: dict) -> MemoryRecord:
        m = hit["metadata"]
        source = m.get("source", "")
        session_id = source.removeprefix("session:") if source.startswith("session:") else ""
        laa = m.get("last_accessed_at") or 0.0
        return MemoryRecord(
            id=MemoryStore.doc_id(m.get("user_id", ""), m.get("tenant_id", "default"),
                                  hit["text"]),
            user_id=m.get("user_id", "default"),
            session_id=session_id,
            content=hit["text"],
            type=m.get("legacy_type") or m.get("memory_type", "general"),
            importance=float(m.get("importance", 0.5)),
            created_at=datetime.fromtimestamp(float(m.get("created_at") or 0),
                                              tz=timezone.utc).isoformat(),
            last_accessed_at=(datetime.fromtimestamp(float(laa), tz=timezone.utc).isoformat()
                              if laa else ""),
            access_count=int(m.get("access_count", 0)),
            status=m.get("status", "active"),
        )

    def add(self, record: MemoryRecord) -> dict:
        try:
            rid = self.store.add(record.content, self._meta_from_record(record))
            return {"ok": True, "record_id": rid}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def recall(self, query: str, user_id: str = "default", top_k: int = 3) -> list[MemoryRecord]:
        if not query.strip():
            return []
        try:
            hits = self.store.recall(query, {"user_id": user_id}, top_k=top_k)
        except Exception:
            return []
        return [self._record_from_hit(h) for h in hits]

    def archive(self, record_id: str) -> dict:
        return self.store.archive(record_id)

    def close(self) -> None:
        self.store.close()
