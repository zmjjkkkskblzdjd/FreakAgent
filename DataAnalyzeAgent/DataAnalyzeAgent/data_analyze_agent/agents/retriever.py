
from __future__ import annotations

import re
import time

from langchain_core.messages import AIMessage

from config import get_app_config
from data_analyze_agent.core import RetrievedCard
from data_analyze_agent.db import gateway
from data_analyze_agent.graph.state import DataAnalyzeState
from data_analyze_agent.metrics import (
    load_dictionary, match_fewshots, match_fewshots_semantic, match_metrics,
    render_fewshot_cards, render_metric_cards,
)

__all__ = [
    "get_glossary_terms",
    "make_retrieve_schema_node",
    "after_retrieval",
]

_NO_SCHEMA_REPLY = (
    "抱歉，当前数据服务暂时不可用，或数据仓库中还没有可查询的表。"
    "本系统覆盖销售经营主题（订单、客户、商品、区域等数据），"
    "请稍后再试，或联系管理员确认查询网关与数据同步状态。"
)

_schema_card_cache: dict[int | None, tuple[float, list[RetrievedCard]]] = {}


def get_glossary_terms() -> list[str]:
    return load_dictionary().all_terms()


def _render_schema_card(table: dict, schema: dict, database: str) -> RetrievedCard:
    name = table["name"]
    is_view = table.get("is_view", False)
    kind_label = "最新版本视图" if is_view else "物理表（多版本）"
    lines = [f"表：{database}.{name}（{kind_label}）"]
    comment = (table.get("comment") or "").strip()
    if comment:
        lines.append(f"说明：{comment}")
    cols = schema.get("columns", [])
    if cols:
        lines.append("列：")
        for c in cols:
            ccomment = (c.get("comment") or "").strip()
            suffix = f" — {ccomment}" if ccomment else ""
            lines.append(f"- {c['name']} {c.get('type', '')}{suffix}")
    hint = (table.get("dedup_hint") or "").strip()
    if hint:
        lines.append(f"查询提示：{hint}")
    if gateway.current_datasource_id() is not None and name.startswith("v_"):
        lines.append(f"写入提示：写操作请使用源表名 {name[2:]}（{name} 为只读镜像视图）")
    return RetrievedCard(
        card_id=f"schema:{database}.{name}",
        kind="schema",
        content="\n".join(lines),
        score=1.0,
        match_type="keyword",
    )


def _table_database(table: dict, default: str) -> str:
    db = (table.get("database") or "").strip()
    if db:
        return db
    full = (table.get("full_name") or "").strip()
    if full and "." in full:
        return full.rsplit(".", 1)[0]
    return default


def _load_schema_cards() -> list[RetrievedCard]:
    cfg = get_app_config().gateway
    key = gateway.current_datasource_id()
    now = time.monotonic()
    cached = _schema_card_cache.get(key)
    if cached and now < cached[0]:
        return cached[1]

    tables = sorted(
        (t for t in gateway.list_tables() if gateway.is_business_table(t["name"])),
        key=lambda t: t["name"],
    )
    cards = [
        _render_schema_card(t, gateway.get_table_schema(t["name"]),
                            _table_database(t, cfg.database))
        for t in tables
    ]
    _schema_card_cache[key] = (now + cfg.catalog_ttl_sec, cards)
    return cards


def invalidate_schema_cache() -> None:
    _schema_card_cache.clear()


def _scope_metric_matches(matches, schema_cards):
    if not schema_cards:
        return matches
    haystack = "\n".join(card.content for card in schema_cards).lower()
    kept = []
    for m in matches:
        tables = [t for t in (m.entry.related_tables or []) if t]
        if not tables or any(t.lower() in haystack for t in tables):
            kept.append(m)
    return kept


_FROM_JOIN = re.compile(r"(?:from|join)\s+([A-Za-z0-9_$.]+)", re.IGNORECASE)


def _scope_fewshot_hits(hits, schema_cards):
    if not schema_cards:
        return hits
    haystack = "\n".join(card.content for card in schema_cards).lower()
    kept = []
    for hit in hits:
        sql = getattr(getattr(hit, "example", None), "reference_sql", "") or ""
        tables = {tok.split(".")[-1].lower() for tok in _FROM_JOIN.findall(sql)}
        if not tables or any(t in haystack for t in tables):
            kept.append(hit)
    return kept


def make_retrieve_schema_node(retrievers=None):

    def retrieve_schema_node(state: DataAnalyzeState) -> dict:
        question = state.get("clarified_question")
        if not question:
            raise ValueError("retrieve_schema 未取到 clarified_question，无法检索")

        try:
            schema_cards = _load_schema_cards()
            metric_cards = render_metric_cards(
                _scope_metric_matches(match_metrics(question), schema_cards))
        except gateway.GatewayUnavailable:
            schema_cards, metric_cards = [], []
        glossary_cards = metric_cards

        fewshot_cards = []
        if schema_cards:
            raw_hits = match_fewshots(question)
            scoped = _scope_fewshot_hits(raw_hits, schema_cards)
            if scoped:
                fewshot_cards = render_fewshot_cards(scoped)
            elif not raw_hits:
                fewshot_cards = match_fewshots_semantic(question)

        patch: dict = {
            "schema_docs": schema_cards,
            "glossary_hits": glossary_cards,
            "fewshot_hits": fewshot_cards,
        }
        if not schema_cards:
            patch["messages"] = [AIMessage(content=_NO_SCHEMA_REPLY)]
        return patch

    return retrieve_schema_node


def after_retrieval(state: DataAnalyzeState) -> str:
    if state.get("node_error"):
        return "end"           
    if not state.get("schema_docs"):
        return "end"           
    if state.get("tier") == "deep":
        return "plan"          
    return "write_sql"
