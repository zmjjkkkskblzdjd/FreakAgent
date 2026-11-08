from __future__ import annotations

import os
import re as _re
import sys as _sys
from decimal import Decimal, InvalidOperation

from unified_agent.agents.fact_check import attach_fact_check
from unified_agent.core.contracts import (
    Claim, NumberClaim, PartialFailure, SqlBlock, UnifiedFinalReport,
    lane_failed, lane_produced,
)
from unified_agent.core.limiters import cost_ledger_entry
from unified_agent.core.llm import ask_llm_json, ask_llm_text
from unified_agent.core.numbers import (
    approx_equal, as_decimal, extract_numbers, normalize, unit_scaled_values,
)
from unified_agent.core.calc_sandbox import (
    RUN_PYTHON_TOOL, build_vars_from_claims, safe_eval,
)
from unified_agent.core.state import RAGContext, SQLContext, UnifiedState
from unified_agent.memory.recall import lesson_block
from unified_agent.observability.tracer import emit_event
from unified_agent.prompts import load_prompt

_CLAIMS_SYSTEM, _CLAIMS_TPL = load_prompt("merge_claims")
_RENDER_SYSTEM, _RENDER_TPL = load_prompt("merge_render")

_EXPECTED_LANES = {"rag": ("rag",), "sql": ("sql",), "hybrid": ("rag", "sql")}

_LANE_NAME = {"rag": "文档检索（RAG）", "sql": "库内数据（SQL）"}

_SOURCE_LABEL = {"kb": "知识库", "web": "联网搜索"}

_MERGE_MAX_TOKENS = 16000

_CLAIMS_TIMEOUT = int(os.getenv("UA_MERGE_CLAIMS_TIMEOUT", "240"))

_REASON_BY_PREFIX = {
    "schema_no_hit": "库内没有问题涉及的表或字段",
    "review_need_clarify": "问题语义需要澄清，无法生成可靠 SQL",
    "review_escalated": "升档深分析后仍未通过 SQL 审查",
    "review_fix_sql": "SQL 多轮修复后仍未通过审查",
    "review_failed": "SQL 未通过审查",
    "runtime": "SQL 在数据库侧执行失败",
    "execute_failed": "SQL 执行失败",
    "node_crash": "车道内部异常",
    "lane_deadline": "本轮分析超出车道时限，已中止（未完成的结论不作答）",
    "llm_timeout": "模型调用超时，降档重试后仍未完成分析",
    "gate": "执行网关拒绝",
    "write_not_approved": "写操作未获批准，未执行任何变更",
    "write_precheck_failed": "写操作预检未通过，未执行任何变更",
    "write_execute_failed": "写操作执行失败",
    "guard_rejected": "安全守卫拒绝",
    "write_forbidden": "当前账号没有该表的写权限，写入已被拦截（未执行任何变更）",
    "too_many_rows": "预估影响行数超过单次上限，已拒绝执行，请缩小 WHERE 范围",
    "actor_required": "写通道缺少会话身份，未执行（请重新登录后再试）",
    "preview_token_required": "写预检令牌缺失或已过期，请重新确认后再执行",
    "preview_invalid": "写预检令牌无效，请重新确认后再执行",
    "preview_expired": "写预检令牌已过期（60 秒有效），请重新确认后再执行",
    "datasource_not_found": "所选数据源不存在或无权访问",
    "unknown_table": "SQL 引用的表不在所选数据源中",
    "invalid_sql": "SQL 不符合所选数据源的语法/只读要求",
    "degrade_query_not_allowed": "该查询需要走源库直读，但不符合直读限额规则",
    "analytics_unavailable": "分析库（ClickHouse 镜像）不可用",
    "source_unavailable": "源库不可用（连接失败或服务繁忙）",
    "source_timeout": "源库查询超时，请缩小范围后重试",
    "source_busy": "源库直读繁忙，请稍后重试",
    "mirror_table_unavailable": "该表镜像不可用，且源库直读失败",
    "lane_absent": "该车道本次未运行",
}


def _channel(state: UnifiedState) -> dict | None:
    sql = state.get("sql_ctx") or SQLContext()
    qr = sql.query_result or {}
    if not isinstance(qr, dict):
        return None
    dsid = state.get("datasource_id")
    source = qr.get("source") or ""
    if dsid in (None, "", 0) and not source:
        return None
    return {
        "datasource_id": dsid,
        "source": source,
        "mirror_note": qr.get("mirror_note") or "",
        "lag_ms": qr.get("lag_ms"),
    }


