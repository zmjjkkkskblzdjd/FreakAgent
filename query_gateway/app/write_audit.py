# -*- coding: utf-8 -*-
import json
import threading
from datetime import datetime
from pathlib import Path

from . import config


class WriteAuditLogger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _next_seq(self, today: str) -> int:
        if not self.path.exists():
            return 1
        seq = 0
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if f'"audit_id": "{today}-' in line:
                    seq += 1
        return seq + 1

    def log(self, **fields) -> str:
        today = datetime.now().strftime("%Y%m%d")
        with self._lock:
            audit_id = f"{today}-{self._next_seq(today):03d}"
            entry = {"audit_id": audit_id,
                     "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                     **fields}
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return audit_id


write_audit = WriteAuditLogger(config.WRITE_AUDIT_LOG)
