# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from . import config

_EXEMPT_PATHS = {"/health"}


def body_sha256(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def canonical(ts: str, path: str, body: bytes) -> str:
    return f"{ts}\n{path}\n{body_sha256(body)}"


def sign(secret: str, ts: str, path: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), canonical(ts, path, body).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def signed_headers(secret: str, path: str, body: bytes,
                   ts: int | None = None) -> dict[str, str]:
    stamp = str(int(time.time()) if ts is None else ts)
    return {"X-AF-TS": stamp, "X-AF-SIGN": sign(secret, stamp, path, body)}


def _deny(code: str, message: str, status: int = 401) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"ok": False, "error_code": code,
                                 "error_message": message})


async def verify_signature(request: Request, call_next):
    if not config.GATEWAY_SECRET or request.url.path in _EXEMPT_PATHS:
        return await call_next(request)

    stamp = (request.headers.get("x-af-ts") or "").strip()
    signature = (request.headers.get("x-af-sign") or "").strip()
    if not stamp or not signature:
        return _deny("unauthorized", "缺少内部签名（X-AF-TS / X-AF-SIGN）")
    try:
        ts = int(stamp)
    except ValueError:
        return _deny("unauthorized", "X-AF-TS 非法")
    if abs(time.time() - ts) > config.AUTH_SKEW_SEC:
        return _deny("signature_expired", "签名时间戳超出允许窗口")

    body = await request.body()
    expected = sign(config.GATEWAY_SECRET, stamp, request.url.path, body)
    if not hmac.compare_digest(expected, signature):
        return _deny("unauthorized", "签名校验失败")

    return await call_next(request)
