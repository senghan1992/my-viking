"""선택 사항: LLM / 임베딩 호출 (OpenAI 호환 API).

없어도 전체가 동작합니다 — 요약은 추출식, 검색은 키워드 기반으로 내려갑니다.
설정돼 있으면 (deploy 스택 environment 의 VIKING_LLM_*) 더 좋은 요약과 의미 검색을
제공합니다. 모든 호출은 실패 시 None 반환 (조용한 폴백).
"""
from __future__ import annotations

import json

import httpx

from ..config import config

_TIMEOUT = 15.0


def _enabled() -> bool:
    return bool(config.llm_api_key or config.llm_base_url != "https://api.openai.com/v1")


def summarize(title: str, content: str, limit: int = 60) -> str | None:
    """L0 한 줄 요약. 실패하면 None → 추출식 요약으로 폴백."""
    if not _enabled():
        return None
    try:
        r = httpx.post(
            f"{config.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {config.llm_api_key}"},
            json={
                "model": config.llm_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "지식 저장소용 한 줄 요약을 만들어라. 60자 이내, 한국어. 문장만 출력.",
                    },
                    {"role": "user", "content": f"제목: {title}\n내용: {content[:2000]}"},
                ],
                "temperature": 0.2,
                "max_tokens": 80,
            },
            timeout=_TIMEOUT,
        )
        data = r.json()
        return data["choices"][0]["message"]["content"].strip() or None
    except Exception:
        return None


def embed(text: str) -> list[float] | None:
    """텍스트 → 임베딩 벡터. 실패하면 None."""
    if not config.embed_api_key and "localhost" not in config.embed_base_url:
        return None
    try:
        r = httpx.post(
            f"{config.embed_base_url}/embeddings",
            headers={"Authorization": f"Bearer {config.embed_api_key}"},
            json={"model": config.embed_model, "input": text[:4000]},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        return data["data"][0]["embedding"]
    except Exception:
        return None


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


_EMBEDDING_CACHE: dict[str, list[float]] = {}


def embed_cached(text: str) -> list[float] | None:
    if text in _EMBEDDING_CACHE:
        return _EMBEDDING_CACHE[text]
    vec = embed(text)
    if vec:
        _EMBEDDING_CACHE[text] = vec
    return vec


def save_embeddings(memory_id: int, text: str) -> None:
    """메모리 저장 시 임베딩을 JSON 컬럼으로 기록 (없으면 아무것도 안 함)."""
    from .. import db

    if not config.embed_api_key and "localhost" not in config.embed_base_url:
        return
    vec = embed(text)
    if vec:
        db.execute(
            "UPDATE memories SET embedding=? WHERE id=?",
            (json.dumps(vec), memory_id),
        )


def load_embedding(row: dict) -> list[float] | None:
    try:
        return json.loads(row.get("embedding") or "[]") or None
    except (TypeError, ValueError):
        return None