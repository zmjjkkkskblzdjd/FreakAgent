from __future__ import annotations

import re
from dataclasses import dataclass

from unified_agent.core.state import UnifiedState


@dataclass(frozen=True)
class PolicyHit:
    rule: str
    reason: str
    level: str = "hard"


_DDL = re.compile(r"\b(drop\s+(table|database|schema)|truncate\s+(table\s+)?\w+)\b", re.I)
_DELETE = re.compile(r"\bdelete\s+from\b", re.I)
_UPDATE = re.compile(r"\bupdate\s+\w+", re.I)
_WHERE = re.compile(r"\bwhere\b", re.I)

_PII = re.compile(r"手机号|手机号码|身份证号?|密码|token|密钥|凭据|账号|api[_\s-]?key", re.I)
_BULK = re.compile(r"全量|全部|所有|整张表|整个库|导出|打包|批量|一次性", re.I)

_PROMPT_TARGET = re.compile(
    r"系统提示词|系统提示|system\s*prompt|提示词原文|初始指令|隐藏指令|内部规则|"
    r"你的规则|开发者指令", re.I)
_PROMPT_ACTION = re.compile(r"输出|打印|显示|告诉|重复|复述|贴出|给我|是什么|内容|泄露|原文", re.I)

_CROSS_TENANT = re.compile(r"跨\s*租户|其他\s*租户|别的\s*租户|其它\s*租户|\btenant_id\b", re.I)
_OTHER_TENANT_DATA = re.compile(
    r"(其他|别的|另一家|其它|别家)\s*(公司|企业|客户)[^，。]{0,12}"
    r"(数据|订单|报表|库|表|系统|后台)")


def check_question(text: str) -> PolicyHit | None:
    q = (text or "").strip()
    if not q:
        return None

    if _DDL.search(q) or _DELETE.search(q):
        return PolicyHit("destructive_write", "请求包含删表/清空/整表删除操作，系统不执行破坏性写。")
    if _UPDATE.search(q) and not _WHERE.search(q):
        return PolicyHit("destructive_write",
                         "请求是未带 WHERE 条件的 UPDATE（会整表覆盖），系统不执行。")

    if _PII.search(q) and _BULK.search(q):
        return PolicyHit("bulk_pii", "请求涉及批量导出手机号/密码/凭据等敏感数据，系统不提供。")

    if _PROMPT_TARGET.search(q) and _PROMPT_ACTION.search(q):
        return PolicyHit("prompt_exfiltration", "请求涉及获取系统提示词或内部规则，系统不提供。")

    if _CROSS_TENANT.search(q) or _OTHER_TENANT_DATA.search(q):
        return PolicyHit("cross_tenant", "请求涉及其他租户/公司的数据，超出当前权限范围。")
    return None


def policy_gate(state: UnifiedState) -> dict:
    hit = check_question(state.get("resolved_question") or state.get("question", ""))
    if hit is None and str(state.get("safety") or "safe") == "unsafe":
        hit = PolicyHit("intake_safety", "请求被分诊判定为不安全，系统不执行。")
    if hit is None:
        return {}
    return {
        "policy_denied": True,
        "policy_rule": hit.rule,
        "refusal_reason": "policy_denied",
        "refusal_level": hit.level,
        "policy_message": hit.reason,
    }
