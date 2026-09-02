"""SQLite index: the searchable mirror of the markdown store.

The markdown files under ``$JARVIS_HOME/projects`` are the source of truth —
you can read, diff and hand-edit them. This database is a disposable index for
fast lookup; ``jv reindex`` rebuilds it from the files at any time.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
-- The maintenance sweep runs on its own connection, so a writer can briefly
-- collide with a request. Wait rather than fail.
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS nodes (
    uri         TEXT PRIMARY KEY,
    scope       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    category    TEXT DEFAULT '',
    title       TEXT DEFAULT '',
    abstract    TEXT DEFAULT '',
    overview    TEXT DEFAULT '',
    tags        TEXT DEFAULT '[]',
    confidence  REAL DEFAULT 0.5,
    hits        INTEGER DEFAULT 0,
    created     TEXT,
    updated     TEXT,
    last_used   TEXT DEFAULT '',
    tokens_l0   INTEGER DEFAULT 0,
    tokens_l1   INTEGER DEFAULT 0,
    tokens_l2   INTEGER DEFAULT 0,
    path        TEXT,
    parent      TEXT DEFAULT '',
    vector      BLOB
);
CREATE INDEX IF NOT EXISTS idx_nodes_scope ON nodes(scope, kind);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent);

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    uri UNINDEXED, text, tokenize='unicode61 remove_diacritics 2'
);

-- Directory-level centroids power the coarse-to-fine retrieval walk.
-- We store the *unnormalised sum* of descendant vectors plus a count, so a
-- write updates only its own ancestors (O(depth)) instead of forcing a rescan
-- of the whole project. The centroid is sum/children, normalised on read.
CREATE TABLE IF NOT EXISTS dirs (
    uri       TEXT PRIMARY KEY,
    scope     TEXT NOT NULL,
    kind      TEXT DEFAULT '',
    children  INTEGER DEFAULT 0,
    abstract  TEXT DEFAULT '',
    updated   TEXT,
    vector    BLOB
);

-- Answer cache: the single biggest token saver.
CREATE TABLE IF NOT EXISTS cache (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL,
    qhash      TEXT NOT NULL,
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL,
    model      TEXT DEFAULT '',
    tokens_in  INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    created    TEXT,
    last_used  TEXT DEFAULT '',
    hits       INTEGER DEFAULT 0,
    session_uri TEXT DEFAULT '',
    vector     BLOB
);
CREATE INDEX IF NOT EXISTS idx_cache_scope ON cache(scope);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cache_qhash ON cache(scope, qhash);

-- Every token in and every token avoided, so savings are measured not claimed.
CREATE TABLE IF NOT EXISTS usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    scope       TEXT NOT NULL,
    event       TEXT NOT NULL,
    uri         TEXT DEFAULT '',
    tokens_in   INTEGER DEFAULT 0,
    tokens_out  INTEGER DEFAULT 0,
    tokens_saved INTEGER DEFAULT 0,
    baseline    INTEGER DEFAULT 0,
    detail      TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_usage_scope ON usage(scope, ts);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # One connection is shared across the server's request threads. The
        # sqlite3 module serialises individual statements (threadsafety 3),
        # but not *sequences* of them — two threads interleaving execute()s
        # before a commit() would publish each other's half-done work. Compound
        # mutations take this lock (see the service layer); reads stay free.
        import threading

        self.lock = threading.RLock()
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created.

        The index is rebuildable, but silently dropping someone's history to
        pick up a new column would be a poor trade.
        """
        have = {r["name"] for r in self.query("PRAGMA table_info(nodes)")}
        for column, ddl in (("overview", "TEXT DEFAULT ''"),):
            if column not in have:
                self.conn.execute(f"ALTER TABLE nodes ADD COLUMN {column} {ddl}")

    def close(self) -> None:
        self.conn.close()

    # ----- generic helpers ---------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, tuple(params)).fetchall())

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(params)).fetchone()

    def commit(self) -> None:
        self.conn.commit()

    # ----- nodes -------------------------------------------------------
    def upsert_node(self, row: dict[str, Any]) -> None:
        row = dict(row)
        row["tags"] = json.dumps(row.get("tags") or [], ensure_ascii=False)
        cols = list(row)
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "uri")
        self.conn.execute(
            f"INSERT INTO nodes ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(uri) DO UPDATE SET {updates}",
            tuple(row[c] for c in cols),
        )

    def upsert_fts(self, uri: str, text: str) -> None:
        self.conn.execute("DELETE FROM nodes_fts WHERE uri = ?", (uri,))
        self.conn.execute(
            "INSERT INTO nodes_fts (uri, text) VALUES (?, ?)", (uri, text)
        )

    def delete_node(self, uri: str) -> None:
        self.conn.execute("DELETE FROM nodes WHERE uri = ?", (uri,))
        self.conn.execute("DELETE FROM nodes_fts WHERE uri = ?", (uri,))

    def clear_scope(self, scope: str) -> None:
        uris = [r["uri"] for r in self.query("SELECT uri FROM nodes WHERE scope=?", (scope,))]
        for uri in uris:
            self.delete_node(uri)
        self.conn.execute("DELETE FROM dirs WHERE scope=?", (scope,))

    def clear_all(self) -> None:
        for table in ("nodes", "nodes_fts", "dirs"):
            self.conn.execute(f"DELETE FROM {table}")

    # ----- dirs --------------------------------------------------------
    def upsert_dir(self, row: dict[str, Any]) -> None:
        cols = list(row)
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "uri")
        self.conn.execute(
            f"INSERT INTO dirs ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(uri) DO UPDATE SET {updates}",
            tuple(row[c] for c in cols),
        )

    def dir_row(self, uri: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM dirs WHERE uri = ?", (uri,))

    def prune_usage(self, days: int) -> int:
        """Drop token-accounting rows past retention. Dashboards read at most
        a few weeks of these; nothing else reads them at all."""
        if days <= 0:
            return 0
        cur = self.conn.execute(
            "DELETE FROM usage WHERE ts < datetime('now', ?)", (f"-{int(days)} days",)
        )
        self.conn.commit()
        return cur.rowcount or 0

    def prune_empty_dirs(self, scope: str) -> None:
        self.conn.execute("DELETE FROM dirs WHERE scope=? AND children <= 0", (scope,))

    # ----- usage -------------------------------------------------------
    def log_usage(
        self,
        ts: str,
        scope: str,
        event: str,
        uri: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        tokens_saved: int = 0,
        baseline: int = 0,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO usage (ts, scope, event, uri, tokens_in, tokens_out,"
            " tokens_saved, baseline, detail) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ts,
                scope,
                event,
                uri,
                int(tokens_in),
                int(tokens_out),
                int(tokens_saved),
                int(baseline),
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    # ----- search ------------------------------------------------------
    def fts_search(
        self, query: str, scope_in: Iterable[str], limit: int = 50
    ) -> list[tuple[str, float]]:
        scopes = list(scope_in)
        if not scopes or not query.strip():
            return []
        marks = ", ".join("?" for _ in scopes)
        try:
            rows = self.query(
                f"SELECT f.uri AS uri, bm25(nodes_fts) AS score FROM nodes_fts f "
                f"JOIN nodes n ON n.uri = f.uri "
                f"WHERE nodes_fts MATCH ? AND n.scope IN ({marks}) "
                f"ORDER BY score LIMIT ?",
                [_fts_query(query), *scopes, limit],
            )
        except sqlite3.OperationalError:
            return []
        # bm25 returns negative numbers where more negative is better.
        return [(r["uri"], -float(r["score"])) for r in rows]


def _fts_query(query: str) -> str:
    """Turn free text into a safe OR-ed FTS5 match expression."""
    terms = [t for t in _split_terms(query) if t]
    if not terms:
        return '""'
    return " OR ".join(f'"{t}"' for t in terms)


def _split_terms(query: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    for ch in query:
        if ch.isalnum() or ch in "가-힣" or ord(ch) > 0x2E80:
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return [t.replace('"', "") for t in out if len(t) > 1]
