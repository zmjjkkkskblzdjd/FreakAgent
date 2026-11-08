from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_analyze_agent.metrics.matcher import (  # noqa: E402
    _semantic_search,
    match_metrics,
    render_metric_cards,
)


def main() -> None:
    print("=" * 60)
    print("测试1：_semantic_search 直连 qa_harness")
    question = "上个月的GMV是多少"
    hits = _semantic_search(question)
    print(f"  问题：{question}")
    print(f"  返回命中数：{len(hits)}")
    for std_name, term, score in hits:
        print(f"    - {std_name} (score={score:.4f})")
    assert hits, "qa_harness 应返回非空语义命中"
    assert all(isinstance(h[0], str) and h[0] for h in hits), "std_name 应为非空字符串"
    assert all(0.0 <= h[2] <= 1.0 for h in hits), "score 应在 [0,1]"
    print("ok｜HTTP 契约 {results: [...]} 解析成功，metadata.metric 正确映射")

    print("=" * 60)
    print("测试2：match_metrics 三级匹配（含第③级语义）")
    matches = match_metrics(question)
    print(f"  命中指标数：{len(matches)}")
    for m in matches:
        print(f"    - {m.entry.name} (level={m.level}, score={m.score:.4f})")
    assert matches, "match_metrics 应返回至少一个命中"
    levels = {m.level for m in matches}
    print(f"  命中级别：{sorted(levels)}")
    print("ok｜三级匹配链路通")

    print("=" * 60)
    print("测试3：render_metric_cards 渲染口径卡片")
    cards = render_metric_cards(matches)
    print(f"  卡片数：{len(cards)}")
    for c in cards[:2]:
        print(f"    kind={c.kind}, content前40字={c.content[:40]!r}")
    assert all(c.kind == "glossary" for c in cards), "卡片 kind 应为 glossary"
    print("ok｜口径卡渲染正常")

    print("=" * 60)
    print("DataAnalyzeAgent ↔ qa_harness 联调通过 ✅")


if __name__ == "__main__":
    main()
