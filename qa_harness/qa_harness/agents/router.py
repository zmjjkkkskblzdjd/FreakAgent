from __future__ import annotations

from typing import Any

from core import DEFAULT_MODEL_NAME, ROUTES, TIERS, RouteDecision
from tools import PermissionLevel

from .base import AgentProfile, BaseAgent

ROUTER_PROFILE = AgentProfile(
    role="router",
    role_description="判定用户问题的意图类型 route 与难度档位 tier，不回答问题本身。",
    allowed_tools=(),
    permission=PermissionLevel.NONE,
)


class RouterAgent(BaseAgent):

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME) -> None:
        super().__init__(ROUTER_PROFILE, model_name=model_name)

    def _validate_and_build(self, raw: dict[str, Any]) -> dict[str, Any]:
        route = raw.get("route")
        tier = raw.get("tier")

        if route not in ROUTES:
            return {"ok": False, "error": f"非法 route: {route!r}（应为 {ROUTES}）"}
        if tier not in TIERS:
            return {"ok": False, "error": f"非法 tier: {tier!r}（应为 {TIERS}）"}

        try:
            data = RouteDecision(
                route=route,
                tier=tier,
                reason=str(raw.get("reason", "")),
                clarifying_question=raw.get("clarifying_question"),
            )
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": f"RouteDecision 构造失败：{e}"}

        return {"ok": True, "data": data}
