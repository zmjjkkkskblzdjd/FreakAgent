from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from allfusion_auth.crypto import (
    SecretDecryptError, decrypt_secret, encrypt_secret, key_fingerprint, mask_secret,
)
from allfusion_auth.db import get_engine
from allfusion_auth.deps import AuthUser, require_db_admin, require_user
from allfusion_auth.models import model_provider, model_route, user_model_pref
from allfusion_auth.redact import redact_text

router = APIRouter()

_CATALOG: list[dict] = [
    {
        "provider_type": "glm", "name": "智谱 GLM", "color": "#3b6fe0",
        "desc": "智谱 AI GLM 系列大模型",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "completions_path": "/chat/completions",
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "models": [
            {"id": "glm-5.3", "name": "GLM-5.3"},
            {"id": "glm-5.3-flash", "name": "GLM-5.3-Flash"},
            {"id": "glm-5.3-flashx", "name": "GLM-5.3-FlashX"},
            {"id": "glm-5.2", "name": "GLM-5.2"},
            {"id": "glm-5.1", "name": "GLM-5.1"},
            {"id": "glm-5", "name": "GLM-5"},
            {"id": "glm-5-turbo", "name": "GLM-5-Turbo"},
            {"id": "glm-4.7", "name": "GLM-4.7"},
            {"id": "glm-4.6", "name": "GLM-4.6"},
            {"id": "glm-4.5", "name": "GLM-4.5"},
        ],
    },
    {
        "provider_type": "qwen", "name": "阿里通义千问", "color": "#615ced",
        "desc": "阿里云通义千问大模型",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "completions_path": "/chat/completions",
        "key_url": "https://bailian.console.aliyun.com/?apiKey=1",
        "models": [
            {"id": "qwen3.8-max", "name": "Qwen3.8-Max"},
            {"id": "qwen3.8-max-0902", "name": "Qwen3.8-Max-0902"},
            {"id": "qwen3.8-flash", "name": "Qwen3.8-Flash"},
            {"id": "qwen3.7-plus", "name": "Qwen3.7-Plus"},
            {"id": "qwen3.7-flash", "name": "Qwen3.7-Flash"},
        ],
    },
    {
        "provider_type": "deepseek", "name": "DeepSeek", "color": "#4d6bfe",
        "desc": "深度求索 DeepSeek 系列",
        "base_url": "https://api.deepseek.com/v1",
        "completions_path": "/chat/completions",
        "key_url": "https://platform.deepseek.com/api_keys",
        "models": [
            {"id": "deepseek-v4-pro", "name": "DeepSeek-V4-Pro"},
            {"id": "deepseek-v4-flash", "name": "DeepSeek-V4-Flash"},
        ],
    },
    {
        "provider_type": "moonshot", "name": "月之暗面 Kimi", "color": "#1d1d1f",
        "desc": "Moonshot Kimi 长上下文模型",
        "base_url": "https://api.moonshot.cn/v1",
        "completions_path": "/chat/completions",
        "key_url": "https://platform.moonshot.cn/console/api-keys",
        "models": [
            {"id": "kimi-k3.1-pro", "name": "Kimi-K3.1-Pro"},
            {"id": "kimi-k3.1-pro-highspeed", "name": "Kimi-K3.1-Pro-HighSpeed"},
            {"id": "kimi-k3", "name": "Kimi-K3"},
            {"id": "kimi-k2.6", "name": "Kimi-K2.6"},
        ],
    },
    {
        "provider_type": "ernie", "name": "百度文心一言", "color": "#2932e1",
        "desc": "百度 ERNIE 文心大模型",
        "base_url": "https://qianfan.baidubce.com/v2",
        "completions_path": "/chat/completions",
        "key_url": "https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application",
        "models": [
            {"id": "ernie-5.1", "name": "ERNIE-5.1"},
            {"id": "ernie-5.0", "name": "ERNIE-5.0"},
            {"id": "ernie-5.0-thinking-preview", "name": "ERNIE-5.0-Thinking"},
            {"id": "ernie-4.5-turbo-128k", "name": "ERNIE-4.5-Turbo-128K"},
            {"id": "ernie-4.5-turbo-32k", "name": "ERNIE-4.5-Turbo-32K"},
        ],
    },
    {
        "provider_type": "spark", "name": "讯飞星火", "color": "#e03a2b",
        "desc": "科大讯飞星火认知大模型",
        "base_url": "https://spark-api-open.xf-yun.com/v1",
        "completions_path": "/chat/completions",
        "key_url": "https://console.xfyun.cn/app/myapp",
        "models": [
            {"id": "4.0Ultra", "name": "Spark-4.0-Ultra"},
            {"id": "generalv3.5", "name": "Spark-3.5"},
            {"id": "pro-128k", "name": "Spark-Pro-128K"},
            {"id": "lite", "name": "Spark-Lite"},
        ],
    },
    {
        "provider_type": "hunyuan", "name": "腾讯混元", "color": "#0053e2",
        "desc": "腾讯混元大模型",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "completions_path": "/chat/completions",
        "key_url": "https://console.cloud.tencent.com/hunyuan/api-key",
        "models": [
            {"id": "hunyuan-turbos-latest", "name": "Hunyuan-TurboS"},
            {"id": "hunyuan-T1-latest", "name": "Hunyuan-T1"},
            {"id": "hunyuan-pro", "name": "Hunyuan-Pro"},
            {"id": "hunyuan-a13b", "name": "Hunyuan-A13B"},
            {"id": "hunyuan-role-latest", "name": "Hunyuan-Role"},
        ],
    },
]

