
from __future__ import annotations

import sys
from pathlib import Path

import httpx

from config import get_app_config

__all__ = ["search_cards"]

_ALLFUSION_ROOT = str(Path(__file__).resolve().parents[4])
if _ALLFUSION_ROOT not in sys.path:
    sys.path.insert(0, _ALLFUSION_ROOT)


def search_cards(kb: str, query: str, top_k: int,
                 where: dict | None = None) -> list[dict]:
    try:
        from kb_service.store import search_semantic   
        if where:
            return search_semantic(kb, query, top_k, where=where)
        return search_semantic(kb, query, top_k)
    except Exception:  # noqa: BLE001 —— 本地库缺席不阻塞主链路
        return _filter_where(_search_via_http(kb, query, top_k * 3), where)[:top_k]


def _filter_where(hits: list[dict], where: dict | None) -> list[dict]:
    if not where:
        return hits
    return [h for h in hits
            if all((h.get("metadata") or {}).get(k) == v for k, v in where.items())]


def _search_via_http(kb: str, query: str, top_k: int) -> list[dict]:
    cfg = get_app_config().metrics
    if not cfg.qa_harness_url or top_k <= 0:
        return []
    try:
        resp = httpx.get(
            f"{cfg.qa_harness_url.rstrip('/')}/api/search",
            params={"kb": kb, "query": query, "top_k": top_k},
            timeout=cfg.qa_harness_timeout_ms / 1000,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception:  # noqa: BLE001 —— 语义层缺席不阻塞主链路
        return []
