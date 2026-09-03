"""Coarse-to-fine retrieval and budget-aware context packing.

Two ideas do the work here:

1. **Directory-first search.** Score directory centroids, enter only the best
   few, then score the nodes inside them. The retrieval trace records the path
   taken, so every answer can be explained by pointing at directories and files.

2. **Tier assignment under a budget.** Selecting *which* nodes to include is
   only half the problem; the other half is choosing how much of each to read.
   We solve a small greedy knapsack over (node, tier) options ranked by
   score-per-token, which is where the token reduction actually comes from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import BudgetConfig
from .embed import batch_cosine, pack_vector, unpack_vector
from .models import KIND_MEMORY, KIND_PROMPT, KIND_RESOURCE, KIND_SESSION, Uri
from .store import DIR_KINDS, GLOBAL_SCOPE, Store
from .tokens import estimate_tokens

TIER_NAMES = {0: "L0", 1: "L1", 2: "L2"}


@dataclass
class Candidate:
    uri: Uri
    kind: str
    category: str
    title: str
    abstract: str
    score: float
    confidence: float
    hits: int
    updated: str
    tokens: dict[int, int]
    vec_score: float = 0.0
    fts_score: float = 0.0
    dir_score: float = 0.0


@dataclass
class PackedItem:
    uri: str
    title: str
    kind: str
    category: str
    tier: int
    tokens: int
    score: float


@dataclass
class PackedContext:
    query: str
    project: str
    text: str
    tokens: int
    items: list[PackedItem] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    # What the *same selection* would cost at full detail (L2). This is the
    # apples-to-apples baseline for the saving that tiering actually earns.
    baseline_tokens: int = 0
    # What pasting *every* retrieved candidate in full would cost — the naive
    # "dump everything relevant" approach, reported for comparison only.
    dump_tokens: int = 0
    # Categories the profile marks as warnings, for the caller to point at.
    warn_categories: list[str] = field(default_factory=list)
    considered: int = 0

    @property
    def saved_tokens(self) -> int:
        return max(0, self.baseline_tokens - self.tokens)

    @property
    def saved_ratio(self) -> float:
        if self.baseline_tokens <= 0:
            return 0.0
        return self.saved_tokens / self.baseline_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "project": self.project,
            "text": self.text,
            "tokens": self.tokens,
            "baseline_tokens": self.baseline_tokens,
            "dump_tokens": self.dump_tokens,
            "saved_tokens": self.saved_tokens,
            "saved_ratio": round(self.saved_ratio, 4),
            "saved_vs_dump": max(0, self.dump_tokens - self.tokens),
            "considered": self.considered,
            "items": [
                {
                    "uri": i.uri,
                    "title": i.title,
                    "kind": i.kind,
                    "category": i.category,
                    "tier": TIER_NAMES[i.tier],
                    "tokens": i.tokens,
                    "score": round(i.score, 4),
                }
                for i in self.items
            ],
            "trace": self.trace,
        }


class Retriever:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db
        self.embedder = store.embedder

    # ------------------------------------------------------------------
    def scopes_for(self, project: str, include_global: bool = True) -> list[str]:
        scopes = [project]
        if include_global and project != GLOBAL_SCOPE:
            scopes.append(GLOBAL_SCOPE)
        return scopes

    def search(
        self,
        query: str,
        project: str,
        kinds: Iterable[str] | None = None,
        limit: int = 20,
        include_global: bool = True,
        dir_fanout: int = 6,
        max_candidates: int = 0,
    ) -> tuple[list[Candidate], list[dict[str, Any]]]:
        """Directory-first semantic search. Returns (candidates, trace)."""
        max_candidates = max_candidates or self.store.config.budget.max_candidates
        scopes = self.scopes_for(project, include_global)
        kind_set = set(kinds) if kinds else None
        qvec = self.embedder.embed(query)
        trace: list[dict[str, Any]] = []

        # --- step 1: rank directories -------------------------------------
        dir_scores: dict[str, float] = {}
        for scope in scopes:
            rows = self.db.query(
                "SELECT uri, kind, children, vector FROM dirs WHERE scope=?", (scope,)
            )
            keep = []
            for row in rows:
                d = Uri.parse(row["uri"])
                if kind_set and d.kind_dir:
                    dk = DIR_KINDS.get(d.kind_dir)
                    if dk and dk not in kind_set:
                        continue
                keep.append((row, d))
            # dirs stores an unnormalised sum; normalise to get the centroid.
            centroids = [
                pack_vector(_normalize(unpack_vector(row["vector"])))
                for row, _d in keep
            ]
            for (row, d), score in zip(keep, batch_cosine(qvec, centroids)):
                # Prefer deeper directories at equal similarity: they are more
                # specific, so drilling into them reads fewer irrelevant nodes.
                dir_scores[row["uri"]] = score + 0.01 * len(d.parts)

        entered = sorted(dir_scores.items(), key=lambda kv: -kv[1])[:dir_fanout]
        # Global preferences are standing instructions, not answers to be found:
        # their directory must always be walked. Once a project has more than a
        # handful of directories (archives included) the global ones fell out of
        # the fan-out and preferences silently stopped riding along.
        if include_global and project != GLOBAL_SCOPE:
            already = {u for u, _ in entered}
            for uri, score in dir_scores.items():
                if uri.startswith(f"jarvis://{GLOBAL_SCOPE}/") and uri not in already:
                    entered.append((uri, score))
        for uri, score in entered:
            trace.append(
                {"step": "dir", "uri": uri, "score": round(score, 4), "action": "entered"}
            )

        # --- step 2: lexical signal ---------------------------------------
        fts = dict(self.db.fts_search(query, scopes, limit=limit * 5))
        fts_max = max(fts.values(), default=0.0) or 1.0

        # --- step 3: gather and score nodes -------------------------------
        entered_uris = [u for u, _ in entered]
        seen: dict[str, Candidate] = {}
        now = datetime.now(timezone.utc)

        # Fetch only what the coarse walk or the lexical index pointed at, and
        # cap how much of a directory we are willing to score. Without the cap a
        # flat category with thousands of files puts every query back to a full
        # scan, which is the difference between a 50ms and a 500ms answer.
        rows, capped = self._candidates(
            scopes, entered_uris, list(fts), kind_set, max_candidates
        )
        if not rows:
            trace.append(
                {"step": "rank", "considered": 0, "selected": 0, "lexical_hits": len(fts)}
            )
            return [], trace
        vec_scores = batch_cosine(qvec, [r["vector"] for r in rows])
        entered_parsed = [(Uri.parse(d), dir_scores.get(d, 0.0)) for d in entered_uris]
        for row, vec_score in zip(rows, vec_scores):
            uri = Uri.parse(row["uri"])

            dir_score = 0.0
            for du, dscore in entered_parsed:
                if uri.is_under(du):
                    dir_score = max(dir_score, dscore)

            fts_score = fts.get(row["uri"], 0.0) / fts_max
            recency = _recency_bonus(row["updated"], now)
            priority = self._priority(project, row["kind"], row["category"])

            score = (
                0.42 * vec_score
                + 0.26 * fts_score
                + 0.12 * dir_score
                + 0.10 * float(row["confidence"])
                + 0.05 * recency
                + 0.05 * priority
            )
            seen[row["uri"]] = Candidate(
                uri=uri,
                kind=row["kind"],
                category=row["category"] or "",
                title=row["title"] or uri.name,
                abstract=row["abstract"] or "",
                score=score,
                confidence=float(row["confidence"]),
                hits=int(row["hits"]),
                updated=row["updated"] or "",
                tokens={
                    0: int(row["tokens_l0"] or 0),
                    1: int(row["tokens_l1"] or 0),
                    2: int(row["tokens_l2"] or 0),
                },
                vec_score=vec_score,
                fts_score=fts_score,
                dir_score=dir_score,
            )

        candidates = sorted(seen.values(), key=lambda c: -c.score)[:limit]
        trace.append(
            {
                "step": "rank",
                "considered": len(seen),
                "selected": len(candidates),
                "lexical_hits": len(fts),
                # Says plainly when recall was truncated, so a surprising answer
                # can be explained rather than guessed at.
                "capped": capped,
            }
        )
        return candidates, trace

    _CANDIDATE_COLS = (
        "uri, kind, category, title, abstract, confidence, hits, updated,"
        " tokens_l0, tokens_l1, tokens_l2, vector"
    )

    def _candidates(
        self,
        scopes: list[str],
        entered_uris: list[str],
        lexical: list[str],
        kind_set: set[str] | None,
        max_candidates: int,
    ) -> tuple[list[Any], bool]:
        """Gather the rows worth scoring: every lexical hit, plus a bounded
        slice of each directory the coarse walk entered.

        The slice is ordered by confidence then recency — the best available
        prior when nothing lexical matched, since those are the memories most
        likely to still be true.
        """
        kind_clause = ""
        kind_params: list[Any] = []
        if kind_set:
            kind_clause = f" AND kind IN ({', '.join('?' for _ in kind_set)})"
            kind_params = sorted(kind_set)

        by_uri: dict[str, Any] = {}
        if lexical:
            marks = ", ".join("?" for _ in scopes)
            uri_marks = ", ".join("?" for _ in lexical)
            for row in self.db.query(
                f"SELECT {self._CANDIDATE_COLS} FROM nodes WHERE scope IN ({marks})"
                f" AND uri IN ({uri_marks}) AND instr(uri, '/_archive/') = 0"
                f"{kind_clause}",
                [*scopes, *lexical, *kind_params],
            ):
                by_uri[row["uri"]] = row

        capped = False
        if entered_uris:
            per_dir = max(60, max_candidates // len(entered_uris))
            for d in entered_uris:
                scope = Uri.parse(d).scope
                rows = self.db.query(
                    f"SELECT {self._CANDIDATE_COLS} FROM nodes WHERE scope = ?"
                    f" AND uri LIKE ? AND instr(uri, '/_archive/') = 0{kind_clause}"
                    f" ORDER BY confidence DESC, updated DESC LIMIT ?",
                    [scope, d + "/%", *kind_params, per_dir + 1],
                )
                if len(rows) > per_dir:
                    capped = True
                    rows = rows[:per_dir]
                for row in rows:
                    by_uri.setdefault(row["uri"], row)
        return list(by_uri.values()), capped

    def _priority(self, project: str, kind: str, category: str) -> float:
        if kind != KIND_MEMORY or not category:
            return 0.5
        cat = self.store.profile(project).category(category)
        if cat is None:
            return 0.5
        return min(1.0, cat.priority / 10.0)

    # ------------------------------------------------------------------
    def pack(
        self,
        query: str,
        project: str,
        budget: BudgetConfig | None = None,
        kinds: Iterable[str] | None = None,
        include_global: bool = True,
        limit: int = 24,
        max_tier: int = 2,
    ) -> PackedContext:
        """Assemble the highest-value context that fits in the token budget."""
        cfg = budget or self.store.config.budget
        profile = self.store.profile(project)
        cfg = _apply_profile_budget(cfg, profile.budget)
        warn_cats = profile.warn_categories()
        candidates, trace = self.search(
            query,
            project,
            kinds=kinds,
            limit=limit,
            include_global=include_global,
            max_candidates=cfg.max_candidates,
        )
        before = len(candidates)
        candidates = _focus(candidates, warn_cats, cfg)
        if len(candidates) != before:
            trace.append(
                {"step": "focus", "before": before, "after": len(candidates)}
            )

        per_kind_cap = {
            KIND_MEMORY: cfg.memories,
            KIND_PROMPT: cfg.prompts,
            KIND_SESSION: cfg.sessions,
            KIND_RESOURCE: cfg.resources,
        }
        spent_kind: dict[str, int] = {k: 0 for k in per_kind_cap}
        total_budget = cfg.total
        spent = 0

        # Greedy knapsack over (candidate, tier) upgrades: every candidate first
        # competes to get in at L0, then the best ones compete to be upgraded.
        chosen: dict[str, int] = {}
        options: list[tuple[float, str, int, Candidate]] = []
        for c in candidates:
            for tier in range(0, max_tier + 1):
                cost = c.tokens.get(tier, 0)
                if cost <= 0:
                    continue
                prev_cost = c.tokens.get(tier - 1, 0) if tier > 0 else 0
                delta = cost - prev_cost
                if tier > 0 and delta <= 0:
                    continue  # tier adds nothing new
                # Value of an upgrade decays: detail is worth less than presence.
                value = c.score * (1.0 if tier == 0 else (0.45 if tier == 1 else 0.2))
                density = value / max(1, delta if tier > 0 else cost)
                options.append((density, str(c.uri), tier, c))
        options.sort(key=lambda o: -o[0])

        by_uri = {str(c.uri): c for c in candidates}
        # Tiers must be taken in order (you cannot hold L1 without L0), but the
        # density sort can offer an upgrade before its prerequisite. So we sweep
        # repeatedly and stop when a full pass buys nothing.
        while True:
            progressed = False
            for _density, uri, tier, cand in options:
                current = chosen.get(uri, -1)
                if tier != current + 1:
                    continue
                cost = cand.tokens.get(tier, 0) - (
                    cand.tokens.get(current, 0) if current >= 0 else 0
                )
                if current < 0:
                    # First admission also pays for the section heading and the
                    # provenance comment; ignoring that overruns the budget.
                    cost += _HEADING_OVERHEAD
                if cost <= 0:
                    chosen[uri] = tier
                    progressed = True
                    continue
                cap = per_kind_cap.get(cand.kind, cfg.resources)
                if spent + cost > total_budget:
                    continue
                if spent_kind.get(cand.kind, 0) + cost > cap:
                    continue
                chosen[uri] = tier
                spent += cost
                spent_kind[cand.kind] = spent_kind.get(cand.kind, 0) + cost
                progressed = True
            if not progressed:
                break

        # Resolve the selection into concrete text. Kept as a list of
        # (candidate, tier, text, full_text) so the trim step below can re-render.
        resolved: list[tuple[Candidate, int, str, str]] = []
        for uri, tier in sorted(
            chosen.items(), key=lambda kv: -by_uri[kv[0]].score
        ):
            cand = by_uri[uri]
            text = self.store.read_tier(cand.uri, tier).strip()
            if not text:
                continue
            full = (
                text
                if tier >= 2
                else (self.store.read_tier(cand.uri, 2).strip() or text)
            )
            resolved.append((cand, tier, text, full))

        # Estimating per-section overhead is close but not exact, so verify
        # against the rendered text and drop the weakest items until it fits.
        # The budget is a promise to the caller, not an aspiration.
        while resolved:
            body = _join_sections(_sections_of(((c, t, x) for c, t, x, _ in resolved), warn_cats))
            if estimate_tokens(body) <= total_budget:
                break
            resolved.pop()  # already ordered by descending score

        items = [
            PackedItem(
                uri=str(cand.uri),
                title=cand.title,
                kind=cand.kind,
                category=cand.category,
                tier=tier,
                tokens=cand.tokens.get(tier, estimate_tokens(text)),
                score=cand.score,
            )
            for cand, tier, text, _full in resolved
        ]
        chosen = {str(c.uri): t for c, t, _x, _f in resolved}
        body = _join_sections(_sections_of(((c, t, x) for c, t, x, _ in resolved), warn_cats))
        full_body = _join_sections(_sections_of(((c, 2, f) for c, _t, _x, f in resolved), warn_cats))
        baseline_tokens = estimate_tokens(full_body)
        # Naive dump = the measured baseline for what we included, plus the full
        # cost of everything we left out. Defined this way it is always a
        # superset of the baseline, so the two numbers can be compared directly.
        dump = baseline_tokens + sum(
            c.tokens.get(2, 0) + _HEADING_OVERHEAD
            for c in candidates
            if str(c.uri) not in chosen
        )
        packed = PackedContext(
            query=query,
            project=project,
            text=body,
            tokens=estimate_tokens(body),
            items=items,
            trace=trace,
            baseline_tokens=baseline_tokens,
            dump_tokens=dump,
            warn_categories=sorted(warn_cats),
            considered=len(candidates),
        )
        trace.append(
            {
                "step": "pack",
                "budget": total_budget,
                "used": packed.tokens,
                "baseline": packed.baseline_tokens,
                "dump": dump,
                "included": len(items),
                "tiers": {TIER_NAMES[t]: sum(1 for i in items if i.tier == t) for t in (0, 1, 2)},
            }
        )
        return packed


# A heading plus a provenance comment costs roughly this much per section.
_HEADING_OVERHEAD = 14


def _normalize(vec: list[float]) -> list[float]:
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec] if norm else vec


def _short_ref(cand: Candidate) -> str:
    """Traceable but cheap: 'commands/테스트-실행' rather than the full URI.

    The full URI costs ~20 tokens per section and adds nothing a reader cannot
    reconstruct — the category and name are enough to find the file.
    """
    parts = cand.uri.parts
    return "/".join(parts[1:]) if len(parts) > 1 else cand.uri.name


def _render_section(cand: Candidate, tier: int, text: str) -> str:
    head = f"### {cand.title or cand.uri.name}"
    meta = f"<!-- {_short_ref(cand)} {TIER_NAMES[tier]} c={cand.confidence:.1f} -->"
    return f"{head}\n{meta}\n{text}"


def _norm_title(title: str) -> str:
    return re.sub(r"[\s\W_]+", "", (title or "").lower())


def _focus(
    candidates: list[Candidate], warn_cats: set[str], cfg: BudgetConfig
) -> list[Candidate]:
    """Keep the pack about the question.

    The token budget alone does not do this: at L0 every memory is cheap, so
    a budget of a few thousand tokens admits the entire project — and every
    prompt then carries a dozen unrelated one-liners, six of them the same
    "테스트" lesson re-learned six times. Three rules, in order:

    * one item per (kind, category, title) — the best-scoring survives;
    * nothing that scores far below the best match, unless it is a warning
      that lexically matched the question (warnings earn their place);
    * at most ``max_items`` in total, warnings first.
    """
    if not candidates:
        return candidates
    ordered = sorted(candidates, key=lambda c: -c.score)
    seen: set[tuple[str, str, str]] = set()
    kept: list[Candidate] = []
    for c in ordered:
        key = (c.kind, c.category, _norm_title(c.title))
        if key in seen:
            continue
        seen.add(key)
        kept.append(c)

    def warned(c: Candidate) -> bool:
        # Warnings that lexically matched, and global preferences — standing
        # instructions that ride into every project regardless of the question.
        return (c.category in warn_cats and c.fts_score > 0) or c.uri.is_global

    # A small store is not the problem: two or three items are read either way,
    # and a bystander must stay retrievable so blame attribution has something
    # to *not* blame. The gate only bites when there is more than fits anyway.
    over = cfg.max_items > 0 and len(kept) > cfg.max_items
    if over and cfg.min_relative_score > 0:
        floor = kept[0].score * cfg.min_relative_score
        gated = [c for c in kept if c.score >= floor or warned(c)]
        if gated:
            kept = gated
    if cfg.max_items > 0 and len(kept) > cfg.max_items:
        warn_first = [c for c in kept if warned(c)]
        rest = [c for c in kept if not warned(c)]
        kept = (warn_first + rest)[: max(cfg.max_items, len(warn_first))]
    return kept


def _sections_of(triples, warn_cats: set[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    for cand, tier, text in triples:
        group = _group_label(cand.kind, cand.category, warn_cats)
        sections.setdefault(group, []).append(_render_section(cand, tier, text))
    return sections


def _join_sections(sections: dict[str, list[str]]) -> str:
    parts: list[str] = []
    for group in sorted(sections, key=lambda g: _GROUP_ORDER.get(g, 99)):
        parts.append(f"## {group}")
        parts.extend(sections[group])
    return "\n\n".join(parts).strip()


WARN_GROUP = "⚠ 주의 — 이 프로젝트에서 이미 밟은 함정"
# Transcripts are a record of what was said at the time. A superseded
# instruction can still be quoted in one, so the heading has to say plainly
# that this is history rather than a standing rule.
SESSION_GROUP = "과거 대화 기록 (당시 내용이며 현재 규칙이 아님)"

_GROUP_ORDER = {
    WARN_GROUP: -1,
    "프로젝트 메모리": 0,
    "전역 선호": 1,
    "프롬프트": 2,
    SESSION_GROUP: 3,
    "참고 자료": 4,
}


def _group_label(kind: str, category: str, warn_cats: set[str] | None = None) -> str:
    if kind == KIND_MEMORY:
        if warn_cats and category in warn_cats:
            # Emphasis by position and heading, not by repeating the text
            # somewhere else in the prompt.
            return WARN_GROUP
        return f"프로젝트 메모리 · {category}" if category else "프로젝트 메모리"
    if kind == KIND_PROMPT:
        return "프롬프트"
    if kind == KIND_SESSION:
        return SESSION_GROUP
    return "참고 자료"


def _recency_bonus(updated: str, now: datetime) -> float:
    if not updated:
        return 0.0
    try:
        ts = datetime.fromisoformat(updated)
    except ValueError:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    days = max(0.0, (now - ts).total_seconds() / 86400.0)
    # Half-life of about a month: recent context matters, old context persists.
    return 0.5 ** (days / 30.0)


def _apply_profile_budget(cfg: BudgetConfig, overrides: dict[str, int]) -> BudgetConfig:
    if not overrides:
        return cfg
    fields = set(BudgetConfig.__dataclass_fields__)
    merged = {**{f: getattr(cfg, f) for f in fields}}
    for k, v in overrides.items():
        if k in fields:
            merged[k] = v
    return BudgetConfig(**merged)