_CATALOG_BY_TYPE = {item["provider_type"]: item for item in _CATALOG}

_MAX_KEY_LEN = 512
_PURPOSES = ("chat", "embedding", "rerank", "sql", "vision")

_MODELS_TTL_HOURS = float(os.getenv("AF_PROVIDER_MODELS_TTL_HOURS", "24") or 0)
_REFRESH_BACKOFF = timedelta(hours=1)      
_refreshing: set[int] = set()
_refresh_lock = threading.Lock()
_next_retry: dict[int, datetime] = {}


def _now() -> datetime:
    return datetime.now()


def _join_url(base_url: str, path: str | None) -> str:
    base = (base_url or "").strip().rstrip("/")
    tail = (path or "/chat/completions").strip()
    if not tail.startswith("/"):
        tail = "/" + tail
    if base.endswith(tail):
        return base
    return base + tail


def _models_url(base_url: str, completions_path: str | None) -> str:
    base = (base_url or "").strip().rstrip("/")
    tail = (completions_path or "/chat/completions").strip()
    if not tail.startswith("/"):
        tail = "/" + tail
    if tail.endswith("/chat/completions"):
        tail = tail[: -len("/chat/completions")]
    elif "/" in tail:
        tail = tail.rsplit("/", 1)[0]
    else:
        tail = ""
    return base + tail + "/models"


def _parse_models(payload) -> list[dict]:
    items = payload if isinstance(payload, list) else None
    if items is None and isinstance(payload, dict):
        for key in ("data", "models", "result", "model_list"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
    out: list[dict] = []
    seen: set[str] = set()
    for item in items or []:
        if isinstance(item, str):
            mid, name = item.strip(), item.strip()
        elif isinstance(item, dict):
            mid = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
            name = str(item.get("name") or item.get("display_name") or mid).strip()
        else:
            continue
        if not mid or len(mid) > 128 or mid in seen:
            continue
        seen.add(mid)
        out.append({"id": mid, "name": name or mid})
    return out[:200]



def _masked(cipher: bytes | None, version: int | None) -> str:
    if not cipher:
        return ""
    try:
        return mask_secret(decrypt_secret(cipher, version))
    except SecretDecryptError:
        return "***"


def _stored_models(row) -> list[dict]:
    raw = getattr(row, "models_json", None)
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return []
    out: list[dict] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "").strip()
        if not mid or len(mid) > 128:
            continue
        out.append({"id": mid, "name": str(item.get("name") or mid)[:128]})
    return out[:200]


def _shared_catalog(conn, provider_types: set[str]) -> dict[str, tuple[list[dict], datetime]]:
    out: dict[str, tuple[list[dict], datetime]] = {}
    for ptype in provider_types:
        row = conn.execute(
            select(model_provider.c.models_json, model_provider.c.models_synced_at)
            .where(model_provider.c.provider_type == ptype,
                   model_provider.c.models_json.is_not(None),
                   model_provider.c.models_synced_at.is_not(None))
            .order_by(model_provider.c.models_synced_at.desc()).limit(1)
        ).first()
        if row is None:
            continue
        models = _stored_models(row)
        if models:
            out[ptype] = (models, row.models_synced_at)
    return out


