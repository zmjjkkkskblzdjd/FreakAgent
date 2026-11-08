
from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

__all__ = [
    "EMBED_DIM", "EMBED_MODEL_TAG", "EMBED_BATCH_SIZE",
    "resolve_model_path", "resolve_device", "get_embedding_model",
    "get_embedding", "get_embeddings_batch",
    "embed_cache_stats", "clear_embed_cache",
]

EMBED_DIM = 512                  
EMBED_MODEL_TAG = "bge-small-zh-v1.5"   
EMBED_BATCH_SIZE = 512           


_CACHE_TTL = float(os.getenv("AF_EMBED_CACHE_TTL", "600"))
_CACHE_MAX = int(os.getenv("AF_EMBED_CACHE_MAX", "512"))
_CACHE_BATCH_LIMIT = 8           
_cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
_cache_lock = threading.Lock()
_cache_stats = {"hit": 0, "miss": 0, "expired": 0}


def _cache_enabled() -> bool:
    return os.getenv("AF_EMBED_CACHE", "1").strip().lower() not in ("0", "off", "false", "no")


def _cache_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> list[float] | None:
    with _cache_lock:
        item = _cache.get(key)
        if item is None:
            _cache_stats["miss"] += 1
            return None
        ts, vec = item
        if time.time() - ts > _CACHE_TTL:
            _cache.pop(key, None)
            _cache_stats["expired"] += 1
            return None
        _cache.move_to_end(key)
        _cache_stats["hit"] += 1
        return vec


def _cache_put(key: str, vec: list[float]) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), vec)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def embed_cache_stats() -> dict:
    with _cache_lock:
        return {"size": len(_cache), "max": _CACHE_MAX, "ttl": _CACHE_TTL,
                **_cache_stats}


def clear_embed_cache() -> None:
    with _cache_lock:
        _cache.clear()


_WORKSPACE_ROOT = Path(__file__).resolve().parents[1].parent
_DEFAULT_LOCAL = str(_WORKSPACE_ROOT / "BAAI--bge-small-zh-v1.5")

_embedding_model = None


def resolve_model_path() -> str:
    local = os.getenv("KB_EMBED_LOCAL", _DEFAULT_LOCAL)
    if Path(local).is_dir():
        return local
    remote = os.getenv("KB_EMBED_HF", "BAAI/bge-small-zh-v1.5")
    print(
        f"未找到本地嵌入模型目录 {local}，回退远端模型 {remote}（首次需联网下载）",
        file=sys.stderr,
    )
    return remote


def resolve_device() -> str:
    want = (os.getenv("KB_EMBED_DEVICE", "cuda") or "cuda").strip().lower()
    if want in ("", "cpu", "none", "false", "0"):
        return "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            return want
    except Exception:          # noqa: BLE001 torch 缺失/驱动异常一律降级
        pass
    print(
        f"CUDA 不可用，嵌入模型回退 device=cpu（KB_EMBED_DEVICE={want}）",
        file=sys.stderr,
    )
    return "cpu"


def _load_model(model_path: str, device: str):
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_path, device=device)
    model.encode(["预热"], normalize_embeddings=True)
    return model


def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        model_path = resolve_model_path()
        device = resolve_device()
        print(f"正在加载嵌入模型：{model_path}（device={device}）", file=sys.stderr)
        try:
            _embedding_model = _load_model(model_path, device)
        except Exception as exc:   # noqa: BLE001 GPU 初始化失败不阻断主流程
            if device == "cpu":
                raise
            print(
                f"嵌入模型在 {device} 上初始化失败（{exc}），回退 device=cpu",
                file=sys.stderr,
            )
            _embedding_model = _load_model(model_path, "cpu")
    return _embedding_model


def get_embedding(text: str) -> list[float]:
    cached = _cache_enabled() and bool(text)
    if cached:
        vec = _cache_get(_cache_key(text))
        if vec is not None:
            return vec
    vec = get_embedding_model().encode(
        text, normalize_embeddings=True
    ).tolist()
    if cached:
        _cache_put(_cache_key(text), vec)
    return vec


def get_embeddings_batch(texts: list[str], *, batch_size: int = EMBED_BATCH_SIZE) -> list[list[float]]:
    if not texts:
        return []
    if not (_cache_enabled() and len(texts) <= _CACHE_BATCH_LIMIT):
        return get_embedding_model().encode(
            texts, batch_size=batch_size, normalize_embeddings=True
        ).tolist()

    keys = [_cache_key(t) for t in texts]
    out: list[list[float] | None] = []
    todo: list[tuple[int, str]] = []
    for idx, (t, k) in enumerate(zip(texts, keys)):
        vec = _cache_get(k)
        out.append(vec)
        if vec is None:
            todo.append((idx, t))
    if todo:
        uniq = list(dict.fromkeys(t for _, t in todo))      
        encoded = get_embedding_model().encode(
            uniq, batch_size=batch_size, normalize_embeddings=True
        ).tolist()
        by_text = dict(zip(uniq, encoded))
        for i, t in todo:
            out[i] = by_text[t]
            _cache_put(keys[i], by_text[t])
    return out                                                 # type: ignore[return-value]
