"""Session recording and the answer cache.

Every exchange you commit becomes a session node — the raw material the
distiller later turns into memory, and the corpus the cache searches.

The cache has two levels:

* **exact** — normalised-question hash. Free, instant, zero risk.
* **near** — cosine similarity over question vectors above a threshold. This is
  where real spend disappears: a repeated question costs 0 input tokens instead
  of a full prompt.

A near hit is always reported with its similarity and the original question so
you can see what was reused rather than trusting it blindly.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .embed import batch_cosine, pack_vector
from .models import KIND_SESSION, Node, Uri, now_iso, slugify
from .store import Store
from .tokens import estimate_tokens, truncate_to_tokens

_WS = re.compile(r"\s+")


def normalize_question(q: str) -> str:
    """Collapse whitespace/case/punctuation so trivial edits still hit the cache."""
    s = _WS.sub(" ", (q or "").strip().lower())
    return re.sub(r"[?!.,·]+$", "", s)


def question_hash(q: str) -> str:
    return hashlib.sha256(normalize_question(q).encode("utf-8")).hexdigest()[:32]


@dataclass
class CacheHit:
    kind: str  # "exact" | "near"
    similarity: float
    question: str
    answer: str
    created: str
    tokens_saved: int
    session_uri: str = ""
    cache_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "similarity": round(self.similarity, 4),
            "question": self.question,
            "answer": self.answer,
            "created": self.created,
            "tokens_saved": self.tokens_saved,
            "session_uri": self.session_uri,
        }


class SessionLog:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db
        self.embedder = store.embedder

    # ------------------------------------------------------------------
    def record(
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
        cache: bool = True,
    ) -> Node:
        """Store one exchange and (optionally) make it cacheable."""
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stub = slugify(question[:48], "session")
        short = question_hash(question)[:8]
        uri = Uri(project, ("sessions", day, f"{stub}-{short}"))

        transcript = f"## Q\n{question.strip()}\n\n## A\n{answer.strip()}"
        node = Node(
            uri=uri,
            kind=KIND_SESSION,
            title=truncate_to_tokens(question.strip().splitlines()[0], 24) if question.strip() else "session",
            category="session",
            abstract="",
            overview="",
            body=transcript,
            tags=tags or [],
            confidence=0.4,
            sources=[prompt_uri] if prompt_uri else [],
            extra={
                "question": question,
                "answer_tokens": estimate_tokens(answer),
                "model": model,
                "tokens_in": int(tokens_in),
                "tokens_out": int(tokens_out),
                "prompt_uri": prompt_uri,
                "outcome": outcome,
                "distilled": False,
            },
        )
        # A session's abstract is the question: that is exactly what a future
        # lookup will be matching against.
        node.abstract = truncate_to_tokens(question.strip(), 100)
        node.overview = truncate_to_tokens(
            f"Q: {question.strip()}\nA: {answer.strip()}", 2000
        )
        node = self.store.write_node(node, regenerate_tiers=False)

        self.db.log_usage(
            now_iso(),
            project,
            "session_record",
            uri=str(uri),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            detail={"model": model, "outcome": outcome},
        )
        if cache and answer.strip():
            self.cache_put(
                project,
                question,
                answer,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                session_uri=str(uri),
            )
        return node

    # ------------------------------------------------------------------
    def cache_put(
        self,
        project: str,
        question: str,
        answer: str,
        model: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        session_uri: str = "",
    ) -> int:
        vec = self.embedder.embed(question)
        qh = question_hash(question)
        existing = self.db.one(
            "SELECT id FROM cache WHERE scope=? AND qhash=?", (project, qh)
        )
        if existing:
            self.db.execute(
                "UPDATE cache SET answer=?, model=?, tokens_in=?, tokens_out=?,"
                " session_uri=?, vector=? WHERE id=?",
                (
                    answer,
                    model,
                    int(tokens_in),
                    int(tokens_out),
                    session_uri,
                    pack_vector(vec),
                    existing["id"],
                ),
            )
            self.db.commit()
            return int(existing["id"])
        cur = self.db.execute(
            "INSERT INTO cache (scope, qhash, question, answer, model, tokens_in,"
            " tokens_out, created, vector) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                project,
                qh,
                question,
                answer,
                model,
                int(tokens_in),
                int(tokens_out),
                now_iso(),
                pack_vector(vec),
            ),
        )
        self.db.commit()
        return int(cur.lastrowid or 0)

    def cache_lookup(
        self,
        project: str,
        question: str,
        threshold: float | None = None,
        include_global: bool = False,
    ) -> CacheHit | None:
        """Return a reusable prior answer, or None."""
        thr = (
            threshold
            if threshold is not None
            else self.store.config.budget.cache_hit_threshold
        )
        scopes = [project] + (["global"] if include_global and project != "global" else [])
        marks = ", ".join("?" for _ in scopes)

        qh = question_hash(question)
        row = self.db.one(
            f"SELECT * FROM cache WHERE scope IN ({marks}) AND qhash=?",
            [*scopes, qh],
        )
        if row is not None:
            return self._hit(row, "exact", 1.0)

        qvec = self.embedder.embed(question)
        rows = self.db.query(f"SELECT * FROM cache WHERE scope IN ({marks})", scopes)
        if not rows:
            return None
        sims = batch_cosine(qvec, [r["vector"] for r in rows])
        best_i = max(range(len(rows)), key=lambda i: sims[i])
        if sims[best_i] >= thr:
            return self._hit(rows[best_i], "near", sims[best_i])
        return None

    def _hit(self, row: Any, kind: str, sim: float) -> CacheHit:
        saved = int(row["tokens_in"] or 0) + int(row["tokens_out"] or 0)
        if saved == 0:
            saved = estimate_tokens(row["question"]) + estimate_tokens(row["answer"])
        self.db.execute(
            "UPDATE cache SET hits = hits + 1, last_used = ? WHERE id = ?",
            (now_iso(), row["id"]),
        )
        self.db.log_usage(
            now_iso(),
            row["scope"],
            "cache_hit",
            uri=row["session_uri"] or "",
            tokens_saved=saved,
            baseline=saved,
            detail={"kind": kind, "similarity": round(sim, 4)},
        )
        self.db.commit()
        return CacheHit(
            kind=kind,
            similarity=sim,
            question=row["question"],
            answer=row["answer"],
            created=row["created"] or "",
            tokens_saved=saved,
            session_uri=row["session_uri"] or "",
            cache_id=int(row["id"]),
        )

    def cache_prune(self, keep_per_project: int) -> int:
        """Cap the answer cache per project, most recently useful first.

        The near-miss lookup scans every cached row of its scope, so an
        unbounded cache is a tax on *every* prompt, not just a storage cost.
        """
        if keep_per_project <= 0:
            return 0
        deleted = 0
        scopes = [r["scope"] for r in self.db.query("SELECT DISTINCT scope FROM cache")]
        for scope in scopes:
            cur = self.db.execute(
                "DELETE FROM cache WHERE scope=? AND id NOT IN ("
                " SELECT id FROM cache WHERE scope=?"
                " ORDER BY COALESCE(last_used, created) DESC LIMIT ?)",
                (scope, scope, keep_per_project),
            )
            deleted += cur.rowcount or 0
        if deleted:
            self.db.commit()
        return deleted

    def cache_clear(self, project: str) -> int:
        cur = self.db.execute("DELETE FROM cache WHERE scope=?", (project,))
        self.db.commit()
        return cur.rowcount or 0

    def cache_list(self, project: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id, question, hits, tokens_in, tokens_out, created, last_used"
            " FROM cache WHERE scope=? ORDER BY hits DESC, created DESC LIMIT ?",
            (project, limit),
        )
        return [
            {
                "id": r["id"],
                "question": r["question"][:160],
                "hits": r["hits"],
                "tokens": (r["tokens_in"] or 0) + (r["tokens_out"] or 0),
                "created": r["created"],
                "last_used": r["last_used"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    def undistilled(self, project: str, limit: int = 50) -> list[Node]:
        """Sessions the learner has not folded into memory yet."""
        rows = self.db.query(
            "SELECT uri FROM nodes WHERE scope=? AND kind=? ORDER BY created ASC",
            (project, KIND_SESSION),
        )
        out: list[Node] = []
        for r in rows:
            node = self.store.read_node(Uri.parse(r["uri"]))
            if node is None or node.extra.get("distilled"):
                continue
            out.append(node)
            if len(out) >= limit:
                break
        return out

    def mark_distilled(self, node: Node, memories: list[str]) -> None:
        node.extra["distilled"] = True
        node.extra["distilled_at"] = now_iso()
        node.extra["memories"] = memories
        self.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)

    def recent(self, project: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT uri, title, abstract, created, tokens_l2 FROM nodes"
            " WHERE scope=? AND kind=? ORDER BY created DESC LIMIT ?",
            (project, KIND_SESSION, limit),
        )
        return [
            {
                "uri": r["uri"],
                "title": r["title"],
                "question": r["abstract"],
                "created": r["created"],
                "tokens": r["tokens_l2"],
            }
            for r in rows
        ]
