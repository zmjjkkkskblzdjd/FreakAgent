from __future__ import annotations

import json

_LOGICAL = {"$and", "$or"}
_FIELD_OPS = {"$in", "$ne", "$gt", "$gte", "$lt", "$lte"}
_COMPARE = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}
_SCALAR = (str, int, float, bool)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def compile_where(where: dict | None, *, column: str = "metadata") -> tuple[str, list]:
    if not where:
        return "", []
    if not isinstance(where, dict):
        raise ValueError(f"where 必须是 dict，收到：{type(where).__name__}")
    return _compile_node(where, column)


def _compile_node(where: dict, column: str) -> tuple[str, list]:
    logical = [k for k in where if isinstance(k, str) and k.startswith("$")]
    if logical:
        if len(where) != 1:
            raise ValueError(f"逻辑算子不能与字段条件混用：{where}")
        op = logical[0]
        if op not in _LOGICAL:
            raise ValueError(f"不支持的逻辑算子：{op}")
        clauses = where[op]
        if not isinstance(clauses, (list, tuple)) or not clauses:
            raise ValueError(f"{op} 需要非空子句列表")
        parts, params = [], []
        for clause in clauses:
            if not isinstance(clause, dict):
                raise ValueError(f"{op} 子句必须是 dict：{clause}")
            sql, sub = _compile_node(clause, column)
            parts.append(sql)
            params.extend(sub)
        joiner = " AND " if op == "$and" else " OR "
        return "(" + joiner.join(parts) + ")", params

    parts, params = [], []
    for key, cond in where.items():
        if not isinstance(key, str):
            raise ValueError(f"metadata key 必须是字符串：{key!r}")
        if not isinstance(cond, dict):
            if not isinstance(cond, _SCALAR):
                raise ValueError(f"metadata[{key!r}] 等值只支持标量，收到：{cond!r}")
            parts.append(f"{column} -> %s = %s::jsonb")
            params.extend([key, _json(cond)])
            continue

        if not cond:
            raise ValueError(f"metadata[{key!r}] 的算子对象不能为空")
        for op, operand in cond.items():
            if op not in _FIELD_OPS:
                raise ValueError(f"不支持的 where 算子：{op}")
            if op == "$in":
                if not isinstance(operand, (list, tuple)) or not operand:
                    raise ValueError(f"$in 需要非空列表：{key!r}")
                if not all(isinstance(v, _SCALAR) for v in operand):
                    raise ValueError(f"$in 只支持标量：{key!r}")
                marks = ",".join(["%s::jsonb"] * len(operand))
                parts.append(f"{column} -> %s IN ({marks})")
                params.extend([key, *[_json(v) for v in operand]])
            elif op == "$ne":
                if not isinstance(operand, _SCALAR):
                    raise ValueError(f"$ne 只支持标量：{key!r}")
                parts.append(f"{column} -> %s <> %s::jsonb")
                params.extend([key, _json(operand)])
            else:
                if not _is_number(operand):
                    raise ValueError(f"{op} 只支持数值比较：{key!r}={operand!r}")
                parts.append(f"({column} ->> %s)::numeric {_COMPARE[op]} %s")
                params.extend([key, operand])
    return "(" + " AND ".join(parts) + ")", params
