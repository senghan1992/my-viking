"""``Jarvis`` — the one class an application needs.

The intended loop is three calls:

    jarvis = Jarvis()
    prepared = jarvis.prepare("myapp", "이 저장소 테스트는 어떻게 돌려?")
    if prepared.cache_hit:            # answered before → 0 input tokens
        answer = prepared.cache_hit.answer
    else:
        answer = your_llm(prepared.messages)   # context already budgeted
    jarvis.commit("myapp", question, answer)   # feeds the learning loop

``prepare`` decides what to remember *into* the request; ``commit`` decides what
to remember *from* it. Everything else in this package serves those two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .budget import UsageReport, usage_report
from .config import BudgetConfig, Config
from .learn import DistillReport, Learner, MemoryCandidate
from .models import KIND_MEMORY, KIND_SESSION, Node, Uri, now_iso, slugify
from .profiles import MemoryProfile, builtin, template_summary
from .prompts import PromptLibrary, RenderResult
from .retrieve import PackedContext, Retriever
from .sessions import CacheHit, SessionLog
from .store import GLOBAL_SCOPE, Store
from .tiers import summarize
from .tokens import estimate_tokens, truncate_to_tokens
from .trace import Tracer, memory_impact, metrics, timeseries

# Ordering for the review queue: a contradiction or a memory that showed up in
# bad outcomes is worth interrupting for; an unconfirmed guess can wait.
_REVIEW_WEIGHT = {
    "conflict": 5,
    "harmful": 4,
    "unproven": 3,
    "unconfirmed": 2,
    "fading": 1,
}

SYSTEM_PREFIX = (
    "당신은 이 프로젝트에 대한 누적 컨텍스트를 가진 어시스턴트입니다.\n"
    "아래 컨텍스트는 이 프로젝트의 메모리·프롬프트·과거 세션에서 관련도 순으로 선택된 것입니다.\n"
    "컨텍스트와 충돌하는 내용을 답하지 말고, 컨텍스트에 없는 사실은 모른다고 말하세요."
)


@dataclass
class Prepared:
    """A request that is ready to send — or already answered."""

    project: str
    question: str
    system: str
    context: str
    user: str
    packed: PackedContext | None = None
    cache_hit: CacheHit | None = None
    prompt_uri: str = ""
    prompt_render: RenderResult | None = None
    references: list[dict[str, Any]] = field(default_factory=list)
    # Identifies this retrieval in the trace log, so the agent can report back
    # how the answer turned out. Without it, quality feedback has no anchor.
    trace_id: str = ""
    latency_ms: int = 0

    @property
    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user},
        ]

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.system) + estimate_tokens(self.user)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "question": self.question,
            "system": self.system,
            "context": self.context,
            "user": self.user,
            "tokens": self.tokens,
            "cache_hit": self.cache_hit.to_dict() if self.cache_hit else None,
            "packed": self.packed.to_dict() if self.packed else None,
            "prompt_uri": self.prompt_uri,
            "references": self.references,
            "trace_id": self.trace_id,
            "latency_ms": self.latency_ms,
        }


class Jarvis:
    def __init__(self, config: Config | None = None, home: str | None = None):
        self.config = config or Config.load(home)
        self.store = Store(self.config)
        self.retriever = Retriever(self.store)
        self.prompts = PromptLibrary(self.store)
        self.sessions = SessionLog(self.store)
        self.learner = Learner(self.store, self.sessions)
        self.tracer = Tracer(self.store.db)
        # The global scope always exists: it holds cross-project preferences.
        self.store.ensure_project(GLOBAL_SCOPE)

    # ==================================================================
    # projects & profiles
    # ==================================================================
    def init_project(
        self,
        project: str,
        template: str = "default",
        description: str = "",
        stack: Iterable[str] | None = None,
    ) -> MemoryProfile:
        return self.store.ensure_project(project, template, description, stack)

    def projects(self) -> list[dict[str, Any]]:
        stats = self.store.stats()
        out = []
        for name in self.store.projects():
            s = stats.get(name, {})
            prof = self.store.profile(name)
            out.append(
                {
                    "project": name,
                    "template": prof.template,
                    "description": prof.description,
                    "categories": prof.category_names(),
                    "nodes": s.get("total_nodes", 0),
                    "cache_entries": s.get("cache_entries", 0),
                }
            )
        return out

    def profile(self, project: str) -> MemoryProfile:
        return self.store.profile(project)

    def set_profile(self, project: str, profile: MemoryProfile) -> MemoryProfile:
        self.store.save_profile(project, profile)
        return profile

    def apply_template(self, project: str, template: str) -> MemoryProfile:
        """Swap a project's category schema, keeping its description and budget."""
        current = self.store.profile(project)
        new = builtin(template)
        new.description = current.description or new.description
        new.stack = current.stack or new.stack
        new.budget = current.budget or new.budget
        self.store.save_profile(project, new)
        return new

    def templates(self) -> list[dict[str, Any]]:
        return template_summary()

    def delete_project(self, project: str) -> bool:
        return self.store.delete_project(project)

    # ==================================================================
    # prompts
    # ==================================================================
    def save_prompt(
        self,
        project: str,
        name: str,
        template: str,
        title: str = "",
        description: str = "",
        tags: list[str] | None = None,
    ) -> Node:
        self.store.ensure_project(project)
        return self.prompts.save(project, name, template, title, description, tags)

    def get_prompt(self, project: str, name: str) -> Node | None:
        return self.prompts.get(project, name)

    def list_prompts(self, project: str) -> list[dict[str, Any]]:
        return self.prompts.list(project)

    def render_prompt(
        self, project: str, name: str, values: dict[str, Any] | None = None,
        strict: bool = False,
    ) -> RenderResult:
        return self.prompts.render(project, name, values, strict=strict)

    def prompt_versions(self, project: str, name: str) -> list[dict[str, Any]]:
        return self.prompts.versions(project, name)

    def rollback_prompt(self, project: str, name: str, version: str) -> Node | None:
        return self.prompts.rollback(project, name, version)

    def delete_prompt(self, project: str, name: str) -> bool:
        return self.prompts.delete(project, name)

    # ==================================================================
    # memory (manual)
    # ==================================================================
    def remember(
        self,
        project: str,
        category: str,
        title: str,
        statement: str,
        detail: str = "",
        tags: list[str] | None = None,
        confidence: float = 0.8,
    ) -> Uri:
        """Write a memory by hand. Manual memories start with high confidence:
        you asserted them, so they are not guesses to be decayed away quickly."""
        self.store.ensure_project(project)
        profile = self.store.profile(project)
        if profile.category(category) is None:
            raise ValueError(
                f"'{category}' 는 이 프로젝트의 카테고리가 아닙니다. "
                f"사용 가능: {', '.join(profile.category_names())}"
            )
        cand = MemoryCandidate(
            category=category,
            title=title,
            statement=statement,
            detail=detail,
            confidence=confidence,
            tags=tags or ["수동"],
            source="manual",
        )
        uri, _action, _conflict = self.learner.absorb(project, cand, profile)
        return uri  # type: ignore[return-value]

    def forget(self, uri: str, archive: bool = True) -> bool:
        if archive:
            return self.store.archive_node(uri, reason="manual") is not None
        return self.store.delete_node(uri)

    def memories(
        self, project: str, category: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        params: list[Any] = [project, KIND_MEMORY]
        sql = (
            "SELECT uri, title, category, abstract, confidence, hits, updated,"
            " tokens_l0, tokens_l2 FROM nodes WHERE scope=? AND kind=?"
        )
        if category:
            sql += " AND category=?"
            params.append(category)
        sql += " ORDER BY confidence DESC, updated DESC LIMIT ?"
        params.append(limit)
        rows = self.store.db.query(sql, params)
        return [
            {
                "uri": r["uri"],
                "title": r["title"],
                "category": r["category"],
                "abstract": r["abstract"],
                "confidence": round(r["confidence"], 3),
                "hits": r["hits"],
                "updated": r["updated"],
                "tokens": {"l0": r["tokens_l0"], "l2": r["tokens_l2"]},
            }
            for r in rows
            if Uri.parse(r["uri"]).parts[:1] != ("_archive",)
        ]

    # ------------------------------------------------------------------
    # curation — the part you do *after* the work, not during it
    # ------------------------------------------------------------------
    def review_queue(self, project: str, limit: int = 50) -> list[dict[str, Any]]:
        """Memories that want a human decision, most consequential first.

        You are not watching while your agents work, so the useful question is
        not "what is happening" but "what did my agents write down, and is it
        right?" Each entry carries the reason it surfaced so the decision is a
        few seconds of reading rather than an investigation.
        """
        rows = self.store.db.query(
            "SELECT uri, title, category, abstract, confidence, hits, updated"
            " FROM nodes WHERE scope=? AND kind=? ORDER BY updated DESC",
            (project, KIND_MEMORY),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            uri = Uri.parse(row["uri"])
            if uri.parts[:1] == ("_archive",):
                continue
            node = self.store.read_node(uri)
            if node is None:
                continue
            impact = self.tracer.scores_for_uri(row["uri"])
            reasons = self._review_reasons(node, impact)
            if not reasons:
                continue
            out.append(
                {
                    "uri": row["uri"],
                    "title": node.title,
                    "category": node.category,
                    "abstract": node.abstract,
                    "confidence": round(node.confidence, 3),
                    "hits": node.hits,
                    "updated": node.updated,
                    "origin": node.extra.get("origin", "distilled"),
                    "reviewed": bool(node.extra.get("reviewed")),
                    "conflict": node.extra.get("conflict"),
                    "uses": impact["uses"],
                    "avg_score": impact["avg_score"],
                    "reasons": reasons,
                    "priority": max(_REVIEW_WEIGHT.get(r, 0) for r in reasons),
                    "sources": node.sources[-3:],
                }
            )
        out.sort(key=lambda d: (-d["priority"], -d["uses"], d["updated"]))
        return out[:limit]

    def _review_reasons(self, node: Node, impact: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        if node.extra.get("conflict"):
            reasons.append("conflict")
        if impact["avg_score"] is not None and impact["avg_score"] < 0.4:
            reasons.append("harmful")
        if not node.extra.get("reviewed"):
            reasons.append("unconfirmed")
        if impact["uses"] >= 3 and impact["scored"] == 0:
            # Leaned on repeatedly, never judged. Popular is not the same as
            # correct, and this is where a wrong belief quietly compounds.
            reasons.append("unproven")
        if node.confidence <= self.config.learn.archive_below + 0.05:
            reasons.append("fading")
        return reasons

    def review_summary(self, project: str = "") -> dict[str, Any]:
        """Counts per reason, for a digest you can read in five seconds."""
        projects = [project] if project else self.store.projects()
        out: dict[str, Any] = {"projects": {}, "total": 0, "by_reason": {}}
        for name in projects:
            queue = self.review_queue(name, limit=500)
            counts: dict[str, int] = {}
            for item in queue:
                for reason in item["reasons"]:
                    counts[reason] = counts.get(reason, 0) + 1
                    out["by_reason"][reason] = out["by_reason"].get(reason, 0) + 1
            out["projects"][name] = {"items": len(queue), "by_reason": counts}
            out["total"] += len(queue)
        return out

    def confirm_memory(self, uri: str, confidence: float | None = None) -> dict[str, Any]:
        """Mark a memory human-verified. It stops asking and gains confidence."""
        u = Uri.parse(uri)
        node = self.store.read_node(u)
        if node is None:
            raise KeyError(f"없는 메모리: {uri}")
        node.extra["reviewed"] = True
        node.extra["reviewed_at"] = now_iso()
        node.extra.pop("conflict", None)
        node.confidence = (
            max(0.0, min(1.0, confidence))
            if confidence is not None
            else min(1.0, max(node.confidence, 0.85))
        )
        self.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)
        return {"uri": uri, "reviewed": True, "confidence": node.confidence}

    def edit_memory(
        self,
        uri: str,
        title: str | None = None,
        statement: str | None = None,
        body: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """Correct a memory in place.

        Editing counts as review: you just read it. Changing the category moves
        the file, because the category *is* the directory.
        """
        u = Uri.parse(uri)
        node = self.store.read_node(u)
        if node is None:
            raise KeyError(f"없는 메모리: {uri}")
        project = u.scope

        if category is not None and category != node.category:
            profile = self.store.profile(project)
            if profile.category(category) is None:
                raise ValueError(
                    f"'{category}' 는 이 프로젝트의 카테고리가 아닙니다. "
                    f"사용 가능: {', '.join(profile.category_names())}"
                )
            node.category = category

        if title is not None:
            node.title = title
        if statement is not None:
            node.abstract = truncate_to_tokens(statement, 100)
        if body is not None:
            node.body = body
        if tags is not None:
            node.tags = list(tags)
        if confidence is not None:
            node.confidence = max(0.0, min(1.0, confidence))

        # Tiers are derived, so regenerate them from whatever the text is now.
        node.overview = ""
        abstract, overview = summarize(node.body, node.title, self.store.llm)
        node.overview = overview or node.abstract
        if statement is None and abstract:
            node.abstract = abstract
        node.extra["reviewed"] = True
        node.extra["reviewed_at"] = now_iso()
        node.extra["edited_by_human"] = True
        node.extra.pop("conflict", None)

        target = Uri(project, ("memories", node.category, slugify(node.title, "memory")))
        if target != u:
            self.store.delete_node(u)
            node.uri = target
        self.store.write_node(node, regenerate_tiers=False)
        return {"uri": str(node.uri), "moved_from": uri if target != u else ""}

    def memory_detail(self, uri: str) -> dict[str, Any] | None:
        """Everything the review UI needs about one memory, in one call."""
        node = self.store.read_node(uri)
        if node is None:
            return None
        impact = self.tracer.scores_for_uri(uri)
        u = Uri.parse(uri)
        return {
            "uri": uri,
            "project": u.scope,
            "kind": node.kind,
            "title": node.title,
            "category": node.category,
            "abstract": node.abstract,
            "overview": node.overview,
            "body": node.body,
            "tags": node.tags,
            "confidence": round(node.confidence, 3),
            "hits": node.hits,
            "created": node.created,
            "updated": node.updated,
            "sources": node.sources,
            "origin": node.extra.get("origin", "distilled"),
            "reviewed": bool(node.extra.get("reviewed")),
            "conflict": node.extra.get("conflict"),
            "tokens": {
                "l0": estimate_tokens(node.tier(0)),
                "l1": estimate_tokens(node.tier(1)),
                "l2": estimate_tokens(node.tier(2)),
            },
            "impact": impact,
            "reasons": self._review_reasons(node, impact),
            "path": str(self.store.path_for(u)),
        }

    def add_resource(
        self,
        project: str,
        name: str,
        text: str,
        title: str = "",
        tags: list[str] | None = None,
    ) -> Node:
        """Ingest reference material (docs, specs, transcripts) as a tiered node."""
        self.store.ensure_project(project)
        node = Node(
            uri=Uri(project, ("resources", slugify(name, "resource"))),
            kind="resource",
            title=title or name,
            category="resource",
            body=text,
            tags=tags or [],
            confidence=0.6,
        )
        return self.store.write_node(node)

    # ==================================================================
    # the ask/commit loop
    # ==================================================================
    def prepare(
        self,
        project: str,
        question: str,
        prompt: str = "",
        values: dict[str, Any] | None = None,
        use_cache: bool = True,
        budget: BudgetConfig | None = None,
        kinds: Iterable[str] | None = None,
        include_global: bool = True,
        max_tier: int = 2,
        reinforce: bool = True,
        agent: str = "",
        session_id: str = "",
    ) -> Prepared:
        """Assemble the best context available for ``question``, and trace it."""
        self.store.ensure_project(project)
        trace_id, t0 = self.tracer.start_trace(
            project,
            "prepare",
            input_text=question,
            agent=agent,
            session_id=session_id,
            metadata={"prompt": prompt, "max_tier": max_tier},
        )
        if agent:
            self.tracer.touch_agent(agent, project=project)

        if use_cache:
            obs = self.tracer.start_observation(
                trace_id, "cache", "cache_lookup", input_text=question
            )
            hit = self.sessions.cache_lookup(project, question)
            self.tracer.end_observation(
                obs,
                output_text=hit.kind if hit else "miss",
                metadata={"similarity": round(hit.similarity, 4) if hit else 0.0},
            )
            if hit is not None:
                latency = self.tracer.end_trace(
                    trace_id,
                    t0,
                    output_text=hit.answer,
                    cache_hit=hit.kind,
                    metadata={"similarity": round(hit.similarity, 4)},
                )
                return Prepared(
                    project=project,
                    question=question,
                    system="",
                    context="",
                    user=question,
                    cache_hit=hit,
                    trace_id=trace_id,
                    latency_ms=latency,
                )

        prompt_render: RenderResult | None = None
        prompt_uri = ""
        if prompt:
            prompt_render = self.prompts.render(project, prompt, values or {})
            prompt_uri = prompt_render.uri

        # The retrieval query includes the rendered prompt when present: the
        # prompt often carries the domain words the raw question omits.
        retrieval_query = question if not prompt_render else f"{question}\n{prompt_render.text}"
        # Prompt templates are rendered into the request, not pasted as context;
        # retrieving them here would pay for the same text twice.
        pack_kinds = list(kinds) if kinds else [KIND_MEMORY, KIND_SESSION, "resource"]
        obs = self.tracer.start_observation(
            trace_id, "retrieval", "pack", input_text=retrieval_query
        )
        packed = self.retriever.pack(
            retrieval_query,
            project,
            budget=budget,
            kinds=pack_kinds,
            include_global=include_global,
            max_tier=max_tier,
        )
        self.tracer.end_observation(
            obs,
            output_text=f"{len(packed.items)}개 항목 / {packed.tokens} 토큰",
            tokens_in=packed.tokens,
            metadata={
                "considered": packed.considered,
                "baseline_tokens": packed.baseline_tokens,
                "dump_tokens": packed.dump_tokens,
                "trace": packed.trace,
            },
        )
        self.tracer.record_context(
            trace_id,
            project,
            [
                {"uri": i.uri, "tier": i.tier, "tokens": i.tokens, "score": i.score}
                for i in packed.items
            ],
        )

        refs = self._references(project, question, packed)
        system = SYSTEM_PREFIX
        if packed.text:
            system += "\n\n# 컨텍스트\n" + packed.text
        user_parts = []
        if prompt_render is not None:
            user_parts.append(prompt_render.text)
        user_parts.append(question)
        user = "\n\n".join(p for p in user_parts if p.strip())

        self.store.db.log_usage(
            now_iso(),
            project,
            "pack",
            tokens_in=estimate_tokens(system) + estimate_tokens(user),
            tokens_saved=packed.saved_tokens,
            baseline=packed.baseline_tokens,
            detail={
                "included": len(packed.items),
                "considered": packed.considered,
                "prompt": prompt or "",
            },
        )
        if reinforce and packed.items:
            self.learner.reinforce(project, [i.uri for i in packed.items])

        latency = self.tracer.end_trace(
            trace_id,
            t0,
            output_text=packed.text[:2000],
            tokens_in=estimate_tokens(system) + estimate_tokens(user),
            metadata={
                "included": len(packed.items),
                "tokens": packed.tokens,
                "saved_ratio": round(packed.saved_ratio, 4),
            },
        )
        return Prepared(
            project=project,
            question=question,
            system=system,
            context=packed.text,
            user=user,
            packed=packed,
            prompt_uri=prompt_uri,
            prompt_render=prompt_render,
            references=refs,
            trace_id=trace_id,
            latency_ms=latency,
        )

    def _references(
        self, project: str, question: str, packed: PackedContext
    ) -> list[dict[str, Any]]:
        """Past sessions worth showing the human, even if not worth sending."""
        thr = self.config.budget.reference_threshold
        included = {i.uri for i in packed.items}
        out: list[dict[str, Any]] = []
        candidates, _trace = self.retriever.search(
            question, project, kinds=[KIND_SESSION], limit=5, include_global=False
        )
        for c in candidates:
            if c.score < thr or str(c.uri) in included:
                continue
            out.append(
                {
                    "uri": str(c.uri),
                    "title": c.title,
                    "question": c.abstract,
                    "score": round(c.score, 4),
                    "tokens_if_loaded": c.tokens.get(2, 0),
                }
            )
        return out

    def commit(
        self,
        project: str,
        question: str,
        answer: str,
        model: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        prompt_uri: str = "",
        tags: list[str] | None = None,
        outcome: str = "",
        distill: bool | None = None,
        trace_id: str = "",
        latency_ms: int = 0,
        agent: str = "",
    ) -> dict[str, Any]:
        """Record an exchange and (by default) fold it into memory.

        Pass the ``trace_id`` from ``prepare`` so the generation lands on the
        same trace as the retrieval that fed it — otherwise you can see that a
        request was slow but not which half was slow.
        """
        if trace_id:
            self.tracer.event(
                trace_id,
                "generation",
                model or "generation",
                output_text=answer,
                latency_ms=latency_ms,
                metadata={
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "outcome": outcome,
                },
            )
            self.store.db.execute(
                "UPDATE traces SET output=?, tokens_in=tokens_in+?, tokens_out=?,"
                " status=?, total_ms=latency_ms+? WHERE id=?",
                (
                    answer[:8000],
                    int(tokens_in),
                    int(tokens_out),
                    "error" if outcome == "실패" else "ok",
                    int(latency_ms),
                    trace_id,
                ),
            )
            self.store.db.commit()

        node = self.sessions.record(
            project,
            question,
            answer,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            prompt_uri=prompt_uri,
            tags=tags,
            outcome=outcome,
        )
        result: dict[str, Any] = {"session": str(node.uri), "trace_id": trace_id}
        should = self.config.learn.auto_distill if distill is None else distill
        if should:
            obs = self.tracer.start_observation(trace_id or "", "distill", "distill") if trace_id else None
            report = self.learner.distill(project, limit=5)
            if obs is not None:
                self.tracer.end_observation(
                    obs,
                    output_text=f"신규 {len(report.created)} / 병합 {len(report.merged)}",
                    metadata={"created": report.created, "merged": report.merged},
                )
            result["distill"] = report.to_dict()
        if agent:
            self.tracer.touch_agent(agent, project=project)
        return result

    # ==================================================================
    # quality loop
    # ==================================================================
    def score(
        self,
        trace_id: str,
        name: str = "helpfulness",
        value: float = 1.0,
        comment: str = "",
        source: str = "human",
        apply_to_memory: bool = True,
    ) -> dict[str, Any]:
        """Record a judgement about a trace, and let it move memory confidence.

        This is the difference between monitoring and learning. A score names an
        outcome; the context that was in the room for that outcome is known, so
        the same signal that fills a dashboard can also promote the memories that
        helped and demote the ones that were present when things went wrong.
        """
        row = self.store.db.one("SELECT scope FROM traces WHERE id = ?", (trace_id,))
        if row is None:
            raise KeyError(f"없는 트레이스: {trace_id}")
        scope = row["scope"]
        score_id = self.tracer.add_score(
            scope, name, value, trace_id=trace_id, comment=comment, source=source
        )

        moved: list[str] = []
        if apply_to_memory:
            used = self.tracer.context_of(trace_id)
            # Only memories move: sessions and resources are records, not beliefs.
            delta = self._score_delta(value)
            for item in used:
                uri = Uri.parse(item["uri"])
                if uri.kind_dir != "memories":
                    continue
                node = self.store.read_node(uri)
                if node is None:
                    continue
                node.confidence = max(0.0, min(1.0, node.confidence + delta))
                self.store.write_node(
                    node, regenerate_tiers=False, reinforce_dirs=False
                )
                moved.append(item["uri"])
                if node.confidence < self.config.learn.archive_below:
                    self.store.archive_node(uri, reason=f"low score ({name})")
        return {
            "score_id": score_id,
            "trace_id": trace_id,
            "name": name,
            "value": value,
            "memories_adjusted": moved,
        }

    @staticmethod
    def _score_delta(value: float) -> float:
        """Map a 0..1 score onto a confidence nudge centred on neutral (0.5).

        Deliberately asymmetric: a bad outcome costs more than a good one earns,
        because a confidently wrong memory does more damage than a missing one.
        """
        centred = float(value) - 0.5
        return centred * (0.24 if centred >= 0 else 0.44)

    # ==================================================================
    # observability reads
    # ==================================================================
    def traces(
        self,
        project: str = "",
        limit: int = 50,
        cursor: str = "",
        name: str = "",
        min_latency: int = 0,
    ) -> list[dict[str, Any]]:
        return self.tracer.list_traces(project, limit, cursor, name, min_latency)

    def trace(self, trace_id: str) -> dict[str, Any] | None:
        return self.tracer.get_trace(trace_id)

    def metrics(self, project: str = "", days: int = 7) -> dict[str, Any]:
        return metrics(self.store.db, project, days)

    def timeseries(self, project: str = "", days: int = 14) -> list[dict[str, Any]]:
        return timeseries(self.store.db, project, days)

    def memory_impact(self, project: str, limit: int = 20) -> list[dict[str, Any]]:
        return memory_impact(self.store.db, project, limit)

    def agents(self) -> list[dict[str, Any]]:
        return self.tracer.agents()

    # ==================================================================
    # project resolution (same repo -> same project, from any machine)
    # ==================================================================
    def bind_alias(self, alias: str, project: str, kind: str = "repo") -> None:
        self.store.ensure_project(project)
        self.tracer.bind_alias(alias, project, kind)

    def aliases(self, project: str = "") -> list[dict[str, Any]]:
        return self.tracer.aliases(project)

    def resolve_project(
        self, project: str = "", repo: str = "", path: str = "", create: bool = False
    ) -> dict[str, Any]:
        """Work out which project an agent is talking about.

        An agent running in a checkout knows its git remote but not what you
        named the project here. Binding the remote once makes every later call
        from any machine resolve to the same context.
        """
        if project and self.store.project_exists(project):
            return {"project": project, "resolved_by": "name", "created": False}

        for alias, kind in ((repo, "repo"), (path, "path")):
            if not alias:
                continue
            found = self.tracer.resolve_alias(_normalise_alias(alias))
            if found and self.store.project_exists(found):
                return {"project": found, "resolved_by": kind, "created": False}

        if project:
            if not create:
                return {"project": "", "resolved_by": "", "created": False,
                        "candidates": self.store.projects()}
            self.store.ensure_project(project)
            for alias, kind in ((repo, "repo"), (path, "path")):
                if alias:
                    self.tracer.bind_alias(_normalise_alias(alias), project, kind)
            return {"project": project, "resolved_by": "created", "created": True}

        guess = _project_from_alias(repo or path)
        if guess and self.store.project_exists(guess):
            return {"project": guess, "resolved_by": "guess", "created": False}
        if guess and create:
            self.store.ensure_project(guess)
            if repo:
                self.tracer.bind_alias(_normalise_alias(repo), guess, "repo")
            return {"project": guess, "resolved_by": "guess", "created": True}
        return {
            "project": "",
            "resolved_by": "",
            "created": False,
            "candidates": self.store.projects(),
        }

    def distill(self, project: str, limit: int = 20) -> DistillReport:
        return self.learner.distill(project, limit=limit)

    def feedback(
        self, project: str, uri: str, helpful: bool, note: str = ""
    ) -> Node | None:
        return self.learner.feedback(project, uri, helpful, note)

    # ==================================================================
    # browsing & reporting
    # ==================================================================
    def ls(self, uri: str) -> dict[str, Any]:
        return self.store.ls(uri)

    def tree(self, uri: str, depth: int = 3) -> dict[str, Any]:
        return self.store.tree(uri, depth)

    def find(
        self, query: str, project: str, kinds: Iterable[str] | None = None, limit: int = 15
    ) -> dict[str, Any]:
        candidates, trace = self.retriever.search(query, project, kinds=kinds, limit=limit)
        return {
            "query": query,
            "project": project,
            "results": [
                {
                    "uri": str(c.uri),
                    "kind": c.kind,
                    "category": c.category,
                    "title": c.title,
                    "abstract": c.abstract,
                    "score": round(c.score, 4),
                    "signals": {
                        "vector": round(c.vec_score, 4),
                        "lexical": round(c.fts_score, 4),
                        "dir": round(c.dir_score, 4),
                        "confidence": round(c.confidence, 3),
                    },
                    "tokens": c.tokens,
                }
                for c in candidates
            ],
            "trace": trace,
        }

    def grep(self, term: str, uri: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        return self.store.grep(term, uri, limit)

    def read(self, uri: str, tier: int = 2) -> dict[str, Any] | None:
        node = self.store.read_node(uri)
        if node is None:
            return None
        return {
            "uri": str(node.uri),
            "kind": node.kind,
            "title": node.title,
            "category": node.category,
            "tier": tier,
            "text": node.tier(tier),
            "confidence": node.confidence,
            "hits": node.hits,
            "tags": node.tags,
            "sources": node.sources,
            "updated": node.updated,
            "extra": node.extra,
        }

    def stats(self, project: str | None = None) -> dict[str, Any]:
        return self.store.stats(project)

    def report(self, project: str | None = None, days: int = 0) -> UsageReport:
        return usage_report(self.store.db, project, days)

    def reindex(self, project: str | None = None) -> dict[str, int]:
        return self.store.reindex(project)

    # ==================================================================
    def cache_list(self, project: str, limit: int = 30) -> list[dict[str, Any]]:
        return self.sessions.cache_list(project, limit)

    def cache_clear(self, project: str) -> int:
        return self.sessions.cache_clear(project)

    def recent_sessions(self, project: str, limit: int = 10) -> list[dict[str, Any]]:
        return self.sessions.recent(project, limit)


def _normalise_alias(alias: str) -> str:
    """Make git remotes comparable across ssh/https and trailing .git."""
    a = (alias or "").strip()
    a = a.removesuffix(".git")
    for prefix in ("git@", "https://", "http://", "ssh://git@", "ssh://"):
        if a.startswith(prefix):
            a = a[len(prefix) :]
            break
    return a.replace(":", "/").rstrip("/").lower()


def _project_from_alias(alias: str) -> str:
    norm = _normalise_alias(alias)
    return norm.rsplit("/", 1)[-1] if norm else ""