def _provider_payload(row, selected: dict[int, list[str]],
                      shared: dict[str, tuple[list[dict], datetime]] | None = None) -> dict:
    cat = _CATALOG_BY_TYPE.get(row.provider_type, {})
    seen = row.key_fingerprint or ""
    stored = _stored_models(row)
    if stored:
        base_models, source = stored, "live"
        synced = getattr(row, "models_synced_at", None)
    elif (shared or {}).get(row.provider_type, ([], None))[0]:
        base_models, synced = shared[row.provider_type]
        source = "shared"
    else:
        base_models, source, synced = list(cat.get("models") or []), "catalog", None
    selected_models = selected.get(int(row.id), [])
    known = {m["id"] for m in base_models}
    extra = [{"id": mid, "name": mid} for mid in selected_models if mid not in known]
    return {
        "id": int(row.id),
        "name": row.name,
        "provider_type": row.provider_type,
        "base_url": row.base_url,
        "completions_path": row.completions_path or "/chat/completions",
        "enabled": bool(row.enabled),
        "has_key": bool(row.api_key_cipher) or bool(seen),
        "api_key_masked": _masked(row.api_key_cipher, row.key_version),
        "key_fingerprint": seen,
        "color": cat.get("color", "#64748b"),
        "desc": cat.get("desc", "自定义供应商"),
        "key_url": cat.get("key_url", ""),
        "models": base_models + extra,
        "models_source": source,
        "models_synced_at": synced.isoformat(timespec="seconds") if synced else None,
        "selected_models": selected_models,
    }


