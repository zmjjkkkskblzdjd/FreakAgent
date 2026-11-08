
from __future__ import annotations

import re
import warnings
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  
from matplotlib import font_manager, pyplot as plt  # noqa: E402

from config import PROJECT_ROOT, get_app_config  # noqa: E402
from data_analyze_agent.core import ChartArtifact, ChartSpec, QueryResult  # noqa: E402

__all__ = ["setup_chinese_font", "render_chart", "build_chart_artifact"]

_font_resolved: str | None = None
_font_warned: bool = False

_THREAD_SAFE_RE = re.compile(r"[^\w\-]+")


def setup_chinese_font() -> str | None:
    global _font_resolved, _font_warned
    if _font_resolved is not None:
        return _font_resolved

    installed = {f.name for f in font_manager.fontManager.ttflist}
    for candidate in get_app_config().chart.font_candidates:
        if candidate in installed:
            _font_resolved = candidate
            plt.rcParams["font.sans-serif"] = [candidate] + [
                f for f in plt.rcParams["font.sans-serif"] if f != candidate
            ]
            plt.rcParams["axes.unicode_minus"] = False
            return candidate

    if not _font_warned:
        warnings.warn(
            "未找到任何候选中文字体（"
            + "、".join(get_app_config().chart.font_candidates)
            + "），图表中文可能乱码；请安装 SimHei（黑体）或 Microsoft YaHei（微软雅黑）",
            stacklevel=2,
        )
        _font_warned = True
    return None


def _to_float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):  
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _label_text(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _fmt_number(value: float) -> str:
    if value == int(value):
        return f"{value:,.0f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _resolve_columns(spec: ChartSpec, columns: list[str]) -> tuple[int, list[int]]:
    try:
        x_idx = columns.index(spec.x_field)
    except ValueError as exc:
        raise ValueError(f"ChartSpec.x_field {spec.x_field!r} 不在结果列 {columns} 中") from exc
    y_idxs: list[int] = []
    for name in spec.y_fields:
        try:
            y_idxs.append(columns.index(name))
        except ValueError as exc:
            raise ValueError(f"ChartSpec.y_fields 含未知列 {name!r}，可用列：{columns}") from exc
    if not y_idxs:
        raise ValueError("ChartSpec.y_fields 为空，没有可渲染的指标列")
    return x_idx, y_idxs


def _extract_series(
    spec: ChartSpec, query_result: QueryResult
) -> tuple[list[str], list[list[float]]]:
    x_idx, y_idxs = _resolve_columns(spec, list(query_result.columns))
    labels = [_label_text(row[x_idx]) for row in query_result.rows]
    series = [[_to_float(row[i]) for row in query_result.rows] for i in y_idxs]
    return labels, series


def _render_line(spec: ChartSpec, labels: list[str], series: list[list[float]]) -> plt.Figure:
    cfg = get_app_config().chart
    fig, ax = plt.subplots(figsize=cfg.figsize)
    x = list(range(len(labels)))
    for name, values in zip(spec.y_fields, series):
        ax.plot(x, values, marker="o", linewidth=1.8, markersize=4, label=name)
    ax.set_title(spec.title, fontsize=14, pad=12)
    ax.set_xlabel(spec.x_field, fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    if len(labels) > 8:
        fig.autofmt_xdate(rotation=30, ha="right")
    ax.grid(True, linestyle="--", alpha=0.3)
    if len(spec.y_fields) > 1:
        ax.legend(fontsize=10)
    fig.tight_layout()
    return fig


def _render_bar(spec: ChartSpec, labels: list[str], series: list[list[float]]) -> plt.Figure:
    cfg = get_app_config().chart
    fig, ax = plt.subplots(figsize=cfg.figsize)
    x = list(range(len(labels)))

    if len(series) == 1:
        values = series[0]
        bars = ax.bar(x, values, width=0.6, color="#4C78A8")
        for rect, value in zip(bars, values):
            ax.annotate(
                _fmt_number(value),
                xy=(rect.get_x() + rect.get_width() / 2, rect.get_height()),
                xytext=(0, 3), textcoords="offset points",
                ha="center", va="bottom", fontsize=8,
            )
    else:
        n = len(series)
        width = 0.8 / n
        for s, (name, values) in enumerate(zip(spec.y_fields, series)):
            offset = (s - (n - 1) / 2) * width
            ax.bar([p + offset for p in x], values, width=width, label=name)
        ax.legend(fontsize=10)

    ax.set_title(spec.title, fontsize=14, pad=12)
    ax.set_xlabel(spec.x_field, fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    if len(labels) > 6:
        fig.autofmt_xdate(rotation=30, ha="right")
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    ax.set_axisbelow(True)
    fig.tight_layout()
    return fig


def _render_pie(spec: ChartSpec, labels: list[str], series: list[list[float]]) -> plt.Figure:
    cfg = get_app_config().chart
    fig, ax = plt.subplots(figsize=cfg.figsize)
    values = series[0]  
    total = sum(values)

    def _autopct(pct: float) -> str:
        return f"{pct:.1f}%" if pct >= 3 and total > 0 else ""

    wedges, _texts, autotexts = ax.pie(
        values,
        labels=None,  
        autopct=_autopct,
        startangle=90,
        counterclock=False,
        pctdistance=0.75,
        wedgeprops={"linewidth": 1, "edgecolor": "white"},
    )
    for at in autotexts:
        at.set_fontsize(9)
    legend_labels = [
        f"{lab}：{_fmt_number(val)}"
        + (f"（{val / total * 100:.1f}%）" if total > 0 else "")
        for lab, val in zip(labels, values)
    ]
    ax.legend(
        wedges, legend_labels, title=spec.x_field, loc="center left",
        bbox_to_anchor=(1.02, 0.5), fontsize=10, frameon=False,
    )
    ax.set_title(spec.title, fontsize=14, pad=12)
    ax.axis("equal")
    fig.tight_layout()
    return fig


_RENDERERS = {
    "line": _render_line,
    "bar": _render_bar,
    "pie": _render_pie,
}


def _chart_output_path(thread_id: str) -> Path:
    safe = _THREAD_SAFE_RE.sub("_", (thread_id or "session").strip()) or "session"
    safe = safe[:48].strip("_") or "session"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    chart_dir = PROJECT_ROOT / get_app_config().paths.chart_dir
    chart_dir.mkdir(parents=True, exist_ok=True)
    return chart_dir / f"{safe}_{ts}.png"


def render_chart(spec: ChartSpec, query_result: QueryResult, *, thread_id: str = "session") -> ChartArtifact:
    if spec.chart_type not in _RENDERERS:
        raise ValueError(
            f"render_chart 只接受 line/bar/pie，收到 {spec.chart_type!r}（none/table 不应调用本函数）"
        )
    setup_chinese_font()
    labels, series = _extract_series(spec, query_result)
    if not labels:
        raise ValueError("QueryResult.rows 为空，spec 决策本应返回 none——图被装配错误地渲染")

    fig = _RENDERERS[spec.chart_type](spec, labels, series)
    path = _chart_output_path(thread_id)
    try:
        fig.savefig(path, dpi=get_app_config().chart.dpi, bbox_inches="tight")
    finally:
        plt.close(fig)  
    return ChartArtifact(spec=spec, path=str(path.resolve()))


def build_chart_artifact(
    spec: ChartSpec, query_result: QueryResult, *, thread_id: str = "session"
) -> ChartArtifact | None:
    if spec.chart_type not in ("line", "bar", "pie"):
        return None
    return render_chart(spec, query_result, thread_id=thread_id)
