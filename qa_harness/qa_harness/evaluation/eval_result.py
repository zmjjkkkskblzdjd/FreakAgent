from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class QuestionResult:
    question: str
    group: str                   
    expected_route: str | None = None
    expected_tier: str | None = None
    expected_refusal: bool | None = None
    expected_citation_sources: list[str] = field(default_factory=list)  

    actual_route: str | None = None
    actual_tier: str | None = None
    actual_refusal: bool | None = None
    actual_confidence: str | None = None
    actual_citations: list[str] = field(default_factory=list)  
    actual_sources: list[str] = field(default_factory=list)    
    answer: str = ""
    error: bool = False
    error_msg: str = ""

    route_correct: bool | None = None
    tier_correct: bool | None = None
    refusal_correct: bool | None = None
    citation_hit: bool | None = None      
    source_recall: float = 0.0            
    has_citations: bool = False            


@dataclass
class EvalScorecard:
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    model: str = ""
    total_questions: int = 0
    error_count: int = 0

    route_accuracy: float = 0.0
    route_by_group: dict[str, float] = field(default_factory=dict)  

    tier_accuracy: float = 0.0
    tier_escalation_rate: float = 0.0    
    tier_downgrade_rate: float = 0.0     
    tier_by_group: dict[str, float] = field(default_factory=dict)

    refusal_accuracy: float = 0.0        
    false_positive_rate: float = 0.0     
    false_negative_rate: float = 0.0     

    citation_rate: float = 0.0           
    source_recall_at_5: float = 0.0      

    group_counts: dict[str, int] = field(default_factory=dict)

    questions: list[QuestionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "model": self.model,
            "total_questions": self.total_questions,
            "error_count": self.error_count,
            "route_accuracy": round(self.route_accuracy, 4),
            "route_by_group": {k: round(v, 4) for k, v in self.route_by_group.items()},
            "tier_accuracy": round(self.tier_accuracy, 4),
            "tier_escalation_rate": round(self.tier_escalation_rate, 4),
            "tier_downgrade_rate": round(self.tier_downgrade_rate, 4),
            "tier_by_group": {k: round(v, 4) for k, v in self.tier_by_group.items()},
            "refusal_accuracy": round(self.refusal_accuracy, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "false_negative_rate": round(self.false_negative_rate, 4),
            "citation_rate": round(self.citation_rate, 4),
            "source_recall_at_5": round(self.source_recall_at_5, 4),
            "group_counts": self.group_counts,
            "questions": [asdict(q) for q in self.questions],
        }

    def summary_table(self) -> str:
        lines = []
        lines.append(f"{'='*60}")
        lines.append(f"  评测成绩单  {self.timestamp[:19]}")
        lines.append(f"  模型: {self.model}  |  共 {self.total_questions} 题  |  异常 {self.error_count}")
        lines.append(f"{'='*60}")
        lines.append("")
        lines.append(f"  {'指标':<25} {'数值':>10}")
        lines.append(f"  {'-'*25} {'-'*10}")
        lines.append(f"  {'route 准确率':<25} {self.route_accuracy:>10.2%}")
        lines.append(f"  {'tier 准确率':<25} {self.tier_accuracy:>10.2%}")
        lines.append(f"  {'  升级率':<25} {self.tier_escalation_rate:>10.2%}")
        lines.append(f"  {'  降级率':<25} {self.tier_downgrade_rate:>10.2%}")
        lines.append(f"  {'拒答准确率':<25} {self.refusal_accuracy:>10.2%}")
        lines.append(f"  {'  误拒答率':<25} {self.false_positive_rate:>10.2%}")
        lines.append(f"  {'  漏拒答率':<25} {self.false_negative_rate:>10.2%}")
        lines.append(f"  {'引用覆盖率':<25} {self.citation_rate:>10.2%}")
        lines.append(f"  {'source 召回率':<25} {self.source_recall_at_5:>10.2%}")
        lines.append("")
        lines.append(f"  {'分组':<15} {'题数':>6} {'route准':>8} {'tier准':>8}")
        lines.append(f"  {'-'*15} {'-'*6} {'-'*8} {'-'*8}")
        for g in ("direct", "lite", "deep", "refusal", "clarify", "web"):
            cnt = self.group_counts.get(g, 0)
            if cnt == 0:
                continue
            r_acc = self.route_by_group.get(g, 0)
            t_acc = self.tier_by_group.get(g, 0)
            lines.append(f"  {g:<15} {cnt:>6} {r_acc:>8.1%} {t_acc:>8.1%}")
        lines.append(f"{'='*60}")
        return "\n".join(lines)