def build_evidence_ledger(state: UnifiedState) -> list[dict]:
    ledger: list[dict] = []
    rag = state.get("rag_ctx") or RAGContext()
    for ev in rag.provenance:
        ledger.append({"origin": "rag", "value": ev.get("text", ""), "evidence_id": ev.get("chunk_id")})
    sql = state.get("sql_ctx") or SQLContext()
    rows = (sql.query_result or {}).get("rows", [])
    for i, row in enumerate(rows):
        for cell in row:
            ledger.append({"origin": "sql", "value": str(cell), "evidence_id": f"sql-row-{i}"})
    return ledger


_LEDGER_VALUE_CAP = 300   

_LEDGER_MAX_LINES = int(os.getenv("UA_MERGE_LEDGER_MAX_LINES", "40"))

_SQL_DIGEST_MAX_CHARS = int(os.getenv("UA_MERGE_SQL_DIGEST_MAX_CHARS", "1500"))


def _ledger_lines(ledger: list[dict], question: str = "") -> str:
    import re as _re

    def _has_number(text: str) -> bool:
        return bool(_re.search(r'\d', text))

    def _kw_match(text: str, kw: str) -> int:
        return text.count(kw) if kw else 0

    keywords = []
    if question:
        stopwords = {'多少', '什么', '如何', '为什么', '哪个', '哪些', '怎样', '同比', '环比',
                     '变化', '情况', '介绍', '说明'}
        keywords = [w for w in _re.findall(r'[\u4e00-\u9fff]{2,}', question)
                    if w not in stopwords]

    def _score(e) -> tuple:
        value = str(e["value"])
        has_num = _has_number(value)
        kw_score = sum(_kw_match(value, kw) for kw in keywords)
        return (1 if has_num else 0, kw_score, 1 if e.get("origin") == "sql" else 0)

    sorted_ledger = sorted(ledger, key=_score, reverse=True)

    lines = []
    for e in sorted_ledger[:_LEDGER_MAX_LINES]:
        value = str(e["value"])
        if len(value) > _LEDGER_VALUE_CAP:
            value = value[:_LEDGER_VALUE_CAP] + "…"
        lines.append(f"{e['origin']} | {e['evidence_id']} | {value}")
    return "\n".join(lines) or "（空）"


def _sql_digest(sql: SQLContext) -> str:
    text = (getattr(sql, "analysis_md", "") or "").strip()
    if text:
        return _clip_digest(text)
    qr = getattr(sql, "query_result", None) or {}
    rows = qr.get("rows") if isinstance(qr, dict) else None
    if not rows:
        return "（sql 车道缺席）"
    cols = [str(c) for c in (qr.get("columns") or [])]
    total = len(rows)
    head = f"sql 结果集：共 {total} 行" + (f"，列：{'、'.join(cols)}" if cols else "")
    lines = []
    for row in rows:
        cells = row.values() if isinstance(row, dict) else row
        lines.append("| " + " | ".join(str(v) for v in cells) + " |")
    budget = max(0, _SQL_DIGEST_MAX_CHARS - len(head) - 60)
    kept, used = [], 0
    for line in lines:
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    out = "\n".join([head, *kept])
    if len(kept) < total:
        out += f"\n…共 {total} 行，此处展示前 {len(kept)} 行；完整数据已在证据台账"
    return out


def _clip_digest(text: str) -> str:
    if len(text) <= _SQL_DIGEST_MAX_CHARS:
        return text
    return text[:_SQL_DIGEST_MAX_CHARS] + "…（已截断，完整数据见证据台账）"


def build_claims(state: UnifiedState) -> list[Claim]:
    rag = state.get("rag_ctx") or RAGContext()
    sql = state.get("sql_ctx") or SQLContext()
    question = state.get("question", "")

    ledger = build_evidence_ledger(state)
    ledger_with_num = sum(1 for e in ledger if any(c.isdigit() for c in str(e["value"])))
    ledger_snr = ledger_with_num / len(ledger) if ledger else 0.0
    print(f"[merge 质量] ledger={len(ledger)} | snr={ledger_snr:.2f}", file=_sys.stderr)

    ledger_lines = _ledger_lines(ledger, question=question)

    ledger_value_cap = _LEDGER_VALUE_CAP
    try:
        from qa_harness.qa_harness.context.budget import count_tokens
        est_tokens = count_tokens(ledger_lines) + 800
        if est_tokens > 5000 and ledger_value_cap > 80:
            ledger_value_cap = 80
            ledger_lines = _ledger_lines(ledger, question=question)  
            print(f"[merge] claims token 预算超限，_LEDGER_VALUE_CAP→{ledger_value_cap}",
                  file=_sys.stderr)
    except Exception:
        pass  

    digest = _sql_digest(sql)
    user = _CLAIMS_TPL.format(
        question=question,
        ledger_lines=ledger_lines,
        sql_digest=digest,
    )
    try:
        d = ask_llm_json(_CLAIMS_SYSTEM, user, max_tokens=_MERGE_MAX_TOKENS,
                         thinking=False, timeout=_CLAIMS_TIMEOUT)
    except Exception as e:  # noqa: BLE001 — fail-soft，但必须显式留痕（宪法 4）
        print(f"[merge] claims 抽取失败：{type(e).__name__}: {e}", file=_sys.stderr)
        return []
    claims: list[Claim] = []
    for raw in (d or {}).get("claims", []):
        try:
            claims.append(Claim(
                text=str(raw.get("text", "")),
                numbers=[NumberClaim(value=str(n.get("value", "")),
                                     origin=n.get("origin", "sql"),
                                     evidence_id=str(n.get("evidence_id", "")))
                         for n in raw.get("numbers", [])],
                relation=raw.get("relation"),
            ))
        except Exception:
            continue                    
    return claims



