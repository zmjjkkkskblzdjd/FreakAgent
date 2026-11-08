
from __future__ import annotations

from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompt"

_ROLES: tuple[str, ...] = (
    "router",
    "planner",
    "retriever",
    "answerer",
    "critic",
    "supervisor",
)


def _load_prompts() -> dict[str, str]:
    prompts: dict[str, str] = {}
    for role in _ROLES:
        path = _PROMPT_DIR / f"{role}.md"
        if not path.exists():
            raise FileNotFoundError(
                f"提示词文件缺失：{path}（期望存在 prompt/{role}.md）"
            )
        text = path.read_text(encoding="utf-8")
        prompts[role] = text.rstrip("\n")
    return prompts


PROMPTS: dict[str, str] = _load_prompts()


def get_prompt(section: str) -> str:
    return PROMPTS[section]
