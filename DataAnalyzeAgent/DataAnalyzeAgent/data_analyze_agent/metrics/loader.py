
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from config import get_app_config
from data_analyze_agent.db import gateway

__all__ = ["MetricEntry", "MetricDictionary", "load_dictionary"]

KB_GLOSSARY_TABLE = "sales_dw.kb_glossary"   
KB_META_TABLE = "sales_dw.kb_meta"           


@dataclass(frozen=True)
class MetricEntry:

    name: str                       
    formula: str                    
    related_tables: list[str]
    aliases: tuple[str, ...] = ()   
    unit: str = ""
    note: str = ""


@dataclass(frozen=True)
class MetricDictionary:

    domain: str
    database: str
    default_time_field: str
    default_status_filter: str
    metrics: tuple[MetricEntry, ...]
    alias_index: dict[str, str] = field(default_factory=dict)

    def names(self) -> list[str]:
        return [m.name for m in self.metrics]

    def all_terms(self) -> list[str]:
        seen: set[str] = set()
        terms: list[str] = []
        for m in self.metrics:
            for t in (m.name, *m.aliases):
                if t not in seen:
                    seen.add(t)
                    terms.append(t)
        return terms

    def get(self, name: str) -> MetricEntry | None:
        for m in self.metrics:
            if m.name == name:
                return m
        return None


_cache: tuple[float, MetricDictionary] | None = None   


def _require_text(value: object, *, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where} 必须是字符串，实际是 {type(value).__name__}")
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        raise ValueError(f"{where} 不能为空字符串")
    return text


def _as_str_list(value: object, *, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{where} 必须是字符串数组，实际是 {value!r}")
    return [v for v in value if v.strip()]


def load_dictionary() -> MetricDictionary:
    global _cache
    now = time.monotonic()
    if _cache and now < _cache[0]:
        return _cache[1]

    with gateway.use_default_channel():
        result = gateway.query(
            f"SELECT name, aliases, formula, related_tables, unit, note "
            f"FROM {KB_GLOSSARY_TABLE} WHERE enabled = 1 ORDER BY name"
        )
    if not result["ok"]:
        raise gateway.GatewayUnavailable(
            f"指标字典读取失败（{KB_GLOSSARY_TABLE}）：{result['data'].message}"
        )
    qr = result["data"]
    idx = {c: i for i, c in enumerate(qr.columns)}

    metrics: list[MetricEntry] = []
    alias_index: dict[str, str] = {}
    names: set[str] = set()
    for row in qr.rows:
        name = _require_text(row[idx["name"]], where="kb_glossary.name")
        if name in names:
            raise ValueError(f"指标 {name!r} 在字典表中重复定义，请合并（MySQL 主存去重）")
        names.add(name)
        formula = _require_text(row[idx["formula"]], where=f"指标 {name} 的 formula")
        aliases = _as_str_list(row[idx["aliases"]], where=f"指标 {name} 的 aliases")
        related = _as_str_list(row[idx["related_tables"]],
                               where=f"指标 {name} 的 related_tables")
        entry = MetricEntry(
            name=name,
            formula=formula,
            related_tables=related,
            aliases=tuple(aliases),
            unit=str(row[idx["unit"]] or ""),
            note=str(row[idx["note"]] or ""),
        )
        for alias in aliases:
            key = alias.lower()
            if key in alias_index and alias_index[key] != name:
                raise ValueError(
                    f"别名 {alias!r} 同时挂在 {alias_index[key]!r} 与 {name!r} 下，请归并"
                )
            alias_index[key] = name
        metrics.append(entry)

    meta = {k: v for k, v in _load_kb_meta().items()}
    dictionary = MetricDictionary(
        domain=meta.get("domain", ""),
        database=meta.get("database", ""),
        default_time_field=meta.get("default_time_field", ""),
        default_status_filter=meta.get("default_status_filter", ""),
        metrics=tuple(metrics),
        alias_index=alias_index,
    )
    _cache = (now + get_app_config().gateway.catalog_ttl_sec, dictionary)
    return dictionary


def _load_kb_meta() -> dict[str, str]:
    with gateway.use_default_channel():
        result = gateway.query(f"SELECT meta_key, meta_value FROM {KB_META_TABLE}")
    if not result["ok"]:
        raise gateway.GatewayUnavailable(
            f"指标字典元信息读取失败（{KB_META_TABLE}）：{result['data'].message}"
        )
    qr = result["data"]
    idx = {c: i for i, c in enumerate(qr.columns)}
    return {str(r[idx["meta_key"]]): str(r[idx["meta_value"]]) for r in qr.rows}
