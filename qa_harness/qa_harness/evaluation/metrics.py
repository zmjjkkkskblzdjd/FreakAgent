from __future__ import annotations

from typing import Iterable

from core.types import TIER_ORDER
from evaluation.eval_result import EvalScorecard, QuestionResult



def recall_at_k(ranked_items: list[str], relevant: set[str], k: int) -> bool:
    top_k = ranked_items[:k]
    return any(item in relevant for item in top_k)


def mrr(ranked_items: list[str], relevant: set[str]) -> float:
    for i, item in enumerate(ranked_items):
        if item in relevant:
            return 1.0 / (i + 1)
    return 0.0



def compute_route_metrics(results: Iterable[QuestionResult]) -> tuple[float, dict[str, float]]:
    total = 0
    correct = 0
    by_group_total: dict[str, int] = {}
    by_group_correct: dict[str, int] = {}

    for r in results:
        if r.expected_route is None or r.error:
            continue
        total += 1
        by_group_total[r.group] = by_group_total.get(r.group, 0) + 1
        if r.actual_route == r.expected_route:
            correct += 1
            by_group_correct[r.group] = by_group_correct.get(r.group, 0) + 1

    accuracy = correct / max(1, total)
    by_group = {
        g: by_group_correct.get(g, 0) / max(1, by_group_total.get(g, 0))
        for g in by_group_total
    }
    return accuracy, by_group


def _tier_rank(tier: str | None) -> int:
    if tier is None:
        return -1
    return TIER_ORDER.get(tier, -1)


def compute_tier_metrics(
    results: Iterable[QuestionResult],
) -> tuple[float, float, float, dict[str, float]]:
    total = 0
    correct = 0
    escalations = 0
    downgrades = 0
    by_group_total: dict[str, int] = {}
    by_group_correct: dict[str, int] = {}

    for r in results:
        if r.expected_tier is None or r.error:
            continue
        total += 1
        by_group_total[r.group] = by_group_total.get(r.group, 0) + 1

        exp_rank = _tier_rank(r.expected_tier)
        act_rank = _tier_rank(r.actual_tier)

        if exp_rank == act_rank:
            correct += 1
            by_group_correct[r.group] = by_group_correct.get(r.group, 0) + 1
        elif act_rank > exp_rank:
            escalations += 1
        else:
            downgrades += 1

    accuracy = correct / max(1, total)
    esc_rate = escalations / max(1, total)
    dwn_rate = downgrades / max(1, total)
    by_group = {
        g: by_group_correct.get(g, 0) / max(1, by_group_total.get(g, 0))
        for g in by_group_total
    }
    return accuracy, esc_rate, dwn_rate, by_group



def compute_refusal_metrics(
    results: Iterable[QuestionResult],
) -> tuple[float, float, float]:
    total = 0
    correct = 0
    fp = 0   
    fn = 0   
    pos_total = 0  
    neg_total = 0  

    for r in results:
        if r.expected_refusal is None or r.error:
            continue
        total += 1
        actual = r.actual_refusal or False
        expected = r.expected_refusal

        if actual == expected:
            correct += 1
        elif expected and not actual:
            fn += 1   
        elif not expected and actual:
            fp += 1   

        if expected:
            pos_total += 1
        else:
            neg_total += 1

    accuracy = correct / max(1, total)
    fpr = fp / max(1, neg_total)
    fnr = fn / max(1, pos_total)
    return accuracy, fpr, fnr



def compute_citation_metrics(
    results: Iterable[QuestionResult],
) -> tuple[float, float]:
    kb_total = 0
    with_citations = 0
    recall_sum = 0.0
    recall_count = 0

    for r in results:
        if r.error:
            continue
        if r.group not in ("lite", "deep"):
            continue
        kb_total += 1
        if r.actual_citations:
            with_citations += 1

        if r.expected_citation_sources:
            expected = set(r.expected_citation_sources)
            actual = set(r.actual_sources)
            hits = sum(
                1 for exp_src in expected
                if any(exp_src.lower() in act_src.lower() for act_src in actual)
            )
            recall = hits / max(1, len(expected))
            recall_sum += recall
            recall_count += 1

    citation_rate = with_citations / max(1, kb_total)
    avg_recall = recall_sum / max(1, recall_count)
    return citation_rate, avg_recall



def build_scorecard(
    results: list[QuestionResult],
    model: str = "",
) -> EvalScorecard:
    card = EvalScorecard(
        model=model,
        total_questions=len(results),
        error_count=sum(1 for r in results if r.error),
        questions=results,
    )

    for r in results:
        card.group_counts[r.group] = card.group_counts.get(r.group, 0) + 1

    card.route_accuracy, card.route_by_group = compute_route_metrics(results)

    card.tier_accuracy, card.tier_escalation_rate, card.tier_downgrade_rate, card.tier_by_group = (
        compute_tier_metrics(results)
    )

    card.refusal_accuracy, card.false_positive_rate, card.false_negative_rate = (
        compute_refusal_metrics(results)
    )

    card.citation_rate, card.source_recall_at_5 = compute_citation_metrics(results)

    return card
