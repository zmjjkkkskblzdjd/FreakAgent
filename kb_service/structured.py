
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class StructuredChunk:
    text: str
    start: int  
    end: int    



_BREAK_PATTERNS: list[tuple[re.Pattern, int]] = [
    (re.compile(r'\n#{1}(?!#)'), 100),   
    (re.compile(r'\n#{2}(?!#)'), 90),    
    (re.compile(r'\n#{3}(?!#)'), 80),    
    (re.compile(r'\n#{4}(?!#)'), 70),    
    (re.compile(r'\n#{5}(?!#)'), 60),    
    (re.compile(r'\n#{6}(?!#)'), 50),    
    (re.compile(r'\n```'), 80),          
    (re.compile(r'\n(?:---|\*\*\*|___)\s*\n'), 60),  
    (re.compile(r'\n\n+'), 20),          
    (re.compile(r'\n[-*]\s'), 5),         
    (re.compile(r'\n\d+\.\s'), 5),        
    (re.compile(r'\n'), 1),               
]

_WINDOW_RATIO = 0.22
_DECAY_FACTOR = 0.7

_STRUCTURED_SEPARATOR_SCORE = 30

_DELIMITER_SEPARATOR_SCORE = 100

_DELIMITER_FALLBACKS: list[tuple[str, int]] = [
    ('\n\n', 20),
    ('\n', 12),
    ('。', 10),
    ('. ', 8),
    (' ', 3),
]


@dataclass
class _BreakPoint:
    pos: int
    score: int


@dataclass
class _CodeFenceRegion:
    start: int
    end: int



def _target_to_chars(text: str, target_size: int) -> int:
    return max(1, target_size)



def _scan_break_points(text: str, separator: str, use_structure: bool) -> list[_BreakPoint]:
    best: dict[int, int] = {}

    def consider(pos: int, score: int) -> None:
        existing = best.get(pos)
        if existing is None or score > existing:
            best[pos] = score

    if use_structure:
        for pattern, score in _BREAK_PATTERNS:
            for m in pattern.finditer(text):
                consider(m.start(), score)
        if separator:
            _add_literal_breaks(text, separator, _STRUCTURED_SEPARATOR_SCORE, consider)
    else:
        if separator:
            _add_literal_breaks(text, separator, _DELIMITER_SEPARATOR_SCORE, consider)
        for sep, score in _DELIMITER_FALLBACKS:
            _add_literal_breaks(text, sep, score, consider)

    return sorted(
        (_BreakPoint(pos=p, score=s) for p, s in best.items()),
        key=lambda bp: bp.pos,
    )


def _add_literal_breaks(text: str, separator: str, score: int, consider) -> None:
    if not separator:
        return
    idx = text.find(separator)
    while idx != -1:
        consider(idx + len(separator), score)
        idx = text.find(separator, idx + len(separator))


def _find_code_fences(text: str) -> list[_CodeFenceRegion]:
    regions: list[_CodeFenceRegion] = []
    open_pos: int | None = None
    for m in re.finditer(r'\n```', text):
        if open_pos is None:
            open_pos = m.start()
        else:
            regions.append(_CodeFenceRegion(start=open_pos, end=m.end()))
            open_pos = None
    if open_pos is not None:
        regions.append(_CodeFenceRegion(start=open_pos, end=len(text)))
    return regions


def _is_inside_fence(pos: int, fences: list[_CodeFenceRegion]) -> bool:
    return any(f.start < pos < f.end for f in fences)


def _find_best_cutoff(
    break_points: list[_BreakPoint],
    target: int,
    window_chars: int,
    fences: list[_CodeFenceRegion],
) -> int:
    window_start = target - window_chars
    best_score = -1
    best_pos = target

    for bp in break_points:
        if bp.pos < window_start:
            continue
        if bp.pos > target:
            break  
        if _is_inside_fence(bp.pos, fences):
            continue

        normalized_distance = (target - bp.pos) / window_chars
        decayed = bp.score * (1 - normalized_distance * normalized_distance * _DECAY_FACTOR)
        if decayed > best_score:
            best_score = decayed
            best_pos = bp.pos

    return best_pos


def _trim_to_chunk(text: str, start: int, end: int) -> StructuredChunk | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start >= end:
        return None
    return StructuredChunk(text=text[start:end], start=start, end=end)



def split_structured(
    text: str,
    target_size: int = 1000,
    overlap: int = 50,
    separator: str = '\n\n',
) -> list[str]:
    if not text.strip():
        return []

    max_chars = max(1, _target_to_chars(text, target_size))
    overlap_chars = max(0, min(overlap, max_chars - 1))
    window_chars = max(1, round(max_chars * _WINDOW_RATIO))

    break_points = _scan_break_points(text, separator, use_structure=True)
    fences = _find_code_fences(text)

    chunks: list[StructuredChunk] = []
    cursor = 0

    while cursor < len(text):
        end_pos = min(cursor + max_chars, len(text))

        if end_pos < len(text):
            cutoff = _find_best_cutoff(break_points, end_pos, window_chars, fences)
            if cutoff > cursor and cutoff <= end_pos:
                end_pos = cutoff

        chunk = _trim_to_chunk(text, cursor, end_pos)
        if chunk is not None:
            chunks.append(chunk)

        if end_pos >= len(text):
            break
        next_cursor = end_pos - overlap_chars
        cursor = next_cursor if next_cursor > cursor else end_pos

    return [c.text for c in chunks]



def _test() -> None:
    md_text = """# 介绍
这是第一段，包含一些内容。

## 背景
第二段讲了背景知识，内容比较多，需要在合适的地方断。

## 总结
最后是总结。"""
    chunks = split_structured(md_text, target_size=60, overlap=0)
    print(f"测试1（Markdown 标题）：{len(chunks)} 块")
    for i, c in enumerate(chunks):
        print(f"  [{i}] {c[:40]}...")

    plain_text = "第一段内容。" * 5 + "\n\n" + "第二段内容。" * 5 + "\n\n" + "第三段内容。" * 5
    chunks2 = split_structured(plain_text, target_size=80, overlap=0)
    print(f"\n测试2（纯段落）：{len(chunks2)} 块")

    code_text = """前面介绍。

```python
def hello():
    print("hello world")
```

后面还有内容。"""
    chunks3 = split_structured(code_text, target_size=120, overlap=0)
    print(f"\n测试3（code fence 不切）：{len(chunks3)} 块")
    for i, c in enumerate(chunks3):
        print(f"  [{i}] {c[:60]}")
    any_fence_cut = any(
        c.strip().startswith('```') and c.strip().count('```') == 1
        for c in chunks3
    )
    assert not any_fence_cut, f"code fence 被切断了：{chunks3}"
    print("  ✓ code fence 未被切断")

    long_code = """介绍部分。

```python
""" + ('x = very_long_variable_name_that_takes_many_chars_to_declare\n' * 15) + """```

结语。"""
    chunks4 = split_structured(long_code, target_size=80, overlap=0)
    print(f"\n测试4（长 code block，允许硬切）：{len(chunks4)} 块")

    assert split_structured("") == [], "空文本应返回空列表"
    print("\n测试5（空文本）：✓")

    print("\n全部测试通过！")


if __name__ == "__main__":
    _test()
