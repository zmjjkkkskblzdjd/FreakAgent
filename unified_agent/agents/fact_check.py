from __future__ import annotations

import re

from unified_agent.core.contracts import FactCheck, UnifiedFinalReport
from unified_agent.core.llm import ask_llm_json
from unified_agent.core.state import RAGContext, SQLContext, UnifiedState
from unified_agent.prompts import load_prompt

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:%|pp|万|亿|千)?", re.I)
_UNIT_MULT = {"千": 1000, "万": 10000, "亿": 100000000}
_CITE = re.compile(r"\[\s*\d+\s*\]")
_HAS_NUM = re.compile(r"\d")
_SENT_END = re.compile(r"(?<=[。！？!?；;])")
_HEADING_LABEL = re.compile(r"[一二三四五六七八九十]+、[^。！？；]{0,30}")
_NUMBERED_HEADING = re.compile(r"\d+\.\s*[^。！？；]{0,30}")
_BRACKET_HEADING = re.compile(r"【[^】]{1,40}】")

_SOCIAL = re.compile(r"^(你好|您好|谢谢|感谢|很高兴|我是助手|不客气|抱歉|明白)")
_OPINION = re.compile(r"可能|大概|或许|预计|我认为|建议|值得|看起来|应该是|通常|往往")
_PROCEDURAL = re.compile(r"首先|其次|然后|最后|第一步|步骤|点击|打开|进入|配置|设置|操作方式")
_META = re.compile(r"知识库|未联网|未查库|覆盖限制|照录原文|以上清单|以下结论|本回答|口径")

_GRAY_MAX = int(__import__("os").getenv("UA_FACT_CHECK_GRAY_MAX", "10"))
_OVERLAP_MIN = float(__import__("os").getenv("UA_FACT_CHECK_OVERLAP", "0.30"))
_MAX_TOKENS = 1200


_SYSTEM_BLOCK = re.compile(r"车道失败备忘|已被安全策略拒绝|如需撤回|（系统追加）")

_REF_LINE = re.compile(r"^[\[【]\s*\d+\s*[\]】]\s*(?:sub_)?[rg]\d{3,}\s*$", re.I)

_REF_HEADING = re.compile(r"^\s*(?:\*\*)?\s*参考来源\s*(?:\*\*)?\s*[:：]?\s*$", re.M)

_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
             "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}
_CN_NUM = re.compile(r"[零〇一二两三四五六七八九十百千万亿]+")
_CN_PERCENT = re.compile(r"百分之([零〇一二两三四五六七八九十百千]+)")


def _cn_to_int(text: str) -> int:
    total = section = number = 0
    for ch in text:
        if ch in _CN_DIGIT:
            number = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            section += (number or 1) * _CN_UNIT[ch]
            number = 0
        elif ch == "万":
            total += (section + number) * 10000
            section = number = 0
        elif ch == "亿":
            total = (total + section + number) * 100000000
            section = number = 0
    return total + section + number


