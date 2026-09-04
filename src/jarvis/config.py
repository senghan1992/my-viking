"""Configuration and on-disk layout for MyViking."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

ENV_HOME = "JARVIS_HOME"
DEFAULT_HOME = Path.home() / ".jarvis"
CONFIG_NAME = "jarvis.yaml"


def jarvis_home() -> Path:
    return Path(os.environ.get(ENV_HOME) or DEFAULT_HOME).expanduser()


@dataclass
class LLMConfig:
    """Which model distills memories and writes tier summaries.

    ``provider = "none"`` keeps MyViking fully offline: tier summaries and
    memory extraction fall back to deterministic heuristics. Everything else
    (storage, retrieval, caching, budgeting) works identically.
    """

    provider: str = "none"  # none | anthropic | openai | volcengine | ollama
    model: str = "claude-sonnet-5"
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str = ""
    # 요청 경로. 기본은 base_url 뒤의 /chat/completions. Databricks Serving 처럼
    # base_url 이 곧 엔드포인트(…/invocations)면 "/" 로 요청 그 자체를 맞춘다.
    path: str = ""
    max_output_tokens: int = 2048
    timeout: float = 60.0


@dataclass
class EmbedConfig:
    provider: str = "hashing"  # hashing | openai | volcengine | ollama
    model: str = ""
    api_key_env: str = ""
    base_url: str = ""
    dim: int = 512


@dataclass
class BudgetConfig:
    """Default token budget for one packed context."""

    total: int = 8000
    memories: int = 3000
    # Per-kind caps must be able to hold at least one L1 (~2000 tokens), or a
    # highly relevant document can never be read past its abstract.
    resources: int = 3000
    sessions: int = 1500
    prompts: int = 800
    # Similarity at/above which a cached answer is reused verbatim.
    cache_hit_threshold: float = 0.92
    # Similarity at/above which a past session is offered as a reference.
    reference_threshold: float = 0.45
    # A pack is about one question. Five agents using the store live all
    # reported the same thing: every prompt carried the whole project's memory
    # ("README 오타 수정" got nine unrelated items). So the pack keeps at most
    # this many items and drops anything scoring far below the best match.
    # Warning-category items with a lexical hit are always kept. 0 disables.
    max_items: int = 8
    min_relative_score: float = 0.45
    # Absolute floor on *relevance evidence* (the vector/lexical/directory part
    # of the score, without the confidence/recency/priority priors). The
    # relative gate above only bites once a store outgrows max_items; below
    # that every memory rode along on every prompt — "hello" got five payment
    # and test notes. With nothing that actually matches, the pack is empty
    # and the agent works exactly as it would without MyViking. Warnings with
    # a lexical hit and global preferences are exempt. 0 disables.
    min_relevance: float = 0.12
    # Ceiling on candidates scored per query. Directory-first retrieval keeps
    # L0 reads proportional to depth, but a single flat category can still hold
    # thousands of files — and then every query pays for all of them. Beyond
    # this we keep the lexical matches plus the most-trusted, most-recent
    # entries of each directory we entered.
    max_candidates: int = 600


@dataclass
class LearnConfig:
    """Self-learning loop knobs."""

    auto_distill: bool = True
    # A memory whose confidence decays below this is archived.
    archive_below: float = 0.25
    # Multiplicative decay applied per distill run to unused memories.
    decay: float = 0.97
    # Confidence added each time a memory is used in a packed context.
    #
    # Zero by default, and that is the point: being retrieved is not evidence of
    # being *right*. Retrieval already records `last_used`, which is what stops
    # decay from archiving something in active use, so a confidence bonus on top
    # only muddies the signal — at 0.08 it cancelled the penalty for having
    # driven a wrong answer, and knowledge that kept failing looked identical to
    # knowledge that kept working. Confidence moves on outcomes.
    reinforce: float = 0.0
    # Two candidate memories above this similarity are merged, not duplicated.
    merge_threshold: float = 0.82
    max_per_category: int = 40
    # What to do when two memories contradict each other.
    #   "newest" — the later statement supersedes; the older is archived with a
    #              pointer, so the store stays self-maintaining and the change
    #              is reversible. A later instruction usually *is* the current
    #              one, which is why this is the default.
    #   "flag"   — keep both and surface the pair for a person to decide.
    conflict_policy: str = "newest"
    # Infer how the last answer landed from what gets asked next. Explicit
    # scores are rare in practice; being asked the same thing again is not.
    implicit_feedback: bool = True
    # Implicit signals move confidence less than a stated judgement, because
    # they are inferred. An explicit score on the same trace still lands at
    # full strength.
    implicit_strength: float = 0.5
    # Topic-overlap at/above which the next question counts as the same request
    # coming back rather than a new subject. Calibrated on measured pairs:
    # repeats landed 0.50-1.00, every change of subject 0.00.
    rework_similarity: float = 0.25
    # --- the answer key evolves ---------------------------------------------
    # Knowledge is a hypothesis until outcomes confirm it, and it can go stale.
    # These knobs decide when a once-"established" fact is retracted back into
    # "verify before you rely on this", and when a newly learned lesson is
    # treated as a *correction* that supersedes the belief it just disproved.
    #
    # Confidence at/above which a memory reads as a settled fact rather than a
    # tentative note the agent should double-check.
    solid_confidence: float = 0.6
    # Attributed contradictions in a row (since the last confirmation) that flip
    # an established belief to "contested" — it leaves the injected answer key
    # until a good outcome re-confirms it. This is the self-correcting core:
    # repeated bad outcomes *retract* a fact, they do not merely nudge a number.
    contested_after: int = 2
    # Days without any use or confirmation after which a memory is shown as
    # "오래됨" so the agent re-verifies it. Zero disables staleness.
    stale_days: int = 90
    # Topic-overlap at/above which a freshly learned memory that contradicts a
    # recently-blamed one is taken to *correct* it (supersede), not sit beside
    # it as a rival fact. Same scale as rework_similarity.
    correction_similarity: float = 0.35
    # A distilled memory is shown as "검증 전" until it has been confirmed, or
    # has ridden in this many answers whose follow-up moved on without complaint
    # ("settled"). Retrieval count and age alone never promote it: being shown
    # is not evidence of being right.
    settled_after: int = 3
    # Strip credentials (API keys, tokens, passwords, private keys) from what the
    # hooks capture before it is stored or injected. Off only if you know every
    # prompt and answer in the project is safe to keep verbatim.
    redact_secrets: bool = True


@dataclass
class RetentionConfig:
    """How long the *record* layer is kept.

    Memories already decay and cap themselves; without this, the observability
    side (traces, usage accounting, the answer cache) grows forever on an
    always-on server — and the near-miss cache lookup scans every row of its
    scope, so an unbounded cache slowly taxes every single prompt.
    0 disables a rule (keep forever).
    """

    # Traces older than this go, along with their observations/scores/context.
    # Their learning value has already been applied to memory confidence.
    traces_days: int = 180
    # Session transcripts older than this are dropped. They are the record
    # layer, not the knowledge: their durable value is already folded into
    # memories by distillation, and one exchange per prompt on an always-on
    # server is otherwise the fastest-growing thing in the store.
    sessions_days: int = 180
    # Archived memories (decayed, capped, or superseded) kept recoverable this
    # long, then purged. Archiving must stay reversible for a while — the note
    # you deleted is often the one you want when a project comes back — but a
    # quarter is long enough, and _archive/ otherwise only ever grows.
    archive_days: int = 90
    # Token accounting rows. Only dashboards read these.
    usage_days: int = 90
    # Answer cache entries kept per project, most recently used first.
    cache_per_project: int = 500


@dataclass
class Config:
    home: Path = field(default_factory=jarvis_home)
    user_id: str = "me"
    llm: LLMConfig = field(default_factory=LLMConfig)
    embed: EmbedConfig = field(default_factory=EmbedConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    learn: LearnConfig = field(default_factory=LearnConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)

    # ----- paths -------------------------------------------------------
    @property
    def config_path(self) -> Path:
        return self.home / CONFIG_NAME

    @property
    def db_path(self) -> Path:
        return self.home / "index.db"

    @property
    def projects_dir(self) -> Path:
        return self.home / "projects"

    def project_dir(self, project: str) -> Path:
        return self.projects_dir / project

    # ----- (de)serialisation -------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["home"] = str(self.home)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        data = dict(data or {})
        home = Path(data.pop("home", jarvis_home())).expanduser()
        sections = {
            "llm": LLMConfig,
            "embed": EmbedConfig,
            "budget": BudgetConfig,
            "learn": LearnConfig,
            "retention": RetentionConfig,
        }
        kwargs: dict[str, Any] = {}
        for key, klass in sections.items():
            raw = data.pop(key, None) or {}
            known = {f for f in klass.__dataclass_fields__}
            kwargs[key] = klass(**{k: v for k, v in raw.items() if k in known})
        kwargs["user_id"] = data.pop("user_id", "me")
        return cls(home=home, **kwargs)

    @classmethod
    def load(cls, home: Path | str | None = None, env: bool = True) -> "Config":
        """``env=False`` returns exactly what the file says — what ``jv config
        --set`` must edit, or an environment-only provider would be baked into
        the file and survive the variable being removed."""
        base = Path(home).expanduser() if home else jarvis_home()
        path = base / CONFIG_NAME
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            raw["home"] = str(base)
            cfg = cls.from_dict(raw)
        else:
            cfg = cls(home=base)
        if env:
            cfg.apply_env()
        return cfg

    def apply_env(self) -> "Config":
        """Environment overrides for the provider settings.

        A Docker deployment has no ``jv config`` step: the operator edits
        ``deploy/.env`` and re-runs ``up.sh``. So the model/embedding choice
        must be settable with variables, and the /health note that said
        "JARVIS_EMBED_PROVIDER 권장" has to name something that exists.

        ``ANTHROPIC_API_KEY`` alone turns the LLM on (provider anthropic) —
        the README and .env.example have promised that all along.
        """
        env = os.environ
        for section, prefix, fields in (
            (self.llm, "JARVIS_LLM_", ("provider", "model", "api_key_env", "base_url", "path")),
            (self.embed, "JARVIS_EMBED_", ("provider", "model", "api_key_env", "base_url")),
        ):
            for field_name in fields:
                val = env.get(prefix + field_name.upper(), "")
                if val:
                    setattr(section, field_name, val.strip())
        dim = env.get("JARVIS_EMBED_DIM", "")
        if dim.strip().isdigit() and int(dim) > 0:
            self.embed.dim = int(dim)
        # reasoner(deepseek 등)는 사고 토큰을 따로 쓰므로 출력 상한을 올릴 수 있게.
        out = env.get("JARVIS_LLM_MAX_OUTPUT_TOKENS", "")
        if out.strip().isdigit() and int(out) > 0:
            self.llm.max_output_tokens = int(out)
        if (
            self.llm.provider in ("", "none")
            and not env.get("JARVIS_LLM_PROVIDER")
            and env.get("ANTHROPIC_API_KEY")
        ):
            self.llm.provider = "anthropic"
            self.llm.api_key_env = "ANTHROPIC_API_KEY"
        _fill_provider_defaults(self)
        return self

    def save(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return self.config_path

    def api_key(self, which: str = "llm") -> str:
        env = self.llm.api_key_env if which == "llm" else self.embed.api_key_env
        return os.environ.get(env, "") if env else ""


# Sensible per-provider defaults so `JARVIS_EMBED_PROVIDER=openai` is enough.
# The vector dimension must match the model: stored vectors are compared by
# length, and a wrong ``dim`` silently scores every node zero.
_EMBED_KEY_ENV = {"openai": "OPENAI_API_KEY", "volcengine": "ARK_API_KEY", "ollama": ""}
_EMBED_DEFAULT_MODEL = {"openai": "text-embedding-3-small", "ollama": "bge-m3"}
EMBED_MODEL_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "bge-m3": 1024,
    "multilingual-e5-small": 384,
    "multilingual-e5-base": 768,
    "multilingual-e5-large": 1024,
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "snowflake-arctic-embed": 1024,
    "all-minilm": 384,
}
_LLM_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "volcengine": "ARK_API_KEY"}


def _fill_provider_defaults(cfg: "Config") -> None:
    e = cfg.embed
    if e.provider not in ("", "hashing"):
        if not e.model and e.provider in _EMBED_DEFAULT_MODEL:
            e.model = _EMBED_DEFAULT_MODEL[e.provider]
        if not e.api_key_env and e.provider in _EMBED_KEY_ENV:
            e.api_key_env = _EMBED_KEY_ENV[e.provider]
        # ``dim`` is only ever the hashing default (512) when a real model has
        # been named without one, so a known model's size takes over.
        known = EMBED_MODEL_DIMS.get(e.model.split(":")[0].lower())
        if known and e.dim == 512:
            e.dim = known
    l = cfg.llm
    if l.provider not in ("", "none") and l.provider in _LLM_KEY_ENV:
        if l.api_key_env == "ANTHROPIC_API_KEY" and l.provider != "anthropic":
            l.api_key_env = _LLM_KEY_ENV[l.provider]