_CALC_PATTERNS: list[tuple[re.Pattern, str]] = [
    (_re.compile(r"多少倍|几倍|多少.?倍|倍数是|倍数有多少"), "times"),
    (_re.compile(r"百分之几|百分之多少|占.?比例|占.?多少|相当于.?百分之"), "percent"),
    (_re.compile(r"同比.{0,4}(增长|下降|变化|变化率|涨|跌)|年同比|YoY", _re.I), "yoy"),
    (_re.compile(r"(比.+?)(高|低|多出|少出|相差|差额)", _re.I), "diff"),
]


def _detect_calc_intent(question: str) -> str | None:
    if not question:
        return None
    for pat, ctype in _CALC_PATTERNS:
        if pat.search(question):
            return ctype
    return None


def _operand_labels(question: str, ctype: str) -> tuple[str, str] | None:
    q = _re.sub(r"^问题[:：;；]\s*", "", question).strip()
    q = q.strip("。，,；; ")

    if ctype in ("times", "percent"):
        m = _re.match(r"^(.+?)[,，]?\s*(?:是|相当于|为|占)\s*(.+?)\s*的\s*(?:多少倍|几倍|百分之几|百分之多少|比例)", q)
        if m:
            return (m.group(1).strip(), m.group(2).strip())
        m = _re.match(r"^(.+?)\s*(?:的)\s*(?:多少倍|几倍|百分之几|百分之多少|比例)\s*(?:是|为|相当于)\s*(.+)", q)
        if m:
            return (m.group(2).strip(), m.group(1).strip())
    elif ctype == "yoy":
        return None  
    return None


def _parse_value(text: str, raw_value: str) -> Decimal | None:
    scales = unit_scaled_values(text)
    norm_key = normalize(raw_value)
    if norm_key in scales:
        return next(iter(scales[norm_key]))
    d = as_decimal(normalize(raw_value))
    return d


def _claim_score(claim: Claim, label_words: set[str]) -> int:
    text = claim.text or ""
    for nc in claim.numbers:
        text += " " + nc.value
    score = 0
    for w in label_words:
        if w in text:
            score += 2 if len(w) >= 2 else 1
    return score


def _match_operand(label: str, claims: list[Claim]) -> tuple[Claim, Decimal, NumberClaim] | None:
    clean = label.strip()
    label_words: set[str] = set()
    for chunk in _re.split(r"[^\u4e00-\u9fff\d]+", clean):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk[0].isdigit():
            label_words.add(chunk)
            continue
        for i in range(len(chunk)):
            for j in range(i + 2, min(i + 6, len(chunk) + 1)):
                label_words.add(chunk[i:j])

    best: tuple[Claim, int] | None = None
    for c in claims:
        s = _claim_score(c, label_words)
        if s == 0:
            continue
        if best is None or s > best[1]:
            best = (c, s)

    if best is None:
        return None
    claim, _ = best
    for nc in claim.numbers:
        d = _parse_value(claim.text, nc.value)
        if d is not None:
            return (claim, d, nc)
    return None


def _find_yr_pair(claims: list[Claim]) -> tuple[Claim, Decimal, NumberClaim, Claim, Decimal, NumberClaim] | None:
    yr_re = _re.compile(r"(20\d{2}|去年|今年|上年|本年)")
    pairs: dict[str, list[tuple[Claim, Decimal, NumberClaim]]] = {}
    for c in claims:
        m = yr_re.search(c.text or "")
        if not m:
            continue
        yr_label = m.group(0)
        for nc in c.numbers:
            d = _parse_value(c.text, nc.value)
            if d is not None:
                pairs.setdefault(yr_label, []).append((c, d, nc))
                break

    yr_map = {
        "今年": "去年", "本年": "上年",
        "2025": "2024", "2026": "2025", "2024": "2023",
    }
    for cur_label, prev_label in yr_map.items():
        if cur_label in pairs and prev_label in pairs:
            cur_c, cur_d, cur_nc = pairs[cur_label][0]
            prev_c, prev_d, prev_nc = pairs[prev_label][0]
            return (cur_c, cur_d, cur_nc, prev_c, prev_d, prev_nc)
    return None


