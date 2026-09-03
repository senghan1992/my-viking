"""Traces, observations and scores — the observability layer.

Modelled on the shape Langfuse uses, because that shape is the right one:

* a **trace** is one unit of agent work (a task, a turn),
* **observations** are the steps inside it (retrieval, cache lookup, generation),
* **scores** are judgements attached afterwards (by you, or by the agent).

The reason this is not just monitoring: a score on a trace tells us which stored
context was in the room when the answer was good. ``apply_score`` feeds that back
into memory confidence, so the store gets better at being useful rather than
merely bigger. Latency is recorded per step for the same reason — "faster and
better" is only actionable if you can see which step was slow.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .db import Database

TRACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id          TEXT PRIMARY KEY,
    scope       TEXT NOT NULL,
    name        TEXT DEFAULT '',
    agent       TEXT DEFAULT '',
    session_id  TEXT DEFAULT '',
    started     TEXT NOT NULL,
    ended       TEXT DEFAULT '',
    -- latency_ms is the time MyViking spent assembling context (what this
    -- service controls). total_ms additionally covers the model generation the
    -- caller reports back, so a slow request can be attributed to the right half.
    latency_ms  INTEGER DEFAULT 0,
    total_ms    INTEGER DEFAULT 0,
    input       TEXT DEFAULT '',
    output      TEXT DEFAULT '',
    tokens_in   INTEGER DEFAULT 0,
    tokens_out  INTEGER DEFAULT 0,
    cache_hit   TEXT DEFAULT '',
    status      TEXT DEFAULT 'ok',
    metadata    TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_traces_scope ON traces(scope, started DESC);
CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id);

CREATE TABLE IF NOT EXISTS observations (
    id          TEXT PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    parent_id   TEXT DEFAULT '',
    type        TEXT DEFAULT 'span',
    name        TEXT DEFAULT '',
    started     TEXT NOT NULL,
    ended       TEXT DEFAULT '',
    latency_ms  INTEGER DEFAULT 0,
    input       TEXT DEFAULT '',
    output      TEXT DEFAULT '',
    tokens_in   INTEGER DEFAULT 0,
    tokens_out  INTEGER DEFAULT 0,
    model       TEXT DEFAULT '',
    level       TEXT DEFAULT 'info',
    metadata    TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_obs_trace ON observations(trace_id, started);

CREATE TABLE IF NOT EXISTS scores (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id       TEXT DEFAULT '',
    observation_id TEXT DEFAULT '',
    scope          TEXT NOT NULL,
    name           TEXT NOT NULL,
    value          REAL NOT NULL,
    comment        TEXT DEFAULT '',
    source         TEXT DEFAULT 'human',
    created        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_trace ON scores(trace_id);
CREATE INDEX IF NOT EXISTS idx_scores_scope ON scores(scope, created DESC);

-- Which stored context was actually in the room for a trace. This is what makes
-- a score actionable: it names the memories that earned (or cost) the outcome.
CREATE TABLE IF NOT EXISTS context_used (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    scope    TEXT NOT NULL,
    uri      TEXT NOT NULL,
    tier     INTEGER DEFAULT 0,
    tokens   INTEGER DEFAULT 0,
    score    REAL DEFAULT 0,
    -- The confidence adjustment actually attributed to this memory for this
    -- trace's outcome (see Jarvis.score). Blame is weighted and floored, so a
    -- memory that merely rode along in a bad trace keeps applied=0 and is never
    -- mistaken for the one that caused it.
    applied  REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ctxused_trace ON context_used(trace_id);
CREATE INDEX IF NOT EXISTS idx_ctxused_uri ON context_used(uri);

-- Agents that have connected, so you can see what is wired up from where.
CREATE TABLE IF NOT EXISTS agents (
    name      TEXT PRIMARY KEY,
    client    TEXT DEFAULT '',
    host      TEXT DEFAULT '',
    projects  TEXT DEFAULT '[]',
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    calls     INTEGER DEFAULT 0
);

-- Repository/path fingerprints so the same project resolves from any machine.
CREATE TABLE IF NOT EXISTS project_aliases (
    alias   TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    kind    TEXT DEFAULT 'repo',
    created TEXT NOT NULL
);
"""


