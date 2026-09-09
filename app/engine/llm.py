"""선택 사항: LLM / 임베딩 호출 (OpenAI 호환 API).

없어도 전체가 동작합니다 — 요약은 추출식, 검색은 키워드 기반으로 내려갑니다.
설정돼 있으면 (docker-compose.yml 의 VIKING_LLM_* 또는 관리자 → 모델 설정 화면) 더
좋은 요약과 의미 검색을 제공합니다. 모든 호출은 실패 시 None 반환 (조용한 폴백).
"""
from __future__ import annotations

import json

import httpx

from ..config import config

_TIMEOUT = 15.0


def _clean(base: str) -> str:
    """# 와 마지막 / 를 걷어낸 주소."""
    return (base or "").strip().rstrip("#").rstrip("/")


def chat_url(base: str) -> str:
    """채팅 완성 엔드포인트 URL.

    주소가 이미 완전한 경로(/invocations, /chat/completions 로 끝나면 — Databricks
    Serving 등 — 그대로 쓰고, 아니면 /chat/completions 를 붙입니다.
    """
    b = _clean(base)
    if b.endswith("/invocations") or b.endswith("/chat/completions"):
        return b
    return f"{b}/chat/completions"


def embed_url(base: str) -> str:
    """임베딩 엔드포인트 URL — 완전한 경로면 그대로, 아니면 /embeddings 를 붙임."""
    b = _clean(base)
    if b.endswith("/invocations") or b.endswith("/embeddings"):
        return b
    return f"{b}/embeddings"


def _enabled() -> bool:
    return bool(config.llm_api_key or config.llm_base_url != "https://api.openai.com/v1")


def summarize(title: str, content: str, limit: int = 60) -> str | None:
    """L0 한 줄 요약. 실패하면 None → 추출식 요약으로 폴백."""
    if not _enabled():
        return None
    try:
        r = httpx.post(
            chat_url(config.llm_base_url),
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
            embed_url(config.embed_base_url),
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


# ── 관리자 화면용: 연결 테스트 / 전체 재색인 ───────────────────────────── #
def test_llm(base_url: str | None = None, api_key: str | None = None,
             model: str | None = None) -> tuple[bool, str]:
    """LLM 연결 테스트 — (성공, 실패 사유). 인자를 비우면 현재 설정을 사용."""
    base = (base_url or config.llm_base_url or "").rstrip("/")
    key = config.llm_api_key if api_key is None else api_key
    model = model or config.llm_model
    if not base:
        return False, "Base URL 이 설정되어 있지 않습니다"
    if not key and "localhost" not in base:
        return False, "API 키가 설정되어 있지 않습니다"
    try:
        r = httpx.post(
            chat_url(base),
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 4},
            timeout=10,
        )
        r.raise_for_status()
        return True, ""
    except Exception as e:  # 네트워크·인증·모델명 오류 등을 그대로 보여줌
        return False, str(e)[:200]


def test_embed(base_url: str | None = None, api_key: str | None = None,
               model: str | None = None) -> tuple[bool, str]:
    """임베딩 연결 테스트 — (성공, 실패 사유). 인자를 비우면 현재 설정을 사용."""
    base = (base_url or config.embed_base_url or "").rstrip("/")
    key = config.embed_api_key if api_key is None else api_key
    model = model or config.embed_model
    if not base:
        return False, "Base URL 이 설정되어 있지 않습니다"
    if not key and "localhost" not in base:
        return False, "API 키가 설정되어 있지 않습니다"
    try:
        r = httpx.post(
            embed_url(base),
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "input": "ping"},
            timeout=10,
        )
        r.raise_for_status()
        return True, ""
    except Exception as e:
        return False, str(e)[:200]


def reindex_all() -> int:
    """전체 지식의 임베딩을 재계산. 임베딩 미설정이면 0. (임베딩 설정 변경 후 호출)"""
    from .. import db

    if not config.embed_api_key and "localhost" not in config.embed_base_url:
        return 0
    rows = db.rows("SELECT id, title, content FROM memories WHERE content IS NOT NULL AND content != ''")
    n = 0
    for r in rows:
        vec = embed(f"{r['title']} {r['content'][:4000]}")
        if vec:
            db.execute("UPDATE memories SET embedding=? WHERE id=?", (json.dumps(vec), r["id"]))
            n += 1
    return n