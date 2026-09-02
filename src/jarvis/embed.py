"""Embeddings with an offline default.

The default ``hashing`` embedder is a deterministic hashed n-gram vectoriser.
It is not a semantic model, but it is very good at the two jobs MyViking
actually needs a vector for:

1. near-duplicate detection (is this question the one I answered yesterday?)
2. merge detection (is this new memory the same lesson I already wrote down?)

Character n-grams also handle Korean without a tokenizer, which a word-level
scheme would not. If you configure a real embedding provider, semantic recall
improves and everything else keeps working unchanged.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
from typing import Iterable, Sequence

from .config import Config, EmbedConfig

_WORD = re.compile(r"[0-9A-Za-z]+|[가-힣]+|[぀-ヿ㐀-䶿一-鿿]+")


def _tokens(text: str, n: int = 3) -> Iterable[str]:
    low = (text or "").lower()
    for w in _WORD.findall(low):
        yield "w:" + w
        if len(w) > n:
            for i in range(len(w) - n + 1):
                yield "c:" + w[i : i + n]
        else:
            yield "c:" + w


def _bucket(token: str, dim: int) -> tuple[int, float]:
    h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    val = struct.unpack("<Q", h)[0]
    # Low bit chooses the sign so unrelated collisions cancel instead of add.
    return val % dim, 1.0 if (val >> 63) & 1 else -1.0


def hashing_embed(text: str, dim: int = 512) -> list[float]:
    vec = [0.0] * dim
    counts: dict[str, int] = {}
    for tok in _tokens(text):
        counts[tok] = counts.get(tok, 0) + 1
    for tok, c in counts.items():
        idx, sign = _bucket(tok, dim)
        vec[idx] += sign * (1.0 + math.log(c))
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    # Vectors are stored normalised, so the dot product is already cosine.
    return max(-1.0, min(1.0, dot))


class Embedder:
    def __init__(self, cfg: EmbedConfig | None = None, api_key: str = ""):
        self.cfg = cfg or EmbedConfig()
        self.api_key = api_key

    @classmethod
    def from_config(cls, config: Config) -> "Embedder":
        return cls(config.embed, config.api_key("embed"))

    @property
    def dim(self) -> int:
        return self.cfg.dim

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        if self.cfg.provider == "hashing" or not self.api_key:
            return [hashing_embed(t, self.dim) for t in texts]
        try:
            return self._remote(texts)
        except Exception:
            # Never fail a write because an embedding endpoint is down.
            return [hashing_embed(t, self.dim) for t in texts]

    # ----- remote providers -------------------------------------------
    def _remote(self, texts: Sequence[str]) -> list[list[float]]:
        import httpx

        base = self.cfg.base_url or {
            "openai": "https://api.openai.com/v1",
            "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
            "ollama": "http://localhost:11434/v1",
        }.get(self.cfg.provider, "")
        if not base:
            raise ValueError(f"알 수 없는 embed provider: {self.cfg.provider}")
        resp = httpx.post(
            base.rstrip("/") + "/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.cfg.model, "input": list(texts)},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        out = []
        for item in sorted(data, key=lambda d: d.get("index", 0)):
            v = [float(x) for x in item["embedding"]]
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


def pack_vector(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))
