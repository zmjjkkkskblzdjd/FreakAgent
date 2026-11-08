
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import MISSING, fields, is_dataclass
from types import UnionType
from typing import Any, Literal, TypeVar, Union, get_args, get_origin, get_type_hints

__all__ = [
    "strip_json_fence",
    "parse_json_safe",
    "check_enum",
    "missing_keys",
    "build_dataclass",
]

T = TypeVar("T")

_FENCE_RE = re.compile(r"```[A-Za-z0-9_-]*\s*(.*?)\s*```", re.DOTALL)


def strip_json_fence(text: str) -> str:
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def parse_json_safe(text: str) -> dict[str, Any]:
    cleaned = strip_json_fence(text)
    if not cleaned:
        raise ValueError("模型返回内容为空，无法解析为 JSON")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON 解析失败：{exc}；原文前 200 字符：{cleaned[:200]!r}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(f"JSON 根节点不是对象 (dict)，而是 {type(data).__name__}")
    return data


def check_enum(value: object, allowed: Sequence[str], field: str = "值") -> str | None:
    if value in allowed:
        return None
    return f"{field} 的值 {value!r} 不在允许范围内，必须是 {list(allowed)} 之一"


def missing_keys(data: Mapping[str, Any], required: Iterable[str]) -> list[str]:
    return [key for key in required if key not in data]


def build_dataclass(cls: type[T], data: Mapping[str, Any], *, strict: bool = False) -> T:
    if not is_dataclass(cls):
        raise TypeError(f"build_dataclass 只接受 dataclass 类型，收到 {cls!r}")
    if not isinstance(data, Mapping):
        raise ValueError(f"{cls.__name__} 需要 dict 输入，实际是 {type(data).__name__}")

    hints = get_type_hints(cls)
    field_names = {f.name for f in fields(cls)}
    unknown = [key for key in data if key not in field_names]
    if unknown and strict:
        raise ValueError(f"{cls.__name__} 收到未声明的字段：{', '.join(unknown)}")

    errors: list[str] = []
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data:
            if f.default is MISSING and f.default_factory is MISSING:
                errors.append(f"缺少必填字段 {cls.__name__}.{f.name}")
            continue
        try:
            kwargs[f.name] = _build_value(
                hints.get(f.name, Any), data[f.name], f"{cls.__name__}.{f.name}", strict
            )
        except ValueError as exc:
            errors.append(str(exc))

    if errors:
        raise ValueError(
            f"{cls.__name__} 构造失败：\n" + "\n".join(f"- {e}" for e in errors)
        )
    return cls(**kwargs)


def _accepts_none(hint: Any) -> bool:
    if hint is Any or hint is type(None):
        return True
    if get_origin(hint) in (Union, UnionType):
        return type(None) in get_args(hint)
    return False


def _build_value(hint: Any, value: Any, path: str, strict: bool) -> Any:
    if value is None:
        if _accepts_none(hint):
            return None
        raise ValueError(f"{path} 收到 null，但该字段不允许为空")

    origin = get_origin(hint)

    if origin is Literal:
        allowed = get_args(hint)
        if value in allowed:
            return value
        raise ValueError(f"{path} 的值 {value!r} 不在允许范围 {list(allowed)} 内")

    if is_dataclass(hint):
        if not isinstance(value, Mapping):
            raise ValueError(
                f"{path} 需要 dict（对应 {hint.__name__}），实际是 {type(value).__name__}"
            )
        return build_dataclass(hint, value, strict=strict)

    if origin is list:
        args = get_args(hint)
        item_hint = args[0] if args else Any
        if isinstance(value, (list, tuple)):
            return [
                _build_value(item_hint, item, f"{path}[{i}]", strict)
                for i, item in enumerate(value)
            ]
        raise ValueError(f"{path} 需要 list，实际是 {type(value).__name__}")

    if origin is dict or hint is dict or hint is Any:
        return value

    if isinstance(hint, type):
        if hint is tuple and isinstance(value, (list, tuple)):
            return tuple(value)
        if hint in (int, float) and isinstance(value, bool):
            raise ValueError(f"{path} 需要 {hint.__name__}，实际是 bool")
        if isinstance(value, hint):
            return value
        raise ValueError(f"{path} 需要 {hint.__name__}，实际是 {type(value).__name__}")

    return value