def _fmt_pct(d: Decimal, digits: int = 2) -> str:
    q = Decimal(10) ** -digits
    return str(d.quantize(q)) + "%"


def _fmt_times(d: Decimal, digits: int = 1) -> str:
    q = Decimal(10) ** -digits
    return str(d.quantize(q))


def _inject_derived_claims(question: str, claims: list[Claim]) -> list[Claim]:
    ctype = _detect_calc_intent(question)
    if not ctype:
        return claims

    derived: list[Claim] = []

    if ctype in ("times", "percent"):
        labels = _operand_labels(question, ctype)
        if labels is None:
            return claims
        x_label, y_label = labels
        x_hit = _match_operand(x_label, claims)
        y_hit = _match_operand(y_label, claims)
        if x_hit is None or y_hit is None:
            print(f"[merge] 派生计算放弃：{ctype} 匹配不到 operands "
                  f"(x={x_label}→{x_hit is not None}, y={y_label}→{y_hit is not None})",
                  file=_sys.stderr)
            return claims
        _, x_val, x_nc = x_hit
        _, y_val, y_nc = y_hit
        if y_val == 0:
            print("[merge] 派生计算放弃：除数为 0", file=_sys.stderr)
            return claims

        if ctype == "times":
            ratio = x_val / y_val
            ratio_str = _fmt_times(ratio, digits=2)
            derived.append(Claim(
                text=f"{x_label} 是 {y_label} 的 {ratio_str} 倍",
                numbers=[NumberClaim(
                    value=ratio_str, origin="sql", evidence_id=f"derived:{ctype}",
                    derived=True,
                    expression=f"{x_val} / {y_val} = {ratio_str}",
                )],
            ))
        else:  
            pct = x_val / y_val * Decimal("100")
            pct_str = _fmt_pct(pct)
            derived.append(Claim(
                text=f"{x_label} 占 {y_label} 的 {pct_str}",
                numbers=[NumberClaim(
                    value=pct_str, origin="sql", evidence_id=f"derived:{ctype}",
                    derived=True,
                    expression=f"{x_val} / {y_val} * 100 = {pct_str}",
                )],
            ))

    elif ctype == "yoy":
        pair = _find_yr_pair(claims)
        if pair is None:
            return claims
        cur_c, cur_d, cur_nc, prev_c, prev_d, prev_nc = pair
        if prev_d == 0:
            return claims
        yoy = (cur_d - prev_d) / prev_d * Decimal("100")
        yoy_str = _fmt_pct(yoy, digits=1)
        sign = "增长" if yoy >= 0 else "下降"
        derived.append(Claim(
            text=f"同比{sign} {yoy_str}（{cur_d} vs {prev_d}）",
            numbers=[NumberClaim(
                value=yoy_str, origin="sql", evidence_id="derived:yoy",
                derived=True,
                expression=f"({cur_d} - {prev_d}) / {prev_d} * 100 = {yoy_str}",
            )],
        ))

    if derived:
        print(f"[merge] 派生计算注入：ctype={ctype}, derived={len(derived)} 条",
              file=_sys.stderr)
    return claims + derived



def _failure_reason(lane: str, ctx) -> tuple[str, str | None]:
    if ctx is None:
        return _REASON_BY_PREFIX["lane_absent"], "lane_absent"
    code = ctx.error_code or ""
    prefix = code.split(":", 1)[0].strip()
    reason = _REASON_BY_PREFIX.get(prefix)
    if reason is None:
        if prefix.startswith("review_"):
            reason = "SQL 未通过审查"
        elif lane == "sql":
            reason = "库内查询未能完成" if ctx.status == "error" else "库内数据无法作答该问题"
        else:
            reason = "文档检索未能完成" if ctx.status == "error" else "文档库内未找到足以作答的证据"
    safe = prefix if prefix and not any(ch.isdigit() for ch in prefix) else None
    return reason, safe


