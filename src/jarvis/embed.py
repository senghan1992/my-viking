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

import functools
import hashlib
import math
import re
import struct
from typing import Iterable, Sequence

from .config import Config, EmbedConfig

# Bump when the feature extraction changes: stored vectors become
# incomparable with new ones, and the index has to be rebuilt.
FEATURE_VERSION = "2-cjk-bigram"

_WORD = re.compile(r"[0-9A-Za-z]+|[가-힣]+|[぀-ヿ㐀-䶿一-鿿]+")
_CJK_WORD = re.compile(r"^[가-힣぀-ヿ㐀-䶿一-鿿]+$")


def _tokens(text: str, n: int = 3) -> Iterable[str]:
    """Hashed features for one text.

    Korean and Japanese attach particles and inflections directly to the stem,
    so a word-level or trigram-level feature never matches the bare form:
    "배포는" gives the trigram {배포는} and "배포" gives {배포}, sharing nothing.
    Measured on real queries, that left "배포는 어떻게 해?" scoring identically
    against a deployment note and an unrelated test note — the subject
    contributed nothing and only filler words did.

    Character *bigrams* for CJK survive the particle (both yield "배포"), while
    Latin keeps trigrams, where they work well.
    """
    low = (text or "").lower()
    for w in _WORD.findall(low):
        yield "w:" + w
        size = 2 if _CJK_WORD.match(w) else n
        if len(w) > size:
            for i in range(len(w) - size + 1):
                yield "c:" + w[i : i + size]
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


@functools.lru_cache(maxsize=1)
def _numpy():
    """NumPy is optional but changes retrieval latency by an order of magnitude.

    Scoring N candidates is one matrix-vector product; in pure Python it is
    N x dim multiplications in the interpreter. Both paths return the same
    numbers, so this is purely a speed switch.
    """
    try:
        import numpy  # type: ignore

        return numpy
    except Exception:
        return None


def batch_cosine(query: Sequence[float], blobs: Sequence[bytes | None]) -> list[float]:
    """Cosine of ``query`` against many packed vectors, in one pass."""
    if not query:
        return [0.0] * len(blobs)
    np = _numpy()
    dim = len(query)
    if np is None:
        return [cosine(query, unpack_vector(b)) for b in blobs]

    q = np.frombuffer(pack_vector(query), dtype="<f4")
    rows = np.zeros((len(blobs), dim), dtype="<f4")
    for i, blob in enumerate(blobs):
        if blob and len(blob) == dim * 4:
            rows[i] = np.frombuffer(blob, dtype="<f4")
    return np.clip(rows @ q, -1.0, 1.0).tolist()


# Mutually unrelated sentences, used once per model to measure what "no
# relation" scores. Cosine is not comparable across models: the hashing
# vectoriser gives unrelated text ~0.0, OpenAI's models ~0.1–0.2, and e5-style
# models ~0.8 (measured: unrelated median 0.83, related 0.80–0.90). Every
# threshold in MyViking was tuned on the hashing scale, so raw cosine from a
# real model put "hello" above the relevance gate and merged unrelated
# memories (0.82). ``calibrate`` maps a model's cosine back onto that scale.
_CALIBRATION_TEXTS = (
    "make deploy 로 배포한다",
    "승인 응답이 0000 이 아니면 재시도하지 않는다",
    "커밋 메시지는 한글로, 제목 50자 이내",
    "버튼 색상을 파란색으로 바꿔줘",
    "The quarterly report is due on Friday",
    "Rotate the database password every 90 days",
    "고양이는 하루에 열두 시간 이상 잔다",
    "How do I reset the router to factory settings?",
)


class Embedder:
    def __init__(self, cfg: EmbedConfig | None = None, api_key: str = ""):
        self.cfg = cfg or EmbedConfig()
        self.api_key = api_key
        self._floor: float | None = None
        # Observability for the silent degrade: a remote model that cannot be
        # reached is *configured* as semantic but *behaves* as hashing. Both
        # numbers are reported by /health so nobody reads "품질 최상" off a store
        # that is quietly filling with hash vectors.
        self.probe_error: str = ""
        self.fallbacks: int = 0

    @property
    def similarity_floor(self) -> float:
        """Median cosine between unrelated texts for the model in use.

        0 for the hashing vectoriser (its thresholds are the reference scale).
        Measured once per process for a real model — one small batch call."""
        if self._floor is None:
            if not self.configured_semantic:
                self._floor = 0.0
            else:
                try:
                    vecs = self._remote(list(_CALIBRATION_TEXTS))
                    self.probe_error = ""
                    if vecs and vecs[0] and len(vecs[0]) != self.cfg.dim:
                        self.cfg.dim = len(vecs[0])
                    sims = sorted(
                        cosine(vecs[i], vecs[j])
                        for i in range(len(vecs))
                        for j in range(i + 1, len(vecs))
                    )
                    mid = len(sims) // 2
                    floor = sims[mid] if len(sims) % 2 else (sims[mid - 1] + sims[mid]) / 2
                    # A negative or tiny floor means the model already behaves
                    # like the reference scale; a floor near 1 would be a
                    # degenerate model, and dividing by ~0 must not happen.
                    self._floor = min(max(floor, 0.0), 0.95)
                except Exception as exc:
                    # Endpoint down: vectors will be hashing until a restart.
                    self.probe_error = f"{type(exc).__name__}: {exc}"[:200]
                    self._floor = 0.0
        return self._floor

    def calibrate(self, cos: float) -> float:
        """Cosine on the model's own scale → the reference scale thresholds
        were tuned on: the floor becomes 0 and 1 stays 1."""
        floor = self.similarity_floor
        if floor <= 0.0:
            return cos
        return max(-1.0, min(1.0, (cos - floor) / (1.0 - floor)))

    def calibrate_many(self, sims: Sequence[float]) -> list[float]:
        floor = self.similarity_floor
        if floor <= 0.0:
            return list(sims)
        scale = 1.0 - floor
        return [max(-1.0, min(1.0, (s - floor) / scale)) for s in sims]

    @classmethod
    def from_config(cls, config: Config) -> "Embedder":
        return cls(config.embed, config.api_key("embed"))

    @property
    def dim(self) -> int:
        return self.cfg.dim

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    @property
    def configured_semantic(self) -> bool:
        """A real provider with what it needs to be called. Ollama runs locally
        and needs no key; every other provider does."""
        p = self.cfg.provider
        if p in ("", "hashing"):
            return False
        return p == "ollama" or bool(self.api_key)

    @property
    def semantic(self) -> bool:
        """Is a real embedding model actually answering — configured *and* the
        startup probe reached it? A wrong URL or dead Ollama is reported as
        not semantic, so /health says so instead of "최상"."""
        if not self.configured_semantic:
            return False
        if self._floor is None:
            self.similarity_floor  # runs the probe
        return not self.probe_error

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.configured_semantic:
            return [hashing_embed(t, self.dim) for t in texts]
        try:
            out = self._remote(texts)
        except Exception:
            # Never fail a write because an embedding endpoint is down.
            self.fallbacks += 1
            return [hashing_embed(t, self.dim) for t in texts]
        # The model decides the dimension, not the config. Learn it from the
        # first real response so a later fallback vector has the same length
        # (a length mismatch scores zero, silently) and so the index signature
        # reflects what is actually stored.
        if out and out[0] and len(out[0]) != self.cfg.dim:
            self.cfg.dim = len(out[0])
        return out

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
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
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
