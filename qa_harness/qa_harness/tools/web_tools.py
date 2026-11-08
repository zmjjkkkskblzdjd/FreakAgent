from __future__ import annotations

import hashlib
import os

import requests

from tools import tool


BASE_URL = "https://qianfan.baidubce.com/v2/ai_search"


def _get_api_key() -> str:
    return os.getenv("BAIDU_API_KEY", "")


def _check_api_key() -> tuple[bool, str]:
    key = _get_api_key()
    if not key:
        return False, (
            "缺少环境变量 BAIDU_API_KEY，请先执行（替换成真实密钥，不要照抄占位文字）：\n"
            '  $env:BAIDU_API_KEY="bce-v3/ALTAK-xxxx"\n'
            "API Key 申请页：https://console.bce.baidu.com/iam/#/iam/apikey/list"
        )
    if not key.isascii():
        return False, (
            "BAIDU_API_KEY 包含中文字符，说明存的是占位文字而不是真实密钥。\n"
            "请到 https://console.bce.baidu.com/iam/#/iam/apikey/list 复制真实 Key（形如 bce-v3/ALTAK-xxxx）。"
        )
    return True, ""


def create_web_tools():

    @tool(
        name="web_search",
        description=(
            "联网搜索百度，返回与查询最相关的网页摘要列表，"
            "每条含 chunk_id / content / source(url) / score / match_type=web。"
            "仅当问题需要实时信息、最新动态或知识库没有的内容时使用。"
            "只读工具；查不到时返回空列表而非报错。"
        ),
        parameter_descriptions={
            "query": "搜索关键词或完整问题",
            "top_k": "返回结果数量上限，默认 5",
        },
    )
    def web_search(query: str, top_k: int = 5) -> dict:
        ok, err = _check_api_key()
        if not ok:
            return {"ok": False, "error": err}

        try:
            resp = requests.post(
                BASE_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {_get_api_key()}",
                },
                json={
                    "messages": [{"role": "user", "content": query}],
                    "search_source": "baidu_search_v2",
                },
                timeout=60,
            )
        except requests.Timeout:
            return {"ok": False, "error": f"联网搜索超时（60s）：{query!r}"}
        except requests.ConnectionError:
            return {"ok": False, "error": "联网搜索失败：无法连接到百度搜索服务"}
        except Exception as e:
            return {"ok": False, "error": f"联网搜索异常：{type(e).__name__}: {e}"}

        if resp.status_code != 200:
            return {
                "ok": False,
                "error": f"联网搜索 HTTP {resp.status_code}：{resp.text[:200]}",
            }

        try:
            data = resp.json()
        except ValueError as e:
            return {"ok": False, "error": f"联网搜索返回不是合法 JSON：{e}"}

        results = data.get("references") or []
        if not isinstance(results, list):
            return {"ok": False, "error": f"联网搜索返回格式异常：references 不是列表，实际 {type(results).__name__}"}

        if not results:
            return {"ok": True, "evidences": []}

        def _sort_key(rr: dict) -> str:
            d = (rr.get("date") or "").strip()
            return d  

        results_sorted = sorted(results, key=_sort_key, reverse=True)

        evidences: list[dict] = []
        for r in results_sorted[:top_k]:
            url = r.get("url") or ""
            content = r.get("content") or ""
            if not url and not content:
                continue  
            cid = hashlib.md5(url.encode("utf-8")).hexdigest()[:8] if url else hashlib.md5(content.encode("utf-8")).hexdigest()[:8]
            evidences.append({
                "chunk_id": cid,
                "content": content,
                "source": url,
                "doc_type": "web",
                "chunk_index": 0,
                "score": 1.0,  
                "match_type": "web",
            })

        return {"ok": True, "evidences": evidences}

    return (web_search,)
