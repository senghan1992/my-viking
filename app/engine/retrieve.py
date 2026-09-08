"""검색·패킹 — 질문에 관련된 지식을 예산 안에서 골라 티어별로 팩킹합니다.

OpenViking 의 '디렉터리 재귀 검색'을 도서관에 맞게 단순화:
1) 모든 후보를 L0(요약)로 훑고 점수순 정렬
2) 상위만 요청한 깊이(L1 개요 / L2 전문)까지 펼침
3) contested(검증 필요) 지식은 일반 결과에서 빠지고 '경고'로 따로 전달

점수 = 키워드 겹침(어휘) + CJK 바이그램 보너스 + (있으면) 임베딩 코사인.
"""
from __future__ import annotations

import math

from .. import db
from . import llm
from .tiers import tier_text
from .tokens import tokenize

# 카테고리 이름이 질문에 그대로 나오면 가산점 (도서관 '섹션' 느낌)
_CATEGORY_BONUS = {"commands": 2, "pitfalls": 2, "decisions": 1, "knowledge": 0}

# 검색 결과에서 제외할 최소 상대 점수 (최고 점수의 40% 미만은 버림)
_RELATIVE_FLOOR = 0.40


def _score_memory(mem: dict, q_tokens: list[str], q_ngrams: set[str], q_vec=None) -> float:
    """하나의 메모리가 질문과 얼마나 관련 있는지."""
    text = f"{mem.get('title','')} {mem.get('summary','')} {mem.get('keywords','')}"
    m_tokens = tokenize(text)
    m_ngrams = {t for t in m_tokens if len(t) == 2}

    overlap = len(set(q_tokens) & set(m_tokens))
    ngram_bonus = len(q_ngrams & m_ngrams) * 0.5
    if overlap == 0:
        return 0.0

    # 길이 보정: 짧은 지식이 우연히 겹치기 쉬움 → 로그 페널티
    length_penalty = 1.0 / (1.0 + math.log10(max(1, len(m_tokens))) * 0.3)

    cat = mem.get("category", "knowledge")
    category_bonus = _CATEGORY_BONUS.get(cat, 0)

    base = overlap * length_penalty + ngram_bonus + category_bonus

    # 의미 검색 가산 (임베딩이 둘 다 있을 때만)
    if q_vec:
        vec = llm.load_embedding(mem) or None
        if vec:
            base += llm.cosine(vec, q_vec) * 0.8
    return round(base, 3)


def search(
    project_id: int,
    query: str,
    max_items: int = 6,
    max_tier: int = 1,
    include_contested: bool = False,
) -> dict:
    """검색 실행 → {items: [...], warnings: [...], query_tokens}"""
    q_tokens = tokenize(query)
    q_ngrams = {t for t in q_tokens if len(t) == 2}
    q_vec = llm.embed_cached(" ".join(q_tokens))

    memories = db.rows(
        "SELECT * FROM memories WHERE project_id=? AND status!='superseded' ORDER BY updated_at DESC LIMIT 200",
        (project_id,),
    )

    scored: list[tuple[float, dict]] = []
    warnings: list[dict] = []
    for mem in memories:
        if mem["status"] == "contested":
            s = _score_memory(mem, q_tokens, q_ngrams, q_vec)
            if s > 0:
                warnings.append(_pack(mem, 0))
            continue
        s = _score_memory(mem, q_tokens, q_ngrams, q_vec)
        if s > 0:
            scored.append((s, mem))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored:
        return {"items": [], "warnings": warnings, "query_tokens": q_tokens[:8]}

    floor = scored[0][0] * _RELATIVE_FLOOR
    picked = [m for s, m in scored if s >= floor][:max_items]

    items = [_pack(m, max_tier) for m in picked]

    # 사용 시각 갱신 (도서관 출납 기록)
    for m in picked:
        db.execute(
            "UPDATE memories SET last_used_at=?, use_count=use_count+1 WHERE id=?",
            (db.now(), m["id"]),
        )

    return {"items": items, "warnings": warnings, "query_tokens": q_tokens[:8]}


def _pack(mem: dict, max_tier: int) -> dict:
    """검색 결과용으로 티어 깊이에 맞춰 잘라 포장."""
    tier = min(max_tier, 2)
    return {
        "id": mem["id"],
        "uri": f"viking://{mem['project_id']}/memories/{mem['category']}/{mem['id']}",
        "category": mem["category"],
        "title": mem["title"],
        "status": mem["status"],
        "tier": tier,
        "text": tier_text(tier, mem),
        "verified": mem["status"] == "established",
        "updated_at": mem["updated_at"],
    }


def inject_block(project_name: str, result: dict, trace_id: str = "") -> str:
    """검색 결과를 에이전트 프롬프트에 넣을 마크다운 블록으로."""
    items = result["items"]
    warnings = result["warnings"]
    if not items and not warnings:
        return ""

    lines = [f"## 📚 관련 지식 (도서관 · {project_name})"]
    if trace_id:
        lines.append(f"> trace `{trace_id}` — 결과가 어땠는지 score 로 알려주면 지식이 적응합니다.")
    lines.append("")
    for i, it in enumerate(items, 1):
        mark = "" if it["verified"] else " ⟨검증 전⟩"
        lines.append(f"**{i}. [{it['category']}] {it['title']}**{mark}")
        lines.append(it["text"].strip()[:600])
        lines.append("")
    if warnings:
        lines.append("⚠ **검증 필요** (아래는 최근 결과가 어긋난 지식 — 사실로 단정하지 말 것):")
        for it in warnings:
            lines.append(f"- [{it['category']}] {it['title']} — {it['text'].strip()[:120]}")
    return "\n".join(lines).strip()