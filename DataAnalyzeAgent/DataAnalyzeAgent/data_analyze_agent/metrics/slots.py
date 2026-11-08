
from __future__ import annotations

import re
from dataclasses import dataclass

from data_analyze_agent.metrics.loader import load_dictionary

__all__ = [
    "QuestionSlots", "extract_slots", "tags_of", "split_tags", "score_tags",
    "TIME_SHAPES", "SHAPES", "DIM_LEXICON",
]


TIME_SHAPES = ("single", "mom", "yoy", "trend", "recent")
SHAPES = ("aggregate", "rank", "ratio", "compare", "detail", "distribution")

DIM_LEXICON: tuple[tuple[str, str], ...] = (
    ("会员等级", "customer_level"), ("客户等级", "customer_level"),
    ("会员级别", "customer_level"),
    ("区域", "region"), ("大区", "region"), ("地区", "region"),
    ("省份", "province"), ("城市", "city"),
    ("渠道", "channel"),
    ("品类", "category"), ("类别", "category"), ("类目", "category"),
    ("商品", "product"), ("产品", "product"), ("sku", "product"),
    ("客户", "customer"),
    ("月份", "month"), ("按月", "month"), ("各月", "month"), ("月度", "month"),
)

_TIME_RULES: tuple[tuple[str, str], ...] = (
    (r"同比|去年同期", "yoy"),
    (r"环比|比上月|较上月", "mom"),
    (r"趋势|走势|逐月|连续", "trend"),
    (r"近\s*\d+\s*[天周月]|最近|近[一两三四五六七八九十\d]+个?月", "recent"),
)

_SHAPE_RULES: tuple[tuple[str, str], ...] = (
    (r"top\s*\d*|前几|前\s*\d+|排行|排名|最高|最多|最低|最少", "rank"),
    (r"占比|份额|比例|构成", "ratio"),
    (r"对比|相比|比较|\bvs\b", "compare"),
    (r"分布", "distribution"),
    (r"明细|哪些|清单|列出|列表", "detail"),
)

_WEIGHTS = {"metric": 4, "dim": 2, "time": 2, "shape": 2}
MIN_MATCH_SCORE = 4


@dataclass(frozen=True)
class QuestionSlots:

    metrics: tuple[str, ...] = ()   
    dims: tuple[str, ...] = ()      
    time_shape: str = "single"
    shape: str = "aggregate"


def extract_slots(question: str) -> QuestionSlots:
    q_lower = question.lower()

    dictionary = load_dictionary()
    metrics: list[str] = []
    for entry in dictionary.metrics:
        if entry.name.lower() in q_lower and entry.name not in metrics:
            metrics.append(entry.name)
    for alias, std_name in sorted(dictionary.alias_index.items(),
                                  key=lambda kv: len(kv[0]), reverse=True):
        if alias and alias in q_lower and std_name not in metrics:
            metrics.append(std_name)

    dims: list[str] = []
    for keyword, code in DIM_LEXICON:
        if keyword in q_lower and code not in dims:
            dims.append(code)

    time_shape = next((code for pattern, code in _TIME_RULES
                       if re.search(pattern, q_lower)), "single")
    shape = next((code for pattern, code in _SHAPE_RULES
                  if re.search(pattern, q_lower)), "aggregate")

    return QuestionSlots(metrics=tuple(metrics), dims=tuple(dims),
                         time_shape=time_shape, shape=shape)


def tags_of(slots: QuestionSlots) -> tuple[str, ...]:
    return (
        *(f"metric:{m}" for m in slots.metrics),
        *(f"dim:{d}" for d in slots.dims),
        f"time:{slots.time_shape}",
        f"shape:{slots.shape}",
    )


def split_tags(tags: list[str] | tuple[str, ...]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {k: set() for k in _WEIGHTS}
    for tag in tags or ():
        if ":" not in tag:
            continue
        slot, _, value = tag.partition(":")
        if slot in out and value:
            out[slot].add(value)
    return out


def score_tags(question_tags: tuple[str, ...], example_tags) -> int:
    q = split_tags(question_tags)
    e = split_tags(example_tags)
    return sum(_WEIGHTS[slot] for slot in _WEIGHTS if q[slot] & e[slot])
