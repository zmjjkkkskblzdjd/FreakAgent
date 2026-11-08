
from __future__ import annotations

from dataclasses import dataclass

from config import get_app_config
from data_analyze_agent.metrics.loader import MetricEntry, load_dictionary
from data_analyze_agent.metrics.semantic import search_cards

__all__ = ["MetricMatch", "match_metrics", "render_metric_cards"]

_SEMANTIC_HIT_SCORE = 0.50


@dataclass(frozen=True)
class MetricMatch:

    entry: MetricEntry
    level: int            
    matched_term: str     
    score: float = 1.0    


def match_metrics(question: str) -> list[MetricMatch]:
    dictionary = load_dictionary()
    hits: dict[str, MetricMatch] = {}   
    q_lower = question.lower()

    for entry in dictionary.metrics:
        if entry.name.lower() in q_lower:
            hits[entry.name] = MetricMatch(entry=entry, level=1, matched_term=entry.name)

    for alias, std_name in sorted(dictionary.alias_index.items(),
                                  key=lambda kv: len(kv[0]), reverse=True):
        if alias and alias in q_lower and std_name not in hits:
            entry = dictionary.get(std_name)
            if entry is not None:
                hits[std_name] = MetricMatch(entry=entry, level=2, matched_term=alias)

    for semantic in _semantic_search(question):
        std_name = semantic[0]
        if std_name not in hits:
            entry = dictionary.get(std_name)
            if entry is not None:
                hits[std_name] = MetricMatch(
                    entry=entry, level=3,
                    matched_term=semantic[1], score=semantic[2],
                )

    return sorted(hits.values(), key=lambda m: m.level)


def _semantic_search(question: str) -> list[tuple[str, str, float]]:
    results = search_cards(
        "glossary", question, get_app_config().retrieval.glossary_top_k)

    hits: list[tuple[str, str, float]] = []
    for item in results:
        score = float(item.get("score") or 0.0)
        meta = item.get("metadata") or {}
        std_name = (meta.get("metric") or meta.get("term") or "").strip()
        if std_name and score >= _SEMANTIC_HIT_SCORE:
            hits.append((std_name, std_name, score))
    return hits


def render_metric_cards(matches: list[MetricMatch]) -> list:
    from data_analyze_agent.core import RetrievedCard

    cards = []
    for m in matches:
        e = m.entry
        lines = [
            f"【指标】{e.name}",
            f"口径表达式：{e.formula}",
            f"相关表：{'、'.join(e.related_tables) if e.related_tables else '（未声明）'}",
        ]
        if e.unit:
            lines.append(f"单位：{e.unit}")
        if e.note:
            lines.append(f"备注：{e.note}")
        if e.aliases:
            lines.append(f"别名：{'、'.join(e.aliases)}")
        cards.append(RetrievedCard(
            card_id=f"metric:{e.name}",
            kind="glossary",
            content="\n".join(lines),
            score=m.score,
            match_type="vector" if m.level == 3 else "keyword",
        ))
    return cards
