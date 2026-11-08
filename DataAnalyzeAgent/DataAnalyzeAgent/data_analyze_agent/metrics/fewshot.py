
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from config import get_app_config
from data_analyze_agent.db import gateway
from data_analyze_agent.metrics.semantic import search_cards
from data_analyze_agent.metrics.slots import (
    MIN_MATCH_SCORE, extract_slots, score_tags, tags_of,
)

__all__ = ["FewshotExample", "FewshotHit", "load_fewshots", "match_fewshots",
           "match_fewshots_semantic", "render_fewshot_cards",
           "bind_fewshot_user", "current_fewshot_user"]

KB_FEWSHOT_TABLE = "sales_dw.kb_fewshot"   


@dataclass(frozen=True)
class FewshotExample:

    id: int
    question: str
    reference_sql: str
    explanation: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class FewshotHit:

    example: FewshotExample
    score: int


_cache: dict[str, tuple[float, list[FewshotExample]]] = {}   

_FEWSHOT_USER: ContextVar[str | None] = ContextVar("af_fewshot_user", default=None)


def current_fewshot_user() -> str:
    bound = (_FEWSHOT_USER.get() or "").strip()
    if bound:
        return bound
    return (get_app_config().memory.user_id or "local").strip() or "local"


@contextmanager
def bind_fewshot_user(user_id):
    if user_id in (None, ""):
        yield
        return
    token = _FEWSHOT_USER.set(str(user_id))
    try:
        yield
    finally:
        _FEWSHOT_USER.reset(token)


def _sql_str(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def load_fewshots() -> list[FewshotExample]:
    user = current_fewshot_user()
    now = time.monotonic()
    hit = _cache.get(user)
    if hit and now < hit[0]:
        return hit[1]

    with gateway.use_default_channel():
        result = gateway.query(
            f"SELECT id, question, reference_sql, explanation, tags "
            f"FROM {KB_FEWSHOT_TABLE} WHERE enabled = 1 AND status = 'published' "
            f"AND user_id = '{_sql_str(user)}' ORDER BY id"
        )
    if not result["ok"]:
        raise gateway.GatewayUnavailable(
            f"fewshot 例题读取失败（{KB_FEWSHOT_TABLE}）：{result['data'].message}"
        )
    qr = result["data"]
    idx = {c: i for i, c in enumerate(qr.columns)}
    examples = [
        FewshotExample(
            id=int(r[idx["id"]]),
            question=str(r[idx["question"]] or ""),
            reference_sql=str(r[idx["reference_sql"]] or ""),
            explanation=str(r[idx["explanation"]] or ""),
            tags=tuple(r[idx["tags"]] or ()),
        )
        for r in qr.rows
    ]
    _cache[user] = (now + get_app_config().gateway.catalog_ttl_sec, examples)
    return examples


def match_fewshots(question: str, top_k: int | None = None) -> list[FewshotHit]:
    if top_k is None:
        top_k = get_app_config().retrieval.fewshot_top_k
    q_tags = tags_of(extract_slots(question))
    hits = [
        FewshotHit(example=ex, score=score_tags(q_tags, ex.tags))
        for ex in load_fewshots()
    ]
    hits = [h for h in hits if h.score >= MIN_MATCH_SCORE]
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]


def match_fewshots_semantic(question: str, top_k: int | None = None) -> list:
    from data_analyze_agent.core import RetrievedCard

    if top_k is None:
        top_k = get_app_config().retrieval.fewshot_top_k
    if top_k <= 0:
        return []
    results = search_cards("fewshot", question, top_k,
                           where={"user_id": current_fewshot_user()})
    return [
        RetrievedCard(
            card_id=f"fewshot:semantic:{i}",
            kind="fewshot",
            content=str(item.get("content") or ""),
            score=float(item.get("score") or 0.0),
            match_type="vector",
        )
        for i, item in enumerate(results)
        if item.get("content")
    ]


def render_fewshot_cards(hits: list[FewshotHit]) -> list:
    from data_analyze_agent.core import RetrievedCard

    cards = []
    for h in hits:
        ex = h.example
        lines = [
            f"【例题】{ex.question}",
            f"参考SQL：\n{ex.reference_sql}",
        ]
        if ex.explanation:
            lines.append(f"解读要点：{ex.explanation}")
        cards.append(RetrievedCard(
            card_id=f"fewshot:{ex.id}",
            kind="fewshot",
            content="\n".join(lines),
            score=float(h.score),
            match_type="keyword",
        ))
    return cards
