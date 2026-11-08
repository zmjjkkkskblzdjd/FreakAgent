
from __future__ import annotations

import json
import time
from pathlib import Path

from config import PROJECT_ROOT
from data_analyze_agent.metrics.fewshot import current_fewshot_user
from data_analyze_agent.metrics.slots import extract_slots, tags_of

__all__ = ["CANDIDATES_PATH", "maybe_collect_fewshot"]

CANDIDATES_PATH = PROJECT_ROOT / "data" / "fewshot_candidates.jsonl"


def _already_queued(question: str) -> bool:
    if not CANDIDATES_PATH.is_file():
        return False
    try:
        for line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("question") == question:
                return True
    except (OSError, json.JSONDecodeError):
        return False        
    return False


def maybe_collect_fewshot(question: str, report) -> bool:
    try:
        if getattr(report, "status", None) != "answered":
            return False
        sql = (getattr(report, "sql", "") or "").strip()
        if not sql or getattr(report, "repairs_used", 1) != 0:
            return False
        if getattr(report, "row_count", 0) <= 0:
            return False
        if _already_queued(question):
            return False

        record = {
            "user_id": current_fewshot_user(),
            "question": question,
            "reference_sql": sql,
            "suggested_tags": list(tags_of(extract_slots(question))),
            "related_tables": [str(t) for t in getattr(report, "tables_used", [])],
            "explanation": "",
            "extracted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CANDIDATES_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001 —— 旁路永不影响主流程
        return False