def collect_lane_failures(state: UnifiedState, mode: str) -> tuple[list[PartialFailure], list[str]]:
    entries: list[PartialFailure] = []
    hard: list[str] = []
    for lane in _EXPECTED_LANES.get(mode, ()):
        ctx = state.get(f"{lane}_ctx")
        if lane_produced(ctx):
            continue
        reason, _ = _failure_reason(lane, ctx)
        entries.append(PartialFailure(
            lane=lane,
            status=ctx.status if ctx is not None and ctx.status in ("ok", "empty", "error") else "empty",
            outcome=ctx.outcome if ctx is not None and ctx.outcome in ("answered", "refused") else "refused",
            error_code=ctx.error_code if ctx is not None else "lane_absent",
            reason=reason,
            retriable=bool(ctx.retriable) if ctx is not None else False,
        ))
        if lane_failed(ctx):
            hard.append(lane)
    return entries, hard


def _failure_memo(state: UnifiedState, hard: list[str]) -> str:
    if not hard:
        return ""
    parts = []
    for lane in hard:
        reason, safe = _failure_reason(lane, state.get(f"{lane}_ctx"))
        code = f"（错误码：{safe}）" if safe else ""
        parts.append(f"{_LANE_NAME[lane]}车道本次未能产出：{reason}{code}。")
    return ("---\n> **车道失败备忘（系统追加）**：" + "".join(parts)
            + "该车道结果缺失，上文结论仅基于已成功车道；"
              "如上文将其描述为已产出，请以本备忘为准。")


def _has_web_provenance(state: UnifiedState) -> bool:
    rag = state.get("rag_ctx") or RAGContext()
    for p in rag.provenance or []:
        if str((p or {}).get("doc_type") or "").lower() == "web":
            return True
        if str((p or {}).get("match_type") or "").lower() == "web":
            return True
    return False


def _scope_tail(state: UnifiedState) -> str:
    if _has_web_provenance(state):
        return "已按你的联网需求改用联网检索，本轮引用来自联网结果。"
    if state.get("web_intent"):
        return ("联网开关已关闭，未执行联网检索。"
                if not state.get("web_enabled", True)
                else "已按你的联网需求尝试联网检索，但未获得可用结果。")
    return "自动联网兜底已跳过。"


def _scope_note(state: UnifiedState, mode: str) -> str:
    if mode not in ("rag", "hybrid"):
        return ""
    kb_ids = state.get("kb_ids")
    if kb_ids is not None and not kb_ids:
        return ("---\n> **检索范围说明（系统追加）**："
                f"本轮未选择任何知识库，未检索内部资料；{_scope_tail(state)}")
    scope = state.get("kb_scope")
    if not isinstance(scope, dict):
        return ""
    current = state.get("resolved_question") or state.get("question") or ""
    if scope.get("question") and scope["question"] != current:
        return ""
    empty = [str(n).strip() for n in (scope.get("empty_names") or []) if str(n).strip()]
    if not empty:
        return ""
    effective = scope.get("effective_ids") or []
    names = "" if any(_re.search(r"\d", n) for n in empty) else "、".join(empty)
    who = f"（{names}）" if names else ""
    if effective:
        body = (f"本次检索的知识库中，{who}未检索到可用资料，"
                "已从本轮检索范围排除；其余知识库正常参与回答。")
    else:
        body = (f"本次检索的知识库{who}未检索到可用资料，本轮没有知识库引用；"
                f"{_scope_tail(state)}")
    return f"---\n> **检索范围说明（系统追加）**：{body}"


def _refusal_report(state: UnifiedState, mode: str,
                    entries: list[PartialFailure], hard: list[str]) -> UnifiedFinalReport:
    lines = ["**本次未能给出答案。**", ""]
    for lane in hard:
        ctx = state.get(f"{lane}_ctx")
        if ctx is None:
            continue
        prose = (getattr(ctx, "analysis_md", "") or getattr(ctx, "answer", "") or "").strip()
        if prose:
            lines += [prose, ""]
    lines.append("**失败明细**")
    for e in entries:
        code = f"（错误码：{e.error_code}）" if e.error_code else ""
        retry = "，可重试" if e.retriable else ""
        lines.append(f"- {_LANE_NAME[e.lane]}车道：{e.reason}{code}{retry}")
    sql_ctx = state.get("sql_ctx")
    if sql_ctx is not None and "sql" in hard:
        if sql_ctx.db_error:
            lines.append(f"- 数据库回执（截断留痕）：{sql_ctx.db_error[:300]}")
        draft = sql_ctx.checked_sql_pre_injection or sql_ctx.sql
        if draft:
            lines += ["", "**SQL 草稿（未成功执行，仅供参考，勿当作已执行结果）**",
                      "", "```sql", draft.strip(), "```"]
    note = _scope_note(state, mode)
    if note:
        lines += ["", note]
    return UnifiedFinalReport(
        answer_md="\n".join(lines), claims=[], sql=None, mode=mode,
        partial_failures=entries, lane_decisions=state.get("lane_decisions") or [],
        channel=_channel(state), refusal_reason="lane_failed", refusal_level="soft")


