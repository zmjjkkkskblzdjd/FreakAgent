
from __future__ import annotations

import data_analyze_agent.metrics.slots as slots
from data_analyze_agent.metrics.loader import MetricDictionary, MetricEntry

_FAKE_DICT = MetricDictionary(
    domain="销售经营",
    database="sales_dw",
    default_time_field="order_date",
    default_status_filter="order_status = 'completed'",
    metrics=(
        MetricEntry(name="成交额", formula="sum(amount)",
                    related_tables=["v_fact_sales_order_line"],
                    aliases=("GMV", "销售额")),
        MetricEntry(name="订单量", formula="uniqExact(order_id)",
                    related_tables=["v_fact_sales_order_line"],
                    aliases=("单量",)),
    ),
    alias_index={"gmv": "成交额", "销售额": "成交额", "单量": "订单量"},
)


def _patch(monkeypatch):
    monkeypatch.setattr(slots, "load_dictionary", lambda: _FAKE_DICT)


def test_metric_hit_by_name_and_alias(monkeypatch):
    _patch(monkeypatch)
    s = slots.extract_slots("9月各渠道的成交额和GMV对比")
    assert s.metrics == ("成交额",)          
    assert "channel" in s.dims
    assert s.time_shape == "single"
    assert s.shape == "compare"


def test_time_shape_priority(monkeypatch):
    _patch(monkeypatch)
    assert slots.extract_slots("成交额同比趋势").time_shape == "yoy"   
    assert slots.extract_slots("上月成交额环比").time_shape == "mom"
    assert slots.extract_slots("近30天成交额").time_shape == "recent"
    assert slots.extract_slots("近30天成交额走势").time_shape == "trend"  
    assert slots.extract_slots("2025年9月成交额").time_shape == "single"


def test_shape_rules(monkeypatch):
    _patch(monkeypatch)
    assert slots.extract_slots("成交额 top5 的区域").shape == "rank"
    assert slots.extract_slots("各渠道成交额占比").shape == "ratio"
    assert slots.extract_slots("哪些客户有订单").shape == "detail"
    assert slots.extract_slots("9月成交额").shape == "aggregate"


def test_no_metric_hit(monkeypatch):
    _patch(monkeypatch)
    s = slots.extract_slots("今天天气怎么样")
    assert s.metrics == ()


def test_tags_roundtrip_and_score():
    s = slots.QuestionSlots(metrics=("成交额",), dims=("channel",),
                            time_shape="mom", shape="compare")
    tags = slots.tags_of(s)
    assert set(tags) == {"metric:成交额", "dim:channel", "time:mom", "shape:compare"}
    parsed = slots.split_tags(tags)
    assert parsed["metric"] == {"成交额"} and parsed["dim"] == {"channel"}

    assert slots.score_tags(tags, tags) == 10
    assert slots.score_tags(tags, ["metric:成交额"]) == 4
    assert slots.score_tags(tags, ["dim:channel", "shape:rank"]) == 2
    assert slots.score_tags(tags, ["自由标签", "metric:订单量"]) == 0
