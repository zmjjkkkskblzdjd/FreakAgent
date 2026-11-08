from __future__ import annotations

import sys
from dataclasses import dataclass

from config import TokenBudgetConfig



_tokenizer = None
_tokenizer_warned = False


def _get_tokenizer():
    global _tokenizer, _tokenizer_warned
    if _tokenizer is not None:
        return _tokenizer
    try:
        import tiktoken

        _tokenizer = tiktoken.get_encoding("cl100k_base")
    except Exception as e:
        if not _tokenizer_warned:
            print(f"[budget] tiktoken 加载失败，回退 len/3 粗估：{e}", file=sys.stderr)
            _tokenizer_warned = True
        _tokenizer = None
    return _tokenizer



_PER_MSG_OVERHEAD = 4


def count_tokens(text: str) -> int:
    if not text:
        return 0
    tok = _get_tokenizer()
    if tok is not None:
        return len(tok.encode(text, disallowed_special=()))
    return len(text) // 3


def count_message_tokens(msg: dict) -> int:
    content = msg.get("content")
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            str(c.get("text", "")) if isinstance(c, dict) else str(c)
            for c in content
        )
    else:
        text = str(content)
    return count_tokens(text) + _PER_MSG_OVERHEAD


def count_messages_tokens(messages: list[dict]) -> int:
    return sum(count_message_tokens(m) for m in messages)




@dataclass
class TokenUsage:

    input_tokens: int
    window_tokens: int
    utilization: float
    status: str
    recommendation: str


def measure(messages: list[dict], budget: TokenBudgetConfig) -> TokenUsage:
    input_tokens = count_messages_tokens(messages)
    window = budget.window_tokens
    util = input_tokens / window if window > 0 else 0.0
    if util < budget.cleanup_ratio:
        status, rec = "healthy", "no_action"
    elif util < budget.compress_ratio:
        status, rec = "warning", "cleanup"
    else:
        status, rec = "critical", "compress"
    return TokenUsage(
        input_tokens=input_tokens,
        window_tokens=window,
        utilization=util,
        status=status,
        recommendation=rec,
    )
