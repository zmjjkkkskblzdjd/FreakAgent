from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


class Config:
    HOST: str = os.environ.get("DOC_PARSER_HOST", "127.0.0.1")
    PORT: int = int(os.environ.get("DOC_PARSER_PORT", "8002"))

    MAX_FILE_SIZE: int = int(os.environ.get("MAX_FILE_SIZE", str(100 * 1024 * 1024)))
    MAX_FILES_PER_TASK: int = int(os.environ.get("MAX_FILES_PER_TASK", "20"))
    MAX_QUEUE_SIZE: int = int(os.environ.get("MAX_QUEUE_SIZE", "100"))

    PARSE_TIMEOUT: int = int(os.environ.get("PARSE_TIMEOUT", "120"))
    TASK_TTL: int = int(os.environ.get("TASK_TTL", "3600"))
    SSE_HEARTBEAT: int = int(os.environ.get("SSE_HEARTBEAT", "15"))

    SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
        {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm",
         ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
    )


config = Config()
