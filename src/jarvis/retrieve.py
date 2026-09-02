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
    ) -> tuple[list[Candidate], list[dict[str, Any]]]:
        """Directory-first semantic search. Returns (candidates, trace)."""
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

        # Fetch only what the coarse walk or the lexical index actually pointed
        # at. Scanning every node in scope and discarding in Python makes query
        # latency grow with the size of the whole project rather than with the
        # size of the answer.
        marks = ", ".join("?" for _ in scopes)
        params: list[Any] = list(scopes)
        reach: list[str] = []
        for d in entered_uris:
            reach.append("uri LIKE ?")
            params.append(d + "/%")
        if fts:
            reach.append(f"uri IN ({', '.join('?' for _ in fts)})")
            params.extend(fts)
        if not reach:
            trace.append({"step": "rank", "considered": 0, "selected": 0, "lexical_hits": 0})
            return [], trace
        kind_clause = ""
        if kind_set:
            kind_clause = f" AND kind IN ({', '.join('?' for _ in kind_set)})"
            params.extend(sorted(kind_set))

        rows = self.db.query(
            f"SELECT uri, kind, category, title, abstract, confidence, hits,"
            f" updated, tokens_l0, tokens_l1, tokens_l2, vector FROM nodes"
            f" WHERE scope IN ({marks}) AND ({' OR '.join(reach)})"
            f" AND instr(uri, '/_archive/') = 0{kind_clause}",
            params,
        )
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
            }
        )
        return candidates, trace

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
        cfg = _apply_profile_budget(cfg, self.store.profile(project).budget)
        candidates, trace = self.search(
            query, project, kinds=kinds, limit=limit, include_global=include_global
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
            body = _join_sections(_sections_of((c, t, x) for c, t, x, _ in resolved))
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
        body = _join_sections(_sections_of((c, t, x) for c, t, x, _ in resolved))
        full_body = _join_sections(_sections_of((c, 2, f) for c, _t, _x, f in resolved))
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


def _sections_of(triples) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    for cand, tier, text in triples:
        group = _group_label(cand.kind, cand.category)
        sections.setdefault(group, []).append(_render_section(cand, tier, text))
    return sections


def _join_sections(sections: dict[str, list[str]]) -> str:
    parts: list[str] = []
    for group in sorted(sections, key=lambda g: _GROUP_ORDER.get(g, 99)):
        parts.append(f"## {group}")
        parts.extend(sections[group])
    return "\n\n".join(parts).strip()


_GROUP_ORDER = {
    "프로젝트 메모리": 0,
    "전역 선호": 1,
    "프롬프트": 2,
    "과거 세션": 3,
    "참고 자료": 4,
}


def _group_label(kind: str, category: str) -> str:
    if kind == KIND_MEMORY:
        return f"프로젝트 메모리 · {category}" if category else "프로젝트 메모리"
    if kind == KIND_PROMPT:
        return "프롬프트"
    if kind == KIND_SESSION:
        return "과거 세션"
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