def _policy_refusal_report(state: UnifiedState, mode: str) -> UnifiedFinalReport:
    rule = str(state.get("policy_rule") or "policy_denied")
    reason = str(state.get("policy_message")
                 or "请求涉及超出安全边界的操作，系统不执行。").strip()
    lines = [
        "**本次请求已被安全策略拒绝，未执行任何操作。**",
        "",
        f"原因：{reason}",
        f"（策略：{rule}）",
        "",
        "可以换一种不涉及破坏性写、批量敏感数据或越权范围的方式提问。",
    ]
    return UnifiedFinalReport(
        answer_md="\n".join(lines), claims=[], sql=None, mode=mode,
        partial_failures=[], lane_decisions=state.get("lane_decisions") or [],
        channel=_channel(state),
        refusal_reason="policy_denied", refusal_level="hard")


def _fallback_render(state: UnifiedState, claims: list[Claim], mode: str) -> str:
    parts: list[str] = []
    rag = state.get("rag_ctx") or RAGContext()
    sql = state.get("sql_ctx") or SQLContext()

    if mode in ("rag", "hybrid") and rag.answer:
        parts.append(rag.answer)
    if mode in ("sql", "hybrid") and sql.analysis_md:
        parts.append(sql.analysis_md)

    if claims:
        parts.append("\n## 数字明细")
        for c in claims:
            nums = "、".join(n.value for n in c.numbers)
            parts.append(f"- {c.text}（{nums}）")

    sql_text = sql.executed_sql or sql.checked_sql_pre_injection or sql.sql or ""
    if sql_text and mode in ("sql", "hybrid"):
        parts.append(f"\n## 执行 SQL\n\n```sql\n{sql_text}\n```")

    return "\n".join(parts) if parts else "（LLM 渲染不可用，本次仅保留结构化结论，无数字。）"


def _source_type(prov: dict) -> str:
    if str((prov or {}).get("doc_type") or "").lower() == "web":
        return "web"
    if str((prov or {}).get("match_type") or "").lower() == "web":
        return "web"
    if str((prov or {}).get("source") or "").startswith(("http://", "https://")):
        return "web"
    return "kb"


def _build_citations(rag: RAGContext, mode: str, produced: bool,
                     sql_sources: list[dict] | None = None) -> list[dict]:
    citations: list[dict] = []
    if mode in ("rag", "hybrid") and produced:
        prov = {p.get("chunk_id"): p for p in rag.provenance}
        for cid in rag.citations:
            item = prov.get(cid) or {}
            citations.append({
                "chunk_id": cid,
                "source": item.get("source") or "",
                "excerpt": item.get("text") or "",
                "source_type": _source_type(item),
            })
    if mode in ("sql", "hybrid"):
        for item in sql_sources or []:
            citations.append({
                "chunk_id": item.get("chunk_id") or "",
                "source": item.get("source") or "",
                "excerpt": item.get("excerpt") or "",
                "source_type": item.get("source_type") or "web",
            })
    return citations


def _source_note(citations: list[dict]) -> str:
    if not citations:
        return ""
    lines = ["---", "**来源标注（系统追加）**"]
    for idx, c in enumerate(citations, 1):
        lines.append(f"- [{idx}] {_SOURCE_LABEL.get(c.get('source_type'), '知识库')}")
    return "\n".join(lines)


