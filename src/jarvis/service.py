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
from .tokens import estimate_tokens

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
        }


class Jarvis:
    def __init__(self, config: Config | None = None, home: str | None = None):
        self.config = config or Config.load(home)
        self.store = Store(self.config)
        self.retriever = Retriever(self.store)
        self.prompts = PromptLibrary(self.store)
        self.sessions = SessionLog(self.store)
        self.learner = Learner(self.store, self.sessions)
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
    ) -> Prepared:
        """Build the cheapest request that can still answer ``question``."""
        self.store.ensure_project(project)

        if use_cache:
            hit = self.sessions.cache_lookup(project, question)
            if hit is not None:
                return Prepared(
                    project=project,
                    question=question,
                    system="",
                    context="",
                    user=question,
                    cache_hit=hit,
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
        packed = self.retriever.pack(
            retrieval_query,
            project,
            budget=budget,
            kinds=pack_kinds,
            include_global=include_global,
            max_tier=max_tier,
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
    ) -> dict[str, Any]:
        """Record an exchange and (by default) fold it into memory."""
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
        result: dict[str, Any] = {"session": str(node.uri)}
        should = self.config.learn.auto_distill if distill is None else distill
        if should:
            result["distill"] = self.learner.distill(project, limit=5).to_dict()
        return result

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
