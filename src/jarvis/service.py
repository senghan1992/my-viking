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

import functools
from dataclasses import dataclass, field
from typing import Any, Iterable

from .budget import UsageReport, usage_report
import re

from .config import BudgetConfig, Config
from .learn import DistillReport, Learner, MemoryCandidate
from .models import KIND_MEMORY, KIND_PROMPT, KIND_SESSION, Node, Uri, now_iso, slugify
from .profiles import MemoryProfile, builtin, template_summary
from .prompts import PromptLibrary, RenderResult
from .redact import redact
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

# A negative score reaches only the top-ranked context and anything effectively
# tied with it. Retrieval hands over a ranked list and a wrong answer is driven
# by what came first; at a looser floor (0.6) a merely adjacent memory took more
# damage than the actual culprit, because in a small corpus the scores sit close
# together and a *relative* threshold stops separating them.
_BLAME_FLOOR = 0.9

# A memory is flagged "harmful" once the demotion attributed to it (summed over
# every bad outcome it actually drove) crosses this. One clearly-bad judgement on
# a top-ranked memory lands around -0.06, so this catches a single real culprit
# while ignoring the tiny nudges a memory picks up from riding along.
_HARM_THRESHOLD = -0.05
# Attributed harm at which trust() retracts a memory to "contested" while its
# challenge streak is open. One inferred blame lands around -0.07 to -0.09; a
# stated bad score around -0.2. So this needs a stated verdict or two inferred
# ones — a single misread follow-up must not retract a fact.
_HARM_CONTESTED = -0.15

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
    # Pitfalls and incidents that matched, pulled out of the context so the
    # agent leads with them instead of skimming past them.
    warnings: list[dict[str, Any]] = field(default_factory=list)
    # Relevant knowledge from *other* projects, always labelled as such.
    from_other_projects: list[dict[str, Any]] = field(default_factory=list)
    # Packed items that are *not* settled fact (contested/stale/tentative/fresh),
    # so the injection can tell the agent which lines to verify before relying on
    # them instead of presenting everything as confirmed.
    trust_notes: list[dict[str, Any]] = field(default_factory=list)
    trace_id: str = ""
    session_id: str = ""
    latency_ms: int = 0
    # Present only on the first call of a sitting: what happened here recently,
    # so a fresh session starts oriented instead of re-deriving the project.
    catch_up: dict[str, Any] | None = None

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
            "warnings": self.warnings,
            "from_other_projects": self.from_other_projects,
            "trust_notes": self.trust_notes,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "latency_ms": self.latency_ms,
            "catch_up": self.catch_up,
        }


