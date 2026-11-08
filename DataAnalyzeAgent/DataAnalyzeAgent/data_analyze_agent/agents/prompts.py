
from __future__ import annotations

from pathlib import Path

__all__ = ["PROMPTS", "get_prompt", "PROMPT_ROLES"]

_PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompt"

PROMPT_ROLES: tuple[str, ...] = (
    "analyst",
    "chitchat",
    "clarify_classify",
    "planner",
    "reviewer",
    "router",
    "sql_writer",
    "sql_writer_write",
    "memory_extract"
)


def _load_prompts() -> dict[str, str]:
    prompts: dict[str, str] = {}
    missing: list[Path] = []
    for role in PROMPT_ROLES:
        path = _PROMPT_DIR / f"{role}.md"
        if not path.exists():
            missing.append(path)
            continue
        text = path.read_text(encoding="utf-8").strip("\n")
        if not text.strip():
            raise RuntimeError(f"提示词文件为空：{path}（请检查 prompt/{role}.md）")
        prompts[role] = text
    if missing:
        raise FileNotFoundError(
            "提示词文件缺失（应位于项目根 prompt/ 目录）：\n  - "
            + "\n  - ".join(str(p) for p in missing)
        )
    return prompts


PROMPTS: dict[str, str] = _load_prompts()


def get_prompt(role: str) -> str:
    return PROMPTS[role]
