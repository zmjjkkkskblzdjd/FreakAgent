from __future__ import annotations

import json

from kb_service import kb_config, mysql_source
from kb_service.store import VectorStore

"""语义库向量索引构建（§7/§8）：从 MySQL 主存读行 → 按 embed_text 规范 →
整文档（不切分）灌入 glossary / fewshot 两个 collection，id=主存主键，
metadata 最小，正文（formula/reference_sql）不进向量库。
"""
def parse_json_array(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw).strip()
    if not s:
        return []
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
    except (ValueError, TypeError):
        pass
    return [x.strip() for x in s.split(",") if x.strip()]


def build_embed_text(row: dict, kb: str) -> str:
    if kb == "glossary":
        parts = [f"指标：{row['name']}"]
        aliases = parse_json_array(row.get("aliases"))
        if aliases:
            parts.append("同义说法：" + "、".join(aliases))
        formula_desc = (row.get("formula_desc") or "").strip()
        if formula_desc:
            parts.append("口径：" + formula_desc)
        note = (row.get("note") or "").strip()
        if note:
            parts.append("备注：" + note)
        return "。".join(parts)
    if kb == "fewshot":
        parts = [f"例题：{row['question']}"]
        explanation = (row.get("explanation") or "").strip()
        if explanation:
            parts.append("说明：" + explanation)
        return "。".join(parts)
    raise ValueError(f"非法 kb：{kb}（仅 glossary / fewshot）")


def index_rows(store: VectorStore, rows: list[dict], kb: str) -> int:
    if not rows:
        return 0
    docs = [build_embed_text(r, kb) for r in rows]
    ids = [str(r["id"]) for r in rows]
    if kb == "glossary":
        metadatas = [{"kb": "glossary", "metric": r["name"]} for r in rows]
    else:
        metadatas = [{"kb": "fewshot",
                      "tags": json.dumps(parse_json_array(r.get("tags")), ensure_ascii=False),
                      "user_id": (r.get("user_id") or "")}
                     for r in rows]
    store.add_documents(docs, metadatas=metadatas, ids=ids)
    return len(rows)


def build_kb_index() -> tuple[int, int]:
    glossary = VectorStore(kb_config.GLOSSARY_COLLECTION)
    fewshot = VectorStore(kb_config.FEWSHOT_COLLECTION)
    glossary.clear()
    fewshot.clear()
    g_n = index_rows(glossary, mysql_source.fetch_kb_rows("kb_glossary"), "glossary")
    f_n = index_rows(fewshot, mysql_source.fetch_kb_rows("kb_fewshot"), "fewshot")
    glossary.close()
    fewshot.close()
    return g_n, f_n