def _pull_models(row) -> dict:
    try:
        api_key = decrypt_secret(row.api_key_cipher, row.key_version)
    except SecretDecryptError:
        return {"ok": False, "error": "key_undecryptable",
                "hint": "密文无法解密，请重新录入 Key"}
    if not api_key:
        return {"ok": False, "error": "no_key", "hint": "尚未录入 API Key"}
    if not (row.base_url or "").strip():
        return {"ok": False, "error": "no_base_url", "hint": "尚未配置请求地址"}

    url = _models_url(row.base_url, row.completions_path)
    started = time.monotonic()
    try:
        resp = requests.get(
            url, headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
    except Exception as e:  # noqa: BLE001 — 网络错误按字符串脱敏回传
        return {"ok": False, "error": "request_failed",
                "hint": redact_text(str(e))[:300]}
    latency = int((time.monotonic() - started) * 1000)
    if resp.status_code >= 400:
        return {"ok": False, "status": resp.status_code, "error": "http_error",
                "hint": redact_text(resp.text)[:300]}
    try:
        payload = resp.json()
    except ValueError:
        return {"ok": False, "error": "invalid_response",
                "hint": "响应不是 JSON，无法解析模型清单"}
    models = _parse_models(payload)
    if not models:
        return {"ok": False, "error": "empty_models",
                "hint": "服务商未返回可识别的模型清单，可手动添加模型名"}
    return {"ok": True, "models": models, "count": len(models),
            "source": url, "latency_ms": latency}


def _save_models(provider_id: int, tenant_id: int, models: list[dict]) -> datetime | None:
    now = _now()
    with get_engine().begin() as conn:
        hit = conn.execute(update(model_provider).where(
            model_provider.c.id == provider_id,
            model_provider.c.tenant_id == tenant_id,
        ).values(models_json=json.dumps(models, ensure_ascii=False),
                 models_synced_at=now, updated_at=now)).rowcount
    return now if hit else None


def _refresh_worker(provider_id: int, tenant_id: int, row) -> None:
    try:
        result = _pull_models(row)
        if result.get("ok") and _save_models(provider_id, tenant_id, result["models"]):
            _next_retry.pop(provider_id, None)
        else:
            _next_retry[provider_id] = _now() + _REFRESH_BACKOFF
    except Exception:  # noqa: BLE001 — 后台刷新失败只影响下次重试，绝不影响请求
        _next_retry[provider_id] = _now() + _REFRESH_BACKOFF
    finally:
        with _refresh_lock:
            _refreshing.discard(provider_id)


def _refresh_stale_models(rows) -> None:
    if _MODELS_TTL_HOURS <= 0:
        return
    now = _now()
    for row in rows:
        if not row.api_key_cipher or not row.enabled:
            continue
        synced = getattr(row, "models_synced_at", None)
        if synced is not None and now - synced < timedelta(hours=_MODELS_TTL_HOURS):
            continue
        if _next_retry.get(int(row.id), now) > now:
            continue
        with _refresh_lock:
            if int(row.id) in _refreshing:
                continue
            _refreshing.add(int(row.id))
        threading.Thread(target=_refresh_worker,
                         args=(int(row.id), int(row.tenant_id), row),
                         daemon=True).start()



def _seed_if_empty(conn, tenant_id: int) -> None:
    existing = {
        r.name for r in conn.execute(
            select(model_provider.c.name).where(model_provider.c.tenant_id == tenant_id)
        ).all()
    }
    missing = [item for item in _CATALOG if item["name"] not in existing]
    if not missing:
        return
    now = _now()
    for item in missing:
        conn.execute(insert(model_provider).values(
            tenant_id=tenant_id, name=item["name"],
            provider_type=item["provider_type"], base_url=item["base_url"],
            completions_path=item["completions_path"], api_key_cipher=None,
            key_version=1, key_fingerprint="", enabled=0, created_by=None,
            created_at=now, updated_at=now,
        ))



@router.get("/providers")
def list_providers(user: AuthUser = Depends(require_user)) -> dict:
    if user.dev_mode:
        return {"providers": [], "catalog": _CATALOG}
    with get_engine().begin() as conn:
        _seed_if_empty(conn, user.tenant_id)
        rows = conn.execute(
            select(model_provider)
            .where(model_provider.c.tenant_id == user.tenant_id)
            .order_by(model_provider.c.id.asc())
        ).all()
        routes = conn.execute(
            select(model_route.c.provider_id, model_route.c.model)
            .where(model_route.c.tenant_id == user.tenant_id,
                   model_route.c.purpose == "chat",
                   model_route.c.enabled == 1)
        ).all()
        shared = _shared_catalog(conn, {r.provider_type for r in rows})
    selected: dict[int, list[str]] = {}
    for r in routes:
        selected.setdefault(int(r.provider_id), []).append(r.model)
    _refresh_stale_models(rows)          
    return {"providers": [_provider_payload(r, selected, shared) for r in rows]}


class ProviderIn(BaseModel):
    name: str
    provider_type: str
    base_url: str
    completions_path: str | None = "/chat/completions"
    api_key: str | None = None
    enabled: int = 1


@router.post("/providers")
def create_provider(req: ProviderIn, user: AuthUser = Depends(require_db_admin)) -> dict:
    name = (req.name or "").strip()[:64]
    if not name:
        raise HTTPException(status_code=400, detail={"error": "invalid_name"})
    cipher, version = b"", 1
    fp = ""
    if req.api_key:
        if len(req.api_key) > _MAX_KEY_LEN:
            raise HTTPException(status_code=400, detail={"error": "key_too_long"})
        cipher, version = encrypt_secret(req.api_key)
        fp = key_fingerprint(req.api_key)
    now = _now()
    try:
        with get_engine().begin() as conn:
            pid = conn.execute(insert(model_provider).values(
                tenant_id=user.tenant_id, name=name,
                provider_type=(req.provider_type or "custom")[:32],
                base_url=(req.base_url or "").strip()[:512],
                completions_path=(req.completions_path or "/chat/completions")[:256],
                api_key_cipher=cipher or None, key_version=version,
                key_fingerprint=fp, enabled=1 if req.enabled else 0,
                created_by=user.user_id, created_at=now, updated_at=now,
            )).inserted_primary_key[0]
    except IntegrityError:
        raise HTTPException(status_code=409, detail={
            "error": "provider_exists", "hint": "同名供应商已存在",
        })
    return {"ok": True, "provider": _one(pid, user.tenant_id)}


def _one(provider_id: int, tenant_id: int) -> dict:
    with get_engine().connect() as conn:
        row = conn.execute(select(model_provider).where(
            model_provider.c.id == provider_id,
            model_provider.c.tenant_id == tenant_id)).first()
        if row is None:
            raise HTTPException(status_code=404, detail={"error": "provider_not_found"})
        routes = conn.execute(
            select(model_route.c.model).where(
                model_route.c.tenant_id == tenant_id,
                model_route.c.provider_id == provider_id,
                model_route.c.purpose == "chat",
                model_route.c.enabled == 1)
        ).all()
        shared = _shared_catalog(conn, {row.provider_type})
    return _provider_payload(row, {provider_id: [r.model for r in routes]}, shared)


class ProviderPatchIn(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    base_url: str | None = None
    completions_path: str | None = None
    enabled: int | None = None


@router.patch("/providers/{provider_id}")
def patch_provider(provider_id: int, req: ProviderPatchIn,
                   user: AuthUser = Depends(require_db_admin)) -> dict:
    values: dict = {"updated_at": _now()}
    if req.name is not None:
        name = req.name.strip()[:64]
        if not name:
            raise HTTPException(status_code=400, detail={"error": "invalid_name"})
        values["name"] = name
    if req.provider_type is not None:
        values["provider_type"] = req.provider_type.strip()[:32]
    if req.base_url is not None:
        values["base_url"] = req.base_url.strip()[:512]
    if req.completions_path is not None:
        values["completions_path"] = req.completions_path.strip()[:256]
    if req.enabled is not None:
        values["enabled"] = 1 if req.enabled else 0
    try:
        with get_engine().begin() as conn:
            hit = conn.execute(update(model_provider).where(
                model_provider.c.id == provider_id,
                model_provider.c.tenant_id == user.tenant_id,
            ).values(**values)).rowcount
    except IntegrityError:
        raise HTTPException(status_code=409, detail={
            "error": "provider_exists", "hint": "同名供应商已存在",
        })
    if not hit:
        raise HTTPException(status_code=404, detail={"error": "provider_not_found"})
    return {"ok": True, "provider": _one(provider_id, user.tenant_id)}


@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: int, user: AuthUser = Depends(require_db_admin)) -> dict:
    with get_engine().begin() as conn:
        hit = conn.execute(delete(model_provider).where(
            model_provider.c.id == provider_id,
            model_provider.c.tenant_id == user.tenant_id,
        )).rowcount
        if hit:
            conn.execute(delete(model_route).where(
                model_route.c.tenant_id == user.tenant_id,
                model_route.c.provider_id == provider_id))
    if not hit:
        raise HTTPException(status_code=404, detail={"error": "provider_not_found"})
    return {"ok": True}


class KeyIn(BaseModel):
    api_key: str | None = None       


@router.put("/providers/{provider_id}/key")
def set_provider_key(provider_id: int, req: KeyIn,
                     user: AuthUser = Depends(require_db_admin)) -> dict:
    plain = (req.api_key or "").strip()
    if not plain:
        return {"ok": True, "updated": False,
                "provider": _one(provider_id, user.tenant_id)}
    if len(plain) > _MAX_KEY_LEN:
        raise HTTPException(status_code=400, detail={"error": "key_too_long"})
    cipher, version = encrypt_secret(plain)
    with get_engine().begin() as conn:
        hit = conn.execute(update(model_provider).where(
            model_provider.c.id == provider_id,
            model_provider.c.tenant_id == user.tenant_id,
        ).values(api_key_cipher=cipher, key_version=version,
                 key_fingerprint=key_fingerprint(plain),
                 models_json=None, models_synced_at=None,   
                 updated_at=_now())).rowcount
    if not hit:
        raise HTTPException(status_code=404, detail={"error": "provider_not_found"})
    plain = None  # noqa: F841
    return {"ok": True, "updated": True,
            "provider": _one(provider_id, user.tenant_id)}


@router.post("/providers/{provider_id}/test")
def test_provider(provider_id: int, user: AuthUser = Depends(require_db_admin)) -> dict:
    with get_engine().connect() as conn:
        row = conn.execute(select(model_provider).where(
            model_provider.c.id == provider_id,
            model_provider.c.tenant_id == user.tenant_id)).first()
        route_model = conn.execute(
            select(model_route.c.model).where(
                model_route.c.tenant_id == user.tenant_id,
                model_route.c.provider_id == provider_id,
                model_route.c.purpose == "chat",
                model_route.c.enabled == 1)
            .order_by(model_route.c.priority.asc()).limit(1)
        ).scalar()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "provider_not_found"})
    try:
        api_key = decrypt_secret(row.api_key_cipher, row.key_version)
    except SecretDecryptError:
        return {"ok": False, "error": "key_undecryptable",
                "hint": "密文无法解密，请重新录入 Key"}
    if not api_key:
        return {"ok": False, "error": "no_key", "hint": "尚未录入 API Key"}

    cat = _CATALOG_BY_TYPE.get(row.provider_type, {})
    stored = _stored_models(row)
    model = route_model or (stored[0]["id"] if stored
                            else (cat.get("models") or [{}])[0].get("id"))
    if not model:
        return {"ok": False, "error": "no_model", "hint": "请先勾选至少一个模型"}
    url = _join_url(row.base_url, row.completions_path)
    started = time.monotonic()
    try:
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": "ping"}],
                  "max_tokens": 8, "stream": False},
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001 — 网络错误按字符串脱敏回传
        return {"ok": False, "error": "request_failed",
                "hint": redact_text(str(e))[:300]}
    latency = int((time.monotonic() - started) * 1000)
    if resp.status_code >= 400:
        return {"ok": False, "status": resp.status_code,
                "error": "http_error", "hint": redact_text(resp.text)[:300]}
    return {"ok": True, "latency_ms": latency, "model": model}