def _locked(fn):
    """Serialise a compound mutation on the shared connection.

    HTTP request handlers run in a threadpool over one ``Jarvis``. SQLite
    serialises single statements, but a mutation here is several statements
    plus file writes; without this, one thread's commit() publishes another's
    half-finished transaction. Reentrant, so mutators can call each other, and
    reads take no lock at all. Writers finish in milliseconds — serialising
    them costs nothing a personal server can feel.
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self.store.db.lock:
            return fn(self, *args, **kwargs)

    return wrapper


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
    @_locked
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
            by_kind = s.get("by_kind", {})
            last = self.store.db.one(
                "SELECT MAX(started) AS ts, COUNT(*) AS n FROM traces WHERE scope=?",
                (name,),
            )
            out.append(
                {
                    "project": name,
                    "template": prof.template,
                    "description": prof.description,
                    "categories": prof.category_names(),
                    "warn_categories": sorted(prof.warn_categories()),
                    "nodes": s.get("total_nodes", 0),
                    # Split out because "how much does it know" and "how much
                    # has it been used" are the two things a project card is
                    # actually asked to answer.
                    "memories": by_kind.get(KIND_MEMORY, {}).get("count", 0),
                    "sessions": by_kind.get(KIND_SESSION, {}).get("count", 0),
                    "prompts": by_kind.get(KIND_PROMPT, {}).get("count", 0),
                    "cache_entries": s.get("cache_entries", 0),
                    "tasks": (last["n"] if last else 0) or 0,
                    "first_try_rate": self.metrics(name, days=0)["first_try_rate"],
                    "last_active": (last["ts"] if last else "") or "",
                    "aliases": [a["alias"] for a in self.aliases(name)],
                }
            )
        out.sort(key=lambda d: (d["last_active"] or "", d["project"]), reverse=True)
        return out

    def profile(self, project: str) -> MemoryProfile:
        return self.store.profile(project)

    @_locked
    def set_profile(self, project: str, profile: MemoryProfile) -> MemoryProfile:
        self.store.save_profile(project, profile)
        return profile

    @_locked
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

    @_locked
    def delete_project(self, project: str) -> bool:
        return self.store.delete_project(project)

    # ==================================================================
    # prompts
    # ==================================================================
    @_locked
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

    @_locked
    def rollback_prompt(self, project: str, name: str, version: str) -> Node | None:
        return self.prompts.rollback(project, name, version)

    @_locked
    def delete_prompt(self, project: str, name: str) -> bool:
        return self.prompts.delete(project, name)

    # ==================================================================
    # memory (manual)
    # ==================================================================
    @_locked
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
        uri, action, _conflict = self.learner.absorb(project, cand, profile)
        # A hand-written memory is a strong signal. If it corrects a belief a
        # recent bad outcome blamed, let it supersede the old one right away:
        # a person asserted it, so it does not wait for an outcome.
        if uri is not None and action in ("created", "merged"):
            self._reconcile_correction(project, [str(uri)], immediate=True)
        return uri  # type: ignore[return-value]

    @_locked
    def forget(self, uri: str, archive: bool = True) -> bool:
        if archive:
            return self.store.archive_node(uri, reason="manual") is not None
        return self.store.delete_node(uri)

    # ------------------------------------------------------------------
    # trust — is this still a fact, or a hypothesis due for a recheck?
    # ------------------------------------------------------------------
    def trust(
        self, node: Node, impact: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """How far this memory has earned the right to be treated as fact.

        Injected knowledge is only safe if the agent can tell what is settled
        from what is provisional. A belief that keeps riding in bad outcomes, or
        that nothing has confirmed in months, should be *offered* for checking
        rather than *asserted* — so the agent verifies it instead of building on
        it. Re-confirmation heals it: a good outcome resets the challenge streak
        and the item earns its way back into the answer key. The store's answer
        key thus updates itself in both directions, which is the whole point.

        Status, worst first: ``superseded`` (replaced by a later correction),
        ``contested`` (recent outcomes turned against it), ``tentative`` (never
        rose above the fact line), ``stale`` (nothing has touched it in a long
        time), ``fresh`` (just written, not yet tested), ``established``.
        """
        cfg = self.config.learn
        extra = node.extra
        contradictions = int(extra.get("contradictions", 0))
        confirmations = int(extra.get("confirmations", 0))
        streak = int(extra.get("challenge_streak", 0))
        last_confirmed = str(extra.get("last_confirmed", "") or "")
        conflict = extra.get("conflict") or {}
        superseded_by = str(extra.get("superseded_by", "") or "")
        corrects = list(extra.get("corrects", []))
        harm = (impact or {}).get("harm")
        age = _days_since(
            max(node.updated or "", node.last_used or "", last_confirmed)
        )

        def out(status: str, label: str) -> dict[str, Any]:
            return {
                "status": status,
                "label": label,
                "established": status == "established",
                "confidence": round(node.confidence, 3),
                "contradictions": contradictions,
                "confirmations": confirmations,
                "streak": streak,
                "last_confirmed": last_confirmed,
                "age_days": round(age, 1),
                "corrects": corrects,
                "superseded_by": superseded_by,
            }

        if superseded_by:
            return out("superseded", "대체됨 · 최신 기록으로 갱신됨")
        if extra.get("challenged_by"):
            return out("contested", "확인 필요 · 교정 후보가 있음")
        if conflict and conflict.get("resolution") != "superseded":
            return out("contested", "확인 필요 · 다른 기록과 충돌")
        if streak >= cfg.contested_after:
            return out("contested", f"확인 필요 · 최근 {streak}회 어긋남")
        # Attributed harm is a lifetime sum, so on its own it would be a ratchet:
        # one misread follow-up and the memory reads "contested" for ever. It
        # counts only while the challenge is still open (streak > 0) and only
        # when it amounts to more than a single inferred blame — a stated "this
        # was wrong" or two inferred ones.
        if harm is not None and harm <= _HARM_CONTESTED and streak > 0:
            return out("contested", "확인 필요 · 최근 나쁜 결과에 관여")
        if node.confidence < 0.5:
            return out("tentative", f"미확정 · 신뢰 {node.confidence:.2f}")
        if cfg.stale_days and age > cfg.stale_days and not extra.get("reviewed"):
            return out("stale", f"오래됨 · {age:.0f}일간 확인·사용 없음")
        if node.confidence < cfg.solid_confidence:
            return out("tentative", f"미확정 · 신뢰 {node.confidence:.2f}")
        # Being retrieved is not evidence of being right, and neither is age.
        # A distilled memory stays "fresh" until something has actually gone
        # well with it in the room: a confirmation, a person reviewing it, or
        # enough follow-ups that moved on without complaint (settled).
        if (
            extra.get("origin", "distilled") != "manual"
            and confirmations == 0
            and int(extra.get("settled", 0)) < cfg.settled_after
            and not extra.get("reviewed")
        ):
            return out("fresh", "최근 기록 · 아직 검증 안 됨")
        return out("established", "확립")

    def memories(
        self,
        project: str,
        category: str = "",
        limit: int = 100,
        with_trust: bool = False,
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
        out = [
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
        if with_trust:
            # One node read + one impact query per row. Only callers that render
            # trust (the dashboard, the briefing) ask for it, so the plain list
            # stays a single query.
            for m in out:
                node = self.store.read_node(Uri.parse(m["uri"]))
                if node is not None:
                    m["trust"] = self.trust(
                        node, self.tracer.scores_for_uri(m["uri"])
                    )
        return out

    # ------------------------------------------------------------------
    # you — preferences that follow you into every project
    # ------------------------------------------------------------------
    def remember_about_me(
        self,
        statement: str,
        title: str = "",
        category: str = "preferences",
        confidence: float = 0.9,
    ) -> Uri:
        """Record something true of you rather than of one project.

        These live in ``jarvis://global`` and are retrieved alongside every
        project's own memory. This is the difference between a context database
        and an assistant that is *yours*: how you want things done should not
        need re-stating in each repo.
        """
        self.store.ensure_project(GLOBAL_SCOPE)
        profile = self.store.profile(GLOBAL_SCOPE)
        if profile.category(category) is None:
            raise ValueError(
                f"'{category}' 는 전역 스코프의 카테고리가 아닙니다. "
                f"사용 가능: {', '.join(profile.category_names())}"
            )
        return self.remember(
            GLOBAL_SCOPE,
            category,
            title or _title_of(statement),
            statement,
            confidence=confidence,
            tags=["나"],
        )

    def about_me(self) -> list[dict[str, Any]]:
        return self.memories(GLOBAL_SCOPE)

    def forget_about_me(self, uri: str) -> bool:
        if not Uri.parse(uri).is_global:
            raise ValueError("전역 메모리가 아닙니다")
        return self.forget(uri, archive=True)

    # ------------------------------------------------------------------
    # briefing — what to know when you come back to a project
    # ------------------------------------------------------------------
    def brief(self, project: str, limit: int = 8) -> dict[str, Any]:
        """A short read for returning to a project after a while.

        Not a dump of everything: the highest-confidence knowledge, the standing
        warnings, what is unresolved, and what changed recently.
        """
        profile = self.store.profile(project)
        warn_cats = profile.warn_categories()
        mems = self.memories(project, limit=500, with_trust=True)
        review = self.review_queue(project, limit=200)

        # Anything with an open contradiction — or that recent outcomes have
        # turned against — is not established knowledge, no matter how confident
        # its number still looks. Listing both sides of a disagreement, or a
        # belief the last few tasks disproved, under "확립된 지식" is worse than
        # not listing it at all. This is where a retracted fact leaves the
        # injected answer key.
        disputed = {
            it["uri"] for it in review if {"conflict", "harmful"} & set(it["reasons"])
        }
        disputed |= {
            m["uri"]
            for m in mems
            if m.get("trust", {}).get("status") in ("contested", "superseded")
        }

        def _est(m: dict[str, Any]) -> bool:
            t = m.get("trust")
            return t["established"] if t else m["confidence"] >= 0.6

        def top(pred, n=limit):
            return [
                {
                    "uri": m["uri"],
                    "title": m["title"],
                    "category": m["category"],
                    "abstract": m["abstract"],
                    "confidence": m["confidence"],
                    "trust": m.get("trust"),
                }
                for m in mems
                if pred(m)
            ][:n]

        established = [
            m
            for m in mems
            if m["uri"] not in disputed
            and m["category"] not in warn_cats
            and _est(m)
        ]
        return {
            "project": project,
            "template": profile.template,
            "description": profile.description,
            "totals": {
                "memories": len(mems),
                "established": len(established),
                "disputed": len(disputed),
                "needs_review": len(review),
            },
            "know": top(
                lambda m: m["uri"] not in disputed
                and m["category"] not in warn_cats
                and _est(m)
            ),
            "warnings": top(
                lambda m: m["category"] in warn_cats
                and m.get("trust", {}).get("status") != "superseded"
            ),
            "unresolved": [
                {
                    "uri": it["uri"],
                    "title": it["title"],
                    "reasons": it["reasons"],
                }
                for it in review
                if it["priority"] >= 4
            ][:limit],
            "open_threads": self.tracer.unresolved_threads(project, limit=5),
            "recent_work": self.recent_work(project, limit=4),
            "recently_learned": self.recently_learned(
                project, days=14, limit=limit, established_only=True
            ),
            "recent_sessions": self.recent_sessions(project, limit=5),
            "prompts": [
                {"name": p["name"], "description": p["description"], "uses": p["uses"]}
                for p in self.list_prompts(project)
            ][:limit],
        }

    def catch_up(
        self, project: str, limit: int = 3, exclude_session: str = ""
    ) -> dict[str, Any]:
        """A compact orientation for the start of a sitting.

        Deliberately small: it rides along on the first request of a session, so
        it has to be worth its tokens. Last threads, what is new, what is
        unsettled — enough to stop the agent re-discovering the project.
        """
        # The sitting that is asking must not appear in its own catch-up.
        work = self.recent_work(
            project, limit=limit, exclude_session=exclude_session
        )
        learned = self.recently_learned(
            project, days=14, limit=5, established_only=True
        )
        unresolved = [
            {"uri": it["uri"], "title": it["title"], "reasons": it["reasons"]}
            for it in self.review_queue(project, limit=20)
            if it["priority"] >= 4
        ][:3]
        return {
            "last_worked_on": work[0]["started"] if work else "",
            # The single most useful thing to hand a fresh session: what was
            # asked more than once and may still not be done.
            "open_threads": self.tracer.unresolved_threads(project, limit=3),
            "recent_threads": [
                {
                    "at": w["started"],
                    "agent": w["agent"],
                    "questions": [t["question"] for t in w["work"]][:4],
                    "score": w["avg_score"],
                }
                for w in work
            ],
            "new_since_last_time": [
                {"category": x["category"], "title": x["title"], "abstract": x["abstract"]}
                for x in learned
            ],
            "unsettled": unresolved,
        }

    def recent_work(
        self, project: str, limit: int = 4, exclude_session: str = ""
    ) -> list[dict[str, Any]]:
        """The last few sittings on this project, condensed.

        This is what "catch me up" means in practice: not every question ever
        asked, but the recent threads — what was being worked on, what came out
        of it, and whether it went well.
        """
        out = []
        for sess in self.tracer.work_sessions(project, limit=limit + 1):
            if exclude_session and sess["session_id"] == exclude_session:
                continue
            if len(out) >= limit:
                break
            out.append(
                {
                    "session_id": sess["session_id"],
                    "agent": sess["agent"],
                    "started": sess["started"],
                    "traces": sess["traces"],
                    "avg_score": sess["avg_score"],
                    "files": sess.get("files") or [],
                    "work": [
                        {
                            "question": (w["question"] or "")[:180],
                            "answer": (w["answer"] or "")[:280],
                            "score": w["score"],
                            "trace_id": w["trace_id"],
                        }
                        for w in sess["work"]
                    ],
                }
            )
        return out

    def recently_learned(
        self,
        project: str,
        days: int = 14,
        limit: int = 8,
        established_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Knowledge added or changed lately — what is new since you last looked.

        ``established_only`` drops the project's fallback category, which by
        definition holds exchanges that matched no rule. A briefing should say
        what got *decided*, not replay the log — and confidence is the wrong
        filter for that, since being retrieved often raises it regardless.
        """
        rows = self.store.db.query(
            "SELECT uri, title, category, abstract, confidence, created, updated"
            " FROM nodes WHERE scope=? AND kind=?"
            " AND updated >= datetime('now', ?) ORDER BY updated DESC LIMIT ?",
            (project, KIND_MEMORY, f"-{int(days)} days", limit * 5),
        )
        skip = (
            {self.store.profile(project).fallback_category()}
            if established_only
            else set()
        )
        out = []
        for row in rows:
            uri = Uri.parse(row["uri"])
            if uri.parts[:1] == ("_archive",):
                continue
            if row["category"] in skip:
                continue
            out.append(
                {
                    "uri": row["uri"],
                    "title": row["title"],
                    "category": row["category"],
                    "abstract": row["abstract"],
                    "confidence": round(row["confidence"], 3),
                    "new": (row["created"] or "")[:10] == (row["updated"] or "")[:10],
                    "updated": row["updated"],
                }
            )
            if len(out) >= limit:
                break
        return out

    def work_sessions(self, project: str = "", limit: int = 20) -> list[dict[str, Any]]:
        return self.tracer.work_sessions(project, limit)

    def open_threads(self, project: str, limit: int = 5) -> list[dict[str, Any]]:
        return self.tracer.unresolved_threads(project, limit)

    def digest(self, days: int = 7) -> dict[str, Any]:
        """Everything that happened across projects, for a periodic read."""
        projects = self.store.projects()
        out: dict[str, Any] = {
            "days": days,
            "projects": [],
            "review_total": 0,
            "about_me": len(self.about_me()),
        }
        for name in projects:
            m = self.metrics(name, days=days)
            review = self.review_summary(name)["projects"].get(name, {})
            new_mems = self.store.db.one(
                "SELECT COUNT(*) c FROM nodes WHERE scope=? AND kind=?"
                " AND created >= datetime('now', ?)",
                (name, KIND_MEMORY, f"-{int(days)} days"),
            )
            entry = {
                "project": name,
                "traces": m["traces"],
                "reuse_rate": m["reuse"]["rate"],
                "answer_p50_ms": m["answer_ms"]["p50"],
                "scores": m["scores"],
                "new_memories": (new_mems["c"] if new_mems else 0) or 0,
                "needs_review": review.get("items", 0),
                "review_by_reason": review.get("by_reason", {}),
            }
            out["review_total"] += entry["needs_review"]
            out["projects"].append(entry)
        out["projects"].sort(key=lambda d: -d["needs_review"])
        return out

    @_locked
    def maintain(self, days_unused: int = 0) -> dict[str, Any]:
        """Run the housekeeping the learning loop needs: distill, decay, cap.

        Meant for a scheduler. ``jv commit`` already distills the session it just
        recorded, but sessions arriving over HTTP with ``distill=false``, and the
        confidence decay that keeps the store from growing forever, need a
        periodic sweep.
        """
        results = []
        for name in self.store.projects():
            report = self.learner.distill(name, limit=50)
            results.append(report.to_dict())
        return {"projects": results, "retention": self.prune_records()}

    @_locked
    def prune_records(self) -> dict[str, int]:
        """Apply retention to the record layer (traces, accounting, cache).

        Memories decay and cap themselves; this is the same discipline for
        observability data, which otherwise grows for as long as the server
        stays up — and the cache's near-miss scan pays for every stale row on
        every prompt.
        """
        r = self.config.retention
        result = {
            "traces": self.tracer.prune(r.traces_days),
            "sessions": self.store.prune_sessions(r.sessions_days),
            "archive": self.store.purge_archive(r.archive_days),
            "usage": self.store.db.prune_usage(r.usage_days),
            "cache": self.sessions.cache_prune(r.cache_per_project),
        }
        # Reclaim the space those deletes freed: in WAL mode the file only
        # grows until the log is folded back in.
        self.store.db.checkpoint()
        return result

    # ------------------------------------------------------------------
    # curation — the part you do *after* the work, not during it
    # ------------------------------------------------------------------
    def review_queue(
        self, project: str, limit: int = 50, include_unconfirmed: bool = False
    ) -> list[dict[str, Any]]:
        """Memories automation could not settle by itself, worst first.

        The store is meant to maintain itself: usage reinforces, disuse decays,
        outcomes adjust confidence, and contradictions supersede. So by default
        this lists only what those rules cannot decide — an open contradiction,
        or knowledge that keeps showing up in bad outcomes.

        "An agent wrote this and no human has read it" is the normal state of a
        working knowledge base, not a defect, so it is excluded unless you ask
        for it with ``include_unconfirmed``.
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
            reasons = self._review_reasons(
                node, impact, include_unconfirmed=include_unconfirmed
            )
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
                    "extractor": node.extra.get("extractor", ""),
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

    def _review_reasons(
        self, node: Node, impact: dict[str, Any], include_unconfirmed: bool = False
    ) -> list[str]:
        reasons: list[str] = []
        clash = node.extra.get("conflict") or {}
        # A superseded contradiction is settled; only an open one needs a person.
        if clash and clash.get("resolution") != "superseded":
            reasons.append("conflict")
        # Harmful means *this* memory was blamed for bad outcomes, not merely
        # that it sat in traces that went badly for other reasons. So it keys off
        # the attributed demotion, not the raw average of every co-occurring score.
        if impact.get("harm") is not None and impact["harm"] <= _HARM_THRESHOLD:
            reasons.append("harmful")
        if impact["uses"] >= 3 and impact["scored"] == 0:
            # Leaned on repeatedly, never judged. Popular is not the same as
            # correct, and this is where a wrong belief quietly compounds.
            reasons.append("unproven")
        if include_unconfirmed and not node.extra.get("reviewed"):
            reasons.append("unconfirmed")
        if node.confidence <= self.config.learn.archive_below + 0.05:
            reasons.append("fading")
        return reasons

    def review_summary(
        self, project: str = "", include_unconfirmed: bool = False
    ) -> dict[str, Any]:
        """Counts per reason, for a digest you can read in five seconds."""
        projects = [project] if project else self.store.projects()
        out: dict[str, Any] = {"projects": {}, "total": 0, "by_reason": {}}
        for name in projects:
            queue = self.review_queue(
                name, limit=500, include_unconfirmed=include_unconfirmed
            )
            counts: dict[str, int] = {}
            for item in queue:
                for reason in item["reasons"]:
                    counts[reason] = counts.get(reason, 0) + 1
                    out["by_reason"][reason] = out["by_reason"].get(reason, 0) + 1
            out["projects"][name] = {"items": len(queue), "by_reason": counts}
            out["total"] += len(queue)
        return out

    @_locked
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

    @_locked
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
            "extractor": node.extra.get("extractor", ""),
            "reviewed": bool(node.extra.get("reviewed")),
            "conflict": node.extra.get("conflict"),
            "tokens": {
                "l0": estimate_tokens(node.tier(0)),
                "l1": estimate_tokens(node.tier(1)),
                "l2": estimate_tokens(node.tier(2)),
            },
            "impact": impact,
            "reasons": self._review_reasons(node, impact),
            "trust": self.trust(node, impact),
            "evidence": list(node.extra.get("evidence", []))[-10:],
            "corrects": list(node.extra.get("corrects", [])),
            "superseded_by": node.extra.get("superseded_by", ""),
            "path": str(self.store.path_for(u)),
        }

    @_locked
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
    @_locked
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
        cross_project: bool = True,
    ) -> Prepared:
        """Assemble the best context available for ``question``, and trace it."""
        self.store.ensure_project(project)
        if self.config.learn.redact_secrets:
            # The question is stored on the trace and may be cached with its
            # answer; a pasted credential must not be kept under either.
            question = redact(question)
        # Group work into sittings even when the caller does not track sessions.
        # Requiring an agent to invent and carry a session id is a requirement it
        # will quietly ignore, and then "catch me up on last time" has nothing to
        # group by. One agent's work on one day is a serviceable sitting.
        derived_session = not session_id
        sitting = session_id or _derived_session(agent)
        first_call = not self.tracer.known_session(project, sitting)
        trace_id, t0 = self.tracer.start_trace(
            project,
            "prepare",
            input_text=question,
            agent=agent,
            session_id=sitting,
            metadata={
                "prompt": prompt,
                "max_tier": max_tier,
                "derived_session": derived_session,
            },
        )
        if agent:
            self.tracer.touch_agent(agent, project=project)

        # What is being asked now judges what was answered before.
        inferred = None
        if not first_call:
            inferred = self._infer_previous_outcome(
                project, sitting, question, trace_id
            )
        if inferred is not None:
            self.store.db.execute(
                "UPDATE traces SET metadata = json_set(COALESCE(NULLIF(metadata,''),'{}'),"
                " '$.follows_up_on', ?) WHERE id = ?",
                (inferred["kind"], trace_id),
            )
            self.store.db.commit()

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
                    session_id=sitting,
                    latency_ms=latency,
                    catch_up=(
                        self.catch_up(project, exclude_session=sitting)
                        if first_call
                        else None
                    ),
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
        #
        # Session transcripts are left out for the same reason: whatever was
        # worth keeping from a session has already been distilled into memory,
        # so including both put the same content in the prompt twice — and a
        # transcript quoting a since-superseded instruction made two conflicting
        # rules readable at once. Repeated questions are still answered from the
        # cache, and relevant sessions still surface under ``references``.
        pack_kinds = list(kinds) if kinds else [KIND_MEMORY, "resource"]
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
        catch_up = (
            self.catch_up(project, exclude_session=sitting) if first_call else None
        )
        warnings = self._warnings(project, packed)
        trust_notes = self._trust_notes(packed)
        cross = (
            self._cross_project(project, retrieval_query) if cross_project else []
        )

        system = SYSTEM_PREFIX
        if warnings:
            # A pointer, not a second copy: the packed context already carries
            # these first under their own heading. Repeating the text here would
            # pay for it twice and make the prompt read as if it stutters.
            system += (
                "\n\n# 아래 '주의' 항목을 먼저 읽고, 거스르는 제안은 하지 마세요: "
                + ", ".join(w["title"] for w in warnings)
            )
        if packed.text:
            system += "\n\n# 컨텍스트\n" + packed.text
        if cross:
            system += (
                "\n\n# 다른 프로젝트에서 온 참고 — 이 프로젝트에서 검증된 것이 아닙니다\n"
                + "\n".join(f"- [{c['project']}] {c['title']}: {c['abstract']}" for c in cross)
            )
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
            # A trace's output is the *answer*, which only `commit` knows. The
            # assembled context belongs to the retrieval observation, and
            # storing it here made every uncommitted trace look answered — both
            # in the UI's answer column and to the implicit-feedback check.
            output_text="",
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
            warnings=warnings,
            from_other_projects=cross,
            trust_notes=trust_notes,
            trace_id=trace_id,
            session_id=sitting,
            latency_ms=latency,
            catch_up=catch_up,
        )

    def _warnings(
        self, project: str, packed: PackedContext
    ) -> list[dict[str, Any]]:
        """Matches from categories the profile marks as warnings."""
        warn_cats = self.store.profile(project).warn_categories()
        if not warn_cats:
            return []
        return [
            {
                "uri": item.uri,
                "title": item.title,
                "category": item.category,
                "tier": f"L{item.tier}",
            }
            for item in packed.items
            if item.kind == KIND_MEMORY and item.category in warn_cats
        ]

    def _trust_notes(
        self, packed: PackedContext, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Which packed memories are not settled fact, so the injection can flag
        them. This runs on the per-prompt hot path, so it reads the node (cheap)
        but skips the scores lookup — contested-by-conflict/streak still shows;
        only harm-based contested is deferred to the slower review views."""
        out: list[dict[str, Any]] = []
        for item in packed.items:
            if item.kind != KIND_MEMORY:
                continue
            node = self.store.read_node(Uri.parse(item.uri))
            if node is None:
                continue
            t = self.trust(node)
            if t["established"] or t["status"] == "superseded":
                continue
            out.append(
                {
                    "uri": item.uri,
                    "title": item.title,
                    "status": t["status"],
                    "label": t["label"],
                }
            )
            if len(out) >= limit:
                break
        return out

    def _cross_project(
        self, project: str, query: str, limit: int = 3, threshold: float = 0.5
    ) -> list[dict[str, Any]]:
        """Strongly matching memories from your *other* projects.

        The reason projects are isolated is that their facts must not be mistaken
        for each other. But "how did I solve this last time" is the single most
        useful thing a personal assistant can answer, and the answer usually
        lives in a different repo. So we look, and we label the result loudly:
        abstracts only, capped at a few, and never presented as verified here.
        """
        others = [p for p in self.store.projects() if p != project]
        if not others:
            return []
        out: list[dict[str, Any]] = []
        for other in others:
            hits, _trace = self.retriever.search(
                query,
                other,
                kinds=[KIND_MEMORY],
                limit=2,
                include_global=False,
            )
            for hit in hits:
                if hit.score < threshold:
                    continue
                out.append(
                    {
                        "project": other,
                        "uri": str(hit.uri),
                        "title": hit.title,
                        "category": hit.category,
                        "abstract": hit.abstract,
                        "score": round(hit.score, 4),
                    }
                )
        out.sort(key=lambda d: -d["score"])
        return out[:limit]

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

    @_locked
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
        files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Record an exchange and (by default) fold it into memory.

        Pass the ``trace_id`` from ``prepare`` so the generation lands on the
        same trace as the retrieval that fed it — otherwise you can see that a
        request was slow but not which half was slow.

        ``files`` are the paths this exchange wrote to, so a returning session
        learns which files last time's work touched, not just the topic.
        """
        files = [f for f in (files or []) if f][:50]
        if self.config.learn.redact_secrets:
            # Second line of defence behind the hooks: MCP and shell-bridge
            # clients send exchanges too, and what is stored here is re-injected
            # into every later session on this project.
            question, answer = redact(question), redact(answer)
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
            if files:
                self.tracer.annotate(trace_id, {"files": files})

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
            files=files,
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
            # If any of what we just learned corrects a belief that a recent bad
            # outcome blamed, let it supersede the old one — the answer key
            # upgrades itself instead of keeping both the wrong and the right.
            new_uris = [str(u) for u in (report.created + report.merged)]
            corrected = self._reconcile_correction(
                project, new_uris, immediate=False, trace_id=trace_id
            )
            if corrected:
                result["corrected"] = corrected
        if agent:
            self.tracer.touch_agent(agent, project=project)
        return result

    # ==================================================================
    # implicit feedback — the next prompt is the real score
    # ==================================================================
    def _infer_previous_outcome(
        self, project: str, sitting: str, question: str, current_trace: str
    ) -> dict[str, Any] | None:
        """Judge the *previous* answer by what is being asked now.

        People almost never file an explicit score, but their next request says
        plenty. Asking the same thing again — especially with "still doesn't
        work" attached — is a clear statement that the last answer missed.
        Moving on to something else is weaker evidence that it landed: they
        might equally have given up and done it by hand. So the two signals are
        not symmetric, and neither is as strong as a stated judgement.

        Returns the recorded outcome, or None when there is nothing to judge.
        """
        cfg = self.config.learn
        if not cfg.implicit_feedback or not sitting:
            return None
        prev = self.store.db.one(
            "SELECT id, input, output, started, metadata FROM traces"
            " WHERE scope=? AND session_id=? AND id != ? AND output != ''"
            " ORDER BY started DESC LIMIT 1",
            (project, sitting, current_trace),
        )
        if prev is None:
            return None
        # Never overwrite a judgement someone actually made.
        stated = self.store.db.one(
            "SELECT 1 FROM scores WHERE trace_id=? AND source != 'implicit' LIMIT 1",
            (prev["id"],),
        )
        if stated is not None:
            return None
        if self.store.db.one(
            "SELECT 1 FROM scores WHERE trace_id=? AND name=? LIMIT 1",
            (prev["id"], IMPLICIT_SCORE),
        ):
            return None  # already judged this one

        overlap = topic_overlap(prev["input"] or "", question)
        minutes = _minutes_between(prev["started"], now_iso())
        kind, value, why = _classify_followup(
            question, overlap, minutes, cfg.rework_similarity
        )
        if kind is None:
            return None

        outcome = {
            "kind": kind,
            "value": value,
            "signal": "wording" if kind == "reworked" else "topic_overlap",
            "topic_overlap": round(overlap, 4),
            "minutes_later": round(minutes, 1),
            "next_question": question[:200],
            "why": why,
        }
        self.score(
            prev["id"],
            name=IMPLICIT_SCORE,
            value=value,
            comment=why,
            source="implicit",
            strength=cfg.implicit_strength,
            # Moving on is weak evidence the answer landed — the user might just
            # as easily have given up. So a positive implicit signal stays a
            # trace-level metric and must not *promote* the memories it used;
            # only the negative signals (reworked/repeated) move confidence.
            # This keeps faith with the design rule that being retrieved is not
            # evidence of being right (config learn.reinforce=0.0).
            apply_to_memory=value < 0.5,
        )
        if value >= 0.5:
            # Moving on is too weak to *promote* a memory, but it is enough to
            # close an open challenge against one: the user used the answer it
            # fed and did not come back. Without this, a memory demoted by a
            # misread follow-up could only be healed by a stated score, which
            # in unattended operation never arrives — retraction would be a
            # one-way ratchet.
            self._settle(prev["id"], why)
        self.tracer.annotate(prev["id"], {"implicit_outcome": outcome})
        self.tracer.event(
            prev["id"],
            "outcome",
            kind,
            output_text=why,
            metadata=outcome,
            level="warning" if value < 0.5 else "info",
        )
        return outcome

    def _settle(self, trace_id: str, why: str) -> list[str]:
        """The answer this trace produced was accepted without complaint.

        For every memory that rode in it: count a settled use, clear any open
        challenge streak, and withdraw a pending correction against it. No
        confidence moves — this is the absence of a complaint, not praise.
        """
        settled: list[str] = []
        for item in self.tracer.context_of(trace_id):
            uri = Uri.parse(item["uri"])
            if uri.kind_dir != "memories" or uri.is_global:
                continue
            node = self.store.read_node(uri)
            if node is None:
                continue
            extra = node.extra
            extra["settled"] = int(extra.get("settled", 0)) + 1
            healed = int(extra.get("challenge_streak", 0)) > 0 or bool(
                extra.get("challenged_by")
            )
            if healed:
                extra["challenge_streak"] = 0
                extra.pop("challenged_by", None)
                extra.pop("challenged_at", None)
                extra.setdefault("evidence", []).append(
                    {
                        "at": now_iso(),
                        "kind": "settled",
                        "delta": 0.0,
                        "confidence": round(node.confidence, 3),
                        "source": "implicit",
                        "trace": trace_id,
                        "why": (why or "")[:160],
                    }
                )
                del extra["evidence"][:-20]
            self.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)
            settled.append(str(uri))
        return settled

    # ==================================================================
    # quality loop
    # ==================================================================
    @_locked
    def score(
        self,
        trace_id: str,
        name: str = "helpfulness",
        value: float = 1.0,
        comment: str = "",
        source: str = "human",
        apply_to_memory: bool = True,
        uris: list[str] | None = None,
        strength: float = 1.0,
    ) -> dict[str, Any]:
        """Record a judgement about a trace, and let it move memory confidence.

        This is the difference between monitoring and learning: the context that
        was in the room for an outcome is known, so the same signal that fills a
        dashboard can also promote what helped and demote what did not.

        Blame is *attributed*, not spread. A retrieval hands over a dozen items
        ranked by relevance; a wrong answer is almost always driven by the ones
        at the top, and penalising all twelve equally means every unrelated
        memory that happened to be nearby accumulates damage from failures it had
        no part in. So each item's adjustment is weighted by how strongly it was
        matched, and a negative score only reaches items that actually drove the
        answer. Pass ``uris`` when the caller knows exactly what was at fault.
        """
        row = self.store.db.one(
            "SELECT scope, input FROM traces WHERE id = ?", (trace_id,)
        )
        if row is None:
            raise KeyError(f"없는 트레이스: {trace_id}")
        scope = row["scope"]
        score_id = self.tracer.add_score(
            scope, name, value, trace_id=trace_id, comment=comment, source=source
        )
        if value < 0.5 and row["input"]:
            # The answer this trace produced was cached under its question. A
            # bad verdict on the answer must reach the cache too, or the same
            # question next time is served the very answer that just failed —
            # the one place the evolving answer key otherwise cannot reach.
            self.sessions.cache_forget(scope, row["input"])

        moved: list[dict[str, Any]] = []
        if apply_to_memory:
            used = self.tracer.context_of(trace_id)
            named = {u.rstrip("/") for u in (uris or [])}
            top = max((float(i["score"]) for i in used), default=0.0)
            base = self._score_delta(value) * max(0.0, float(strength))
            negative = base < 0
            for item in used:
                uri = Uri.parse(item["uri"])
                # Only memories move: sessions and resources are records, and
                # global preferences are your standing instructions, not
                # hypotheses to be scored down by a bad answer elsewhere.
                if uri.kind_dir != "memories" or uri.is_global:
                    continue
                if named:
                    if item["uri"] not in named:
                        continue
                    weight = 1.0
                else:
                    weight = (float(item["score"]) / top) if top > 0 else 1.0
                    if negative and weight < _BLAME_FLOOR:
                        continue
                delta = base * weight
                if abs(delta) < 0.005:
                    continue
                node = self.store.read_node(uri)
                if node is None:
                    continue
                node.confidence = max(0.0, min(1.0, node.confidence + delta))
                self._note_outcome(node, delta, trace_id, comment, source)
                if delta > 0:
                    self._confirm_corrections(node, trace_id)
                self.store.write_node(
                    node, regenerate_tiers=False, reinforce_dirs=False
                )
                moved.append(
                    {
                        "uri": item["uri"],
                        "weight": round(weight, 3),
                        "delta": round(delta, 4),
                        "confidence": round(node.confidence, 3),
                    }
                )
                if node.confidence < self.config.learn.archive_below:
                    self.store.archive_node(uri, reason=f"low score ({name})")
            # Pin the blame to the trace so the review queue can later tell the
            # memory that caused a bad outcome from the ones that merely rode along.
            if moved:
                self.tracer.record_attribution(
                    trace_id, {m["uri"]: m["delta"] for m in moved}
                )
        return {
            "score_id": score_id,
            "trace_id": trace_id,
            "name": name,
            "value": value,
            "memories_adjusted": [m["uri"] for m in moved],
            "adjustments": moved,
        }

    @staticmethod
    def _note_outcome(
        node: Node, delta: float, trace_id: str, why: str, source: str
    ) -> None:
        """Write the running evidence trail that makes a belief self-correcting.

        A confidence number tells you *where* a memory is; it does not tell you
        *why*, or whether it just got there by drifting down one bad task at a
        time. So each outcome that touches a memory leaves a dated line, a
        contradiction bumps a streak, and a confirmation clears it. ``trust()``
        reads these to decide when an established fact should step back to
        "verify me". Kept to the last 20 lines — enough to see the trend.
        """
        extra = node.extra
        if delta < 0:
            extra["contradictions"] = int(extra.get("contradictions", 0)) + 1
            extra["challenge_streak"] = int(extra.get("challenge_streak", 0)) + 1
            kind = "contradicted"
        else:
            extra["confirmations"] = int(extra.get("confirmations", 0)) + 1
            extra["challenge_streak"] = 0
            extra["last_confirmed"] = now_iso()
            kind = "confirmed"
        trail = extra.setdefault("evidence", [])
        trail.append(
            {
                "at": now_iso(),
                "kind": kind,
                "delta": round(delta, 4),
                "confidence": round(node.confidence, 3),
                "source": source,
                "trace": trace_id,
                "why": (why or "")[:160],
            }
        )
        del trail[:-20]

    def _confirm_corrections(self, node: Node, trace_id: str) -> None:
        """A good outcome for ``node`` resolves what it was pending against.

        If it was a provisional correction, the belief it challenged is now
        superseded for real (archived with a pointer). If it was itself under
        challenge, the challenge is withdrawn — it just worked.
        """
        extra = node.extra
        if extra.pop("challenged_by", None) is not None:
            extra.pop("challenged_at", None)
            extra.setdefault("evidence", []).append(
                {
                    "at": now_iso(),
                    "kind": "vindicated",
                    "delta": 0.0,
                    "confidence": round(node.confidence, 3),
                    "source": "outcome",
                    "trace": trace_id,
                    "why": "교정 후보가 있었지만 이 기록이 다시 좋은 결과를 냈습니다",
                }
            )
        for old_uri in list(extra.get("corrects", [])):
            old = self.store.read_node(Uri.parse(old_uri))
            if old is None or old.extra.get("challenged_by") != str(node.uri):
                continue
            self.learner._mark_superseded(Uri.parse(old_uri), node.uri, node.abstract)
            extra.setdefault("evidence", []).append(
                {
                    "at": now_iso(),
                    "kind": "correction",
                    "replaced": old_uri,
                    "trace": trace_id,
                    "why": "좋은 결과로 확인되어 이전 기록을 대체했습니다",
                }
            )
            node.confidence = max(node.confidence, self.config.learn.solid_confidence)
        if extra.get("evidence"):
            del extra["evidence"][:-20]

    @staticmethod
    def _score_delta(value: float) -> float:
        """Map a 0..1 score onto a confidence nudge centred on neutral (0.5).

        Deliberately asymmetric: a bad outcome costs more than a good one earns,
        because a confidently wrong memory does more damage than a missing one.
        """
        centred = float(value) - 0.5
        return centred * (0.24 if centred >= 0 else 0.44)

    # ==================================================================
    # the answer key upgrades itself — a correction supersedes what it fixed
    # ==================================================================
    def _recent_blamed(
        self, project: str, hours: int = 4, trace_id: str = ""
    ) -> list[str]:
        """Memories an attributed bad outcome demoted in the last few hours.

        These are the beliefs most likely to be *wrong*, not merely stale: the
        implicit-feedback loop pinned the blame on them when the user said the
        last answer missed. When a corrected lesson is learned right after, it
        is almost certainly the fix for one of these.

        With ``trace_id`` the search stays inside that trace's sitting: on a
        shared project, a teammate's unrelated lesson must not be read as the
        fix for a failure it never saw.
        """
        from datetime import datetime, timedelta, timezone

        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        sql = (
            "SELECT DISTINCT c.uri FROM context_used c"
            " JOIN traces t ON t.id = c.trace_id"
            " WHERE t.scope=? AND c.applied < 0 AND t.started >= ?"
        )
        params: list[Any] = [project, since]
        if trace_id:
            sql += (
                " AND t.session_id = (SELECT session_id FROM traces WHERE id=?)"
            )
            params.append(trace_id)
        rows = self.store.db.query(sql, params)
        out: list[str] = []
        for r in rows:
            u = Uri.parse(r["uri"])
            if u.kind_dir == "memories" and u.parts[:1] != ("_archive",):
                out.append(r["uri"])
        return out

    def _reconcile_correction(
        self,
        project: str,
        new_uris: list[str],
        immediate: bool = False,
        trace_id: str = "",
    ) -> list[dict[str, Any]]:
        """When a freshly learned lesson disproves a recently-blamed belief,
        let the new one *supersede* the old rather than sit beside it.

        This is the upgrade the user asked for: what was taken as the answer,
        then shown wrong by later prompts, is replaced by the corrected version
        — reversibly (the old node is archived with a pointer), with the
        correction recorded so the history is legible.

        Two speeds. ``immediate`` is for a person's hand-written correction:
        they asserted it, so the old belief is superseded on the spot. A lesson
        distilled from the agent's *next attempt* is not yet known to be right
        — it is the retry after a complaint, not the verified fix — so it only
        *challenges* the old belief: the old one drops to "확인 필요" and stays
        retrievable, and the supersede is completed when the new one earns a
        good outcome (see ``_confirm_corrections``) or withdrawn when the old
        one does.
        """
        blamed = self._recent_blamed(project, trace_id=trace_id)
        if not blamed:
            return []
        thr = self.config.learn.correction_similarity
        corrected: list[dict[str, Any]] = []
        for nu in new_uris:
            if nu in blamed:
                continue
            new = self.store.read_node(Uri.parse(nu))
            if new is None or new.extra.get("superseded_by"):
                continue
            changed = False
            for bu in blamed:
                if bu == nu:
                    continue
                old = self.store.read_node(Uri.parse(bu))
                if old is None or old.extra.get("superseded_by"):
                    continue
                if old.category != new.category:
                    continue
                sim = topic_overlap(
                    f"{old.title} {old.abstract}", f"{new.title} {new.abstract}"
                )
                if sim < thr:
                    continue
                corrects = new.extra.setdefault("corrects", [])
                if bu not in corrects:
                    corrects.append(bu)
                if immediate:
                    self.learner._mark_superseded(
                        Uri.parse(bu), Uri.parse(nu), new.abstract
                    )
                    new.extra.setdefault("evidence", []).append(
                        {
                            "at": now_iso(),
                            "kind": "correction",
                            "replaced": bu,
                            "similarity": round(sim, 3),
                        }
                    )
                    # A person's correction of a real failure is not a guess.
                    new.confidence = max(new.confidence, 0.7)
                    mode = "superseded"
                else:
                    if old.extra.get("challenged_by") in ("", None):
                        old.extra["challenged_by"] = nu
                        old.extra["challenged_at"] = now_iso()
                        old.extra.setdefault("evidence", []).append(
                            {
                                "at": now_iso(),
                                "kind": "challenged",
                                "delta": 0.0,
                                "confidence": round(old.confidence, 3),
                                "source": "distill",
                                "trace": trace_id,
                                "why": f"교정 후보가 생김: {new.title}"[:160],
                            }
                        )
                        del old.extra["evidence"][:-20]
                        self.store.write_node(
                            old, regenerate_tiers=False, reinforce_dirs=False
                        )
                    new.extra.setdefault("evidence", []).append(
                        {
                            "at": now_iso(),
                            "kind": "correction_pending",
                            "replaced": bu,
                            "similarity": round(sim, 3),
                            "why": "좋은 결과로 확인되면 이전 기록을 대체합니다",
                        }
                    )
                    mode = "pending"
                del new.extra["evidence"][:-20]
                changed = True
                corrected.append(
                    {
                        "new": nu,
                        "replaced": bu,
                        "similarity": round(sim, 3),
                        "mode": mode,
                    }
                )
            if changed:
                self.store.write_node(
                    new, regenerate_tiers=False, reinforce_dirs=False
                )
        return corrected

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
    @_locked
    def bind_alias(self, alias: str, project: str, kind: str = "repo") -> None:
        # Stored normalised, because lookups are: `resolve_project` normalises
        # the incoming remote/path before searching, so a raw ssh-form alias
        # bound here would never match — not even against itself.
        self.store.ensure_project(project)
        self.tracer.bind_alias(_normalise_alias(alias), project, kind)

    @_locked
    def unbind_alias(self, alias: str) -> bool:
        """Remove one repo/path → project binding. Returns whether it existed.
        Same normalisation as bind, so any URL form of the remote unbinds it."""
        return self.tracer.unbind_alias(_normalise_alias(alias))

    def alias_owner(self, alias: str) -> str:
        """Which project an alias currently points at ('' if unbound)."""
        return self.tracer.resolve_alias(_normalise_alias(alias))

    def aliases(self, project: str = "") -> list[dict[str, Any]]:
        return self.tracer.aliases(project)

    def resolve_project(
        self,
        project: str = "",
        repo: str = "",
        path: str = "",
        create: bool = False,
        template: str = "default",
    ) -> dict[str, Any]:
        """Work out which project an agent is talking about.

        An agent running in a checkout knows its git remote but not what you
        named the project here. Binding the remote once makes every later call
        from any machine resolve to the same context.

        ``template`` only matters when this call *creates* the project: a coding
        agent's hooks resolve with ``template='coding'`` so the very first
        auto-made project already has commands/conventions/pitfalls/decisions
        categories — otherwise ``remember('pitfalls', ...)`` silently has nowhere
        to land and the whole learn-from-mistakes path dies quietly.
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
            self.store.ensure_project(project, template=template)
            for alias, kind in ((repo, "repo"), (path, "path")):
                if alias:
                    self.tracer.bind_alias(_normalise_alias(alias), project, kind)
            return {"project": project, "resolved_by": "created", "created": True}

        guess = _project_from_alias(repo or path)
        if guess and self.store.project_exists(guess):
            return {"project": guess, "resolved_by": "guess", "created": False}
        if guess and create:
            self.store.ensure_project(guess, template=template)
            if repo:
                self.tracer.bind_alias(_normalise_alias(repo), guess, "repo")
            return {"project": guess, "resolved_by": "guess", "created": True}
        return {
            "project": "",
            "resolved_by": "",
            "created": False,
            "candidates": self.store.projects(),
        }

    @_locked
    def distill(self, project: str, limit: int = 20) -> DistillReport:
        return self.learner.distill(project, limit=limit)

    @_locked
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

    @_locked
    def reindex(self, project: str | None = None) -> dict[str, int]:
        return self.store.reindex(project)

    # ==================================================================
    def cache_list(self, project: str, limit: int = 30) -> list[dict[str, Any]]:
        return self.sessions.cache_list(project, limit)

    @_locked
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


