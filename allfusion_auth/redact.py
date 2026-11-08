from __future__ import annotations

import re
from typing import Any

_SENSITIVE = re.compile(
    r"(api[_-]?key|password|passwd|secret|token|authorization|cookie|dsn|access[_-]?key)",
    re.I,
)
_URL_USERINFO = re.compile(r"(?P<scheme>[a-zA-Z][\w+.-]*://)[^/\s:@]+:[^/\s@]+@")
_QUERY_SECRET = re.compile(
    r"(?P<key>(?:api[_-]?key|password|passwd|secret|token|access[_-]?key))=(?P<val>[^&\s]+)",
    re.I,
)
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]+")

MASK = "***"


def _redact_str(text: str) -> str:
    out = _URL_USERINFO.sub(lambda m: f"{m.group('scheme')}{MASK}@", text)
    out = _QUERY_SECRET.sub(lambda m: f"{m.group('key')}={MASK}", out)
    out = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", out)
    return out


def redact(obj: Any, depth: int = 0) -> Any:
    if depth > 8:                       
        return MASK
    if isinstance(obj, dict):
        return {
            k: (MASK if _SENSITIVE.search(str(k)) else redact(v, depth + 1))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple, set)):
        seq = [redact(v, depth + 1) for v in obj]
        return type(obj)(seq) if isinstance(obj, tuple) else seq
    if isinstance(obj, str):
        return _redact_str(obj)
    return obj


def redact_text(text: str) -> str:
    return _redact_str(str(text))