@router.post("/providers/{provider_id}/models")
def fetch_provider_models(provider_id: int,
                          user: AuthUser = Depends(require_db_admin)) -> dict:
    with get_engine().connect() as conn:
        row = conn.execute(select(model_provider).where(
            model_provider.c.id == provider_id,
            model_provider.c.tenant_id == user.tenant_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "provider_not_found"})

    result = _pull_models(row)
    if not result.get("ok"):
        return result
    synced = _save_models(provider_id, user.tenant_id, result["models"])
    if synced is None:
        raise HTTPException(status_code=404, detail={"error": "provider_not_found"})
    _next_retry.pop(provider_id, None)
    result["synced_at"] = synced.isoformat(timespec="seconds")
    return result



@router.get("/routes")
def list_routes(purpose: str = Query("chat"),
                user: AuthUser = Depends(require_user)) -> dict:
    if purpose not in _PURPOSES:
        raise HTTPException(status_code=400, detail={"error": "invalid_purpose"})
    if user.dev_mode:
        return {"purpose": purpose, "routes": []}
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(
                model_route.c.id, model_route.c.provider_id, model_route.c.model,
                model_route.c.priority, model_route.c.weight,
                model_route.c.enabled, model_route.c.fallback_order,
                model_provider.c.name,
            )
            .select_from(model_route.join(
                model_provider, model_provider.c.id == model_route.c.provider_id))
            .where(model_route.c.tenant_id == user.tenant_id,
                   model_route.c.purpose == purpose)
            .order_by(model_route.c.priority.asc(), model_route.c.id.asc())
        ).all()
    return {"purpose": purpose, "routes": [
        {"id": int(r.id), "provider_id": int(r.provider_id),
         "provider_name": r.name, "model": r.model, "priority": int(r.priority),
         "weight": int(r.weight), "enabled": bool(r.enabled),
         "fallback_order": int(r.fallback_order)}
        for r in rows
    ]}


