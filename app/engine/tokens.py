"""경량 한국어/영어 토큰화 — 검색 점수와 키워드 추출용.

외부 형태소 분석기 없이 동작합니다:
- 영어/숫자 단어는 소문자로
- 한글 어절은 조사(은/는/이/가/을/를/…)를 벗겨 뿌리만 남김
- 한자/일본어 등 CJK 는 글자 단위 + 바이그램
"""
from __future__ import annotations

import re

_PARTICLE = re.compile(
    r"(?i)(은|는|이|가|을|를|의|에|에서|으로|로|와|과|도|만|까지|부터|보다|"
    r"에게|한테|처럼|같이|이나|이나|든|조차|마저|커녕|이라도|야|아|여|이고|"
    r"이며|하고|랑|이랑|에선|에서의|로써|로부터|들|이라고|라고)\b"
)
_WORD = re.compile(r"[a-z0-9][a-z0-9_.\-/#+]*")
_HANGUL_WORD = re.compile(r"[가-힣]{2,}")
_CJK_CHAR = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]")

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is",
    "are", "was", "were", "be", "been", "how", "what", "why", "when", "where",
    "which", "this", "that", "it", "its", "not", "do", "does", "did",
    "것", "수", "등", "및", "대한", "위해", "통해", "있", "하", "되", "그", "이", "저",
    "하는", "한다", "에서", "으로", "하게", "어떻게", "왜", "뭐", "무엇", "같은",
    "해주", "주", "좀", "제발", "부탁", "것처럼",
}


def _strip_particle(word: str) -> str:
    return _PARTICLE.sub("", word) or word


def tokenize(text: str) -> list[str]:
    """텍스트 → 검색용 토큰 리스트 (중복 포함)."""
    if not text:
        return []
    low = text.lower()
    tokens: list[str] = []

    for m in _WORD.finditer(low):
        tok = m.group(0)
        if tok not in STOPWORDS and len(tok) >= 2:
            tokens.append(tok)

    for m in _HANGUL_WORD.finditer(low):
        word = _strip_particle(m.group(0))
        if word not in STOPWORDS and len(word) >= 2:
            tokens.append(word)

    # CJK 글자 + 바이그램
    chars = _CJK_CHAR.findall(low)
    if 1 <= len(chars) <= 2:
        pass
    for i in range(len(chars) - 1):
        tokens.append(chars[i] + chars[i + 1])
    # 3자 이상 한글 어절은 바이그램도 추가 (조사 가림에 강하게)
    for word in _HANGUL_WORD.findall(low):
        if len(word) >= 3:
            for i in range(len(word) - 1):
                tokens.append(word[i : i + 2])
    return tokens


def keywords(text: str, limit: int = 12) -> list[str]:
    """빈도 기반 키워드 추출 (동점이면 앞쪽 우선)."""
    counter: dict[str, int] = {}
    for tok in tokenize(text):
        counter[tok] = counter.get(tok, 0) + 1
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], -text.lower().find(kv[0])))
    return [k for k, _ in ranked[:limit]]