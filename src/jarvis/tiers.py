"""L0/L1/L2 tier generation.

Every node carries a ~100-token abstract and a ~2000-token overview alongside
its full body. Retrieval reads many abstracts and few bodies; that asymmetry is
the mechanism behind the token savings, so tier quality matters more than any
other single thing in this system.
"""

from __future__ import annotations

import re

from .llm import LLM
from .tokens import estimate_tokens, truncate_to_tokens

L0_TOKENS = 100
L1_TOKENS = 2000

_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n{2,}")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")

_SYSTEM = (
    "당신은 컨텍스트 데이터베이스의 요약기입니다. "
    "주어진 문서에서 사실만 뽑아 간결하게 요약합니다. 추측이나 미사여구를 넣지 않습니다."
)


def summarize(
    text: str,
    title: str = "",
    llm: LLM | None = None,
    l0_tokens: int = L0_TOKENS,
    l1_tokens: int = L1_TOKENS,
) -> tuple[str, str]:
    """Return ``(abstract, overview)`` for ``text``."""
    text = (text or "").strip()
    if not text:
        return (title or "").strip(), ""

    # Short documents are their own overview; summarising them wastes tokens.
    if estimate_tokens(text) <= l0_tokens:
        return text, text

    if llm is not None and llm.available:
        got = _llm_summarize(text, title, llm, l0_tokens, l1_tokens)
        if got is not None:
            return got

    return (
        _extractive_abstract(text, title, l0_tokens),
        _extractive_overview(text, l1_tokens),
    )


def _llm_summarize(
    text: str, title: str, llm: LLM, l0_tokens: int, l1_tokens: int
) -> tuple[str, str] | None:
    source = truncate_to_tokens(text, 12000)
    prompt = (
        f"# 문서 제목\n{title or '(없음)'}\n\n"
        f"# 문서\n{source}\n\n"
        "# 작업\n"
        f"1) abstract: 이 문서가 무엇이고 언제 쓸모 있는지 한두 문장, {l0_tokens} 토큰 이내.\n"
        f"2) overview: 핵심 정보와 사용 시나리오를 담은 구조적 요약, {l1_tokens} 토큰 이내. "
        "마크다운 불릿 사용 가능.\n\n"
        'JSON만 출력: {"abstract": "...", "overview": "..."}'
    )
    data, res = llm.complete_json(prompt, _SYSTEM, max_tokens=min(4096, l1_tokens + 400))
    if not res.ok or not isinstance(data, dict):
        return None
    abstract = str(data.get("abstract") or "").strip()
    overview = str(data.get("overview") or "").strip()
    if not abstract:
        return None
    return (
        truncate_to_tokens(abstract, l0_tokens),
        truncate_to_tokens(overview or abstract, l1_tokens),
    )


def _first_sentences(text: str, limit_tokens: int) -> str:
    body = re.sub(r"^---.*?---\s*", "", text, flags=re.S)
    body = re.sub(r"```.*?```", " ", body, flags=re.S)
    lines = [ln.strip() for ln in body.splitlines()]
    prose = [ln for ln in lines if ln and not ln.startswith("#")]
    joined = " ".join(prose)
    out: list[str] = []
    used = 0
    for sent in _SENT_SPLIT.split(joined):
        sent = sent.strip()
        if not sent:
            continue
        cost = estimate_tokens(sent) + 1
        if out and used + cost > limit_tokens:
            break
        out.append(sent)
        used += cost
    return truncate_to_tokens(" ".join(out) or joined, limit_tokens)


def _extractive_abstract(text: str, title: str, limit_tokens: int) -> str:
    lead = _first_sentences(text, limit_tokens if not title else limit_tokens - 8)
    if title and title.lower() not in lead.lower():
        return truncate_to_tokens(f"{title}: {lead}", limit_tokens)
    return lead


def _extractive_overview(text: str, limit_tokens: int) -> str:
    """Keep the document's skeleton: headings, bullets, and each paragraph's lead."""
    kept: list[str] = []
    used = 0
    in_fence = False
    para: list[str] = []

    def flush_para() -> None:
        nonlocal used, para
        if not para:
            return
        first = _SENT_SPLIT.split(" ".join(para))[0].strip()
        para = []
        if not first:
            return
        cost = estimate_tokens(first) + 1
        if used + cost <= limit_tokens:
            kept.append(first)
            used += cost

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line.strip():
            flush_para()
            continue
        h = _HEADING.match(line)
        b = _BULLET.match(line)
        if h or b:
            flush_para()
            piece = line.strip()
            cost = estimate_tokens(piece) + 1
            if used + cost > limit_tokens:
                break
            kept.append(piece)
            used += cost
            continue
        para.append(line.strip())
    flush_para()
    return truncate_to_tokens("\n".join(kept), limit_tokens)