class RouteItem(BaseModel):
    provider_id: int
    model: str
    priority: int = 100
    weight: int = 1
    fallback_order: int = 0
    max_tokens: int | None = None
    temperature: float | None = None
    enabled: int = 1


class RoutesIn(BaseModel):
    purpose: str = "chat"
    models: list[RouteItem] = []


@router.put("/routes")
def replace_routes(req: RoutesIn, user: AuthUser = Depends(require_db_admin)) -> dict:
    if req.purpose not in _PURPOSES:
        raise HTTPException(status_code=400, detail={"error": "invalid_purpose"})
    with get_engine().begin() as conn:
        owned = {
            int(r.id) for r in conn.execute(
                select(model_provider.c.id).where(
                    model_provider.c.tenant_id == user.tenant_id)).all()
        }
        for item in req.models:
            if int(item.provider_id) not in owned:
                raise HTTPException(status_code=404, detail={
                    "error": "provider_not_found",
                    "hint": f"供应商 {item.provider_id} 不属于当前租户",
                })
        conn.execute(delete(model_route).where(
            model_route.c.tenant_id == user.tenant_id,
            model_route.c.purpose == req.purpose))
        seen: set[tuple[int, str]] = set()
        for item in req.models:
            key = (int(item.provider_id), item.model[:128])
            if key in seen:
                continue
            seen.add(key)
            conn.execute(insert(model_route).values(
                tenant_id=user.tenant_id, purpose=req.purpose,
                provider_id=key[0], model=key[1],
                priority=int(item.priority), weight=max(1, int(item.weight)),
                max_tokens=item.max_tokens,
                temperature=item.temperature,
                fallback_order=int(item.fallback_order),
                enabled=1 if item.enabled else 0, extra_json=None,
            ))
    return {"ok": True, "purpose": req.purpose, "count": len(seen)}



