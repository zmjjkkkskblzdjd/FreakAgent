from __future__ import annotations

import logging
from typing import Any

from core import DEFAULT_MODEL_NAME, FIX_HINTS, ISSUE_TYPES, Draft, Plan, Verdict, VerdictIssue
from tools import PermissionLevel, ToolRegistry

from .base import AgentProfile, BaseAgent

logger = logging.getLogger(__name__)

CRITIC_PROFILE = AgentProfile(
    role="critic",
    role_description="deep 档按五条检查清单核实草稿，只核实不重写。",
    allowed_tools=("get_chunk",),
    permission=PermissionLevel.READ_ONLY,
)


class CriticAgent(BaseAgent):

    def __init__(self, tool_registry: ToolRegistry | None = None,
                 model_name: str = DEFAULT_MODEL_NAME) -> None:
        super().__init__(CRITIC_PROFILE, tool_registry=tool_registry,
                         model_name=model_name)

    def bind_plan(self, plan: Plan | None) -> None:
        self._bound_plan: Plan | None = plan

    def bind_draft(self, draft: Draft | None) -> None:
        self._bound_draft: Draft | None = draft

    def run(
        self,
        task_summary: str,
        plan: Plan | None = None,
        draft: Draft | None = None,
    ) -> dict[str, Any]:
        p = plan if plan is not None else getattr(self, "_bound_plan", None)
        d = draft if draft is not None else getattr(self, "_bound_draft", None)
        self._success_criteria: list[str] = list(p.success_criteria) if p else []
        self._draft_citations: list[str] = list(d.citations) if d else []
        self._draft_answer: str = d.answer if d else ""
        self._is_refusal: bool = bool(d.is_refusal) if d else False
        self._draft_bound: bool = d is not None
        return super().run(task_summary)

    def _validate_and_build(self, raw: dict[str, Any]) -> dict[str, Any]:
        passed = raw.get("passed")
        if not isinstance(passed, bool):
            return {"ok": False, "error": f"passed 必须是布尔值，收到 {passed!r}"}

        issues_raw = raw.get("issues", [])
        if not isinstance(issues_raw, list):
            return {"ok": False, "error": "issues 必须是列表"}
        issues: list[VerdictIssue] = []
        for i, i_raw in enumerate(issues_raw):
            if not isinstance(i_raw, dict):
                return {"ok": False, "error": f"issues[{i}] 不是 JSON 对象：{i_raw!r}"}

            issue_type = i_raw.get("issue_type")
            if not isinstance(issue_type, str) or not issue_type.strip():
                return {
                    "ok": False,
                    "error": f"issues[{i}].issue_type 必须是非空字符串",
                }
            issue_type = issue_type.strip()
            if issue_type not in ISSUE_TYPES:
                return {
                    "ok": False,
                    "error": (
                        f"issues[{i}].issue_type 非法 {issue_type!r}"
                        f"（应为 {ISSUE_TYPES}）"
                    ),
                }

            detail = i_raw.get("detail")
            if not isinstance(detail, str) or not detail.strip():
                return {
                    "ok": False,
                    "error": f"issues[{i}].detail 必须是非空字符串",
                }

            related_chunk_id = i_raw.get("related_chunk_id")
            if related_chunk_id is not None and not isinstance(related_chunk_id, str):
                return {
                    "ok": False,
                    "error": f"issues[{i}].related_chunk_id 必须是字符串或 null",
                }

            try:
                issues.append(
                    VerdictIssue(
                        issue_type=issue_type,
                        detail=detail.strip(),
                        related_chunk_id=related_chunk_id,
                    )
                )
            except (TypeError, ValueError) as e:
                return {
                    "ok": False,
                    "error": f"VerdictIssue 构造失败（第{i}条）：{e}",
                }

        if passed and issues:
            return {
                "ok": False,
                "error": f"passed=true 时 issues 必须为空，但收到 {len(issues)} 条",
            }
        if not passed and not issues:
            return {
                "ok": False,
                "error": "passed=false 时 issues 至少 1 条（要说明哪里出问题）",
            }

        fix_hint = raw.get("fix_hint")
        if not isinstance(fix_hint, str) or not fix_hint.strip():
            return {"ok": False, "error": "fix_hint 必须是非空字符串"}
        fix_hint = fix_hint.strip()
        if fix_hint not in FIX_HINTS:
            return {
                "ok": False,
                "error": f"非法 fix_hint: {fix_hint!r}（应为 {FIX_HINTS}）",
            }
        if passed and fix_hint != "pass":
            return {
                "ok": False,
                "error": f"passed=true 时 fix_hint 必须为 'pass'，收到 {fix_hint!r}",
            }
        if not passed and fix_hint == "pass":
            return {
                "ok": False,
                "error": "passed=false 时 fix_hint 不可为 'pass'（应 need_more_evidence 或 fix_draft）",
            }


        if not getattr(self, "_draft_bound", False):
            try:
                verdict = Verdict(passed=passed, issues=issues, fix_hint=fix_hint)
            except (TypeError, ValueError) as e:
                return {"ok": False, "error": f"Verdict 构造失败：{e}"}
            return {"ok": True, "data": verdict}

        draft_citations = getattr(self, "_draft_citations", [])
        success_criteria = getattr(self, "_success_criteria", [])
        draft_answer = getattr(self, "_draft_answer", "")
        is_refusal = getattr(self, "_is_refusal", False)

        valid_issues = []
        for idx, issue in enumerate(issues):
            if issue.issue_type == "fake_citation":
                cid = issue.related_chunk_id
                matched = [c for c in draft_citations
                           if cid and (c == cid or (len(cid) >= 8 and c.startswith(cid)))]
                if not matched:
                    logger.warning(
                        "丢弃无法定位的 fake_citation：issues[%s] related_chunk_id=%r，"
                        "草稿引用=%s", idx, cid, draft_citations,
                    )
                    continue
                issue.related_chunk_id = matched[0]
            valid_issues.append(issue)
        if len(valid_issues) != len(issues):
            issues = valid_issues
            if not issues:
                passed, fix_hint = True, "pass"

        if success_criteria:
            for idx, issue in enumerate(issues):
                if issue.issue_type == "missed_criterion":
                    detail = issue.detail
                    matched = any(
                        _criterion_mentioned(criterion, detail)
                        for criterion in success_criteria
                    )
                    if not matched:
                        return {
                            "ok": False,
                            "error": (
                                f"issues[{idx}] missed_criterion 的 detail={detail!r} "
                                f"找不到对应验收标准 {success_criteria}——"
                                "应明确指出漏了哪条标准"
                            ),
                        }

        if not draft_citations and not is_refusal and passed:
            return {
                "ok": False,
                "error": (
                    "草稿无引用且非拒答，但 critic 给了 passed=true——"
                    "无证据的事实性回答不可能通过审查"
                ),
            }

        if is_refusal and passed:
            refusal_keywords = ("无法回答", "不能回答", "无相关", "建议补充",
                                "检索", "知识库", "不足", "无法提供")
            has_refusal_tone = any(kw in draft_answer for kw in refusal_keywords)
            if not has_refusal_tone:
                return {
                    "ok": False,
                    "error": (
                        "草稿 is_refusal=true 且 critic passed=true，"
                        "但正文不含拒答关键词——拒答话术应说明无法回答的原因和建议补什么资料"
                    ),
                }

        try:
            verdict = Verdict(passed=passed, issues=issues, fix_hint=fix_hint)
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": f"Verdict 构造失败：{e}"}

        return {"ok": True, "data": verdict}


def _criterion_mentioned(criterion: str, detail: str) -> bool:
    criterion = criterion.strip()
    detail = detail.strip()
    if not criterion or not detail:
        return False
    if criterion in detail or detail in criterion:
        return True
    for length in (4, 3, 2):
        for i in range(len(criterion) - length + 1):
            frag = criterion[i:i + length]
            if len(frag) >= 2 and frag in detail:
                return True
    return False
