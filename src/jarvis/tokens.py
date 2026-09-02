"""Token estimation that is honest about CJK.

We deliberately avoid a hard tiktoken dependency: MyViking must work offline
and on any Python. If tiktoken happens to be installed we use it, otherwise we
fall back to a script-aware heuristic. The heuristic matters because the naive
``len(text) / 4`` rule under-counts Korean/Chinese/Japanese by ~2.5x, which
would make every token-saving report a lie.
"""

from __future__ import annotations

import functools
import re

_CJK = re.compile(
    r"[ᄀ-ᇿ぀-ヿ㄰-㆏㐀-䶿"
    r"一-鿿ꥠ-꥿가-힯豈-﫿]"
)
_LATIN_WORD = re.compile(r"[A-Za-z0-9]+")


@functools.lru_cache(maxsize=1)
def _tiktoken_encoder():
    try:
        import tiktoken  # type: ignore
    except Exception:
        return None
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def estimate_tokens(text: str) -> int:
    """Return an estimated token count for ``text``."""
    if not text:
        return 0
    enc = _tiktoken_encoder()
    if enc is not None:
        return len(enc.encode(text))

    cjk = len(_CJK.findall(text))
    # Korean averages ~1.5 tokens per syllable block on cl100k-style BPE.
    total = int(cjk * 1.5)

    rest = _CJK.sub("", text)
    # Latin words: ~1 token per 4 characters, minimum 1 per word.
    for word in _LATIN_WORD.findall(rest):
        total += max(1, round(len(word) / 4))
    non_word = _LATIN_WORD.sub("", rest)
    # Punctuation and symbols are roughly one token each, whitespace is free.
    total += len([c for c in non_word if not c.isspace()])
    total += non_word.count("\n")
    return max(1, total)


def truncate_to_tokens(text: str, limit: int) -> str:
    """Trim ``text`` so that it fits in ``limit`` estimated tokens."""
    if limit <= 0:
        return ""
    if estimate_tokens(text) <= limit:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip()
