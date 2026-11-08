from __future__ import annotations

import ast
from decimal import Decimal, InvalidOperation
from typing import Any

_ALLOWED_NODES: tuple[type, ...] = (
    ast.Expression,       
    ast.BinOp,            
    ast.UnaryOp,          
    ast.Constant,         
    ast.Name,             
    ast.Load,             
    ast.Call,             
    ast.List,             
    ast.Tuple,            
    ast.Subscript,        
    ast.Index,            
    ast.Slice,            
    ast.Compare,          
    ast.IfExp,            
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
    ast.UAdd, ast.USub, ast.FloorDiv,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.And, ast.Or, ast.BoolOp,
)

_ALLOWED_OPS: tuple[type, ...] = (
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
    ast.UAdd, ast.USub,
)


class _SandboxVisitor(ast.NodeVisitor):

    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"不允许的语法：{type(node).__name__}")
        super().generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if not isinstance(node.op, _ALLOWED_OPS):
            raise ValueError(f"不允许的运算符：{type(node.op).__name__}")
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        if not isinstance(node.op, _ALLOWED_OPS):
            raise ValueError(f"不允许的一元运算符：{type(node.op).__name__}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        self.generic_visit(node)



def _sum(values: list[Any]) -> Decimal:
    return sum(_to_decimal(v) for v in values)


def _avg(values: list[Any]) -> Decimal:
    if not values:
        raise ValueError("avg() 参数不能为空")
    return _sum(values) / Decimal(len(values))


def _min(values: list[Any]) -> Any:
    return min(values)


def _max(values: list[Any]) -> Any:
    return max(values)


def _round2(v: Any) -> Decimal:
    d = _to_decimal(v)
    return d.quantize(Decimal("0.01"))


def _roundN(v: Any, n: int) -> Decimal:
    d = _to_decimal(v)
    q = Decimal(10) ** -int(n)
    return d.quantize(q)


def _percent(numerator: Any, denominator: Any) -> Decimal:
    num = _to_decimal(numerator)
    den = _to_decimal(denominator)
    if den == 0:
        raise ValueError("百分比分母为 0")
    return num / den * Decimal("100")


def _times(a: Any, b: Any) -> Decimal:
    da = _to_decimal(a)
    db = _to_decimal(b)
    if db == 0:
        raise ValueError("倍数计算除数为 0")
    return da / db


def _yoy(cur: Any, prev: Any) -> Decimal:
    dc = _to_decimal(cur)
    dp = _to_decimal(prev)
    if dp == 0:
        raise ValueError("同比分母为 0")
    return (dc - dp) / dp * Decimal("100")


def _diff(a: Any, b: Any) -> Decimal:
    return _to_decimal(a) - _to_decimal(b)


_BUILTINS: dict[str, Any] = {
    "sum": _sum, "avg": _avg, "min": _min, "max": _max,
    "round2": _round2, "roundN": _roundN,
    "percent": _percent, "times": _times, "yoy": _yoy, "diff": _diff,
    "Decimal": Decimal,
}


def _to_decimal(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):   
        return Decimal(int(v))
    if isinstance(v, (int, float)):
        return Decimal(str(v))   
    if isinstance(v, str):
        try:
            return Decimal(v)
        except InvalidOperation:
            raise ValueError(f"无法解析为 Decimal：{v!r}")
    raise ValueError(f"不支持的数值类型：{type(v).__name__}")


def safe_eval(expr: str, *, vars: dict[str, Any] | None = None) -> Any:
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("表达式不能为空")

    tree = ast.parse(expr.strip(), mode="eval")

    class _StrictVisitor(_SandboxVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            allowed = set(_BUILTINS.keys())
            if vars:
                allowed.update(vars.keys())
            if node.id not in allowed:
                raise ValueError(f"未知变量：{node.id}（可用：{sorted(allowed)}）")

    _StrictVisitor().visit(tree)

    exec_globals = {"__builtins__": {}}
    exec_globals.update(_BUILTINS)
    if vars:
        exec_globals.update(vars)

    try:
        result = eval(compile(tree, "<sandbox>", "eval"), exec_globals)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"表达式执行错误：{e}") from e

    if isinstance(result, (int, float, str)) and not isinstance(result, bool):
        return _to_decimal(result)
    if isinstance(result, list):
        return [_to_decimal(v) for v in result]
    return result



RUN_PYTHON_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": (
            "对你已经持有的数字执行算术运算。只用于纯数值计算："
            "加减乘除、百分比/倍数/同比、列表求和/平均、排序后取 TopN。"
            "表达式必须是单个 Python 表达式（不是语句块）。"
            "可用变量在下方 vars 字段里——直接用变量名引用。"
            "返回值会被你用来改写最终答案。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": (
                        "Python 算术表达式，如 'percent(budget, gmv_2025)' 或 "
                        "'times(gmv_2025, budget)' 或 'round2(sum([1.5, 2.3, 3.7]))'。"
                        "可用函数：percent(a,b), times(a,b), yoy(cur,prev), "
                        "diff(a,b), sum(list), avg(list), min(list), max(list), "
                        "round2(x), roundN(x,n), Decimal。"
                        "运算符 + - * / % 和括号都可以用。"
                    ),
                },
            },
            "required": ["expression"],
        },
    },
}


def build_vars_from_claims(claims: list, question: str = "") -> dict[str, Any]:
    import re

    vars_out: dict[str, Any] = {}
    numeric_idx = 0

    for ci, claim in enumerate(claims):
        text = getattr(claim, "text", "") or ""
        for nc in getattr(claim, "numbers", []) or []:
            value_str = getattr(nc, "value", "") or ""
            try:
                d = _to_decimal(value_str)
            except (ValueError, InvalidOperation):
                continue

            generic = f"n{numeric_idx}"
            vars_out[generic] = d

            label_words = re.findall(r"[\u4e00-\u9fff]+|\d{4}|[a-zA-Z_]+", text)
            if label_words:
                parts = []
                for w in label_words[:3]:
                    if w.isdigit() and len(w) == 4:
                        parts.append(f"_{w}")  
                    elif len(w) >= 2:
                        parts.append(w)
                if parts:
                    label = "_".join(parts)
                    if label not in vars_out:
                        vars_out[label] = d

            numeric_idx += 1

    return vars_out
