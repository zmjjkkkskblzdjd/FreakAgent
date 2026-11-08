
from __future__ import annotations

import hashlib
import hmac
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar

import httpx

from config import get_app_config
from data_analyze_agent.core.types import ColumnMeta, DBError, QueryResult, TableMeta

__all__ = [
    "query",
    "list_tables",
    "get_table_schema",
    "introspect",
    "health",
    "use_datasource",
    "use_default_channel",
    "GatewayUnavailable",
    "META_TABLE_PREFIXES",
    "is_business_table",
]


_DS_SCOPE: ContextVar[dict | None] = ContextVar("af_gateway_ds_scope", default=None)


@contextmanager
def use_datasource(datasource_id=None, tenant_id=None, user_id=None,
                   role: str = "member"):
    if datasource_id in (None, "", 0):
        yield
        return
    scope = {
        "datasource_id": int(datasource_id),
        "tenant_id": _as_int(tenant_id),
        "user_id": _as_int(user_id),
        "role": (role or "member").strip() or "member",
    }
    token = _DS_SCOPE.set(scope)
    try:
        yield
    finally:
        _DS_SCOPE.reset(token)


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _scope() -> dict:
    return _DS_SCOPE.get() or {}


def current_datasource_id() -> int | None:
    return (_DS_SCOPE.get() or {}).get("datasource_id")


@contextmanager
def use_default_channel():
    token = _DS_SCOPE.set(None)
    try:
        yield
    finally:
        _DS_SCOPE.reset(token)


_BUSINESS_KIND = {
    "unknown_table": "unknown_table",
    "invalid_sql": "syntax",
    "degrade_query_not_allowed": "syntax",
    "query_error": "other",
    "datasource_not_found": "unknown_table",
    "write_forbidden": "other",
    "guard_rejected": "other",
    "too_many_rows": "other",
    "actor_required": "other",
    "preview_token_required": "other",
    "preview_invalid": "other",
    "preview_expired": "other",
}


def _detail(resp: httpx.Response) -> tuple[str, str, dict]:
    try:
        raw = resp.json()
    except ValueError:
        raw = {}
    detail = raw.get("detail", raw) if isinstance(raw, dict) else {}
    if isinstance(detail, dict):
        code = str(detail.get("error_code") or "")
        message = str(detail.get("error_message") or "")
        if not code and not message:
            message = resp.text[:300]
        return code, message or resp.text[:300], detail
    return "", str(detail)[:300], {}


class GatewayUnavailable(RuntimeError):
    pass


META_TABLE_PREFIXES: tuple[str, ...] = ("kb_",)


def is_business_table(name: str) -> bool:
    return not name.startswith(META_TABLE_PREFIXES)


def _base() -> str:
    return get_app_config().gateway.base_url.rstrip("/")


def _timeout() -> float:
    return get_app_config().gateway.timeout_ms / 1000


def _signed_headers(path: str, body: bytes) -> dict[str, str]:
    secret = get_app_config().gateway.secret
    if not secret:
        return {}
    ts = str(int(time.time()))
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{ts}\n{path}\n{hashlib.sha256(body or b'').hexdigest()}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"X-AF-TS": ts, "X-AF-SIGN": digest}


def _get(path: str, params: dict | None, timeout: float) -> httpx.Response:
    return httpx.get(f"{_base()}{path}", params=params, timeout=timeout,
                     headers=_signed_headers(path, b""))


def _post(path: str, payload: dict, timeout: float) -> httpx.Response:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", **_signed_headers(path, body)}
    return httpx.post(f"{_base()}{path}", content=body, headers=headers, timeout=timeout)


def health() -> bool:
    try:
        resp = httpx.get(f"{_base()}/health", timeout=5.0)
        return resp.status_code == 200 and bool(resp.json().get("ok"))
    except httpx.HTTPError:
        return False


def list_tables() -> list[dict]:
    cfg = get_app_config().gateway
    scope = _scope()
    params = {"database": cfg.database}
    if scope:
        params.update({"datasource_id": scope["datasource_id"],
                       "tenant_id": scope.get("tenant_id")})
    try:
        resp = _get("/catalog/tables", params, _timeout())
    except httpx.HTTPError as exc:
        raise GatewayUnavailable(f"网关不可达（{_base()}）：{exc}") from exc
    if resp.status_code != 200:
        raise GatewayUnavailable(
            f"网关 /catalog/tables 返回 {resp.status_code}：{resp.text[:200]}"
        )
    return resp.json().get("tables", [])


def get_table_schema(table: str) -> dict:
    cfg = get_app_config().gateway
    scope = _scope()
    bare = table.split(".")[-1] if "." in table else table
    db = cfg.database
    params = {"database": db, "table": bare}
    if scope:
        db = str(scope["datasource_id"])
        params.update({"datasource_id": scope["datasource_id"],
                       "tenant_id": scope.get("tenant_id")})
    full = f"{db}.{bare}"
    try:
        resp = _get("/catalog/schema", params, _timeout())
    except httpx.HTTPError as exc:
        raise GatewayUnavailable(f"网关不可达（{_base()}）：{exc}") from exc
    if resp.status_code == 404:
        raise ValueError(f"网关 catalog 中不存在表 {full}")
    if resp.status_code != 200:
        raise GatewayUnavailable(
            f"网关 /catalog/schema 返回 {resp.status_code}：{resp.text[:200]}"
        )
    return resp.json()


