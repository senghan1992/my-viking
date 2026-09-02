"""MyViking + Anthropic API 로 완성된 Jarvis 루프.

    export ANTHROPIC_API_KEY=...
    python examples/with_anthropic.py myapp "이 프로젝트 테스트 어떻게 돌려?"

이 스크립트가 보여주는 것은 세 줄입니다: prepare → 모델 호출 → commit.
캐시가 적중하면 모델을 아예 호출하지 않고, 적중하지 않으면 예산이 맞춰진
요청만 보냅니다. 실제 사용 토큰을 commit 에 넘겨 회계를 정확하게 유지합니다.
"""

from __future__ import annotations

import os
import sys

from jarvis import Jarvis


def call_claude(messages: list[dict[str, str]], model: str = "claude-sonnet-5"):
    import httpx

    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    user = [m for m in messages if m["role"] != "system"]
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={"model": model, "max_tokens": 2048, "system": system, "messages": user},
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()
    text = "".join(b.get("text", "") for b in data["content"] if b["type"] == "text")
    usage = data.get("usage", {})
    return text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    project, question = sys.argv[1], " ".join(sys.argv[2:])

    j = Jarvis()
    j.init_project(project, template="coding")

    prepared = j.prepare(project, question)

    if prepared.cache_hit:
        h = prepared.cache_hit
        print(f"[캐시 적중 · {h.kind} · 유사도 {h.similarity:.3f} · {h.tokens_saved} 토큰 절약]")
        print(f"[원 질문: {h.question}]\n")
        print(h.answer)
        return 0

    pk = prepared.packed
    print(
        f"[컨텍스트 {pk.tokens} 토큰 · 전문 로드 대비 {pk.saved_ratio:.0%} 절감 · "
        f"{len(pk.items)}개 항목]",
    )
    for item in pk.items:
        print(f"   L{item.tier} {item.tokens:>5}t  {item.uri}")

    answer, tin, tout = call_claude(prepared.messages)
    print()
    print(answer)

    # 실제 사용량을 넘기면 이후 캐시 적중 시의 절감액이 정확해집니다.
    result = j.commit(
        project,
        question,
        answer,
        model="claude-sonnet-5",
        tokens_in=tin,
        tokens_out=tout,
        prompt_uri=prepared.prompt_uri,
    )
    d = result.get("distill", {})
    if d.get("created") or d.get("merged"):
        print("\n[학습됨]")
        for uri in d.get("created", []):
            print(f"   + {uri}")
        for uri in d.get("merged", []):
            print(f"   ~ {uri}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
