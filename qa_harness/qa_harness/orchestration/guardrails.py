from core import MAX_FIX_ROUNDS, QATaskContext, TIER_ORDER


def _fix_round(ctx: QATaskContext) -> int:
    return ctx.attempts.get("fix_round", 0)


def _bump_fix_round(ctx: QATaskContext) -> None:
    ctx.attempts["fix_round"] = _fix_round(ctx) + 1


def _next_tier(tier: str) -> str | None:
    if tier not in TIER_ORDER:
        return None
    target_order = TIER_ORDER[tier] + 1
    for k, v in TIER_ORDER.items():
        if v == target_order:
            return k
    return None


def check_repeat_call(
    ctx: QATaskContext,
    action: str,
    args_digest: str,
    threshold: int,
) -> tuple[bool, str]:
    consecutive = 0
    for entry in reversed(ctx.call_log):
        if entry.delegate == action and entry.args_digest == args_digest:
            consecutive += 1
        else:
            break

    if consecutive >= threshold:
        return False, (
            f"熔断：{action} 以相同入参（digest={args_digest}）"
            f"已连续调用 {consecutive} 次（阈值 {threshold}），"
            "疑似死循环。请更换参数或改走其他工具。"
        )
    return True, ""


def check_tier_change(ctx: QATaskContext, new_tier: str) -> tuple[bool, str]:
    rd = ctx.route_decision
    if rd is None:
        return True, ""
    cur_tier = rd.tier
    if TIER_ORDER[new_tier] < TIER_ORDER[cur_tier]:
        count = ctx.attempts.get("downgrade_rejected", 0) + 1
        ctx.attempts["downgrade_rejected"] = count
        return False, (
            f"档位只升不降：当前 tier={cur_tier!r}，拒绝降级到 {new_tier!r}"
            f"（已记账 downgrade_rejected={count}）"
        )
    return True, ""


def apply_escalation(ctx: QATaskContext) -> str | None:
    rd = ctx.route_decision
    if rd is None:
        return None
    cur_tier = rd.tier
    new_tier = _next_tier(cur_tier)  

    if new_tier is None or TIER_ORDER[new_tier] <= TIER_ORDER[cur_tier]:
        return None

    key = f"escalated_from_{cur_tier}"
    if ctx.attempts.get(key, 0) > 0:
        return None

    rd.tier = new_tier  # type: ignore[assignment]  # 运行时 Literal 赋 str 合法
    ctx.attempts[key] = 1
    return new_tier


def check_precondition(ctx: QATaskContext, action: str) -> tuple[bool, str]:
    if action == "delegate_route":
        return True, ""

    if action == "delegate_plan":
        rd = ctx.route_decision
        if rd is None:
            return False, "route_decision 未写入，请先调 delegate_route"
        if rd.tier != "deep":
            return False, f"tier={rd.tier!r} 不需要 planner（仅 deep 档调用）"
        return True, ""

    if action == "delegate_retrieve":
        rd = ctx.route_decision
        if rd is None:
            return False, "route_decision 未写入，请先调 delegate_route"
        if rd.route not in ("kb", "web"):
            return False, f"route={rd.route!r} 不需要检索（仅 kb/web 走 retriever）"
        if rd.tier == "deep" and ctx.plan is None:
            return False, "tier=deep 必须先调 delegate_plan 再 delegate_retrieve"
        v = ctx.verdict
        if v is not None and not v.passed:
            if v.fix_hint != "need_more_evidence":
                return False, (
                    f"verdict.passed=False 但 fix_hint={v.fix_hint!r}，"
                    "应回 delegate_answer 修草稿而不是重检索"
                )
            if _fix_round(ctx) >= MAX_FIX_ROUNDS:
                return False, (
                    f"fix_round 已达上限 {MAX_FIX_ROUNDS}，"
                    "请直接 submit_answer（拒答出口兜底）"
                )
        return True, ""

    if action == "delegate_answer":
        rd = ctx.route_decision
        if rd is None:
            return False, "route_decision 未写入，请先调 delegate_route"
        if rd.tier == "direct":
            return True, ""
        if rd.route == "reject":
            return True, ""
        if ctx.evidence_report is None:
            return False, f"tier={rd.tier} 需要 evidence_report，请先调 delegate_retrieve"
        v = ctx.verdict
        if v is not None and not v.passed:
            if v.fix_hint != "fix_draft":
                return False, (
                    f"verdict.passed=False 但 fix_hint={v.fix_hint!r}，"
                    "应回 delegate_retrieve 补证据而不是修草稿"
                )
            if _fix_round(ctx) >= MAX_FIX_ROUNDS:
                return False, (
                    f"fix_round 已达上限 {MAX_FIX_ROUNDS}，"
                    "请直接 submit_answer（拒答出口兜底）"
                )
        return True, ""

    if action == "delegate_critic":
        rd = ctx.route_decision
        if rd is None:
            return False, "route_decision 未写入，请先调 delegate_route"
        if rd.tier != "deep":
            return False, f"tier={rd.tier!r} 不需要 critic（仅 deep 档调用）"
        if ctx.draft is None:
            return False, "draft 未写入，请先调 delegate_answer"
        if _fix_round(ctx) >= MAX_FIX_ROUNDS:
            return False, (
                f"fix_round 已达上限 {MAX_FIX_ROUNDS}，"
                "请直接 submit_answer（不再 delegate_critic）"
            )
        return True, ""

    if action == "submit_answer":
        if ctx.draft is None:
            return False, "draft 未写入，请先调 delegate_answer"
        rd = ctx.route_decision
        if rd is None:
            return False, "route_decision 未写入，无法按档校验"
        d = ctx.draft

        if rd.tier == "direct":
            return True, ""

        if rd.tier == "lite":
            if d.is_refusal:
                if d.citations:
                    return False, "拒答草稿不允许挂引用"
                return True, ""
            if not d.citations:
                return False, "lite 档事实性回答 citations 不允许为空；证据不足请拒答"
            return True, ""

        if rd.tier == "deep":
            if rd.route == "reject":
                return True, ""
            if d.is_refusal:
                return True, ""
            if not d.citations:
                return False, "deep 档事实性回答 citations 不允许为空；证据不足请拒答"
            if _fix_round(ctx) >= MAX_FIX_ROUNDS:
                return True, ""
            v = ctx.verdict
            if v is None:
                return False, "tier=deep 事实性回答必须先调 delegate_critic 核实草稿"
            if not v.passed:
                _bump_fix_round(ctx)
                round_now = _fix_round(ctx)
                if v.fix_hint == "need_more_evidence":
                    return False, (
                        f"critic 未通过（fix_round={round_now}）：fix_hint=need_more_evidence，"
                        "请回 delegate_retrieve 补证据后重新 delegate_answer"
                    )
                if v.fix_hint == "fix_draft":
                    return False, (
                        f"critic 未通过（fix_round={round_now}）：fix_hint=fix_draft，"
                        "请回 delegate_answer 修草稿后重新 delegate_critic"
                    )
                return False, (
                    f"critic 未通过（fix_round={round_now}）：fix_hint={v.fix_hint!r}"
                )
            return True, ""

        return False, f"未知 tier: {rd.tier!r}"

    return False, f"未知 action: {action!r}"