def _title_of(statement: str, max_chars: int = 40) -> str:
    """Short name for a preference, trimmed on a word boundary."""
    line = " ".join((statement or "").split())
    if len(line) <= max_chars:
        return line.rstrip("?!.,")
    out: list[str] = []
    for word in line.split(" "):
        if out and len(" ".join([*out, word])) > max_chars:
            break
        out.append(word)
    return (" ".join(out) or line[:max_chars]).rstrip("?!.,")


def _derived_session(agent: str) -> str:
    """A sitting id when the caller supplies none: one agent, one day."""
    from datetime import datetime, timezone

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{agent or 'agent'}@{day}"


IMPLICIT_SCORE = "implicit_outcome"
_HANGUL_RE = re.compile(r"^[가-힣]+$")

# Two tiers, because a single list produced false positives that demoted
# knowledge which was fine. In a payments project "결제 승인 실패하면?" is a
# question *about* failure, not a complaint about the last answer — and it was
# being read as one, marking a perfectly good answer as reworked.
#
# Corrections speak about the work just done, whatever the subject. These are
# unambiguous even when the topic has moved on — "여전히 안 돼" / "still doesn't
# work" can only be a verdict on the previous answer:
_CORRECTION_MARKERS = (
    "안 되는데", "안되는데", "안 되네", "안되네", "안 돼", "안돼", "안 됩니다",
    "동작하지", "작동하지", "구현이 안", "왜 안",
    "틀렸", "제대로 안",
    "doesn't work", "does not work", "not working",
    "didn't work", "did not work", "fix it", "fix this", "fix that", "broken",
)
# Repair imperatives are a verdict on the last answer only when they point at
# it. "그 부분 고쳐줘" does; "README 의 오타를 수정해줘" names a new object and is
# simply the next task. Measured on a live store, the second kind — the most
# common sentence in a coding session — was demoting perfectly good memories
# two prompts at a time. So a repair verb counts when the subject is unchanged,
# when a demonstrative refers back ("그거", "이 부분"), or when nothing else is
# named at all ("고쳐줘", "다시 고쳐").
_REPAIR_MARKERS = ("고쳐", "수정해", "수정 해", "바로잡")
_BACK_REFERENCES = (
    "그 부분", "그부분", "이 부분", "이부분", "그거", "그걸", "그건", "이거", "이걸",
    "이건", "저거", "방금", "아까", "위의", "위에", "직전", "그렇게 하지",
)
# Persistence words ("still", "아직") are a complaint when paired with trouble,
# negation, or each other — "아직 에러가 나", "아직 그대로야" — but not on their
# own: "아직 커밋하지 마" is an instruction about the future.
_PERSISTENCE_MARKERS = ("여전히", "아직", "그대로", "still")
_NEGATIONS = ("안 ", "않", "못 ", "없", "not ", "n't")
# Weak quality-adverbs that read as a complaint only when the subject hasn't
# changed. "이제 배포 스크립트 제대로 짜줘" contains "제대로" but is a brand-new
# request, not a verdict on the last answer — so these count only with topic
# overlap.
_WEAK_CORRECTION_MARKERS = (
    "다시 해", "다시 봐", "다시 한", "제대로", "잘못", "again",
)
# Trouble words only count as a complaint when the subject has not changed —
# otherwise they are simply the topic of a new question:
_TROUBLE_WORDS = (
    "실패", "오류", "에러", "안 됨", "안됨", "죽어", "터져", "예외",
    "error", "failed", "failing", "crash", "wrong", "exception",
)
# Phrases that make a same-topic follow-up a *continuation*, not a complaint.
_CONTINUATION_MARKERS = (
    "그럼", "그러면", "추가로", "이번엔", "이번에는", "다음으로", "그리고",
    "also", "next", "additionally", "what about",
)


