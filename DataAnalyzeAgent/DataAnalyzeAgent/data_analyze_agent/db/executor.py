
from __future__ import annotations

from data_analyze_agent.db.gateway import query

__all__ = ["run_select"]


def run_select(sql: str, *, engine=None) -> dict:
    return query(sql)
