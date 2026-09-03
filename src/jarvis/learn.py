"""The self-learning loop.

Four operations, run in this order by ``distill()``:

1. **extract** — read undistilled sessions, propose memory candidates shaped by
   the project's own profile categories.
2. **merge** — fold each candidate into an existing memory when it is the same
   lesson (stable filenames make this possible), otherwise create a new carrier.
3. **decay** — reduce confidence of memories nothing has used, and archive the
   ones that fall through the floor. A memory store that only grows becomes a
   token tax, so forgetting is a feature, not a cleanup task.
4. **cap** — enforce the profile's per-category ``keep`` limit.

Everything works without an LLM; the heuristic extractor is weaker but honest,
and it never invents content that was not in the session text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .embed import cosine, unpack_vector
from .llm import LLM
from .models import KIND_MEMORY, Node, Uri, now_iso, slugify
from .profiles import MemoryCategory, MemoryProfile
from .sessions import SessionLog
from .store import Store
from .tiers import summarize
from .tokens import estimate_tokens, truncate_to_tokens

# Phrases that mark a durable instruction rather than a one-off request.
_PREF_MARKERS = (
    "항상", "반드시", "절대", "앞으로", "기본으로", "규칙", "선호", "원칙", "매번",
    "always", "never", "prefer", "must", "from now on",
)
# A bare prohibition is usually about *this* step, not a standing rule: "아직
# 커밋하지 마, 리뷰 먼저 볼게" was promoted to a convention and then rode into
# every later prompt. These count only next to a generality marker above.
_WEAK_PREF_MARKERS = ("하지 마", "하지마", "말고", "대신", "don't", "do not")
# Exchanges with nothing to keep: greetings, acknowledgements, sign-offs. Ten
# such prompts became ten "cases" in a live run and drowned the four useful ones.
_CHATTER = (
    "고마워", "고맙", "감사", "좋아", "좋다", "ok", "okay", "오케이", "응", "네", "넵",
    "알겠", "다음", "여기까지", "오늘은", "오늘", "수고", "잘했", "굿", "그렇게", "해",
    "하자", "진행", "계속", "그래", "됐어", "됐다", "thanks", "thank", "you", "great",
    "nice", "cool", "done", "bye", "yes", "yep", "sure", "go", "ahead", "next",
)
_NEG_MARKERS = ("아니", "안 ", "않", "말고", "하지", "없", "not ", "never", "no ")
_WORD_RE = re.compile(r"[0-9A-Za-z]+|[가-힣]+|[぀-ヿ㐀-䶿一-鿿]+")

_SYSTEM = (
    "당신은 프로젝트 메모리 증류기입니다. 세션 기록에서 다음 세션에 재사용할 가치가 있는 "
    "지식만 뽑습니다. 세션에 실제로 등장한 내용만 사용하고, 추론이나 창작은 하지 않습니다."
)


@dataclass
class MemoryCandidate:
    category: str
    title: str
    statement: str
    detail: str = ""
    confidence: float = 0.5
    tags: list[str] = field(default_factory=list)
    source: str = ""
    # "llm" or "rule": which extractor produced this. A rule-derived guess and
    # an LLM reading of the transcript are not equally trustworthy, and the
    # store must remember which one filed a memory so briefs can say so.
    extractor: str = ""


@dataclass
class DistillReport:
    project: str
    sessions: int = 0
    created: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    decayed: int = 0
    used_llm: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "sessions": self.sessions,
            "created": self.created,
            "merged": self.merged,
            "conflicts": self.conflicts,
            "superseded": self.superseded,
            "archived": self.archived,
            "decayed": self.decayed,
            "used_llm": self.used_llm,
            "notes": self.notes,
        }


class Learner:
    def __init__(self, store: Store, sessions: SessionLog | None = None):
        self.store = store
        self.db = store.db
        self.llm: LLM = store.llm
        self.sessions = sessions or SessionLog(store)

    # ------------------------------------------------------------------
    def distill(
        self, project: str, limit: int = 20, decay: bool = True
    ) -> DistillReport:
        profile = self.store.profile(project)
        report = DistillReport(project=project, used_llm=self.llm.available)
        pending = self.sessions.undistilled(project, limit=limit)
        report.sessions = len(pending)

        for session in pending:
            candidates = self.extract(session, profile)
            written: list[str] = []
            for cand in candidates:
                uri, action, conflict = self.absorb(project, cand, profile)
                if uri is None:
                    continue
                written.append(str(uri))
                if action == "created":
                    report.created.append(str(uri))
                else:
                    report.merged.append(str(uri))
                if conflict:
                    node = self.store.read_node(uri)
                    resolved = bool(
                        node
                        and (node.extra.get("conflict") or {}).get("resolution")
                        == "superseded"
                    )
                    (report.superseded if resolved else report.conflicts).append(str(uri))
            self.sessions.mark_distilled(session, written)

        if decay:
            d_count, archived = self.decay(project, profile)
            report.decayed = d_count
            report.archived.extend(archived)
        report.archived.extend(self.enforce_caps(project, profile))

        # Directory centroids are maintained incrementally on every write, so
        # there is no full rescan here. `jv reindex` rebuilds them if needed.
        self.db.log_usage(
            now_iso(),
            project,
            "distill",
            detail={
                "sessions": report.sessions,
                "created": len(report.created),
                "merged": len(report.merged),
                "archived": len(report.archived),
                "llm": report.used_llm,
            },
        )
        if not report.used_llm:
            report.notes.append(
                "LLM 미설정 상태로 규칙 기반 추출을 사용했습니다. "
                "`jv config set llm.provider ...` 로 품질을 올릴 수 있습니다."
            )
        return report

    # ------------------------------------------------------------------
    # 1. extract
    # ------------------------------------------------------------------
    def extract(self, session: Node, profile: MemoryProfile) -> list[MemoryCandidate]:
        question = str(session.extra.get("question") or session.abstract or "")
        answer = _answer_of(session)
        if self.llm.available:
            got = self._extract_llm(session, profile, question, answer)
            if got is not None:
                return got
        return self._extract_heuristic(session, profile, question, answer)

    def _extract_llm(
        self,
        session: Node,
        profile: MemoryProfile,
        question: str,
        answer: str,
    ) -> list[MemoryCandidate] | None:
        cat_lines = "\n".join(
            f"- {c.name}: {c.description}\n  기준: {c.extract}" for c in profile.categories
        )
        transcript = truncate_to_tokens(session.body or f"Q: {question}\nA: {answer}", 8000)
        prompt = (
            f"# 이 프로젝트의 메모리 카테고리\n{cat_lines}\n\n"
            + (f"# 추가 지침\n{profile.distill_notes}\n\n" if profile.distill_notes else "")
            + f"# 세션 기록\n{transcript}\n\n"
            "# 작업\n"
            "재사용 가치가 있는 항목만 뽑아 위 카테고리로 분류하세요. "
            "재사용 가치가 없으면 빈 배열을 반환하세요. 각 항목은:\n"
            "- category: 위 목록 중 하나\n"
            "- title: 파일명이 될 짧은 명사구 (같은 지식은 항상 같은 title)\n"
            "- statement: 한두 문장 요약\n"
            "- detail: 근거·명령어·경로 등 원문 인용\n"
            "- confidence: 0~1\n"
            "- tags: 문자열 배열\n\n"
            'JSON만 출력: {"memories": [...]}'
        )
        data, res = self.llm.complete_json(prompt, _SYSTEM, max_tokens=3000)
        if not res.ok or not isinstance(data, dict):
            return None
        raw = data.get("memories")
        if not isinstance(raw, list):
            return None
        valid = set(profile.category_names())
        out: list[MemoryCandidate] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            cat = str(item.get("category") or "").strip()
            title = str(item.get("title") or "").strip()
            statement = str(item.get("statement") or "").strip()
            if cat not in valid or not title or not statement:
                continue
            out.append(
                MemoryCandidate(
                    category=cat,
                    title=title,
                    statement=statement,
                    detail=str(item.get("detail") or "").strip(),
                    confidence=_clamp(item.get("confidence", 0.55)),
                    tags=[str(t) for t in (item.get("tags") or [])][:8],
                    source=str(session.uri),
                    extractor="llm",
                )
            )
        return out

    def _extract_heuristic(
        self,
        session: Node,
        profile: MemoryProfile,
        question: str,
        answer: str,
    ) -> list[MemoryCandidate]:
        """Rule-based extraction: conservative, never invents content."""
        out: list[MemoryCandidate] = []
        names = profile.category_names()

        # Durable instructions in the user's own words -> a preference-like category.
        pref_cat = _first_present(
            names, ("preferences", "conventions", "voice", "thresholds")
        )
        # Nothing durable comes out of "고마워" / "ok 다음" / "오늘은 여기까지",
        # nor out of an answer that is only "완료했습니다". Skip the session.
        if _is_chatter(question, answer):
            return out

        if pref_cat:
            for sent in _sentences(question):
                low = sent.lower()
                # Weak prohibitions ("하지 마", "말고") alone are not rules —
                # see _WEAK_PREF_MARKERS. They ride along only with a strong one.
                if any(m in low for m in _PREF_MARKERS) and len(sent) > 6:
                    out.append(
                        MemoryCandidate(
                            category=pref_cat,
                            title=_title_from(sent),
                            statement=sent,
                            # No provenance in the body: `sources` records it,
                            # and prose here ends up in the regenerated summary.
                            detail="",
                            # Below the brief() "established knowledge" gate (0.6):
                            # a bare marker match in the user's own sentence is a
                            # guess worth keeping, not a fact to surface as settled.
                            confidence=0.55,
                            tags=["규칙"],
                            source=str(session.uri),
                            extractor="rule",
                        )
                    )

        # Commands and paths that actually appeared are worth keeping verbatim.
        cmd_cat = _first_present(names, ("commands", "runbooks", "patterns"))
        if cmd_cat:
            blocks = _code_blocks(answer)
            if blocks:
                low_q = question.lower()
                retry = any(m in low_q for m in _RETRY_MARKERS)
                base_title = _title_from(question.strip() or session.title) or "명령"
                # A retry must not merge into the very memory it may be
                # replacing — that would raise the blamed belief's confidence
                # with an unverified guess. Its own row lets it *challenge*
                # the old one and supersede it only once an outcome confirms it.
                title = f"{base_title} 재시도" if retry else base_title
                out.append(
                    MemoryCandidate(
                        category=cmd_cat,
                        title=title,
                        statement=(
                            f"{_title_from(question.strip(), 160) or question.strip()[:160]}"
                            + (" 에 재시도한 명령/코드 (검증 전)" if retry else " 에 사용된 명령/코드")
                        ),
                        detail="\n\n".join(f"```\n{b}\n```" for b in blocks[:3]),
                        # A retry after a complaint is a guess until an outcome
                        # says otherwise: keep it below the tentative line so it
                        # is never injected as settled and never outranks the
                        # belief it may be trying to replace.
                        confidence=0.4 if retry else 0.5,
                        tags=["코드", "재시도"] if retry else ["코드"],
                        source=str(session.uri),
                        extractor="rule",
                    )
                )

        # Everything else lands as a case: the raw exchange, compressed.
        # Only when nothing else claimed the session, though. The fallback
        # exists so a session is never lost, not to file a second copy of a
        # rule that already has a home — and a duplicate copy also survives
        # the supersession of the rule it duplicates, leaving stale guidance
        # in retrieval.
        case_cat = _first_present(
            names, ("cases", "incidents", "findings", "phrasing", "facts")
        ) or profile.fallback_category()
        if out:
            case_cat = ""
        if case_cat and answer.strip():
            out.append(
                MemoryCandidate(
                    category=case_cat,
                    title=_title_from(question.strip() or session.title)
                    or session.uri.name,
                    # Question first, then the answer's opening sentence(s). Both
                    # sides cut at sentence/clause boundaries so the L0 line a
                    # future prompt sees carries the conclusion, not a stub.
                    statement=(
                        f"{clip_sentences(question.strip(), 70)} → "
                        f"{_lead(answer, limit=140)}"
                    ),
                    detail=truncate_to_tokens(session.body, 900),
                    confidence=0.4,
                    tags=["세션"],
                    source=str(session.uri),
                    extractor="rule",
                )
            )
        return out

    # ------------------------------------------------------------------
    # 2. merge / create
    # ------------------------------------------------------------------
    def absorb(
        self, project: str, cand: MemoryCandidate, profile: MemoryProfile
    ) -> tuple[Uri | None, str, bool]:
        """Fold ``cand`` into memory.

        Returns ``(uri, "created"|"merged", needs_review)``. The last flag means
        the merge put two different claims about one topic in the same file — it
        does not assert which is right.
        """
        cat = profile.category(cand.category) or MemoryCategory(name=cand.category)
        target = self._find_similar(project, cand, cat)

        exact = Uri(project, ("memories", cand.category, slugify(cand.title, "memory")))
        # Replacement is what "the same title" means — a look-alike found by
        # similarity is a merge, not a restatement, and must not be overwritten.
        if target is not None and target == exact and cat.cumulative and cand.source == "manual":
            node = self.store.read_node(target)
            # Under the "flag" policy a contradiction is for a person to settle,
            # so a clashing restatement still goes through the flagging path.
            flag_it = (
                self.store.config.learn.conflict_policy == "flag"
                and node is not None
                and _clash_kind(node.abstract, cand.statement) != ""
            )
            if node is not None and not flag_it:
                return self._replace_by_hand(node, cand), "merged", False

        if target is not None and cat.cumulative:
            node = self.store.read_node(target)
            if node is not None:
                clash_kind = _clash_kind(node.abstract, cand.statement)
                conflict = clash_kind != ""
                clash_existing = node.abstract
                supersede_here = (
                    conflict
                    and self.store.config.learn.conflict_policy == "newest"
                )
                node.body = _append_detail(
                    node.body, cand, conflict, superseded=supersede_here
                )
                if cand.source == "manual":
                    # A person restating the rule is not "another observation":
                    # it must not push the value above what they asserted. (Seen
                    # live: a re-remember at 0.8 came out at 0.92.)
                    node.confidence = max(node.confidence, cand.confidence)
                else:
                    node.confidence = min(1.0, node.confidence + 0.12)
                # Once an LLM reading lands in the file, it is no longer a purely
                # rule-derived guess — record the stronger provenance.
                if cand.extractor == "llm" and node.extra.get("extractor") != "llm":
                    node.extra["extractor"] = "llm"
                node.sources = _add_source(node.sources, cand.source)
                node.tags = sorted(set(node.tags) | set(cand.tags))
                if conflict:
                    node.extra["conflict"] = {
                        "at": now_iso(),
                        "kind": clash_kind,
                        "existing": node.abstract,
                        "incoming": cand.statement,
                        "other": "",  # accumulated in this same file
                        "resolution": (
                            "superseded"
                            if self.store.config.learn.conflict_policy == "newest"
                            else "open"
                        ),
                    }
                    if supersede_here:
                        # Headline follows the newer claim, and so does the
                        # overview. The old wording stays only in the body's
                        # history section, explicitly marked dead — otherwise
                        # both rules keep getting retrieved and the agent has no
                        # way to tell which one is current.
                        node.abstract = truncate_to_tokens(cand.statement, 100)
                        node.overview = truncate_to_tokens(
                            cand.statement
                            + (("\n\n" + cand.detail) if cand.detail else ""),
                            2000,
                        )
                        node.extra["reviewed"] = True
                        node.extra["superseded_statements"] = [
                            *node.extra.get("superseded_statements", []),
                            clash_existing,
                        ][-10:]
                if cand.source != "manual":
                    # New machine-written content landed here, so a previous
                    # human sign-off no longer covers the whole file.
                    node.extra["reviewed"] = False
                # L0 is "what to know at a glance", so for a cumulative
                # carrier it is the current claim — the newest statement, or the
                # existing one when the two disagree and a human must choose.
                # Summarising the accumulated body instead produces a digest of
                # dated log lines, which is not a headline.
                if not conflict:
                    node.abstract = clip_tokens_at_sentence(cand.statement, 100)

                if not supersede_here:
                    # L1 summarises the accumulated record: that is its job.
                    _abstract, overview = summarize(node.body, node.title, self.llm)
                    node.overview = overview or node.overview
                    self.store.write_node(
                        node, regenerate_tiers=False, reinforce_dirs=False
                    )
                    return node.uri, "merged", conflict

                # Supersession replaces the carrier's content, so its name has
                # to follow. Leaving the old statement as the title makes the
                # knowledge list read "한글로" next to a body saying "영어로",
                # and the filename stops describing what is in the file. There
                # is nothing left to accumulate under the old name.
                return self._retitle(node, cand.title or cand.statement), "merged", conflict

        uri = Uri(project, ("memories", cand.category, slugify(cand.title, "memory")))
        predecessor: Uri | None = None
        if self.store.read_node(uri) is not None and not cat.cumulative:
            # Non-cumulative categories keep one file per observation — but the
            # same title is the same observation made again (titles come from
            # the question), and the newer account is the one to keep live. The
            # older one is archived pointing at its replacement, which is also
            # what the injected text promises ("같은 제목이면 이전 것을 대체").
            # Without this a repeated question left six "테스트" cases live.
            predecessor = uri
            uri = Uri(
                project,
                (
                    "memories",
                    cand.category,
                    slugify(f"{cand.title}-{now_iso()[:19]}", "memory"),
                ),
            )
        # L2 must be a superset of L1, so the body carries the statement *and*
        # the detail. A body of detail alone makes the "full" tier smaller than
        # its own overview, and then loading detail looks like it costs nothing.
        clash = self._find_clash(project, cand) if cat.cumulative else None
        body = (
            f"{cand.statement}\n\n{cand.detail}".strip()
            if cand.detail
            else cand.statement
        )
        # Who wrote this decides whether a human still needs to look at it.
        # You asserting something is not a claim awaiting verification; the
        # distiller's guess from a transcript is.
        manual = cand.source == "manual"
        node = Node(
            uri=uri,
            kind=KIND_MEMORY,
            title=cand.title,
            category=cand.category,
            abstract=clip_tokens_at_sentence(cand.statement, 100),
            overview=truncate_to_tokens(
                cand.statement + (("\n\n" + cand.detail) if cand.detail else ""), 2000
            ),
            body=body,
            tags=cand.tags,
            confidence=cand.confidence,
            sources=[cand.source] if cand.source else [],
            extra={
                "origin": "manual" if manual else "distilled",
                # Which distiller wrote it — an LLM read of the transcript vs. a
                # rule-based guess. Empty for manual entries.
                "extractor": "" if manual else (cand.extractor or "rule"),
                "reviewed": manual,
                "reviewed_at": now_iso() if manual else "",
            },
        )
        supersede = self.store.config.learn.conflict_policy == "newest"
        if clash is not None:
            other_uri, kind, other_statement = clash
            node.extra["conflict"] = {
                "at": now_iso(),
                "kind": kind,
                "existing": other_statement,
                "incoming": cand.statement,
                "other": str(other_uri),
                "resolution": "superseded" if supersede else "open",
            }
            if supersede:
                # The newer statement wins and the older is archived, so the
                # store resolves itself instead of waiting on a person. It is a
                # move, not a delete: the archived file records what replaced it.
                node.extra["supersedes"] = [str(other_uri)]
                node.extra["reviewed"] = True
            else:
                node.extra["reviewed"] = False
        self.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)
        if predecessor is not None:
            self._mark_superseded(predecessor, uri, cand.statement)
        if clash is not None:
            other_uri, kind, other_statement = clash
            if supersede:
                self._mark_superseded(other_uri, uri, cand.statement)
            else:
                # Both sides flagged: either one may be the one to correct.
                self._flag_conflict(
                    other_uri, kind, other_statement, cand.statement, uri
                )
            return uri, "created", True
        return uri, "created", False

    def _replace_by_hand(self, node: Node, cand: MemoryCandidate) -> Uri:
        """A person re-stating a memory under the same title *replaces* it.

        The injected text promises exactly this ("같은 제목이면 이전 것을 대체").
        Appending instead — what the merge path does for distilled lessons —
        left the headline and L1/L2 body saying the old, wrong thing, and kept
        the old contradictions so the *correction* surfaced in the review
        queue as harmful. The previous statement stays in the body as history.
        """
        previous = node.abstract or ""
        stamp = now_iso()
        history = ""
        if previous and previous.strip() != cand.statement.strip():
            history = (
                f"\n\n## 대체된 이전 내용 (더 이상 유효하지 않음)\n"
                f"- {stamp}: {previous}"
            )
            prior = node.extra.get("superseded_statements", [])
            node.extra["superseded_statements"] = [*prior, previous][-10:]
        node.abstract = clip_tokens_at_sentence(cand.statement, 100)
        node.overview = truncate_to_tokens(
            cand.statement + (("\n\n" + cand.detail) if cand.detail else ""), 2000
        )
        node.body = (
            f"{cand.statement}\n\n{cand.detail}".strip() if cand.detail else cand.statement
        ) + history
        node.confidence = max(cand.confidence, 0.0)
        node.sources = _add_source(node.sources, cand.source)
        node.tags = sorted(set(node.tags) | set(cand.tags))
        extra = node.extra
        # The verdicts below were about the old statement, not this one.
        for k in ("challenge_streak", "contradictions", "confirmations"):
            extra.pop(k, None)
        for k in ("challenged_by", "challenged_at", "conflict", "last_confirmed"):
            extra.pop(k, None)
        extra["evidence"] = [
            {
                "at": stamp,
                "kind": "replaced",
                "delta": 0.0,
                "confidence": round(node.confidence, 3),
                "source": "manual",
                "trace": "",
                "why": "같은 제목으로 다시 기록되어 이전 내용을 대체했습니다",
            }
        ]
        extra["origin"] = "manual"
        extra["reviewed"] = True
        extra["reviewed_at"] = stamp
        extra["extractor"] = ""
        # Trace-level blame recorded before this instant was about the old
        # statement; review and trust read outcomes from here on. Millisecond
        # precision to match trace timestamps — the blame that prompted the
        # rewrite usually landed in the same second.
        extra["verdicts_reset_at"] = datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        )
        self.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)
        return node.uri

    def _find_clash(
        self, project: str, cand: MemoryCandidate
    ) -> tuple[Uri, str, str] | None:
        """Find an existing memory in the same category that this contradicts.

        Merge similarity and contradiction are different questions. Two opposite
        rules can be textually far enough apart to stay separate files — and
        then both get retrieved, with nothing telling the agent which one wins.
        That is worse than a flagged conflict, so the check does not ride on the
        merge decision. Categories are capped in size, so scanning one is cheap.

        Returns ``(other_uri, clash_kind, other_statement)``.
        """
        # A textual contradiction requires shared words, so the lexical index is
        # a sound prefilter — and it keeps this from costing O(category) on
        # every write, which is what made bulk ingestion quadratic before.
        lexical = [
            uri for uri, _s in self.db.fts_search(cand.statement, [project], limit=40)
        ]
        if not lexical:
            return None
        marks = ", ".join("?" for _ in lexical)
        rows = self.db.query(
            f"SELECT uri, abstract FROM nodes WHERE scope=? AND kind=? AND"
            f" category=? AND uri IN ({marks})",
            [project, KIND_MEMORY, cand.category, *lexical],
        )
        for row in rows:
            if Uri.parse(row["uri"]).parts[:1] == ("_archive",):
                continue
            kind = _clash_kind(row["abstract"] or "", cand.statement)
            if kind:
                return Uri.parse(row["uri"]), kind, row["abstract"] or ""
        return None

    def _flag_conflict(
        self, uri: Uri, kind: str, mine: str, theirs: str, other: Uri
    ) -> None:
        """Mark one side of a contradiction so review can show the pair."""
        node = self.store.read_node(uri)
        if node is None:
            return
        node.extra["conflict"] = {
            "at": now_iso(),
            "kind": kind,
            "existing": mine,
            "incoming": theirs,
            "other": str(other),
        }
        node.extra["reviewed"] = False
        self.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)

    def _retitle(self, node: Node, new_title: str) -> Uri:
        """Move a carrier so its filename matches its current content."""
        old_uri = node.uri
        title = _title_from(new_title) or node.title
        target = Uri(
            old_uri.scope, ("memories", node.category, slugify(title, "memory"))
        )
        node.title = title
        if target == old_uri or self.store.read_node(target) is not None:
            # Target name is taken (or unchanged): keep the current location
            # rather than clobbering a different memory.
            self.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)
            return old_uri
        node.uri = target
        node.extra["renamed_from"] = str(old_uri)
        self.store.delete_node(old_uri)
        self.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)
        return target

    def _mark_superseded(self, old_uri: Uri, new_uri: Uri, replacement: str) -> None:
        """Archive the outdated side of a contradiction, recording what replaced it."""
        node = self.store.read_node(old_uri)
        if node is not None:
            node.extra["superseded_by"] = str(new_uri)
            node.extra["superseded_at"] = now_iso()
            node.body = (
                node.body.rstrip()
                + f"\n\n## Superseded\n- {now_iso()}: 다음 내용으로 대체됨 — "
                + f"{replacement}\n  ({new_uri})"
            ).strip()
            self.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)
        self.store.archive_node(old_uri, reason=f"superseded by {new_uri}")

    def _find_similar(
        self, project: str, cand: MemoryCandidate, cat: MemoryCategory
    ) -> Uri | None:
        """Locate the existing carrier for this lesson, if any."""
        exact = Uri(project, ("memories", cand.category, slugify(cand.title, "memory")))
        if self.store.read_node(exact) is not None:
            return exact

        threshold = self.store.config.learn.merge_threshold
        probe_text = f"{cand.title}\n{cand.statement}"
        probe = self.store.embedder.embed(probe_text)

        # Narrow with the lexical index first. Comparing against every memory in
        # the category makes each write cost O(category size), which turns bulk
        # ingestion quadratic — and a merge candidate that shares no words with
        # the incoming text is not the same lesson anyway.
        lexical = [
            uri
            for uri, _score in self.db.fts_search(probe_text, [project], limit=60)
        ]
        if lexical:
            marks = ", ".join("?" for _ in lexical)
            rows = self.db.query(
                f"SELECT uri, vector FROM nodes WHERE scope=? AND kind=? AND"
                f" category=? AND uri IN ({marks})",
                [project, KIND_MEMORY, cand.category, *lexical],
            )
        else:
            # No lexical overlap at all: fall back to the newest few, so a
            # rephrased duplicate can still merge.
            rows = self.db.query(
                "SELECT uri, vector FROM nodes WHERE scope=? AND kind=? AND"
                " category=? ORDER BY updated DESC LIMIT 40",
                (project, KIND_MEMORY, cand.category),
            )

        best: tuple[float, str] | None = None
        for row in rows:
            sim = cosine(probe, unpack_vector(row["vector"]))
            if best is None or sim > best[0]:
                best = (sim, row["uri"])
        if best is not None and best[0] >= threshold:
            return Uri.parse(best[1])
        return None

    # ------------------------------------------------------------------
    # 3. decay
    # ------------------------------------------------------------------
    def decay(self, project: str, profile: MemoryProfile) -> tuple[int, list[str]]:
        cfg = self.store.config.learn
        rows = self.db.query(
            "SELECT uri, confidence, hits, last_used, updated FROM nodes"
            " WHERE scope=? AND kind=?",
            (project, KIND_MEMORY),
        )
        now = datetime.now(timezone.utc)
        touched = 0
        archived: list[str] = []
        for row in rows:
            uri = Uri.parse(row["uri"])
            if uri.parts and uri.parts[0] == "_archive":
                continue
            # Only decay what has sat unused; a memory used last week is alive.
            stale_days = _days_since(row["last_used"] or row["updated"], now)
            if stale_days < 7:
                continue
            new_conf = float(row["confidence"]) * cfg.decay ** max(1, stale_days // 7)
            # Below the usefulness floor after sitting unused for at least a week:
            # archive it whether or not it was ever used. A once-used note (hits>0)
            # that has faded this far is dead weight in retrieval and the review
            # queue just the same; archiving keeps it recoverable, not deleted.
            if new_conf < cfg.archive_below:
                if self.store.archive_node(uri, reason="decay") is not None:
                    archived.append(row["uri"])
                continue
            self.db.execute(
                "UPDATE nodes SET confidence=? WHERE uri=?", (new_conf, row["uri"])
            )
            node = self.store.read_node(uri)
            if node is not None:
                node.confidence = new_conf
                self.store.path_for(uri).write_text(
                    node.to_markdown(), encoding="utf-8"
                )
            touched += 1
        self.db.commit()
        return touched, archived

    def enforce_caps(self, project: str, profile: MemoryProfile) -> list[str]:
        archived: list[str] = []
        for cat in profile.categories:
            rows = self.db.query(
                "SELECT uri FROM nodes WHERE scope=? AND kind=? AND category=?"
                " ORDER BY confidence DESC, hits DESC, updated DESC",
                (project, KIND_MEMORY, cat.name),
            )
            rows = [r for r in rows if Uri.parse(r["uri"]).parts[:1] != ("_archive",)]
            for row in rows[cat.keep :]:
                if self.store.archive_node(Uri.parse(row["uri"]), reason="cap") is not None:
                    archived.append(row["uri"])
        return archived

    # ------------------------------------------------------------------
    # 4. reinforce
    # ------------------------------------------------------------------
    def reinforce(self, project: str, uris: list[str]) -> int:
        """Strengthen memories that were actually used in a packed context."""
        if not uris:
            return 0
        return self.store.touch_nodes(uris, reinforce=self.store.config.learn.reinforce)

    def feedback(
        self, project: str, uri: str, helpful: bool, note: str = ""
    ) -> Node | None:
        """Explicit human correction — the strongest learning signal there is."""
        u = Uri.parse(uri)
        node = self.store.read_node(u)
        if node is None:
            return None
        delta = 0.25 if helpful else -0.35
        node.confidence = _clamp(node.confidence + delta)
        entry = f"- {now_iso()} 피드백: {'유용' if helpful else '부정확'}"
        if note:
            entry += f" — {note}"
        node.body = (node.body.rstrip() + "\n\n## Feedback\n" + entry).strip()
        self.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)
        if node.confidence < self.store.config.learn.archive_below:
            self.store.archive_node(u, reason="negative feedback")
        self.db.log_usage(
            now_iso(), project, "feedback", uri=uri, detail={"helpful": helpful, "note": note}
        )
        return node


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _clamp(v: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.5
    return max(lo, min(hi, f))


def _first_present(names: list[str], preferred: tuple[str, ...]) -> str:
    """Pick the first preferred category this project actually declares.

    The heuristic extractor must not invent categories: a project whose profile
    has no "preferences" category should get its durable instructions filed
    under whatever it does have, or not at all.
    """
    for want in preferred:
        if want in names:
            return want
    return ""


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。！？\n])\s*", text or "")
    return [p.strip() for p in parts if p and len(p.strip()) > 3]


def _is_chatter(question: str, answer: str) -> bool:
    """True when the exchange carries nothing worth a memory."""
    q = " ".join((question or "").split())
    a = " ".join((answer or "").split())
    q_words = _WORD_RE.findall(q.lower())
    if not q_words:
        return True

    def chat_word(w: str) -> bool:
        # Exact word or a Korean particle/ending on one ("고마워요", "좋아요").
        return any(w == c or (len(c) >= 2 and w.startswith(c)) for c in _CHATTER)

    # Every word is an acknowledgement word: "고마워", "ok 다음", "좋아 그렇게 해".
    if len(q_words) <= 4 and all(chat_word(w) for w in q_words):
        return True
    # A one-word prompt answered with a pleasantry ("응" → "네, 알겠습니다.").
    a_low = a.lower()
    if len(q_words) <= 1 and len(a) < 20 and any(
        a_low.startswith(c) for c in ("완료", "네", "알겠", "수고", "천만", "감사", "ok", "좋")
    ):
        return True
    return False


_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")
_CLAUSE_BREAKS = (", ", " — ", " - ", " (", "; ", " · ")


def _lead(text: str, limit: int = 200) -> str:
    """The opening of an answer, cut at a sentence — never mid-thought.

    A hard character cut produced abstracts like "위험한 점이 세 가지 있어서
    그대로 쓰기 전에" — the warning's conclusion was exactly the part lost.
    Whole sentences are taken while they fit; an over-long first sentence is
    cut at its last clause break and marked with an ellipsis.
    """
    para = ""
    for line in (text or "").splitlines():
        if line.strip() and not line.strip().startswith("#"):
            para = line.strip()
            break
    if not para:
        para = (text or "").strip()
    return clip_sentences(para, limit)


def clip_sentences(text: str, limit: int) -> str:
    """Longest run of leading whole sentences within ``limit`` characters."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    out = ""
    for sent in (s for s in _SENTENCE_END.split(text) if s):
        if not out:
            if len(sent) <= limit:
                out = sent
                continue
            head = sent[:limit]
            cut = max((head.rfind(b) for b in _CLAUSE_BREAKS), default=-1)
            return (head[:cut] if cut > limit // 3 else head[: limit - 1]).rstrip() + "…"
        if len(out) + 1 + len(sent) > limit:
            break
        out = f"{out} {sent}"
    return out


def clip_tokens_at_sentence(text: str, tokens: int) -> str:
    """``truncate_to_tokens`` that backs off to the last full sentence."""
    text = " ".join((text or "").split())
    if estimate_tokens(text) <= tokens:
        return text
    sents = [s for s in _SENTENCE_END.split(text) if s]
    out = ""
    for sent in sents:
        trial = f"{out} {sent}".strip()
        if estimate_tokens(trial) > tokens:
            break
        out = trial
    if out:
        return out
    return truncate_to_tokens(text, tokens).rstrip() + "…"


def _code_blocks(text: str) -> list[str]:
    return [b.strip() for b in re.findall(r"```[a-zA-Z0-9]*\n(.*?)```", text or "", re.S)]


def _answer_of(session: Node) -> str:
    m = re.search(r"^##\s*A\s*$", session.body or "", re.M)
    return session.body[m.end() :].strip() if m else (session.body or "")


def _add_source(sources: list[str], new: str) -> list[str]:
    if not new:
        return sources
    out = [s for s in sources if s != new]
    out.append(new)
    return out[-20:]  # keep provenance bounded


def _append_detail(
    body: str,
    cand: MemoryCandidate,
    needs_review: bool,
    superseded: bool = False,
) -> str:
    """Add this observation to the carrier's log.

    When the new statement supersedes the old one, the *old* text is what moves
    into history — and it is labelled dead, not merely "different". A note that
    only says two things disagree leaves both readable as current.
    """
    stamp = now_iso()
    if superseded:
        old_lead = body.strip().splitlines()[0] if body.strip() else ""
        header = "## 대체된 이전 내용 (더 이상 유효하지 않음)"
        entry = f"- {stamp}: {old_lead}" if old_lead else f"- {stamp}: (이전 내용)"
        new_body = cand.statement
        if cand.detail:
            new_body += f"\n\n{cand.detail}"
        kept = _history_of(body)
        return f"{new_body}\n\n{header}\n{entry}" + (f"\n{kept}" if kept else "")

    marker = " ⚠ 기존 내용과 다름 — 확인 필요" if needs_review else ""
    entry = f"- {stamp}{marker}: {cand.statement}"
    if cand.detail:
        entry += f"\n\n{_indent(cand.detail)}"
    if "## Observations" in body:
        return body.rstrip() + "\n" + entry
    return (body.rstrip() + "\n\n## Observations\n" + entry).strip()


def _history_of(body: str) -> str:
    """Existing history bullets, so supersessions accumulate rather than reset."""
    marker = "## 대체된 이전 내용 (더 이상 유효하지 않음)"
    idx = body.find(marker)
    if idx == -1:
        return ""
    return body[idx + len(marker) :].strip()


def _indent(text: str) -> str:
    return "\n".join("  " + line if line.strip() else line for line in text.splitlines())


def _polarity_differs(a: str, b: str) -> bool:
    """Cheap contradiction signal: same topic, opposite negation polarity."""
    la, lb = (a or "").lower(), (b or "").lower()
    na = any(m in la for m in _NEG_MARKERS)
    nb = any(m in lb for m in _NEG_MARKERS)
    return na != nb


_TITLE_TRAIL = re.compile(r"[\s?!.,:;·]+$")
# Request boilerplate and complaint phrasing that a question carries but a
# memory's *name* should not. Without this a retry after "린트 검사 여전히 안
# 되는데" was filed under that sentence, and the same lesson asked three ways
# became three rows.
_TITLE_NOISE = re.compile(
    r"(?:\b(?:여전히|아직|그대로|계속)\s*)?"
    r"(?:안\s*되는데|안되는데|안\s*돼|안돼|안\s*됩니다|왜\s*안\s*돼|"
    r"어떻게\s*(?:해|하지|하나요|합니까|돌려|써)|"
    r"해\s*줘|해줘|해주세요|알려\s*줘|알려줘|알려주세요|"
    r"좀|다시|please|how\s+do\s+i|how\s+to|can\s+you)"
    r"(?=[\s?!.,]|$)",
    re.IGNORECASE,
)
# Wording that says the question is a complaint about the previous attempt.
# A code block answering it is the *next try*, not a proven command.
_RETRY_MARKERS = (
    "안 되는데", "안되는데", "안 돼", "안돼", "여전히", "아직", "그대로", "틀렸",
    "still", "doesn't work", "not working", "didn't work", "broken",
)


def _title_from(text: str, max_chars: int = 40) -> str:
    """A title fit to be a filename and a list row.

    Measured in characters, not tokens: a title is a name, and the token
    estimator deliberately counts Korean at ~1.5 per syllable, which would clip
    a perfectly reasonable Korean title to a few words.

    Titles are also the identity of a cumulative memory, so cutting one
    mid-word both looks broken and makes two distinct memories render as the
    same row in the review queue.
    """
    line = " ".join((text or "").split())
    cleaned = " ".join(_TITLE_NOISE.sub(" ", line).split())
    # Keep the noise if stripping it leaves nothing — a title beats no title.
    if len(cleaned) >= 2:
        line = cleaned
    if not line:
        return ""
    if len(line) <= max_chars:
        return _TITLE_TRAIL.sub("", line)
    out: list[str] = []
    for word in line.split(" "):
        candidate = " ".join([*out, word])
        if out and len(candidate) > max_chars:
            break
        out.append(word)
    return _TITLE_TRAIL.sub("", " ".join(out) or line[:max_chars])


def _words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _term_swapped(a: str, b: str) -> bool:
    """Same sentence with a term substituted — "커밋 메시지는 한글로" vs "... 영어로".

    The negation check cannot see this, and vector similarity is the wrong tool:
    the longer the sentence, the more a single decisive word is drowned out, so a
    cosine threshold fires on one phrasing and misses an equivalent one.

    A substitution has a specific shape instead: the two statements share most
    of their words, and *each* carries a couple the other lacks. If only one
    side has extra words it is an elaboration, not a disagreement.
    """
    wa, wb = set(_words(a)), set(_words(b))
    if not wa or not wb:
        return False
    only_a, only_b = wa - wb, wb - wa
    if not only_a or not only_b:
        return False  # one side only elaborates the other
    shared = len(wa & wb)
    # Measured against the differing part rather than as a ratio of the whole:
    # a Jaccard threshold that works for a long sentence rejects the same single
    # swap in a three-word one.
    return (
        len(only_a) <= 2
        and len(only_b) <= 2
        and shared >= max(len(only_a), len(only_b))
    )


def _shares_topic(a: str, b: str) -> bool:
    """Do these two statements talk about the same thing at all?

    Required before any contradiction verdict. Negation polarity on its own is
    almost meaningless between unrelated sentences: "빌드는 make 로 한다" and
    "절대 강제 푸시하지 않는다" differ in polarity while contradicting nothing,
    and scanning a whole category with that rule floods review with noise.
    """
    # Frame words carry no topic. Without dropping them "테스트는 pytest 로
    # 돌린다" and "린트는 ruff 로 돌린다" shared {로, 돌린다}, read as the same
    # topic with a swapped term, and the newer rule *superseded* the older one —
    # two unrelated conventions could not coexist in a category (seen live).
    wa = {w for w in _words(a) if w not in _FRAME_WORDS}
    wb = {w for w in _words(b) if w not in _FRAME_WORDS}
    if not wa or not wb:
        return False
    shared = len(wa & wb)
    smallest = min(len(wa), len(wb))
    if smallest <= 2:
        # "테스트는 pytest" vs "테스트는 unittest": one shared content word is
        # the whole subject. But "명령 0" vs "명령 1" is an enumeration, not a
        # dispute — when everything that differs is a number, leave it alone.
        differing = wa ^ wb
        if differing and all(d.isdigit() and len(d) <= 2 for d in differing):
            return False  # "포트는 8080" vs "9090" still counts: values, not indices
        return shared >= 1 and shared >= 0.5 * smallest
    return shared >= 2 and shared >= 0.4 * smallest


_FRAME_WORDS = {
    # particles and light verbs that give a sentence its shape, not its subject
    "로", "으로", "를", "을", "은", "는", "이", "가", "에", "에서", "와", "과", "도", "만",
    "의", "및", "또는", "그리고", "쓴다", "쓴다.", "쓰다", "쓴", "한다", "하다", "합니다",
    "돌린다", "돌리다", "실행한다", "사용한다", "사용", "이용한다", "않는다", "않다",
    "있다", "없다", "된다", "된", "것", "수", "때", "항상", "앞으로", "반드시", "절대",
    "the", "a", "an", "is", "are", "be", "use", "uses", "using", "run", "runs", "with",
    "for", "to", "of", "in", "on", "and", "or", "always", "never", "must", "should",
}


def _clash_kind(existing: str, incoming: str) -> str:
    """Name the kind of textual contradiction, or "" if none is detectable.

    Both checks are textual. Neither reads meaning — a semantic contradiction
    with no shared wording needs the LLM extractor to catch it.
    """
    if not _shares_topic(existing, incoming):
        return ""
    if _polarity_differs(existing, incoming):
        return "negation"
    if _term_swapped(existing, incoming):
        return "substitution"
    return ""


def _days_since(iso: str, now: datetime) -> int:
    if not iso:
        return 999
    try:
        ts = datetime.fromisoformat(iso)
    except ValueError:
        return 999
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0, int((now - ts).total_seconds() // 86400))
