from __future__ import annotations

import hashlib
import os

import requests

_BASE_URL = "https://qianfan.baidubce.com/v2/ai_search"


def _get_api_key() -> str:
    return os.getenv("BAIDU_API_KEY", "")


def web_search(query: str, top_k: int = 5) -> list[dict]:
    key = _get_api_key()
    if not key or not key.isascii():
        return []

    try:
        resp = requests.post(
            _BASE_URL,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            json={"messages": [{"role": "user", "content": query}],
                  "search_source": "baidu_search_v2"},
            timeout=60,
        )
    except Exception:
        return []

    if resp.status_code != 200:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []

    results = data.get("references") or []
    if not isinstance(results, list) or not results:
        return []

    results_sorted = sorted(results, key=lambda r: (r.get("date") or "").strip(), reverse=True)

    evidences: list[dict] = []
    for r in results_sorted[:top_k]:
        url = r.get("url") or ""
        content = r.get("content") or ""
        if not url and not content:
            continue
        cid_src = url or content
        cid = hashlib.md5(cid_src.encode("utf-8")).hexdigest()[:8]
        evidences.append({
            "chunk_id": cid,
            "content": content,
            "source": url,
            "doc_type": "web",
            "chunk_index": 0,
            "score": 1.0,
            "match_type": "web",
        })
    return evidences