def render(state: UnifiedState, claims: list[Claim], *, mode: str | None = None,
           failures: list[PartialFailure] | None = None,
           hard: list[str] | None = None) -> UnifiedFinalReport:
    sql = state.get("sql_ctx") or SQLContext()
    rag = state.get("rag_ctx") or RAGContext()
    if mode is None:
        mode = state.get("task_type", "rag")
        if mode not in ("rag", "sql", "hybrid", "chitchat", "web"):      
            mode = "rag"
    if failures is None or hard is None:
        failures, hard = collect_lane_failures(state, mode)
    memo = _failure_memo(state, hard)
    executed_display = sql.executed_sql or "（无）"
    if "sql" in hard:
        executed_display = "（SQL 车道本次未成功执行，无已执行 SQL 可引用）"
    user = _RENDER_TPL.format(
        question=state.get("resolved_question") or state.get("question", ""),
        claims_json="\n".join(c.model_dump_json() for c in claims) or "[]",
        executed_sql=executed_display,
        citations_table="（首版由 rag_ctx.citations 对账，见 merge 渲染规则）",
        failure_note=memo or "（无）",
    )

    _vars = build_vars_from_claims(claims, question=state.get("question", ""))
    if _vars:
        _var_lines = [f"- `{name}` = {value}" for name, value in sorted(_vars.items())]
        user += "\n\n---\n**可用变量（供 run_python 工具引用）**：\n" + "\n".join(_var_lines)

    _lessons = lesson_block(state, ("rag", "sql", "hybrid"))
    if _lessons:
        user += "\n\n---\n" + _lessons

    user += ("\n\n---\n【硬约束（再次重申，最高优先级）】只允许使用 claims 清单里的"
             "论断与数字；清单外数字一律不得出现；每个数字必须带出处标注；"
             "失败车道不得写成已执行，也不得为其编造数字或结论。\n")

    if not claims and build_evidence_ledger(state):
        print("[merge] claims 为空但台账有数字：跳过 render，走确定性兜底",
              file=_sys.stderr)
        text = _fallback_render(state, claims, mode)
    else:
        try:

            def _run_python_executor(fname: str, fargs: dict) -> str:
                if fname != "run_python":
                    return f"[未知工具：{fname}]"
                expr = fargs.get("expression", "")
                if not expr:
                    return "[错误：expression 不能为空]"
                try:
                    result = safe_eval(expr, vars=_vars)
                    return f"{result}"
                except Exception as e:  # noqa: BLE001
                    return f"[算术错误：{e}]"

            _render_system = _RENDER_SYSTEM + (
                "\n\n# 可用工具：run_python\n"
                "你可以调用 run_python 工具对 claims 里的数字执行任意算术运算。\n"
                "可用变量列表在下方 user message 末尾，工具会用那些变量名。\n"
                "返回结果会自动成为你下一轮推理的依据。\n"
                "示例场景：\n"
                "- 问题是「XX 占 YY 的百分之几」但 claims 里没有派生比例 → 调 run_python 算 percent(x, y)\n"
                "- 问题涉及动态筛选（全国减华东）→ 调 run_python 算 diff(total, huadong)\n"
                "- 多步嵌套计算 → 一次表达式全写完\n"
                "可用函数：percent(a,b), times(a,b), yoy(cur,prev), diff(a,b),\n"
                "sum(list), avg(list), min(list), max(list), round2(x), roundN(x,n)"
            )

            text = ask_llm_text(
                _render_system, user, max_tokens=_MERGE_MAX_TOKENS,
                stream=True, thinking=False,
                tools=[RUN_PYTHON_TOOL],
                tool_executor=_run_python_executor,
            )
        except Exception:
            text = _fallback_render(state, claims, mode)
    if not text.strip():
        text = _fallback_render(state, claims, mode)
    if memo:
        text = text.rstrip() + "\n\n" + memo
    notice = (state.get("degrade_notice") or "").strip()
    if notice:
        text = text.rstrip() + "\n\n" + f"> **提示（系统追加）**：{notice}"
    sql_text = sql.executed_sql or sql.checked_sql_pre_injection or sql.sql or ""
    sql_block = None
    if mode in ("sql", "hybrid") and sql_text and "sql" not in hard:
        sql_block = SqlBlock(sql=sql_text,
                             injected=bool(sql.policy_injected),
                             injection_hash=sql.injection_hash or None)
    citations = _build_citations(
        rag, mode, lane_produced(state.get("rag_ctx")), sql.web_sources)
    note = _source_note(citations)
    if note:
        text = text.rstrip() + "\n\n" + note
    scope = _scope_note(state, mode)
    if scope:
        text = text.rstrip() + "\n\n" + scope
    return UnifiedFinalReport(
        answer_md=text,
        claims=claims,
        sql=sql_block,
        mode=mode,
        citations=citations,
        partial_failures=failures,
        lane_decisions=state.get("lane_decisions") or [],
        channel=_channel(state),
    )


def _direct_report(state: UnifiedState, claims: list[Claim], mode: str,
                   failures: list[PartialFailure],
                   hard: list[str]) -> UnifiedFinalReport:
    rag = state.get("rag_ctx") or RAGContext()
    sql = state.get("sql_ctx") or SQLContext()

    if mode == "rag" and rag.answer:
        text = rag.answer
    elif mode == "sql" and sql.analysis_md:
        text = sql.analysis_md
    else:
        raise ValueError("_direct_report 仅用于单车道且已产出的场景")

    memo = _failure_memo(state, hard)
    if memo:
        text = text.rstrip() + "\n\n" + memo
    notice = (state.get("degrade_notice") or "").strip()
    if notice:
        text = text.rstrip() + "\n\n" + f"> **提示（系统追加）**：{notice}"

    sql_text = sql.executed_sql or sql.checked_sql_pre_injection or sql.sql or ""
    sql_block = None
    if mode in ("sql", "hybrid") and sql_text and "sql" not in hard:
        sql_block = SqlBlock(sql=sql_text,
                             injected=bool(sql.policy_injected),
                             injection_hash=sql.injection_hash or None)

    citations = _build_citations(
        rag, mode, lane_produced(state.get("rag_ctx")), sql.web_sources)
    note = _source_note(citations)
    if note:
        text = text.rstrip() + "\n\n" + note
    scope = _scope_note(state, mode)
    if scope:
        text = text.rstrip() + "\n\n" + scope

    return UnifiedFinalReport(
        answer_md=text,
        claims=claims,
        sql=sql_block,
        mode=mode,
        citations=citations,
        partial_failures=failures,
        passthrough=True,   
        lane_decisions=state.get("lane_decisions") or [],
        channel=_channel(state),
    )


