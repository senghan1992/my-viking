"""SQLite index: the searchable mirror of the markdown store.

The markdown files under ``$JARVIS_HOME/projects`` are the source of truth —
you can read, diff and hand-edit them. This database is a disposable index for
fast lookup; ``jv reindex`` rebuilds it from the files at any time.
"""

from __future__ import annotations

import json
import re
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
        # A corrupt file (half-written volume, bad disk) raises on the first
        # statement. Under `restart: unless-stopped` that is a crash loop nobody
        # sees, so move it aside and start fresh instead — memories are rebuilt
        # from the files; keys/traces come back from the backup. Say so loudly.
        self.recovered_from: str = ""
        try:
            self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self.conn.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError as exc:
            from datetime import datetime, timezone

            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            aside = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
            try:
                self.conn.close()
            except Exception:
                pass
            for suffix in ("", "-wal", "-shm"):
                p = self.path.with_name(self.path.name + suffix)
                if p.exists():
                    p.rename(aside.with_name(aside.name + suffix))
            self.recovered_from = str(aside)
            print(
                f"[myviking] 색인 DB 가 손상되어 옆으로 치웠습니다 ({aside.name}): {exc}\n"
                "           메모리는 파일에서 다시 색인됩니다. API 키·작업 이력은 백업에서 복원하세요.",
                flush=True,
            )
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
        self._restrict_permissions()

    def _restrict_permissions(self) -> None:
        """The index holds everything private: memories, hashed keys, traces. It
        must not be world-readable on a shared box. Best-effort (chmod is a no-op
        or unsupported on some filesystems); the WAL/SHM siblings hold the same
        content, so they get the same treatment once the first write creates them."""
        for suffix in ("", "-wal", "-shm"):
            p = self.path.with_name(self.path.name + suffix)
            try:
                if p.exists():
                    p.chmod(0o600)
            except OSError:
                pass

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
    # One sqlite connection is shared across FastAPI's request threadpool
    # (check_same_thread=False). A connection is *not* safe for two threads to
    # drive at once: overlapping a cursor here with any other statement raises
    # "bad parameter or other API misuse". Concurrent reads alone are enough to
    # trigger it — the dashboard fires several at once. So every touch of the
    # connection goes through the reentrant lock. It is the same RLock the
    # service-layer @_locked mutators hold, so a mutator that calls query()
    # re-enters rather than deadlocks, and read-only callers serialise cheaply
    # (statements finish in microseconds on a personal server).
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self.lock:
            return list(self.conn.execute(sql, tuple(params)).fetchall())

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    def commit(self) -> None:
        with self.lock:
            self.conn.commit()

    # ----- meta (small operational key/values) -------------------------
    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (k, v) VALUES (?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, value),
        )
        self.conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT v FROM meta WHERE k = ?", (key,))
        return row["v"] if row else default

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

    def checkpoint(self) -> None:
        """Fold the write-ahead log back into the main file and truncate it.

        In WAL mode deletes only ever append; without an explicit checkpoint the
        ``-wal`` sidecar grows unbounded on an always-on server even as the
        retention sweep frees rows, so the on-disk footprint never actually
        shrinks. Run this after a sweep, not on the request path. Best-effort:
        a checkpoint blocked by a live reader must not fail the sweep."""
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            pass

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


# Question filler that says nothing about the subject. Case memories index the
# question they answered, so without this a prompt ending in "고쳐줘" lexically
# matched every past request that also ended in "고쳐줘" — and a lexical hit is
# the one relevance signal the offline embedder cannot fake.
_STOP_TERMS = frozenset(
    """
    어떻게 어떡 어디 언제 왜 뭐 뭘 무엇 어떤 어느 얼마 몇
    해줘 해주 해줄 해야 해도 하면 하는 하게 하고 해서 했어 했는데 할까 할지 하자 하지
    고쳐줘 고쳐 수정해 바꿔줘 바꿔 만들어줘 만들어 알려줘 알려 보여줘 보여 봐줘 봐 찾아줘 찾아
    돌려줘 돌려 확인해 정리해 추가해 지워 삭제해 적용해 설명해 검토해
    좀 이거 그거 저거 이건 그건 이게 그게 이런 그런 여기 거기 지금 다시 먼저 그냥 계속 한번
    있는 없는 있어 없어 있나 없나 되나 되는 된다 안돼 안되 되게 같아 같은 관련 대해 대한 위해 위한 통해
    please how what which where when why does should could would want need make let just also
    the this that these those with from into about your our and for are was were will can
    """.split()
)

# Korean particles and the common verb/adjective endings that attach to a
# subject word: "배포는", "결제를", "서버에서", "배포할", "실패하면", "재시도해도".
# unicode61 indexes "배포는" as one token, so the bare form in a memory never
# matched the inflected form in a prompt (or the reverse). Longest first.
_KO_SUFFIXES = (
    "하겠습니다", "했습니다", "합니다", "하세요", "하는데", "했는데", "하려면", "하면서",
    "에서는", "에게는", "으로는", "해야지", "해야", "해도", "하지", "하면", "하고", "해서",
    "하는", "하던", "한다", "했다", "하기", "하며", "할까", "할지", "하나", "하니", "했어",
    "해요", "부터", "까지", "에서", "으로", "에게", "처럼", "보다", "이나", "이랑", "께서",
    "마다", "조차", "밖에", "이다", "인가", "인데", "이고", "이며",
    "은", "는", "이", "가", "을", "를", "도", "로", "에", "의", "와", "과", "만", "나", "랑",
    "든", "할", "한", "함", "해",
)
_KO_ONLY = re.compile(r"^[가-힣]+$")
_LATIN_ONLY = re.compile(r"^[0-9A-Za-z]+$")


def ko_stem(term: str) -> str:
    """The word with one trailing particle or ending removed, when a stem of
    at least two syllables remains: "배포는" → "배포", "재시도해도" → "재시도",
    "실패하면" → "실패". "로그인"/"이미지" are left alone — an earlier version
    shortened words blindly and "로그*" matched a note about log locations."""
    if not _KO_ONLY.match(term):
        return term
    for suf in _KO_SUFFIXES:
        if term.endswith(suf) and len(term) - len(suf) >= 2:
            return term[: -len(suf)]
    return term


def _fts_query(query: str) -> str:
    """Turn free text into a safe OR-ed FTS5 match expression.

    Each subject term becomes one prefix clause (``"배포"*`` finds "배포",
    "배포한다" and "배포는"); Korean terms are stemmed first (``ko_stem``), so
    a prompt's "재시도해도" reaches a memory's "재시도하지". One clause per
    term — an exact clause next to the prefix clause counted the same match
    twice and could lift a bystander above the real answer. Short Latin terms
    ("PR", "ci") match exactly, and filler words are dropped — they matched
    everything and meant nothing.
    """
    terms = [t for t in _split_terms(query) if t and t.lower() not in _STOP_TERMS]
    if not terms:
        return '""'
    parts: list[str] = []
    seen: set[str] = set()

    def add(expr: str) -> None:
        if expr not in seen:
            seen.add(expr)
            parts.append(expr)

    for t in terms:
        stem = ko_stem(t) if _KO_ONLY.match(t) else t
        if stem.lower() in _STOP_TERMS:
            stem = t
        if _LATIN_ONLY.match(stem) and len(stem) < 4:
            add(f'"{stem}"')
        elif len(stem) >= 2:
            add(f'"{stem}"*')
        else:
            add(f'"{t}"')
    return " OR ".join(parts)


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
