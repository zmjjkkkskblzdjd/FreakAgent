from __future__ import annotations

import os

from typing import Any

from core import DEFAULT_MODEL_NAME, Draft, EvidenceReport
from tools import PermissionLevel

from .base import AgentProfile, BaseAgent

ANSWERER_PROFILE_DIRECT = AgentProfile(
    role="answerer",
    role_description="在 direct 档直接回答闲聊或简单问题（无证据、无引用）。",
    allowed_tools=(),
    permission=PermissionLevel.NONE,
)
ANSWERER_PROFILE = AgentProfile(
    role="answerer",
    role_description="基于上游证据撰写带引用的回答，证据不足时拒答。",
    allowed_tools=("get_chunk",),
    permission=PermissionLevel.READ_ONLY,
)

_CONFIDENCES = ("high", "medium", "low")
_LANES = ("direct", "lite", "deep")

_ANSWERER_CHUNK_MAX_CHARS = int(os.getenv("QA_ANSWERER_CHUNK_MAX_CHARS", "2000"))


def _is_web_evidence(e: Any) -> bool:
    return (getattr(e, "match_type", "") == "web"
            or str(getattr(e, "source", "")).startswith(("http://", "https://")))


class AnswererAgent(BaseAgent):
    def __init__(self, lane: str = "lite", tool_registry: Any = None,
                 model_name: str = DEFAULT_MODEL_NAME) -> None:
        if lane not in _LANES:
            raise ValueError(f"answerer 未知车道：{lane!r}，只支持 {_LANES}")
        self._lane = lane
        profile = ANSWERER_PROFILE_DIRECT if lane == "direct" else ANSWERER_PROFILE
        super().__init__(profile, tool_registry=tool_registry, model_name=model_name,
                         max_loops=5, chunk_max_chars=_ANSWERER_CHUNK_MAX_CHARS)

    def bind_evidence(self, evidence_report: EvidenceReport | None) -> None:
        self._bound_evidence: EvidenceReport | None = evidence_report

    def run(self, task_summary: str, evidence_report: EvidenceReport | None = None) -> dict[str, Any]:
        er = evidence_report if evidence_report is not None else getattr(self, "_bound_evidence", None)
        self._ledger: dict[str, Any] = (
            {e.chunk_id: e for e in er.evidences} if er else {}
        )
        return super().run(task_summary)  

    def _validate_and_build(self, raw: dict[str, Any]) -> dict[str, Any]:

        answer = raw.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            return {"ok": False, "error": "输出字段 answer 必须是非空字符串（拒答时也要写拒答话术）"}
        answer = answer.strip()

        citations_raw = raw.get("citations", [])
        if not isinstance(citations_raw, list):
            return {"ok": False, "error": "输出字段 citations 必须是列表（没有引用时为 []）"}
        citations: list[str] = []
        for i, cid in enumerate(citations_raw):
            if not isinstance(cid, str) or not cid.strip():
                return {"ok": False, "error": f"citations[{i}] 必须是非空 chunk_id 字符串"}
            cid = cid.strip()
            if cid not in citations:
                citations.append(cid)

        confidence = raw.get("confidence")
        if confidence not in _CONFIDENCES:
            return {"ok": False, "error": f"confidence 必须是 {_CONFIDENCES} 之一"}

        is_refusal = raw.get("is_refusal", False)
        if not isinstance(is_refusal, bool):
            return {"ok": False, "error": "is_refusal 必须是布尔值 true 或 false"}

        escalate = raw.get("escalate", False)
        if not isinstance(escalate, bool):
            return {"ok": False, "error": "escalate 必须是布尔值 true 或 false"}
        escalate_reason = str(raw.get("escalate_reason", "") or "")
        if escalate and not escalate_reason.strip():
            return {"ok": False, "error": "escalate=true 时必须填写 escalate_reason 说明为什么要升档"}


        if self._lane == "direct":
            if citations:
                return {
                    "ok": False,
                    "error": f"direct 档没有证据，citations 必须为空，但收到了 {citations}",
                }
        else:
            if not self._ledger and not is_refusal:
                return {
                    "ok": False,
                    "error": "本轮证据为空，必须输出拒答草稿（is_refusal=true），禁止无证据硬答",
                }
            if is_refusal and citations:
                return {
                    "ok": False,
                    "error": f"拒答草稿不允许挂引用，is_refusal=true 时 citations 必须为 []，但收到 {citations}",
                }
            if not is_refusal and not citations:
                return {
                    "ok": False,
                    "error": "lite/deep 档的事实性回答 citations 不允许为空；证据不足请拒答（is_refusal=true）",
                }
            fake = [cid for cid in citations if cid not in self._ledger]
            if fake:
                return {
                    "ok": False,
                    "error": f"引用 {fake} 未出现在本轮证据清单中——answerer 禁止编造引用",
                }
            if confidence == "high" and citations and all(
                _is_web_evidence(self._ledger[c]) for c in citations
            ):
                confidence = "medium"


        if not is_refusal and citations:
            if "参考来源" not in answer:
                footer_lines = ["", "参考来源："]
                for idx, cid in enumerate(citations, 1):
                    ev = self._ledger.get(cid)
                    src = getattr(ev, "source", None) or cid
                    if isinstance(src, str) and ("\\" in src or "/" in src):
                        filename = src.replace("\\", "/").rsplit("/", 1)[-1]
                    else:
                        filename = str(src)
                    footer_lines.append(f"[{idx}] {filename}")
                answer = answer.rstrip() + "\n" + "\n".join(footer_lines)


        try:
            draft = Draft(
                answer=answer,
                citations=citations,
                confidence=confidence,
                is_refusal=is_refusal,
                escalate=escalate,
                escalate_reason=escalate_reason,
            )
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": f"Draft 构造失败：{e}"}

        return {"ok": True, "data": draft}