_WORD_RE = re.compile(r"[0-9A-Za-z]+|[가-힣]+")

# Interrogative and request boilerplate. Two questions sharing only these are
# not about the same thing, and leaving them in made "배포 어떻게 해?" look like
# a repeat of "테스트는 어떻게 돌려?".
_TOPIC_STOPWORDS = {
    "어떻게", "어떻", "떻게", "해줘", "해야", "하나", "되나", "뭐야", "무엇",
    "알려", "알려줘", "좀", "줄래", "봐줘", "해", "할", "수", "있", "이거",
    "이건", "그거", "방법", "해주", "주세", "주세요", "줘", "줘요", "지금", "다시", "또",
    "how", "do", "does", "the", "a", "an", "to", "is", "it", "what",
    "please", "can", "i", "me", "my", "you",
}


def _topic_grams(text: str) -> set[str]:
    """Content grams of a question, with Korean particles absorbed.

    Korean attaches particles to nouns, so "배포가" and "배포" are different
    tokens and different trigrams — which is why word- and trigram-level
    matching failed on exactly the follow-ups that matter. Character *bigrams*
    over each Korean word survive the particle: both yield "배포".
    """
    out: set[str] = set()
    for word in _WORD_RE.findall((text or "").lower()):
        if word in _TOPIC_STOPWORDS:
            continue
        if _HANGUL_RE.match(word):
            if len(word) <= 2:
                out.add(word)
            else:
                out.update(word[i : i + 2] for i in range(len(word) - 1))
        else:
            out.add(word)
    return {g for g in out if g not in _TOPIC_STOPWORDS}