def _cn_num_tokens(text: str) -> set[str]:
    out: set[str] = set()
    for m in _CN_NUM.finditer(text):
        s = m.group()
        if len(s) < 2:          
            continue
        val = _cn_to_int(s)
        if val <= 0:
            continue
        out.add(str(val))
        if "万" in s and val % 10000 == 0:
            out.add(str(val // 10000))
        if "亿" in s and val % 100000000 == 0:
            out.add(str(val // 100000000))
    for m in _CN_PERCENT.finditer(text):
        val = _cn_to_int(m.group(1))
        if val > 0:
            out.add(f"{val}%")
    return out


def split_sentences(answer_md: str) -> list[str]:
    out: list[str] = []
    in_code = False
    text = answer_md or ""
    m = _REF_HEADING.search(text)
    if m:
        text = text[:m.start()]
    for block in re.split(r"\n\s*\n", text):
        if _SYSTEM_BLOCK.search(block[:60]):
            continue
        for raw in block.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("```"):
                in_code = not in_code
                continue
            if in_code or line.startswith(("#", "|", ">")):
                continue
            line = re.sub(r"^[-*]\s+|^\d+\.\s+", "", line)
            if _REF_LINE.fullmatch(line):
                continue
            for piece in _SENT_END.split(line):
                piece = piece.strip()
                if len(piece) < 4:
                    continue
                probe = piece.strip("*").strip()
                if (_HEADING_LABEL.fullmatch(probe) or _NUMBERED_HEADING.fullmatch(probe)
                        or _BRACKET_HEADING.fullmatch(probe)
                        or (probe.endswith("：") and len(probe) <= 40)):
                    continue
                out.append(piece)
    return out


def classify_sentence(sentence: str) -> str:
    if _SOCIAL.search(sentence):
        return "social"
    if _HAS_NUM.search(sentence):
        return "factual"
    if _META.search(sentence):
        return "procedural"
    if _PROCEDURAL.search(sentence):
        return "procedural"
    if _OPINION.search(sentence):
        return "opinion"
    return "factual"


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _num_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in _NUM.findall(text or ""):
        t = raw.replace(",", "").replace(" ", "").lower()
        token = t.rstrip("千万元亿")
        unit = t[len(token):][:1]
        if unit:              
            tokens.add(str(int(float(token) * _UNIT_MULT[unit])))
        else:
            tokens.add(token)
    return tokens | _cn_num_tokens(text or "")


def evidence_pool(state: UnifiedState) -> list[dict]:
    pool: list[dict] = []
    rag = state.get("rag_ctx") or RAGContext()
    for item in rag.provenance:
        text = str(item.get("text") or "")
        if text:
            pool.append({"id": str(item.get("chunk_id") or ""), "text": text})
    sql = state.get("sql_ctx") or SQLContext()
    for i, row in enumerate((sql.query_result or {}).get("rows", [])):
        pool.append({"id": f"sql-row-{i}", "text": "\t".join(str(c) for c in row)})
    if getattr(sql, "analysis_md", ""):
        pool.append({"id": "sql-analysis", "text": sql.analysis_md})
    return pool


def _overlap(sentence: str, text: str, n: int = 3) -> float:
    s, t = _norm(sentence), _norm(text)
    if len(s) < n:
        return 0.0
    grams = {s[i:i + n] for i in range(len(s) - n + 1)}
    grams_t = {t[i:i + n] for i in range(len(t) - n + 1)}
    return len(grams & grams_t) / len(grams)


def _top_evidences(sentence: str, pool: list[dict], k: int = 3) -> list[dict]:
    scored = sorted(pool, key=lambda e: _overlap(sentence, e["text"]), reverse=True)
    return [e for e in scored[:k] if _overlap(sentence, e["text"]) > 0]


def _deterministic_check(sentence: str, pool: list[dict]) -> tuple[str, dict | None]:
    clean = _CITE.sub("", sentence)
    nums = _num_tokens(clean)
    if nums:
        pool_nums = set().union(*[_num_tokens(e["text"]) for e in pool]) if pool else set()
        missing = nums - pool_nums
        if missing:
            return "unsupported", {"sentence": sentence, "verdict": "unsupported",
                                   "reason": f"数字无逐字出处：{', '.join(sorted(missing))}"}
        return "supported", None
    best = max((_overlap(clean, e["text"]) for e in pool), default=0.0)
    if best >= _OVERLAP_MIN:
        return "supported", None
    return "gray", {"sentence": sentence, "verdict": "gray"}


def _verify_gray(gray: list[tuple[str, dict]], pool: list[dict]) -> dict[str, dict]:
    if not gray or not pool:
        return {}
    system, user_tpl = load_prompt("fact_check")
    blocks = []
    for idx, (sentence, _issue) in enumerate(gray):
        ev = "\n".join(f"- [{e['id']}] {e['text'][:400]}" for e in _top_evidences(sentence, pool))
        blocks.append(f"【句子 {idx}】{sentence}\n{ev or '（无候选证据）'}")
    user = user_tpl.format(sentences="\n\n".join(blocks))
    try:
        d = ask_llm_json(system, user, max_tokens=_MAX_TOKENS, thinking=False)
    except Exception as exc:  # noqa: BLE001 — 核查是加值项，失败不能拖垮主流程
        print(f"[fact_check] 5b 失败：{type(exc).__name__}: {exc}", file=__import__("sys").stderr)
        return {}

    results: dict[str, dict] = {}
    for item in (d or {}).get("verdicts", []):
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(gray):
            continue
        sentence = gray[idx][0]
        verdict = str(item.get("verdict") or "unknown").lower()
        if verdict not in ("entailed", "contradicted", "unknown"):
            verdict = "unknown"
        quote = str(item.get("quote") or "")
        if verdict == "entailed":
            q = _norm(quote)
            hit = next((e for e in pool if q and q in _norm(e["text"])), None)
            if hit is None:
                verdict, quote = "unknown", ""
            else:
                results[sentence] = {"sentence": sentence, "verdict": verdict,
                                     "evidence_id": hit["id"], "quote": quote}
                continue
        results[sentence] = {"sentence": sentence, "verdict": verdict,
                             "evidence_id": str(item.get("evidence_id") or ""),
                             "quote": quote}
    return results


def attach_fact_check(report: UnifiedFinalReport, state: UnifiedState) -> UnifiedFinalReport:
    if report.mode not in ("rag", "sql", "hybrid") or report.refusal_reason:
        return report
    pool = evidence_pool(state)
    if not pool:
        return report

    sentences = split_sentences(report.answer_md)
    if not sentences:
        return report

    counts = {"sentences": len(sentences), "factual": 0, "supported": 0,
              "unsupported": 0, "gray": 0, "verified": 0}
    issues: list[dict] = []
    gray: list[tuple[str, dict]] = []
    for sentence in sentences:
        if classify_sentence(sentence) != "factual":
            continue
        counts["factual"] += 1
        status, issue = _deterministic_check(sentence, pool)
        if status == "supported":
            counts["supported"] += 1
        elif status == "unsupported":
            counts["unsupported"] += 1
            issues.append(issue)
        else:
            counts["gray"] += 1
            gray.append((sentence, issue))

    gray = gray[:_GRAY_MAX]
    verdicts = _verify_gray(gray, pool)
    for sentence, _issue in gray:
        v = verdicts.get(sentence)
        if v and v["verdict"] == "entailed":
            counts["verified"] += 1
        elif v and v["verdict"] in ("contradicted", "unknown"):
            counts["unsupported"] += 1
            issues.append({**v, "source": "ck5b"})

    coverage = counts["factual"] / counts["sentences"]
    summary = FactCheck(**counts, coverage=round(coverage, 3), issues=issues)
    return report.model_copy(update={"fact_check": summary})
