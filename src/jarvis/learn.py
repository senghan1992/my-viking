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
from .tokens import truncate_to_tokens

# Phrases that mark a durable instruction rather than a one-off request.
_PREF_MARKERS = (
    "항상", "반드시", "절대", "하지 마", "하지마", "말고", "대신",
    "앞으로", "기본으로", "규칙", "선호", "원칙", "매번",
    "always", "never", "prefer", "must", "don't", "do not", "from now on",
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


@dataclass
class DistillReport:
    project: str
    sessions: int = 0
    created: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
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
                    report.conflicts.append(str(uri))
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
        if pref_cat:
            for sent in _sentences(question):
                low = sent.lower()
                if any(m in low for m in _PREF_MARKERS) and len(sent) > 6:
                    out.append(
                        MemoryCandidate(
                            category=pref_cat,
                            title=_title_from(sent),
                            statement=sent,
                            # No provenance in the body: `sources` records it,
                            # and prose here ends up in the regenerated summary.
                            detail="",
                            confidence=0.6,
                            tags=["규칙"],
                            source=str(session.uri),
                        )
                    )

        # Commands and paths that actually appeared are worth keeping verbatim.
        cmd_cat = _first_present(names, ("commands", "runbooks", "patterns"))
        if cmd_cat:
            blocks = _code_blocks(answer)
            if blocks:
                out.append(
                    MemoryCandidate(
                        category=cmd_cat,
                        title=_title_from(question.strip() or session.title) or "명령",
                        statement=f"{question.strip()[:160]} 에 사용된 명령/코드",
                        detail="\n\n".join(f"```\n{b}\n```" for b in blocks[:3]),
                        confidence=0.5,
                        tags=["코드"],
                        source=str(session.uri),
                    )
                )

        # Everything else lands as a case: the raw exchange, compressed.
        case_cat = _first_present(
            names, ("cases", "incidents", "findings", "phrasing", "facts")
        ) or profile.fallback_category()
        if case_cat and answer.strip():
            out.append(
                MemoryCandidate(
                    category=case_cat,
                    title=_title_from(question.strip() or session.title)
                    or session.uri.name,
                    statement=truncate_to_tokens(
                        f"{question.strip()} → {_lead(answer)}", 120
                    ),
                    detail=truncate_to_tokens(session.body, 900),
                    confidence=0.4,
                    tags=["세션"],
                    source=str(session.uri),
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

        if target is not None and cat.cumulative:
            node = self.store.read_node(target)
            if node is not None:
                clash = _clash_kind(node.abstract, cand.statement)
                conflict = clash != ""
                node.body = _append_detail(node.body, cand, conflict)
                node.confidence = min(1.0, node.confidence + 0.12)
                node.sources = _add_source(node.sources, cand.source)
                node.tags = sorted(set(node.tags) | set(cand.tags))
                if conflict:
                    node.extra["conflict"] = {
                        "at": now_iso(),
                        "kind": clash,
                        "existing": node.abstract,
                        "incoming": cand.statement,
                    }
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
                    node.abstract = truncate_to_tokens(cand.statement, 100)
                # L1 does summarise the whole record: that is its job.
                _abstract, overview = summarize(node.body, node.title, self.llm)
                node.overview = overview or node.overview
                self.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)
                return node.uri, "merged", conflict

        uri = Uri(project, ("memories", cand.category, slugify(cand.title, "memory")))
        if self.store.read_node(uri) is not None and not cat.cumulative:
            # Non-cumulative categories keep one file per observation.
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
            abstract=truncate_to_tokens(cand.statement, 100),
            overview=truncate_to_tokens(
                cand.statement + (("\n\n" + cand.detail) if cand.detail else ""), 2000
            ),
            body=body,
            tags=cand.tags,
            confidence=cand.confidence,
            sources=[cand.source] if cand.source else [],
            extra={
                "origin": "manual" if manual else "distilled",
                "reviewed": manual,
                "reviewed_at": now_iso() if manual else "",
            },
        )
        self.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)
        return uri, "created", False

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
            if new_conf < cfg.archive_below and int(row["hits"]) == 0:
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
        amount = self.store.config.learn.reinforce
        n = 0
        for uri in uris:
            try:
                self.store.touch_node(Uri.parse(uri), reinforce=amount)
                n += 1
            except Exception:
                continue
        return n

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


def _lead(text: str, limit: int = 200) -> str:
    for line in (text or "").splitlines():
        if line.strip() and not line.strip().startswith("#"):
            return line.strip()[:limit]
    return (text or "").strip()[:limit]


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


def _append_detail(body: str, cand: MemoryCandidate, needs_review: bool) -> str:
    stamp = now_iso()
    marker = " ⚠ 기존 내용과 다름 — 확인 필요" if needs_review else ""
    entry = f"- {stamp}{marker}: {cand.statement}"
    if cand.detail:
        entry += f"\n\n{_indent(cand.detail)}"
    if "## Observations" in body:
        return body.rstrip() + "\n" + entry
    return (body.rstrip() + "\n\n## Observations\n" + entry).strip()


def _indent(text: str) -> str:
    return "\n".join("  " + line if line.strip() else line for line in text.splitlines())


def _polarity_differs(a: str, b: str) -> bool:
    """Cheap contradiction signal: same topic, opposite negation polarity."""
    la, lb = (a or "").lower(), (b or "").lower()
    na = any(m in la for m in _NEG_MARKERS)
    nb = any(m in lb for m in _NEG_MARKERS)
    return na != nb


_TITLE_TRAIL = re.compile(r"[\s?!.,:;·]+$")


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


def _clash_kind(existing: str, incoming: str) -> str:
    """Name the kind of textual contradiction, or "" if none is detectable.

    Both checks are textual. Neither reads meaning — a semantic contradiction
    with no shared wording needs the LLM extractor to catch it.
    """
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