def _allowed_routes(conn, tenant_id: int, purpose: str) -> list[dict]:
    rows = conn.execute(
        select(
            model_route.c.provider_id, model_route.c.model,
            model_route.c.priority, model_route.c.fallback_order,
            model_provider.c.name,
        ).select_from(model_route.join(
            model_provider, model_provider.c.id == model_route.c.provider_id,
        )).where(
            model_route.c.tenant_id == tenant_id,
            model_route.c.purpose == purpose,
            model_route.c.enabled == 1,
            model_provider.c.enabled == 1,
            model_provider.c.api_key_cipher.isnot(None),
        ).order_by(model_route.c.priority.asc(),
                   model_route.c.fallback_order.asc(),
                   model_route.c.id.asc())
    ).all()
    return [{"provider_id": int(r.provider_id), "provider_name": r.name,
             "model": r.model, "priority": int(r.priority)} for r in rows]


class ModelPrefIn(BaseModel):
    purpose: str = "chat"
    provider_id: int | None = None
    model: str | None = None


@router.get("/model-pref")
def get_model_pref(purpose: str = Query("chat"),
                   user: AuthUser = Depends(require_user)) -> dict:
    if purpose not in _PURPOSES:
        raise HTTPException(status_code=400, detail={"error": "invalid_purpose"})
    if user.dev_mode:
        return {"purpose": purpose, "options": [], "selected": None}
    with get_engine().connect() as conn:
        options = _allowed_routes(conn, user.tenant_id, purpose)
        try:
            pref = conn.execute(
                select(user_model_pref.c.provider_id, user_model_pref.c.model)
                .where(user_model_pref.c.tenant_id == user.tenant_id,
                       user_model_pref.c.user_id == user.user_id,
                       user_model_pref.c.purpose == purpose)
            ).first()
        except Exception:  # noqa: BLE001 — 迁移未执行（表缺失）时忽略偏好，不拖垮接口
            pref = None
    selected = None
    if pref is not None:
        key = (int(pref.provider_id), pref.model)
        if any((o["provider_id"], o["model"]) == key for o in options):
            selected = {"provider_id": key[0], "model": key[1]}
    return {"purpose": purpose, "options": options, "selected": selected}


@router.put("/model-pref")
def set_model_pref(req: ModelPrefIn, user: AuthUser = Depends(require_user)) -> dict:
    if req.purpose not in _PURPOSES:
        raise HTTPException(status_code=400, detail={"error": "invalid_purpose"})
    with get_engine().begin() as conn:
        where = (user_model_pref.c.tenant_id == user.tenant_id,
                 user_model_pref.c.user_id == user.user_id,
                 user_model_pref.c.purpose == req.purpose)
        if req.provider_id is None or not (req.model or "").strip():
            conn.execute(delete(user_model_pref).where(*where))
            return {"ok": True, "selected": None}
        key = (int(req.provider_id), (req.model or "").strip()[:128])
        allowed = {(o["provider_id"], o["model"])
                   for o in _allowed_routes(conn, user.tenant_id, req.purpose)}
        if key not in allowed:
            raise HTTPException(status_code=404, detail={
                "error": "model_not_allowed",
                "hint": "只能选择管理员已配置 Key 且已启用的模型",
            })
        conn.execute(delete(user_model_pref).where(*where))
        try:
            conn.execute(insert(user_model_pref).values(
                tenant_id=user.tenant_id, user_id=user.user_id,
                purpose=req.purpose, provider_id=key[0], model=key[1],
                updated_at=_now(),
            ))
        except Exception as e:  # noqa: BLE001 — 表缺失即迁移未执行，给明确指引
            raise HTTPException(status_code=503, detail={
                "error": "pref_unavailable",
                "hint": "请先执行 docs/mysql/007_user_model_pref.sql 建表",
            }) from e
    return {"ok": True, "selected": {"provider_id": key[0], "model": key[1]}}
