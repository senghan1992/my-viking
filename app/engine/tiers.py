"""L0/L1/L2 티어 생성 — 지식 하나를 세 층으로 가공해서 저장합니다.

OpenViking 의 핵심 아이디어: 모든 콘텐츠는 쓰는 순간
  L0 요약 한 줄 → 관련성 판단용 (싸다)
  L1 개요      → 후보로 좁힌 뒤 읽는 층
  L2 전문      → 정말 필요할 때만 펼치는 층
으로 나뉘고, 검색은 필요 깊이까지만 로드합니다. LLM 이 없어도 추출식으로
동작하며, 설정돼 있으면 LLM 요약으로 대체됩니다.
"""
from __future__ import annotations

import re

from . import llm
from .tokens import keywords as extract_keywords
from .tokens import tokenize

_MD_STRIP = re.compile(r"[#*`>_~\[\]()|-]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")


def clean(text: str) -> str:
    """마크다운 기호를 걷어내 요약용 평문으로."""
    return _MD_STRIP.sub(" ", text)


def make_summary(title: str, content: str, max_chars: int = 120) -> str:
    """L0 — 한 줄 요약. LLM 이 있으면 LLM, 없으면 첫 문장 추출."""
    plain = clean(content).strip()
    if not plain:
        return title[:max_chars]

    generated = llm.summarize(title, plain[:2000], limit=max_chars)
    if generated:
        return generated[:max_chars]

    for sentence in _SENTENCE_SPLIT.split(plain):
        sentence = sentence.strip().strip(".").strip()
        if len(sentence) >= 8:
            return sentence[:max_chars]
    return plain[:max_chars]


def make_overview(content: str, max_chars: int = 500) -> str:
    """L1 — 개요. 첫 부분을 잘라 평문으로."""
    plain = " ".join(clean(content).split())
    return plain[:max_chars]


def make_keywords(title: str, content: str) -> list[str]:
    """검색용 키워드 — 제목과 본문에서 빈도 기반으로 뽑습니다."""
    return extract_keywords(f"{title} {content}", limit=12)


def tier_text(tier: int, memory: dict) -> str:
    """tier 0=L0 요약, 1=L1 개요, 2=L2 전문."""
    if tier >= 2:
        return memory.get("content") or ""
    if tier == 1:
        return memory.get("overview") or memory.get("summary") or ""
    return memory.get("summary") or ""


def token_cost(text: str) -> int:
    """대략적 토큰 수 (한글 1자 ≈ 1토큰, 영어 4자 ≈ 1토큰)."""
    ko = len(re.findall(r"[가-힣]", text))
    en = len(re.findall(r"[A-Za-z0-9_]", text))
    return ko + en // 4 + 4