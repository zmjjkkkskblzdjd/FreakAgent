from __future__ import annotations

import inspect
from typing import Any, Callable, get_args, get_origin, get_type_hints

from core.types import ToolDefinition


def _json_schema_type_from_annotation(annotation: Any) -> tuple[str, bool]:
    if annotation is inspect._empty:
        return "string", False
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is None:
        if annotation is str:
            return "string", False
        if annotation is int:
            return "integer", False
        if annotation is float:
            return "number", False
        if annotation is bool:
            return "boolean", False
        if annotation is dict:
            return "object", False
        if annotation is list:
            return "array", False
        return "string", False

    if origin in {list, tuple}:
        return "array", False

    if origin is dict:
        return "object", False

    if origin is Callable:
        return "string", False

    non_none_args = [arg for arg in args if arg is not type(None)]
    if len(non_none_args) == 1 and len(non_none_args) != len(args):
        inner_type, _ = _json_schema_type_from_annotation(non_none_args[0])
        return inner_type, True

    return "string", False


def build_parameters_from_signature(
    func: Callable[..., dict[str, Any]],
    parameter_descriptions: dict[str, str] | None = None,
) -> dict[str, Any]:

    signature = inspect.signature(func)

    type_hints = get_type_hints(func)
    descriptions = parameter_descriptions or {}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, parameter in signature.parameters.items():
        annotation = type_hints.get(param_name, parameter.annotation)
        json_type, is_optional = _json_schema_type_from_annotation(annotation)

        properties[param_name] = {
            "type": json_type,
            "description": descriptions.get(param_name, f"参数：{param_name}"),
        }

        if parameter.default is inspect._empty and not is_optional:
            required.append(param_name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }

    if required:
        schema["required"] = required

    return schema


def tool(
    func: Callable[..., dict[str, Any]] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    parameters: dict[str, Any] | None = None,
    parameter_descriptions: dict[str, str] | None = None,
    risk: str = "read",
    requires_confirmation: bool = False,
) -> Callable[..., dict[str, Any]] | Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:

    def decorator(inner_func: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        tool_name = name or inner_func.__name__
        tool_description = description or inspect.getdoc(inner_func) or f"Tool: {tool_name}"
        tool_parameters = parameters or build_parameters_from_signature(
            inner_func,
            parameter_descriptions=parameter_descriptions,
        )

        setattr(
            inner_func,
            "__tool_definition__",
            ToolDefinition(
                name=tool_name,
                description=tool_description,
                parameters=tool_parameters,
                handler=inner_func,
                risk=risk,
                requires_confirmation=requires_confirmation,
            ),
        )
        return inner_func

    if func is not None:
        return decorator(func)

    return decorator