def topic_overlap(a: str, b: str) -> float:
    """How much of the shorter question's subject the longer one covers.

    Containment rather than cosine: a terse repeat ("배포 어떻게 해?") is mostly
    contained in the verbose original, but cosine dilutes it to zero against all
    the words the original adds. Measured on real pairs, this separates repeats
    (0.50-1.00) from changes of subject (0.00) with nothing in between.
    """
    ga, gb = _topic_grams(a), _topic_grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def _classify_followup(
    question: str, overlap: float, minutes: float, rework_similarity: float
) -> tuple[str | None, float, str]:
    """Map the next request onto an outcome for the previous answer.

    Two signals, in order of how much they can be trusted:

    * the **wording** — a complaint inside a sitting is about the work just
      done. That is what "still doesn't work" means arriving after an answer.
    * the **topic overlap** — without a complaint, the same question again soon
      after means the answer did not land.

    Otherwise, moving on to something else is *mild* evidence it did land.
    Mild, because giving up and doing it by hand looks identical from here.

    Returns ``(kind, value, why)``; ``kind`` is None when the follow-up says
    nothing about the previous answer.
    """
    low = (question or "").lower()
    continuing = any(m in low for m in _CONTINUATION_MARKERS)
    same_topic = overlap >= rework_similarity
    corrected = any(m in low for m in _CORRECTION_MARKERS)
    weakly_corrected = any(m in low for m in _WEAK_CORRECTION_MARKERS)
    troubled = any(m in low for m in _TROUBLE_WORDS)
    refers_back = any(m in low for m in _BACK_REFERENCES)
    repair = any(m in low for m in _REPAIR_MARKERS)
    persist = sum(1 for m in _PERSISTENCE_MARKERS if m in low)
    negated = any(m in low for m in _NEGATIONS)
    # A repair verb with no object named: "고쳐줘", "다시 고쳐". Strip the verb
    # and see whether any subject survives.
    bare = low
    for m in _REPAIR_MARKERS:
        bare = bare.replace(m, " ")
    repair_bare = repair and not _topic_grams(bare)
    complained = (
        corrected
        or (repair and (same_topic or refers_back or repair_bare))
        or (persist > 0 and (troubled or negated or persist > 1 or same_topic))
        or ((weakly_corrected or troubled) and same_topic)
    )

    if complained and not continuing:
        detail = "같은 주제로 " if same_topic else ""
        return (
            "reworked",
            0.1 if same_topic else 0.18,
            f"{detail}직전 작업에 문제가 있다고 언급했습니다"
            f" ({minutes:.0f}분 뒤, 주제 일치 {overlap:.2f})",
        )
    if continuing:
        # "그럼 …", "추가로 …" is the next thing, not a verdict on the last one.
        return None, 0.5, ""
    if same_topic and minutes <= 120:
        return (
            "repeated",
            0.35,
            f"같은 요청이 {minutes:.0f}분 뒤 다시 들어왔습니다 (주제 일치 {overlap:.2f})",
        )
    if same_topic:
        return None, 0.5, ""
    if minutes <= 240:
        # Mild, because giving up and doing it by hand looks identical here.
        return (
            "moved_on",
            0.62,
            f"다른 주제로 넘어갔습니다 (주제 일치 {overlap:.2f})",
        )
    return None, 0.5, ""


def _minutes_between(a: str, b: str) -> float:
    from datetime import datetime, timezone

    def parse(x: str):
        try:
            ts = datetime.fromisoformat(x)
        except (TypeError, ValueError):
            return None
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)

    ta, tb = parse(a), parse(b)
    if ta is None or tb is None:
        return 0.0
    return max(0.0, (tb - ta).total_seconds() / 60.0)


def _days_since(iso: str) -> float:
    """Days between an ISO timestamp and now; 0 if unparseable or in the future."""
    from datetime import datetime, timezone

    try:
        ts = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0)