def now_iso() -> str:
    """Timestamps keep milliseconds: observation ordering depends on them, and
    a retrieval step and the generation that follows it routinely land in the
    same second."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _json(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


@dataclass
class Observation:
    id: str
    trace_id: str
    type: str
    name: str
    started_at: float
    parent_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class Tracer:
    """Writes traces. Cheap enough to leave on: a handful of INSERTs per task."""

    def __init__(self, db: Database):
        self.db = db
        self.db.conn.executescript(TRACE_SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        have = {r["name"] for r in self.db.query("PRAGMA table_info(traces)")}
        if "total_ms" not in have:
            self.db.conn.execute("ALTER TABLE traces ADD COLUMN total_ms INTEGER DEFAULT 0")
        ctx = {r["name"] for r in self.db.query("PRAGMA table_info(context_used)")}
        if "applied" not in ctx:
            self.db.conn.execute("ALTER TABLE context_used ADD COLUMN applied REAL DEFAULT 0")

    # ----- traces ------------------------------------------------------
    def start_trace(
        self,
        scope: str,
        name: str,
        input_text: str = "",
        agent: str = "",
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
        trace_id: str = "",
    ) -> tuple[str, float]:
        tid = trace_id or new_id("tr")
        self.db.execute(
            "INSERT OR REPLACE INTO traces (id, scope, name, agent, session_id,"
            " started, input, metadata) VALUES (?,?,?,?,?,?,?,?)",
            (
                tid,
                scope,
                name,
                agent,
                session_id,
                now_iso(),
                input_text[:4000],
                _json(metadata or {}),
            ),
        )
        self.db.commit()
        return tid, time.perf_counter()

    def end_trace(
        self,
        trace_id: str,
        t0: float,
        output_text: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        cache_hit: str = "",
        status: str = "ok",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        latency = int((time.perf_counter() - t0) * 1000)
        sets = [
            "ended = ?",
            "latency_ms = ?",
            "total_ms = ?",
            "output = ?",
            "tokens_in = ?",
            "tokens_out = ?",
            "cache_hit = ?",
            "status = ?",
        ]
        params: list[Any] = [
            now_iso(),
            latency,
            latency,
            output_text[:8000],
            int(tokens_in),
            int(tokens_out),
            cache_hit,
            status,
        ]
        if metadata is not None:
            sets.append("metadata = ?")
            params.append(_json(metadata))
        params.append(trace_id)
        self.db.execute(f"UPDATE traces SET {', '.join(sets)} WHERE id = ?", params)
        self.db.commit()
        return latency

    # ----- observations ------------------------------------------------
    def start_observation(
        self,
        trace_id: str,
        type: str,
        name: str,
        parent_id: str = "",
        input_text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Observation:
        oid = new_id("ob")
        self.db.execute(
            "INSERT INTO observations (id, trace_id, parent_id, type, name,"
            " started, input, metadata) VALUES (?,?,?,?,?,?,?,?)",
            (
                oid,
                trace_id,
                parent_id,
                type,
                name,
                now_iso(),
                input_text[:2000],
                _json(metadata or {}),
            ),
        )
        self.db.commit()
        return Observation(oid, trace_id, type, name, time.perf_counter(), parent_id)

    def end_observation(
        self,
        obs: Observation,
        output_text: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        model: str = "",
        level: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        latency = int((time.perf_counter() - obs.started_at) * 1000)
        self.db.execute(
            "UPDATE observations SET ended=?, latency_ms=?, output=?, tokens_in=?,"
            " tokens_out=?, model=?, level=?, metadata=? WHERE id=?",
            (
                now_iso(),
                latency,
                output_text[:4000],
                int(tokens_in),
                int(tokens_out),
                model,
                level,
                _json(metadata if metadata is not None else obs.metadata),
                obs.id,
            ),
        )
        self.db.commit()
        return latency

    def event(
        self,
        trace_id: str,
        type: str,
        name: str,
        output_text: str = "",
        latency_ms: int = 0,
        metadata: dict[str, Any] | None = None,
        level: str = "info",
    ) -> str:
        """A point-in-time observation with no duration of its own."""
        oid = new_id("ob")
        stamp = now_iso()
        self.db.execute(
            "INSERT INTO observations (id, trace_id, type, name, started, ended,"
            " latency_ms, output, level, metadata) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                oid,
                trace_id,
                type,
                name,
                stamp,
                stamp,
                int(latency_ms),
                output_text[:4000],
                level,
                _json(metadata or {}),
            ),
        )
        self.db.commit()
        return oid

    def annotate(self, trace_id: str, extra: dict[str, Any]) -> None:
        """Merge fields into a trace's metadata without losing what is there."""
        row = self.db.one("SELECT metadata FROM traces WHERE id = ?", (trace_id,))
        if row is None:
            return
        current = _loads(row["metadata"]) or {}
        if not isinstance(current, dict):
            current = {"raw": current}
        current.update(extra)
        self.db.execute(
            "UPDATE traces SET metadata = ? WHERE id = ?", (_json(current), trace_id)
        )
        self.db.commit()

    def outcome_counts(self, scope: str = "", days: int = 7) -> dict[str, int]:
        """How the last answers landed, judged by what got asked next."""
        where, params = ["o.type = 'outcome'"], []
        if scope:
            where.append("t.scope = ?")
            params.append(scope)
        if days > 0:
            where.append("o.started >= datetime('now', ?)")
            params.append(f"-{int(days)} days")
        rows = self.db.query(
            "SELECT o.name AS kind, COUNT(*) AS n FROM observations o"
            f" JOIN traces t ON t.id = o.trace_id WHERE {' AND '.join(where)}"
            " GROUP BY o.name",
            params,
        )
        return {r["kind"]: r["n"] for r in rows}

    def unresolved_threads(
        self, scope: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Requests that were asked again — the ones that may still be open.

        This is the most useful thing a new session can be told: not "here is
        everything", but "last time this was asked twice and never settled".
        """
        rows = self.db.query(
            "SELECT t.id, t.input, t.output, t.started, t.session_id, o.name AS kind,"
            " o.output AS why FROM observations o JOIN traces t ON t.id = o.trace_id"
            " WHERE t.scope = ? AND o.type = 'outcome' AND o.name IN ('reworked','repeated')"
            " ORDER BY t.started DESC",
            (scope,),
        )
        out: list[dict[str, Any]] = []
        seen_sessions: set[str] = set()
        for r in rows:
            sid = r["session_id"] or ""
            # A re-ask chain (A→B→C on the same topic) marks every link. They are
            # one open thread, not three — keep only the most recent link.
            if sid and sid in seen_sessions:
                continue
            if sid:
                seen_sessions.add(sid)
            # ...and it is only still open if the session did not later land a
            # judged-good answer. If it did, the user got unstuck; drop it.
            if sid and self._session_settled_after(scope, sid, r["started"]):
                continue
            out.append(
                {
                    "trace_id": r["id"],
                    "question": r["input"],
                    "answer": (r["output"] or "")[:280],
                    "at": r["started"],
                    "session_id": r["session_id"],
                    "kind": r["kind"],
                    "why": r["why"],
                }
            )
            if len(out) >= limit:
                break
        return out

    def _session_settled_after(self, scope: str, session_id: str, started: str) -> bool:
        """Did a later trace in this session get a *stated* good judgement? Only an
        explicit score closes a thread — the mild ``moved_on`` implicit signal
        looks identical to giving up and doing it by hand, so it must not."""
        row = self.db.one(
            "SELECT 1 FROM scores s JOIN traces t ON t.id = s.trace_id"
            " WHERE t.scope = ? AND t.session_id = ? AND t.started > ?"
            " AND s.source != 'implicit' AND s.value >= 0.6 LIMIT 1",
            (scope, session_id, started),
        )
        return row is not None

    # ----- retention -----------------------------------------------------
    def prune(self, days: int) -> int:
        """Delete traces past retention, with everything hanging off them.

        Safe to lose: a score's learning value was applied to memory
        confidence the moment it arrived, and session handover only reads
        recent work. What retention protects is the cost of keeping — every
        listing query and every backup pays for rows nobody will read again.
        """
        if days <= 0:
            return 0
        ids = [
            r["id"]
            for r in self.db.query(
                "SELECT id FROM traces WHERE started < datetime('now', ?)",
                (f"-{int(days)} days",),
            )
        ]
        for start in range(0, len(ids), 500):  # SQLite 변수 한도(999) 아래로
            chunk = ids[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            for table in ("observations", "scores", "context_used"):
                self.db.execute(
                    f"DELETE FROM {table} WHERE trace_id IN ({marks})", chunk
                )
            self.db.execute(f"DELETE FROM traces WHERE id IN ({marks})", chunk)
        if ids:
            self.db.commit()
        return len(ids)

    # ----- context provenance -----------------------------------------
    def record_context(self, trace_id: str, scope: str, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        self.db.conn.executemany(
            "INSERT INTO context_used (trace_id, scope, uri, tier, tokens, score)"
            " VALUES (?,?,?,?,?,?)",
            [
                (
                    trace_id,
                    scope,
                    it["uri"],
                    int(it.get("tier", 0)),
                    int(it.get("tokens", 0)),
                    float(it.get("score", 0.0)),
                )
                for it in items
            ],
        )
        self.db.commit()

    def context_of(self, trace_id: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.query(
                "SELECT uri, tier, tokens, score FROM context_used WHERE trace_id=?"
                " ORDER BY score DESC",
                (trace_id,),
            )
        ]

    # ----- scores ------------------------------------------------------
    def add_score(
        self,
        scope: str,
        name: str,
        value: float,
        trace_id: str = "",
        observation_id: str = "",
        comment: str = "",
        source: str = "human",
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO scores (trace_id, observation_id, scope, name, value,"
            " comment, source, created) VALUES (?,?,?,?,?,?,?,?)",
            (
                trace_id,
                observation_id,
                scope,
                name,
                float(value),
                comment,
                source,
                now_iso(),
            ),
        )
        self.db.commit()
        return int(cur.lastrowid or 0)

    # ----- agents ------------------------------------------------------
    def touch_agent(
        self, name: str, client: str = "", host: str = "", project: str = ""
    ) -> None:
        if not name:
            return
        row = self.db.one("SELECT projects, calls FROM agents WHERE name=?", (name,))
        projects: list[str] = []
        calls = 0
        if row is not None:
            try:
                projects = list(json.loads(row["projects"]) or [])
            except Exception:
                projects = []
            calls = int(row["calls"])
        if project and project not in projects:
            projects.append(project)
        stamp = now_iso()
        if row is None:
            self.db.execute(
                "INSERT INTO agents (name, client, host, projects, first_seen,"
                " last_seen, calls) VALUES (?,?,?,?,?,?,?)",
                (name, client, host, _json(projects), stamp, stamp, 1),
            )
        else:
            self.db.execute(
                "UPDATE agents SET client=?, host=?, projects=?, last_seen=?,"
                " calls=? WHERE name=?",
                (client, host, _json(projects), stamp, calls + 1, name),
            )
        self.db.commit()

    def agents(self) -> list[dict[str, Any]]:
        out = []
        for r in self.db.query("SELECT * FROM agents ORDER BY last_seen DESC"):
            d = dict(r)
            try:
                d["projects"] = json.loads(d.get("projects") or "[]")
            except Exception:
                d["projects"] = []
            out.append(d)
        return out

    # ----- project aliases --------------------------------------------
    def bind_alias(self, alias: str, project: str, kind: str = "repo") -> None:
        if not alias:
            return
        self.db.execute(
            "INSERT INTO project_aliases (alias, project, kind, created)"
            " VALUES (?,?,?,?) ON CONFLICT(alias) DO UPDATE SET project=excluded.project",
            (alias.strip(), project, kind, now_iso()),
        )
        self.db.commit()

    def unbind_alias(self, alias: str) -> bool:
        cur = self.db.execute(
            "DELETE FROM project_aliases WHERE alias=?", (alias.strip(),)
        )
        self.db.commit()
        return bool(cur.rowcount)

    def resolve_alias(self, alias: str) -> str:
        row = self.db.one(
            "SELECT project FROM project_aliases WHERE alias=?", (alias.strip(),)
        )
        return row["project"] if row else ""

    def aliases(self, project: str = "") -> list[dict[str, Any]]:
        if project:
            rows = self.db.query(
                "SELECT * FROM project_aliases WHERE project=? ORDER BY created",
                (project,),
            )
        else:
            rows = self.db.query("SELECT * FROM project_aliases ORDER BY project")
        return [dict(r) for r in rows]

    # ----- reads -------------------------------------------------------
    def list_traces(
        self,
        scope: str = "",
        limit: int = 50,
        cursor: str = "",
        name: str = "",
        min_latency: int = 0,
    ) -> list[dict[str, Any]]:
        where, params = [], []
        if scope:
            where.append("t.scope = ?")
            params.append(scope)
        if name:
            where.append("t.name = ?")
            params.append(name)
        if cursor:
            where.append("t.started < ?")
            params.append(cursor)
        if min_latency:
            where.append("t.latency_ms >= ?")
            params.append(min_latency)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        params.append(limit)
        rows = self.db.query(
            "SELECT t.*, (SELECT AVG(value) FROM scores s WHERE s.trace_id = t.id)"
            f" AS avg_score, (SELECT COUNT(*) FROM observations o WHERE o.trace_id = t.id)"
            f" AS steps FROM traces t{clause} ORDER BY t.started DESC LIMIT ?",
            params,
        )
        return [_trace_row(r) for r in rows]

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM traces WHERE id = ?", (trace_id,))
        if row is None:
            return None
        trace = _trace_row(row)
        trace["observations"] = [
            _obs_row(r)
            for r in self.db.query(
                "SELECT * FROM observations WHERE trace_id=? ORDER BY started, rowid",
                (trace_id,),
            )
        ]
        trace["scores"] = [
            dict(r)
            for r in self.db.query(
                "SELECT * FROM scores WHERE trace_id=? ORDER BY created", (trace_id,)
            )
        ]
        trace["context"] = self.context_of(trace_id)
        return trace

    def work_sessions(
        self, scope: str = "", limit: int = 10
    ) -> list[dict[str, Any]]:
        """Traces grouped into the agent sessions they belonged to.

        A trace is one question; a session is a sitting. When you open a new
        session on a project you left two weeks ago, the useful unit is the
        sitting — "last time I was here I was chasing the webhook signature
        thing" — not a flat list of individual questions.
        """
        where, params = ["session_id != ''"], []
        if scope:
            where.append("scope = ?")
            params.append(scope)
        params.append(limit)
        rows = self.db.query(
            f"SELECT session_id, scope, MIN(started) AS started, MAX(started) AS ended,"
            f" COUNT(*) AS traces, SUM(total_ms) AS total_ms,"
            f" SUM(CASE WHEN cache_hit != '' THEN 1 ELSE 0 END) AS reused,"
            f" MAX(agent) AS agent FROM traces WHERE {' AND '.join(where)}"
            f" GROUP BY session_id ORDER BY started DESC LIMIT ?",
            params,
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            traces = self.db.query(
                "SELECT t.id, t.input, t.output, t.started, t.cache_hit, t.metadata,"
                " (SELECT AVG(value) FROM scores s WHERE s.trace_id = t.id) AS avg_score"
                " FROM traces t WHERE t.session_id = ? ORDER BY t.started",
                (row["session_id"],),
            )
            scored = [t["avg_score"] for t in traces if t["avg_score"] is not None]
            work = []
            session_files: list[str] = []
            for t in traces:
                md = _loads(t["metadata"])
                tfiles = [f for f in (md.get("files") or []) if f]
                for f in tfiles:
                    if f not in session_files:
                        session_files.append(f)
                work.append(
                    {
                        "trace_id": t["id"],
                        "question": t["input"],
                        "answer": t["output"],
                        "at": t["started"],
                        "reused": bool(t["cache_hit"]),
                        # How it landed, judged by what got asked next.
                        "outcome": (md.get("implicit_outcome") or {}).get("kind", ""),
                        "files": tfiles,
                        "score": (
                            round(t["avg_score"], 3)
                            if t["avg_score"] is not None
                            else None
                        ),
                    }
                )
            out.append(
                {
                    "session_id": row["session_id"],
                    "project": row["scope"],
                    "agent": row["agent"] or "",
                    "started": row["started"],
                    "ended": row["ended"],
                    "traces": row["traces"],
                    "reused": row["reused"] or 0,
                    "total_ms": row["total_ms"] or 0,
                    "avg_score": round(sum(scored) / len(scored), 3) if scored else None,
                    "files": session_files,
                    "work": work,
                }
            )
        return out

    def known_session(self, scope: str, session_id: str) -> bool:
        """Has this agent session already been seen on this project?"""
        if not session_id:
            return False
        row = self.db.one(
            "SELECT 1 FROM traces WHERE scope=? AND session_id=? LIMIT 1",
            (scope, session_id),
        )
        return row is not None

    def record_attribution(self, trace_id: str, deltas: dict[str, float]) -> None:
        """Persist how much of a scored outcome was pinned on each memory.

        ``deltas`` maps a context uri to the confidence adjustment ``score``
        actually applied to it (positive or negative). Accumulates, so a memory
        scored across several outcomes carries the sum of what it earned or cost.
        Only what is passed is touched; co-occurring memories that were spared by
        the blame floor keep ``applied`` untouched, which is the whole point.
        """
        for uri, delta in deltas.items():
            self.db.execute(
                "UPDATE context_used SET applied = applied + ?"
                " WHERE trace_id = ? AND uri = ?",
                (float(delta), trace_id, uri),
            )
        self.db.commit()

    def scores_for_uri(self, uri: str) -> dict[str, Any]:
        """How did traces that used this memory turn out?

        ``avg_score`` is the raw average outcome of every trace this memory rode
        in — fine for display, but it cannot tell a culprit from a bystander.
        ``harm`` is the total *attributed* demotion this memory actually took
        (see ``record_attribution``); that is the honest basis for calling a
        memory harmful, because a bad answer's blame lands only on what drove it.
        """
        row = self.db.one(
            "SELECT COUNT(DISTINCT c.trace_id) AS uses, AVG(s.value) AS avg_score,"
            " COUNT(s.id) AS scored FROM context_used c"
            " LEFT JOIN scores s ON s.trace_id = c.trace_id WHERE c.uri = ?",
            (uri,),
        )
        harm = self.db.one(
            "SELECT SUM(applied) AS harm FROM context_used"
            " WHERE uri = ? AND applied < 0",
            (uri,),
        )
        return {
            "uri": uri,
            "uses": (row["uses"] if row else 0) or 0,
            "scored": (row["scored"] if row else 0) or 0,
            "avg_score": round(row["avg_score"], 4) if row and row["avg_score"] is not None else None,
            "harm": round(harm["harm"], 4) if harm and harm["harm"] is not None else None,
        }


# --------------------------------------------------------------------------
# row shaping
# --------------------------------------------------------------------------
def _loads(value: Any) -> Any:
    if not value:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return {"raw": str(value)}


def _trace_row(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["metadata"] = _loads(d.get("metadata"))
    if d.get("avg_score") is not None:
        d["avg_score"] = round(float(d["avg_score"]), 4)
    return d


def _obs_row(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["metadata"] = _loads(d.get("metadata"))
    return d


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * pct)))
    return int(ordered[idx])


def metrics(db: Database, scope: str = "", days: int = 7) -> dict[str, Any]:
    """Dashboard numbers, ordered by what actually matters day to day.

    Speed and quality come first; token accounting is kept but demoted to a
    secondary panel. Cheap answers that are wrong or slow are not the goal.
    """
    where, params = ["ended != ''"], []
    if scope:
        where.append("scope = ?")
        params.append(scope)
    if days > 0:
        where.append("started >= datetime('now', ?)")
        params.append(f"-{int(days)} days")
    clause = " WHERE " + " AND ".join(where)

    rows = db.query(
        f"SELECT latency_ms, total_ms, cache_hit, tokens_in, tokens_out, name,"
        f" status FROM traces{clause}",
        params,
    )
    lat_all = [int(r["latency_ms"]) for r in rows]
    lat_hit = [int(r["latency_ms"]) for r in rows if r["cache_hit"]]
    lat_miss = [int(r["latency_ms"]) for r in rows if not r["cache_hit"]]
    total_all = [int(r["total_ms"] or r["latency_ms"]) for r in rows]
    total_hit = [
        int(r["total_ms"] or r["latency_ms"]) for r in rows if r["cache_hit"]
    ]
    total_miss = [
        int(r["total_ms"] or r["latency_ms"]) for r in rows if not r["cache_hit"]
    ]
    hits = len(lat_hit)

    score_rows = db.query(
        "SELECT name, AVG(value) v, COUNT(*) n FROM scores"
        + (" WHERE scope = ?" if scope else "")
        + " GROUP BY name",
        [scope] if scope else [],
    )

    step_rows = db.query(
        "SELECT o.type AS type, COUNT(*) n, AVG(o.latency_ms) avg_ms,"
        " MAX(o.latency_ms) max_ms FROM observations o"
        + (" JOIN traces t ON t.id = o.trace_id WHERE t.scope = ?" if scope else "")
        + " GROUP BY o.type ORDER BY avg_ms DESC",
        [scope] if scope else [],
    )

    outcomes = {}
    for r in db.query(
        "SELECT o.name AS kind, COUNT(*) AS n FROM observations o"
        " JOIN traces t ON t.id = o.trace_id WHERE o.type = 'outcome'"
        + (" AND t.scope = ?" if scope else "")
        + " GROUP BY o.name",
        [scope] if scope else [],
    ):
        outcomes[r["kind"]] = r["n"]
    judged = sum(outcomes.values())
    settled = outcomes.get("moved_on", 0)

    return {
        "scope": scope or "(all)",
        "days": days,
        "traces": len(rows),
        # Judged by what got asked next, not by anyone filing a rating.
        "outcomes": outcomes,
        "first_try_rate": round(settled / judged, 4) if judged else None,
        "rework_rate": (
            round(
                (outcomes.get("reworked", 0) + outcomes.get("repeated", 0)) / judged, 4
            )
            if judged
            else None
        ),
        "errors": sum(1 for r in rows if r["status"] != "ok"),
        # "context" = what MyViking spent; "answer" = what the user waited for.
        "context_ms": {
            "p50": _percentile(lat_all, 0.50),
            "p95": _percentile(lat_all, 0.95),
            "p50_cache_hit": _percentile(lat_hit, 0.50),
            "p50_retrieval": _percentile(lat_miss, 0.50),
        },
        "answer_ms": {
            "p50": _percentile(total_all, 0.50),
            "p95": _percentile(total_all, 0.95),
            "p50_reused": _percentile(total_hit, 0.50),
            "p50_generated": _percentile(total_miss, 0.50),
        },
        "reuse": {
            "hits": hits,
            "rate": round(hits / len(rows), 4) if rows else 0.0,
        },
        "scores": [
            {"name": r["name"], "avg": round(float(r["v"]), 4), "count": r["n"]}
            for r in score_rows
        ],
        "steps": [
            {
                "type": r["type"],
                "count": r["n"],
                "avg_ms": int(r["avg_ms"] or 0),
                "max_ms": int(r["max_ms"] or 0),
            }
            for r in step_rows
        ],
        "tokens": {
            "in": sum(int(r["tokens_in"] or 0) for r in rows),
            "out": sum(int(r["tokens_out"] or 0) for r in rows),
        },
    }


def timeseries(db: Database, scope: str = "", days: int = 14) -> list[dict[str, Any]]:
    """Per-day latency / reuse / score, for the dashboard sparklines."""
    params: list[Any] = []
    where = ["ended != ''"]
    if scope:
        where.append("scope = ?")
        params.append(scope)
    where.append("started >= datetime('now', ?)")
    params.append(f"-{int(days)} days")
    rows = db.query(
        f"SELECT substr(started, 1, 10) AS day, COUNT(*) n,"
        f" AVG(latency_ms) avg_ms, AVG(total_ms) avg_total_ms,"
        f" SUM(CASE WHEN cache_hit != '' THEN 1 ELSE 0 END) hits"
        f" FROM traces WHERE {' AND '.join(where)} GROUP BY day ORDER BY day",
        params,
    )
    return [
        {
            "day": r["day"],
            "traces": r["n"],
            "avg_ms": int(r["avg_ms"] or 0),
            "avg_total_ms": int(r["avg_total_ms"] or 0),
            "reuse_rate": round((r["hits"] or 0) / r["n"], 4) if r["n"] else 0.0,
        }
        for r in rows
    ]


def memory_impact(db: Database, scope: str, limit: int = 20) -> list[dict[str, Any]]:
    """Which stored context shows up in good outcomes, and which in bad ones.

    This is the report that tells you what to keep. A memory used often with no
    scores is unproven, not useful; one used often with low scores is actively
    hurting and should be corrected or archived.
    """
    rows = db.query(
        "SELECT c.uri AS uri, COUNT(DISTINCT c.trace_id) AS uses,"
        " AVG(s.value) AS avg_score, COUNT(s.id) AS scored,"
        " AVG(c.tokens) AS avg_tokens FROM context_used c"
        " LEFT JOIN scores s ON s.trace_id = c.trace_id"
        " WHERE c.scope = ? GROUP BY c.uri ORDER BY uses DESC LIMIT ?",
        (scope, limit),
    )
    return [
        {
            "uri": r["uri"],
            "uses": r["uses"],
            "scored": r["scored"] or 0,
            "avg_score": round(float(r["avg_score"]), 3) if r["avg_score"] is not None else None,
            "avg_tokens": int(r["avg_tokens"] or 0),
        }
        for r in rows
    ]
