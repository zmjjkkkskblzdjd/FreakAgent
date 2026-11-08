
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from config import get_app_config
from data_analyze_agent.core import ChartSpec, ChartType, QueryResult

__all__ = ["decide_chart", "is_temporal_label", "PLOTTABLE_TYPES"]

PLOTTABLE_TYPES: tuple[ChartType, ...] = ("line", "bar", "pie")

_TEMPORAL_STR_RE = re.compile(
    r"^\s*(?:"
    r"\d{4}"                                                
    r"|\d{4}[-/.](?:0?[1-9]|1[0-2])"                        
    r"|\d{4}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])"  
    r"|\d{4}年(?:0?[1-9]|1[0-2])月(?:(?:0?[1-9]|[12]\d|3[01])日)?"  
    r"|\d{4}[-/]?Q[1-4]"                                    
    r")\s*$",
    re.IGNORECASE,
)


def _is_number(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float, Decimal))


def _column_is_numeric(rows: list[tuple], idx: int) -> bool:
    saw_value = False
    for row in rows:
        value = row[idx]
        if value is None:
            continue
        if not _is_number(value):
            return False
        saw_value = True
    return saw_value


def _is_int_year(value: int) -> bool:
    return 1900 <= value <= 2099


def _is_int_yyyymm(value: int) -> bool:
    year, month = divmod(value, 100)
    return 1900 <= year <= 2099 and 1 <= month <= 12


def _is_int_yyyymmdd(value: int) -> bool:
    year, rest = divmod(value, 10000)
    month, day = divmod(rest, 100)
    return 1900 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31


def is_temporal_label(values: list[object]) -> bool:
    non_null = [v for v in values if v is not None]
    if len(non_null) < 2:
        return False
    for value in non_null:
        if isinstance(value, (datetime, date)):
            continue
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            if _is_int_year(value) or _is_int_yyyymm(value) or _is_int_yyyymmdd(value):
                continue
            return False
        if isinstance(value, str) and _TEMPORAL_STR_RE.match(value):
            continue
        return False
    return True


def _none_spec(reason: str) -> ChartSpec:
    return ChartSpec(chart_type="none", title="", x_field="", y_fields=[], reason=reason)


def _make_title(question: str, x_field: str, y_fields: list[str], max_len: int) -> str:
    text = re.sub(r"\s+", " ", question or "").strip()
    if text:
        return text if len(text) <= max_len else text[:max_len] + "…"
    measures = "/".join(y_fields) if y_fields else "数值"
    return f"{measures}（按 {x_field}）"


def decide_chart(
    query_result: QueryResult,
    chart_suggestion: ChartType = "none",
    *,
    question: str = "",
) -> ChartSpec:
    cfg = get_app_config().chart
    columns = list(query_result.columns or [])
    rows = list(query_result.rows or [])

    if not cfg.enabled:
        return _none_spec("可视化总开关关闭（chart.enabled=False）")
    if query_result.truncated:
        return _none_spec("结果命中行数上限被截断（truncated），部分数据画图会误导")
    if len(columns) < 2:
        return _none_spec(f"结果仅 {len(columns)} 列，缺少可映射的维度列与数值指标列")
    if len(rows) < cfg.min_chart_rows:
        return _none_spec(f"仅 {len(rows)} 行数据（< {cfg.min_chart_rows}），不构成图表")

    x_field = columns[0]
    measure_idxs = [i for i in range(1, len(columns)) if _column_is_numeric(rows, i)]
    extra_text_idxs = [i for i in range(1, len(columns)) if i not in measure_idxs]
    if extra_text_idxs:
        names = "、".join(columns[i] for i in extra_text_idxs)
        return _none_spec(f"维度列之外仍有文本列（{names}），属明细/交叉表结果，不自动画图")
    if not measure_idxs:
        return _none_spec("首列之外没有数值指标列，无法画趋势/占比/对比图")

    y_fields = [columns[i] for i in measure_idxs]
    title = _make_title(question, x_field, y_fields, cfg.title_max_len)
    label_values = [row[0] for row in rows]

    def _spec(chart_type: ChartType, reason: str) -> ChartSpec:
        return ChartSpec(
            chart_type=chart_type,
            title=title,
            x_field=x_field,
            y_fields=list(y_fields),
            series_from_rows=len(y_fields) == 1,
            reason=reason,
        )

    if is_temporal_label(label_values):
        if len(rows) > cfg.max_line_points:
            return _none_spec(
                f"时间轴 {len(rows)} 个点超过上限 {cfg.max_line_points}，请先在 SQL 收敛粒度"
            )
        return _spec("line", "首列为时间口径（年/月/日/季），时间序列用折线展示趋势")

    if chart_suggestion in ("none", "table"):
        label = "明细列表" if chart_suggestion == "table" else "解读员建议"
        return _none_spec(f"{label}类结果（suggestion={chart_suggestion}），不画图")

    if chart_suggestion == "pie":
        if len(y_fields) != 1:
            return _spec("bar", "建议饼图但存在多个数值指标列，占比无法定义，降级为分组柱状对比")
        if len(rows) > cfg.max_pie_slices:
            return _spec(
                "bar",
                f"占比类目 {len(rows)} 个超过饼图上限 {cfg.max_pie_slices}，切片过密不可读，降级柱状",
            )
        return _spec("pie", "单指标按类目构成占比，饼图展示份额")

    if chart_suggestion == "line":
        return _spec("bar", "建议折线但首列不是时间口径，类目对比改用柱状，避免连线伪造趋势")

    if len(rows) > cfg.max_bar_rows:
        return _none_spec(f"类目 {len(rows)} 个超过柱状上限 {cfg.max_bar_rows}，图面不可读，不画图")
    if len(y_fields) == 1:
        return _spec("bar", "单指标按类目对比，柱状展示高低")
    return _spec("bar", f"{len(y_fields)} 个数值指标按类目分组对比，画分组柱状")
