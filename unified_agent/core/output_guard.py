from __future__ import annotations

import re

from allfusion_auth.redact import redact_text, MASK

_HIGH_CONFIDENCE = (
    ("api_key", re.compile(r"sk-[A-Za-z0-9_\-]{8,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("url_credential", re.compile(r"[a-zA-Z][\w+.-]*://[^/\s:@]+:[^/\s@]+@")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")),
    ("query_secret", re.compile(
        r"(?i)(?:api[_-]?key|password|passwd|secret|token|access[_-]?key)=[^&\s]+")),
)
_LOCAL_MASK = {"api_key", "aws_key", "phone"}


def sanitize_answer(text: str) -> tuple[str, list[str]]:
    if not text:
        return text, []
    hits = [
        f"{name}×{len(pattern.findall(text))}"
        for name, pattern in _HIGH_CONFIDENCE
        if pattern.search(text)
    ]
    out = redact_text(text)
    for name, pattern in _HIGH_CONFIDENCE:
        if name in _LOCAL_MASK:
            out = pattern.sub(MASK, out)
    return out, hits
