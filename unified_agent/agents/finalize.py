from __future__ import annotations

from unified_agent.core.contracts import UnifiedFinalReport, validate
from unified_agent.core.output_guard import sanitize_answer
from unified_agent.core.state import UnifiedState


def validate_and_set(state: UnifiedState) -> dict:
    report = state.get("final_report")
    if report is None:
        return {"task_status": "done", "validation_errors": [],
                "validation_exempted": []}
    exempted: list[str] = []
    checked = report if isinstance(report, UnifiedFinalReport) \
        else UnifiedFinalReport(**report)
    errs = validate(checked, state, exempted)
    exc = sorted(set(exempted))
    answer_md, redactions = sanitize_answer(checked.answer_md)
    checked = checked.model_copy(
        update={"answer_md": answer_md, "validation_errors": errs,
                "validation_exempted": exc, "redactions": redactions})
    return {"task_status": "done", "validation_errors": errs,
            "validation_exempted": exc, "redactions": redactions,
            "final_report": checked.model_dump()}