from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from core import Draft, QATaskContext
from orchestration.guardrails import check_precondition
from tools import tool


_CONFIDENCES = ("high", "medium", "low")


class AnswerSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")  

    answer: str
    citations: list[str]
    confidence: str
    is_refusal: bool = False
    escalate: bool = False
    escalate_reason: str = ""

    @field_validator("answer")
    @classmethod
    def answer_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("answer 不能为空（拒答时也要写拒答话术）")
        return v.strip()

    @field_validator("confidence")
    @classmethod
    def confidence_enum(cls, v: str) -> str:
        if v not in _CONFIDENCES:
            raise ValueError(f"confidence 必须是 {_CONFIDENCES}，收到 {v!r}")
        return v

    @field_validator("citations")
    @classmethod
    def citations_str_list(cls, v: list[str]) -> list[str]:
        for i, cid in enumerate(v):
            if not isinstance(cid, str) or not cid.strip():
                raise ValueError(f"citations[{i}] 必须是非空字符串")
        return [c.strip() for c in v]

    @model_validator(mode="after")
    def escalate_reason_required(self) -> "AnswerSubmission":
        if self.escalate and not self.escalate_reason.strip():
            raise ValueError("escalate=true 时必须填写 escalate_reason 说明为什么要升档")
        return self


def submit_answer_tool(ctx: QATaskContext) -> Callable[..., dict]:
    @tool(
        name="submit_answer",
        description=(
            "提交草稿为最终答案并结束任务（唯一出口）。"
            "入参即草稿内容：answer（正文）/ citations（chunk_id 列表）/ "
            "confidence（high/medium/low）/ is_refusal（是否拒答，默认 false）。"
            "调用前必须已通过 delegate_answer 产出草稿。"
        ),
        parameter_descriptions={
            "answer": "最终答案正文（拒答时写拒答话术）",
            "citations": "引用的 chunk_id 列表（direct 档可空，lite 档事实性回答不可空）",
            "confidence": "置信度：high / medium / low",
            "is_refusal": "是否拒答，默认 false",
            "escalate": "是否申请升档，默认 false（Day7 升级未接通，撞到转拒答）",
            "escalate_reason": "升档原因，escalate=true 时必填",
        },
    )
    def submit_handler(
        answer: str,
        citations: list[str],
        confidence: str,
        is_refusal: bool = False,
        escalate: bool = False,
        escalate_reason: str = "",
    ) -> dict:
        try:
            try:
                sub = AnswerSubmission(
                    answer=answer,
                    citations=citations,
                    confidence=confidence,
                    is_refusal=is_refusal,
                    escalate=escalate,
                    escalate_reason=escalate_reason,
                )
            except ValidationError as e:
                return {"ok": False, "error": f"submit_answer 入参校验失败：{e}"}

            ok, reason = check_precondition(ctx, "submit_answer")
            if not ok:
                return {"ok": False, "error": f"前置条件不满足：{reason}"}

            authoritative = ctx.draft
            try:
                draft = Draft(
                    answer=authoritative.answer,
                    citations=sub.citations,
                    confidence=sub.confidence,
                    is_refusal=sub.is_refusal,
                    escalate=sub.escalate,
                    escalate_reason=sub.escalate_reason,
                )
            except (TypeError, ValueError) as e:
                return {"ok": False, "error": f"Draft 构造失败：{e}"}
            ctx.draft = draft

            return {
                "ok": True,
                "final_answer": authoritative.answer,
                "is_refusal": sub.is_refusal,
                "completed": True,
                "instruction": (
                    "任务已完成。请把 final_answer 原样输出给用户，"
                    "不要改写、不要继续调用任何工具。"
                ),
            }
        except Exception as e:
            return {"ok": False, "error": f"submit_answer 执行异常：{type(e).__name__}: {e}"}

    return submit_handler
