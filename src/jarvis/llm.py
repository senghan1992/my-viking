"""LLM access, optional by design.

MyViking has two jobs that benefit from a model: writing tier summaries and
distilling sessions into memories. Both have deterministic fallbacks, so
``provider: none`` is a fully working configuration — you just get extractive
summaries instead of abstractive ones.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .config import Config, LLMConfig

_DEFAULT_BASE = {
    "anthropic": "https://api.anthropic.com/v1",
    "openai": "https://api.openai.com/v1",
    "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
    "grok": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "ollama": "http://localhost:11434/v1",
}


@dataclass
class LLMResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    ok: bool = True
    error: str = ""


class LLM:
    def __init__(self, cfg: LLMConfig | None = None, api_key: str = ""):
        self.cfg = cfg or LLMConfig()
        self.api_key = api_key

    @classmethod
    def from_config(cls, config: Config) -> "LLM":
        return cls(config.llm, config.api_key("llm"))

    @property
    def available(self) -> bool:
        if self.cfg.provider in ("", "none"):
            return False
        if self.cfg.provider == "ollama":
            return True  # local, no key needed
        return bool(self.api_key)

    def complete(
        self, prompt: str, system: str = "", max_tokens: int | None = None
    ) -> LLMResult:
        if not self.available:
            return LLMResult("", ok=False, error="LLM 미설정")
        try:
            if self.cfg.provider == "anthropic":
                return self._anthropic(prompt, system, max_tokens)
            return self._openai_compatible(prompt, system, max_tokens)
        except Exception as exc:  # pragma: no cover - network dependent
            return LLMResult("", ok=False, error=f"{type(exc).__name__}: {exc}")

    def complete_json(
        self, prompt: str, system: str = "", max_tokens: int | None = None
    ) -> tuple[Any, LLMResult]:
        res = self.complete(prompt, system, max_tokens)
        if not res.ok or not res.text.strip():
            return None, res
        return extract_json(res.text), res

    # ----- providers ---------------------------------------------------
    def _base(self) -> str:
        return (self.cfg.base_url or _DEFAULT_BASE.get(self.cfg.provider, "")).rstrip("/")

    def _chat_url(self) -> str:
        """OpenAI 호환 요청 경로. 기본은 {base}/chat/completions.

        Databricks Serving 처럼 base_url 이 곧 엔드포인트(…/invocations)인
        게이트웨이는 /chat/completions 경로가 존재하지 않는다. 그 경우
        ``path="/"`` 로 base_url 그 자체로 POST 한다.
        """
        base = self._base()
        path = self.cfg.path
        if path == "/":
            return base
        if path:
            return base + "/" + path.lstrip("/")
        return base + "/chat/completions"

    def _anthropic(self, prompt: str, system: str, max_tokens: int | None) -> LLMResult:
        import httpx

        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": max_tokens or self.cfg.max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        resp = httpx.post(
            self._base() + "/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=self.cfg.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        usage = data.get("usage", {})
        return LLMResult(
            text,
            tokens_in=int(usage.get("input_tokens", 0)),
            tokens_out=int(usage.get("output_tokens", 0)),
        )

    def _openai_compatible(
        self, prompt: str, system: str, max_tokens: int | None
    ) -> LLMResult:
        import httpx

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = httpx.post(
            self._chat_url(),
            headers=headers,
            json={
                "model": self.cfg.model,
                "messages": messages,
                "max_tokens": max_tokens or self.cfg.max_output_tokens,
            },
            timeout=self.cfg.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        # DeepSeek 계열 reasoner 는 생각을 reasoning_content 에 쓰고 content 가
        # 비어 있을 수 있다 — 이 경우 빈 답보다 그 사고 텍스트를 쓴다.
        text = msg.get("content") or msg.get("reasoning_content") or ""
        usage = data.get("usage", {})
        return LLMResult(
            text,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
        )


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Best-effort JSON recovery from a model response."""
    candidates: list[str] = []
    for m in _FENCE.finditer(text):
        candidates.append(m.group(1))
    candidates.append(text)
    for cand in candidates:
        cand = cand.strip()
        if not cand:
            continue
        try:
            return json.loads(cand)
        except Exception:
            pass
        for opener, closer in (("{", "}"), ("[", "]")):
            start = cand.find(opener)
            end = cand.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(cand[start : end + 1])
                except Exception:
                    continue
    return None
