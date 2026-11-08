# -*- coding: utf-8 -*-
import json
import urllib.error
import urllib.parse
import urllib.request

from . import config


class ClickHouseError(Exception):
    def __init__(self, message: str, ch_code: int | None = None):
        super().__init__(message)
        self.ch_code = ch_code


def _ch_code(text: str) -> int | None:
    if not text.startswith("Code:"):
        return None
    try:
        return int(text.split("Code:")[1].split(".")[0].strip().split(",")[0])
    except (IndexError, ValueError):
        return None


def _json_exception(body: str) -> str:
    stripped = body.strip()
    if not stripped.startswith("{"):
        return body
    try:
        exc = json.loads(stripped).get("exception")
    except (json.JSONDecodeError, AttributeError):
        return body
    return str(exc) if exc else body


def _http_post(sql: str, timeout_ms: int, *, user: str = "", password: str = "",
               expect_json: bool = True) -> dict | None:
    params = {
        "max_execution_time": max(1, round(timeout_ms / 1000)),
        "max_memory_usage": config.MAX_MEMORY_BYTES,
        "max_threads": config.MAX_THREADS,
        "output_format_json_quote_64bit_integers": 1,
    }
    url = config.CH_HTTP + "?" + urllib.parse.urlencode(params)
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if user:
        headers["X-ClickHouse-User"] = user
        headers["X-ClickHouse-Key"] = password
    req = urllib.request.Request(url, data=sql.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_ms / 1000 + 5) as resp:
            body = resp.read().decode("utf-8")
            if not expect_json:
                return None
            payload = json.loads(body)
            exc = payload.get("exception") if isinstance(payload, dict) else None
            if exc:
                text = str(exc).strip()
                raise ClickHouseError(text[:800], _ch_code(text))
            return payload
    except urllib.error.HTTPError as e:
        detail = _json_exception(
            e.read().decode("utf-8", errors="replace")).strip()
        raise ClickHouseError(detail[:800], _ch_code(detail)) from None
    except urllib.error.URLError as e:
        raise ClickHouseError(f"无法连接 ClickHouse({config.CH_HTTP}): {e.reason}") from None


def query_json(sql: str, timeout_ms: int = config.DEFAULT_TIMEOUT_MS) -> dict:
    final_sql = sql.rstrip().rstrip(";") + "\nFORMAT JSON"
    return _http_post(final_sql, timeout_ms, user=config.CH_USER, password=config.CH_PASSWORD)


def ping() -> bool:
    try:
        _http_post("SELECT 1", 5000, user=config.CH_USER, password=config.CH_PASSWORD)
        return True
    except ClickHouseError:
        return False
