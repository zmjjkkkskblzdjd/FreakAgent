from __future__ import annotations

from typing import Any

from core import DEFAULT_MODEL_NAME, Plan, SubQuestion
from tools import PermissionLevel

from .base import AgentProfile, BaseAgent

PLANNER_PROFILE = AgentProfile(
    role="planner",
    role_description="把复杂问题拆解成可检索的子问题、检索词和验收标准，仅在 deep 档被调用。",
    allowed_tools=(),
    permission=PermissionLevel.NONE,
)

class PlannerAgent(BaseAgent):

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME) -> None:
        super().__init__(PLANNER_PROFILE, model_name=model_name)

    def _validate_and_build(self, raw: dict[str, Any]) -> dict[str, Any]:
        sub_questions_raw = raw.get("sub_questions")
        if not isinstance(sub_questions_raw, list) or not sub_questions_raw:
            return {"ok": False, "error": "sub_questions 必须是非空列表（至少 1 条子问题）"}

        sub_questions: list[SubQuestion] = []
        for i, sq_raw in enumerate(sub_questions_raw):
            if not isinstance(sq_raw, dict):
                return {"ok": False, "error": f"sub_questions[{i}] 不是 JSON 对象：{sq_raw!r}"}

            question = sq_raw.get("question")
            if not isinstance(question, str) or not question.strip():
                return {"ok": False, "error": f"sub_questions[{i}].question 必须是非空字符串"}

            search_queries = sq_raw.get("search_queries")
            if not isinstance(search_queries, list) or not search_queries:
                return {"ok": False, "error": f"sub_questions[{i}].search_queries 必须是非空列表（至少 1 个检索词）"}
            queries: list[str] = []
            for j, q in enumerate(search_queries):
                if not isinstance(q, str) or not q.strip():
                    return {"ok": False, "error": f"sub_questions[{i}].search_queries[{j}] 必须是非空字符串"}
                queries.append(q.strip())

            criterion = sq_raw.get("criterion")
            if not isinstance(criterion, str) or not criterion.strip():
                return {"ok": False, "error": f"sub_questions[{i}].criterion 必须是非空字符串（验收标准）"}

            try:
                sub_questions.append(SubQuestion(
                    question=question.strip(),
                    search_queries=queries,
                    criterion=criterion.strip(),
                ))
            except (TypeError, ValueError) as e:
                return {"ok": False, "error": f"SubQuestion 构造失败（第{i}条）：{e}"}

        success_criteria_raw = raw.get("success_criteria")
        if not isinstance(success_criteria_raw, list) or not success_criteria_raw:
            return {"ok": False, "error": "success_criteria 必须是非空列表（整体验收标准）"}
        success_criteria: list[str] = []
        for i, sc in enumerate(success_criteria_raw):
            if not isinstance(sc, str) or not sc.strip():
                return {"ok": False, "error": f"success_criteria[{i}] 必须是非空字符串"}
            success_criteria.append(sc.strip())

        need_web = raw.get("need_web", False)
        if not isinstance(need_web, bool):
            return {"ok": False, "error": "need_web 必须是布尔值 true 或 false"}
        notes = str(raw.get("notes", "") or "")

        try:
            plan = Plan(
                sub_questions=sub_questions,
                success_criteria=success_criteria,
                need_web=need_web,
                notes=notes,
            )
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": f"Plan 构造失败：{e}"}

        return {"ok": True, "data": plan}