def introspect() -> list[TableMeta]:
    metas: list[TableMeta] = []
    for t in list_tables():
        if not is_business_table(t["name"]):
            continue                     
        schema = get_table_schema(t["name"])
        cols = [
            ColumnMeta(
                name=c["name"],
                data_type=c.get("type", ""),
                is_nullable=True,            
                column_key="",               
                column_comment=c.get("comment", "") or "",
                extra="",
            )
            for c in schema.get("columns", [])
        ]
        ddl = "\n".join(
            [f"{t['name']} ({t.get('engine', '')})"]
            + [f"  {c.name} {c.data_type}" for c in cols]
        )
        schema_hash = hashlib.md5(
            json.dumps(schema.get("columns", []), sort_keys=True,
                       ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        metas.append(TableMeta(
            table_name=t["name"],
            table_comment=t.get("comment", "") or "",
            ddl=ddl,
            columns=cols,
            schema_hash=schema_hash,
        ))
    return metas


_CH_ERRNO_KIND = {
    60: "unknown_table",
    47: "unknown_column",
    62: "syntax",
    159: "timeout",
    160: "timeout",
}


def query(sql: str) -> dict:
    cfg = get_app_config().gateway
    scope = _scope()
    payload = {"sql": sql, "timeout_ms": cfg.timeout_ms, "max_limit": cfg.max_rows}
    if scope:
        payload.update({"datasource_id": scope["datasource_id"],
                        "tenant_id": scope.get("tenant_id")})
    try:
        resp = _post("/query", payload, _timeout() + 5)   
    except httpx.HTTPError as exc:
        raise GatewayUnavailable(f"网关不可达（{_base()}）：{exc}") from exc

    if 400 <= resp.status_code < 500:
        code, message, _ = _detail(resp)
        kind = _BUSINESS_KIND.get(code, "other")
        label = f"[{code}] " if code else ""
        return {"ok": False, "data": DBError(
            kind=kind, message=f"{label}{message}" if message else label.rstrip() or "查询被网关拒绝")}
    if resp.status_code != 200:
        raise GatewayUnavailable(
            f"网关 /query 返回 {resp.status_code}：{resp.text[:200]}"
        )

    payload = resp.json()
    if not payload.get("ok"):
        errno = int(payload["error_code"]) if str(
            payload.get("error_code", "")).isdigit() else None
        kind = _CH_ERRNO_KIND.get(errno, "other")
        raw_code = "" if errno is not None else str(payload.get("error_code") or "")
        label = f"[{raw_code}] " if raw_code else ""
        return {"ok": False, "data": DBError(
            kind=kind,
            message=label + (payload.get("error_message", "") or "查询失败（网关未附错误信息）"),
            errno=errno,
        )}

    columns = [c["name"] for c in payload.get("columns", [])]
    rows = [tuple(r) for r in payload.get("rows", [])]
    return {"ok": True, "data": QueryResult(
        columns=columns,
        rows=rows,
        row_count=payload.get("row_count", len(rows)),
        truncated=bool(payload.get("truncated", False)),
        duration_ms=payload.get("elapsed_ms", 0),
        executed_sql=payload.get("executed_sql", "") or sql,
        source=payload.get("source", "") or "",
        mirror_note=payload.get("mirror_note", "") or "",
        lag_ms=payload.get("lag_ms"),
    )}


def execute_write(sql: str, target: str | None = None,
                  estimated_rows: int | None = None, dry_run: bool = False,
                  preview_token: str | None = None,
                  transactional_preview: bool = False) -> dict:
    scope = _scope()
    payload = {"sql": sql, "estimated_rows": estimated_rows, "dry_run": dry_run}
    if dry_run and transactional_preview:
        payload["transactional_preview"] = True
    if scope:
        payload.update({"datasource_id": scope["datasource_id"],
                        "tenant_id": scope.get("tenant_id"),
                        "user_id": scope.get("user_id"),
                        "role": scope.get("role") or "member"})
    if preview_token:
        payload["preview_token"] = preview_token
    try:
        resp = _post("/write", payload,
                     _timeout() + 40)         
    except httpx.HTTPError as exc:
        raise GatewayUnavailable(f"网关不可达（{_base()}）：{exc}") from exc

    if 400 <= resp.status_code < 500:
        code, message, detail = _detail(resp)
        label = f"[{code}] " if code else ""
        data = DBError(kind="other", message=f"{label}{message}")
        out = {"ok": False, "data": data}
        if detail.get("audit_id"):
            out["audit_id"] = detail["audit_id"]
        return out
    if resp.status_code != 200:
        raise GatewayUnavailable(f"网关 /write 返回 {resp.status_code}：{resp.text[:200]}")

    payload = resp.json()
    if not payload.get("ok"):
        code = str(payload.get("error_code") or "unknown")
        return {"ok": False, "data": DBError(
            kind="other",
            message=f"[{code}] "
                    f"{payload.get('error_message', '') or '写失败（网关未附错误信息）'}",
        )}
    return {"ok": True, "data": payload}


def undo_write(audit_id: str, *, tenant_id: int, user_id: int,
               datasource_id: int | None = None) -> dict:
    payload: dict = {"tenant_id": tenant_id, "user_id": user_id}
    if datasource_id:
        payload["datasource_id"] = datasource_id
    try:
        resp = _post(f"/write/{audit_id}/undo", payload, _timeout())
    except httpx.HTTPError as exc:
        raise GatewayUnavailable(f"网关不可达（{_base()}）：{exc}") from exc
    if 400 <= resp.status_code < 500:
        code, message, _ = _detail(resp)
        return {"ok": False, "error_code": code or "undo_failed",
                "message": message or "撤回被网关拒绝"}
    if resp.status_code != 200:
        raise GatewayUnavailable(
            f"网关 /write/{audit_id}/undo 返回 {resp.status_code}：{resp.text[:200]}")
    return {"ok": True, "data": resp.json()}
