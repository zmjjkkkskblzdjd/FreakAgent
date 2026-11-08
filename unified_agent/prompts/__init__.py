from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent
_USER_MARK = "## User 模板"


def load_prompt(name: str) -> tuple[str, str]:
    text = (_DIR / f"{name}.md").read_text(encoding="utf-8")
    head, _, user_part = text.partition(_USER_MARK)
    return _strip_doc_head(head), _extract_fence(user_part)


def _strip_doc_head(head: str) -> str:
    lines = head.splitlines()
    start = 0
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            start = i + 1
            break
    while start < len(lines) and (not lines[start].strip() or lines[start].startswith(">")):
        start += 1
    return "\n".join(lines[start:]).strip()


def _extract_fence(user_part: str) -> str:
    i = user_part.find("```")
    if i < 0:                       
        return user_part.strip()
    inner = user_part[i + 3:]
    j = inner.find("```")
    body = inner[:j] if j >= 0 else inner
    lines = body.split("\n")
    if lines and lines[0].strip() and " " not in lines[0].strip() and "{" not in lines[0]:
        lines = lines[1:]
    return "\n".join(lines).strip()
