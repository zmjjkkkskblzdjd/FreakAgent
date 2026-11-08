
from typing import Any

__all__ = ["ok", "err", "from_exception"]

_RESERVED_KEYS = ("ok", "data", "error", "kind")


def _check_reserved(extra: dict[str, Any]) -> None:
    clash = [key for key in extra if key in _RESERVED_KEYS]
    if clash:
        raise ValueError(f"附加字段与保留键冲突：{clash}（保留键：{list(_RESERVED_KEYS)}）")


def ok(data: Any = None, **extra: Any) -> dict[str, Any]:
    _check_reserved(extra)
    return {"ok": True, "data": data, **extra}


def err(error: str, kind: str = "other", **extra: Any) -> dict[str, Any]:
    if not isinstance(error, str):
        error = str(error)
    _check_reserved(extra)
    return {"ok": False, "error": error, "kind": kind, **extra}


def from_exception(exc: Exception, kind: str = "other", prefix: str = "") -> dict[str, Any]:
    message = f"{prefix}{exc}" if prefix else f"{type(exc).__name__}：{exc}"
    return err(message, kind=kind)
