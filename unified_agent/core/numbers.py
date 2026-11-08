from __future__ import annotations
import re
from decimal import Decimal, InvalidOperation

_NON_DATA_SPANS = [
    re.compile(r"```[\s\S]*?```"),                         
    re.compile(r"`[^`]+`"),                                 
    re.compile(r"第\s*[一二三四五六七八九十百\d]+\s*[名位个次项条章节款步轮点篇]"),
    re.compile(r"[一二三四五六七八九十\d]+\s*[个条项次名位家只件篇张]"),
    re.compile(r"(?:19|20)\d{2}\s*年"),                        
    re.compile(r"(?<!\d)\d{1,2}\s*[月日号](?!\d)"),             
    re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"),                 
    re.compile(r"\[\d+\]"),                                    
    re.compile(r"(?m)^[ \t>]*\d+\.(?=\s)"),                     
    re.compile(r"(?m)^[ \t]*[（(]\d+[）)]"),                    
    re.compile(r"(?m)^[ \t]*>[ \t]*(?:SELECT|WITH)\b.*$"),      
    re.compile(r"(?m)^[ \t]*(?:SELECT|WITH)\b.*$"),             
    re.compile(r"report_\d{6}-\d{4}"),                        
    re.compile(r"[a-f0-9]{32}"),                               
]

_NUM_RE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?%?")
_UNIT_AFTER = re.compile(
    r"^\s*(?:元|万元|亿元|美元|港元|人民币|台|套|件|吨|笔|单|行|家|人|％|‰|%)")


def normalize(tok: str) -> str:
    return tok.replace(",", "").rstrip("%")


def _mask_non_data(text: str) -> str:
    masked = text
    for pat in _NON_DATA_SPANS:
        masked = pat.sub(lambda m: " " * len(m.group(0)), masked)
    return masked


def extract_numbers(text: str) -> set[str]:
    masked = _mask_non_data(text)
    out: set[str] = set()
    for m in _NUM_RE.finditer(masked):
        tok = m.group(0)
        if "," in tok or "." in tok or tok.endswith("%"):
            out.add(normalize(tok))
        elif len(tok.lstrip("-")) >= 4:
            out.add(normalize(tok))
        elif _UNIT_AFTER.match(masked[m.end():]):
            out.add(normalize(tok))
    return out

def as_decimal(tok: str) -> Decimal | None:
    try:
        return Decimal(tok)
    except (InvalidOperation, ValueError):
        return None


_UNIT_SCALE = {"亿": Decimal("1e8"), "万": Decimal("1e4"), "千": Decimal("1e3")}
_NUM_UNIT_RE = re.compile(r"(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|千元|亿|万|千)")


def unit_scaled_values(text: str) -> dict[str, set[Decimal]]:
    masked = _mask_non_data(text)
    out: dict[str, set[Decimal]] = {}
    for m in _NUM_UNIT_RE.finditer(masked):
        d = as_decimal(normalize(m.group(1)))
        if d is None:
            continue
        out.setdefault(normalize(m.group(1)), set()).add(d * _UNIT_SCALE[m.group(2)[0]])
    return out


def approx_equal(a: Decimal, b: Decimal, rel: float = 0.01) -> bool:
    if b == 0:
        return a == 0
    return abs(a - b) <= abs(b) * Decimal(str(rel))


def resolve_origin(value: str, ledger: list[dict]) -> str | None:
    hits = [e for e in ledger if normalize(e.get("value", "")) == normalize(value)]
    if not hits:
        return None
    return hits[0]["origin"]