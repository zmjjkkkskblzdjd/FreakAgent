# -*- coding: utf-8 -*-
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config


class AuditLogger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(self, **fields):
        entry = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
                 **fields}
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


audit = AuditLogger(config.AUDIT_LOG)
