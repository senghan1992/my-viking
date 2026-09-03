"""Core data model: URIs and tiered context nodes."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import yaml

SCHEME = "jarvis://"

# Kinds of node MyViking stores. Each lives in its own subtree so that
# retrieval can budget them separately.
KIND_MEMORY = "memory"
KIND_PROMPT = "prompt"
KIND_SESSION = "session"
KIND_RESOURCE = "resource"
KINDS = (KIND_MEMORY, KIND_PROMPT, KIND_SESSION, KIND_RESOURCE)

# No dots in slugs. "Store.all() 이 파일 없을 때" and "store.py 열어봐" both
# collapsed to a name that resolved as ``store`` and the second silently
# overwrote the first — the only data-destroying bug found in live use.
_SLUG_RE = re.compile(r"[^0-9a-z가-힣_-]+")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(text: str, fallback: str = "item") -> str:
    """Stable, human-readable, filesystem-safe name.

    Stable naming is the whole point: OpenViking's ``mem_{uuid}.md`` fragments
    cannot be referenced across sessions, so cumulative project memory has no
    place to accumulate. We derive the filename from the title instead.
    """
    s = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-._")
    s = re.sub(r"-{2,}", "-", s)
    return (s or fallback)[:80]


# --------------------------------------------------------------------------
# URI
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Uri:
    """A ``jarvis://`` path.

    Layout::

        jarvis://projects/{project}/{kind_dir}/{*rest}
        jarvis://global/{kind_dir}/{*rest}

    ``global`` holds context that applies to every project (your durable
    preferences); ``projects/{name}`` holds context that must not leak between
    projects. That split is what makes "different memory per project" real
    rather than a tag on a shared pile.
    """

    scope: str  # "global" or a project name
    parts: tuple[str, ...] = ()

    @property
    def is_global(self) -> bool:
        return self.scope == "global"

    @property
    def kind_dir(self) -> str:
        return self.parts[0] if self.parts else ""

    @property
    def rest(self) -> tuple[str, ...]:
        return self.parts[1:]

    @property
    def name(self) -> str:
        return self.parts[-1] if self.parts else self.scope

    def child(self, *more: str) -> "Uri":
        extra = tuple(p for m in more for p in str(m).split("/") if p)
        return Uri(self.scope, self.parts + extra)

    @property
    def parent(self) -> "Uri | None":
        if not self.parts:
            return None
        return Uri(self.scope, self.parts[:-1])

    def ancestors(self) -> list["Uri"]:
        out: list[Uri] = []
        cur: Uri | None = self.parent
        while cur is not None:
            out.append(cur)
            cur = cur.parent
        return out

    def is_under(self, other: "Uri") -> bool:
        return (
            self.scope == other.scope
            and len(self.parts) >= len(other.parts)
            and self.parts[: len(other.parts)] == other.parts
        )

    def __str__(self) -> str:
        head = "global" if self.is_global else f"projects/{self.scope}"
        tail = "/".join(self.parts)
        return SCHEME + head + (("/" + tail) if tail else "")

    @classmethod
    @functools.lru_cache(maxsize=8192)
    def _parse_cached(cls, uri: str) -> "Uri":
        return cls._parse(uri)

    @classmethod
    def parse(cls, uri: str) -> "Uri":
        # Uri is frozen and parsing is pure, so results are safe to memoise.
        # Retrieval parses the same URIs thousands of times per query.
        return cls._parse_cached(str(uri).strip())

    @classmethod
    def _parse(cls, uri: str) -> "Uri":
        raw = str(uri).strip()
        if raw.startswith(SCHEME):
            raw = raw[len(SCHEME) :]
        segs = [s for s in raw.split("/") if s]
        if not segs:
            raise ValueError("빈 URI 입니다")
        if segs[0] == "global":
            return cls("global", tuple(segs[1:]))
        if segs[0] == "projects":
            if len(segs) < 2:
                raise ValueError("프로젝트 이름이 없습니다: " + uri)
            return cls(segs[1], tuple(segs[2:]))
        # Bare form: "myapp/memories/..." is treated as a project path.
        return cls(segs[0], tuple(segs[1:]))

    @classmethod
    def project(cls, name: str) -> "Uri":
        return cls("global" if name == "global" else name, ())


def project_root(project: str) -> Uri:
    return Uri.project(project)


# --------------------------------------------------------------------------
# Node
# --------------------------------------------------------------------------
@dataclass
class Node:
    """One addressable piece of context, stored in three tiers.

    * ``abstract`` (L0, ~100 tokens) — enough to judge relevance.
    * ``overview`` (L1, ~2k tokens) — enough to plan with.
    * ``body``     (L2)             — the full text, loaded only on demand.

    A retrieval walk reads L0 for many nodes and L2 for very few; that ratio is
    where the token savings come from.
    """

    uri: Uri
    kind: str = KIND_MEMORY
    title: str = ""
    abstract: str = ""
    overview: str = ""
    body: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.5
    hits: int = 0
    created: str = field(default_factory=now_iso)
    updated: str = field(default_factory=now_iso)
    last_used: str = ""
    sources: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    # ----- markdown serialisation --------------------------------------
    def frontmatter(self) -> dict[str, Any]:
        data = {
            "uri": str(self.uri),
            "kind": self.kind,
            "title": self.title,
            "category": self.category,
            "abstract": self.abstract,
            "tags": list(self.tags),
            "confidence": round(float(self.confidence), 4),
            "hits": int(self.hits),
            "created": self.created,
            "updated": self.updated,
            "last_used": self.last_used,
            "sources": list(self.sources),
        }
        if self.extra:
            data["extra"] = self.extra
        return data

    def to_markdown(self) -> str:
        fm = yaml.safe_dump(
            self.frontmatter(), allow_unicode=True, sort_keys=False
        ).rstrip()
        chunks = ["---", fm, "---", ""]
        if self.overview.strip():
            chunks += ["## Overview", self.overview.strip(), ""]
        if self.body.strip():
            chunks += ["## Details", self.body.strip(), ""]
        return "\n".join(chunks)

    @classmethod
    def from_markdown(cls, text: str, uri: Uri | None = None) -> "Node":
        fm: dict[str, Any] = {}
        rest = text
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                fm = yaml.safe_load(text[3:end]) or {}
                rest = text[end + 4 :]
        overview, body = _split_sections(rest)
        parsed_uri = uri
        if parsed_uri is None:
            if not fm.get("uri"):
                raise ValueError("URI를 결정할 수 없습니다")
            parsed_uri = Uri.parse(str(fm["uri"]))
        return cls(
            uri=parsed_uri,
            kind=str(fm.get("kind", KIND_MEMORY)),
            title=str(fm.get("title", "") or ""),
            abstract=str(fm.get("abstract", "") or ""),
            overview=overview,
            body=body,
            category=str(fm.get("category", "") or ""),
            tags=list(fm.get("tags") or []),
            confidence=float(fm.get("confidence", 0.5)),
            hits=int(fm.get("hits", 0)),
            created=str(fm.get("created") or now_iso()),
            updated=str(fm.get("updated") or now_iso()),
            last_used=str(fm.get("last_used") or ""),
            sources=[str(s) for s in (fm.get("sources") or [])],
            extra=dict(fm.get("extra") or {}),
        )

    # ----- tiers -------------------------------------------------------
    def tier(self, level: int) -> str:
        if level <= 0:
            return self.abstract or self.title
        if level == 1:
            return self.overview or self.abstract or self.title
        return self.body or self.overview or self.abstract or self.title

    def searchable_text(self) -> str:
        return "\n".join(
            p
            for p in (
                self.title,
                self.category,
                " ".join(self.tags),
                self.abstract,
                self.overview,
                self.body,
            )
            if p
        )


def body_of(text: str) -> str:
    """Extract the Details section from a stored file without parsing YAML.

    Retrieval reads bodies far more often than it reads metadata, and
    ``yaml.safe_load`` on the frontmatter dominated the pack hot path. This
    walks the two markers directly.
    """
    rest = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            rest = text[end + 4 :]
    overview, body = _split_sections(rest)
    return body or overview


def _split_sections(text: str) -> tuple[str, str]:
    """Pull ``## Overview`` / ``## Details`` back out of a stored markdown file."""
    overview_m = re.search(r"^##\s+Overview\s*$", text, re.M)
    details_m = re.search(r"^##\s+Details\s*$", text, re.M)
    if overview_m is None and details_m is None:
        return "", text.strip()
    overview = ""
    body = ""
    if overview_m is not None:
        end = details_m.start() if details_m and details_m.start() > overview_m.end() else len(text)
        overview = text[overview_m.end() : end].strip()
    if details_m is not None:
        body = text[details_m.end() :].strip()
    return overview, body


def dedupe_preserve(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out
