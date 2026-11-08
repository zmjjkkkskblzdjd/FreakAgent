from __future__ import annotations

import re

from unified_agent.core.llm import ask_llm_json
from unified_agent.prompts import load_prompt

_SYSTEM, _USER_TPL = load_prompt("lane_question")

_YEAR = re.compile(r"(?:19|20)\d{2}\s*年?")
_QUARTER = re.compile(r"第[一二三四1-4]季度|Q[1-4]|[一二三四1-4]季度")
_CODE = re.compile(r"\d{6}")
_PRONOUN = re.compile(r"它|该公司|这家|那家|上述|前述|该|其")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_MAX_LEN = 300


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:_MAX_LEN].strip()


def _tokens(text: str) -> set[str]:
    t = str(text or "")
    out = {m.group(0).replace(" ", "") for m in _YEAR.finditer(t)}
    out |= {m.group(0) for m in _QUARTER.finditer(t)}
    out |= {m.group(0) for m in _CODE.finditer(t)}
    out |= {m.group(0) for m in _NUMBER.finditer(t)}
    return out


def _missing_context(question: str, context: str) -> list[str]:
    found = []
    for pat in (_YEAR, _QUARTER, _CODE):
        for m in pat.finditer(context or ""):
            token = m.group(0).replace(" ", "").strip()
            if token and token not in question and token not in found:
                found.append(token)
    return found


def _ask_llm(question: str, context: str, extra: str) -> str:
    user = _USER_TPL.format(context=context or "（无）", question=question,
                            extra=extra or "（无）")
    try:
        d = ask_llm_json(_SYSTEM, user)
    except Exception:
        return ""
    return _clean((d or {}).get("question") or "")


def validate(candidate: str, allowed_texts: list[str]) -> bool:
    allowed: set[str] = set()
    for text in allowed_texts:
        allowed |= _tokens(text)
    return _tokens(candidate) <= allowed


def normalize(question: str, context: str, extra: str = "", *,
              use_llm: bool = True) -> tuple[str, str]:
    q = _clean(question)
    if not q:
        return "", "none"
    source = "none"
    missing = _missing_context(q, context)
    if missing:
        q = "".join(missing) + q
        source = "rule"

    residual = _PRONOUN.search(q)
    if residual and use_llm:
        llm_q = _ask_llm(q, context, extra)
        if llm_q and validate(llm_q, [context, extra, q]):
            if llm_q != q:
                q, source = llm_q, "llm"
    return _clean(q), source