def _attach_writes(report: UnifiedFinalReport, state: UnifiedState) -> UnifiedFinalReport:
    c = state.get("sql_ctx") or SQLContext()
    pre = dict(getattr(c, "write_precheck", None) or {})
    if not pre.get("committed"):
        return report
    entry = {
        "audit_id": pre.get("audit_id", ""),
        "operation": pre.get("operation", ""),
        "table": pre.get("table", ""),
        "affected_rows": int(pre.get("actual_rows") or pre.get("affected_rows") or 0),
        "undo_deadline": pre.get("undo_deadline", ""),
        "undo_available": bool(pre.get("undo_available")),
    }
    return report.model_copy(update={"writes": [*report.writes, entry]})


def merge(state: UnifiedState) -> dict:
    mode = state.get("task_type", "rag")
    if mode not in ("rag", "sql", "hybrid", "chitchat", "web"):
        mode = "rag"
    if state.get("policy_denied"):
        emit_event("node_exit", "merge.blocks", "ok",
                   {"path": "policy", "ms": {}, "n_claims": 0, "total_ms": 0},
                   thread_id=str(state.get("thread_id") or ""),
                   session_id=str(state.get("session_id") or ""))
        report = _policy_refusal_report(state, mode)
        return {"final_report": report.model_dump(),
                "cost_ledger": [cost_ledger_entry("merge", "merge", 0, 0)]}
    entries, hard = collect_lane_failures(state, mode)
    expected = _EXPECTED_LANES.get(mode, ())
    import time as _time
    _t0 = _time.perf_counter()
    blocks: dict[str, int] = {}
    claims: list[Claim] = []
    path = "refusal"
    if expected and len(hard) == len(expected):
        report = _refusal_report(state, mode, entries, hard)
    else:
        question = state.get("resolved_question") or state.get("question", "")
        lane_ready = (
            (mode == "rag" and lane_produced(state.get("rag_ctx")))
            or (mode == "sql" and lane_produced(state.get("sql_ctx")))
        )
        if lane_ready and not hard and not _detect_calc_intent(question):
            print("[merge] 直通且无算术语义：跳过 claims 抽取（省一次 16000 预算 LLM）",
                  file=_sys.stderr)
            blocks["claims"] = 0
        else:
            claims = build_claims(state)
            blocks["claims"] = round((_time.perf_counter() - _t0) * 1000)
            claims = _inject_derived_claims(question, claims)
        has_derived = any(any(nc.derived for nc in c.numbers) for c in claims)
        lane_produced_ok = (
            (mode == "rag" and lane_produced(state.get("rag_ctx")))
            or (mode == "sql" and lane_produced(state.get("sql_ctx")))
        )
        if lane_produced_ok and not hard and not has_derived:
            print(f"[merge] 单车道直通模式：mode={mode}，跳过 render", file=_sys.stderr)
            path = "direct"
            report = _direct_report(state, claims, mode=mode,
                                    failures=entries, hard=hard)
        else:
            if has_derived:
                print("[merge] 有派生数字，强制走 render 让 LLM 合成算术结论",
                      file=_sys.stderr)
            path = "render"
            _t1 = _time.perf_counter()
            report = render(state, claims, mode=mode, failures=entries, hard=hard)
            blocks["render"] = round((_time.perf_counter() - _t1) * 1000)
    _t2 = _time.perf_counter()
    report = attach_fact_check(report, state)
    blocks["fact_check"] = round((_time.perf_counter() - _t2) * 1000)
    report = _attach_writes(report, state)
    emit_event("node_exit", "merge.blocks", "ok",
               {"path": path, "ms": blocks, "n_claims": len(claims),
                "total_ms": round((_time.perf_counter() - _t0) * 1000)},
               thread_id=str(state.get("thread_id") or ""),
               session_id=str(state.get("session_id") or ""),
               duration_ms=round((_time.perf_counter() - _t0) * 1000))
    return {"final_report": report.model_dump(),
            "cost_ledger": [cost_ledger_entry("merge", "merge", 0, 0)]}
