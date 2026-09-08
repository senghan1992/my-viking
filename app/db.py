"""SQLite 데이터베이스 — 스키마와 질의 도우미.

지식의 원본은 SQLite 하나입니다 (index.db). 사람이 읽을 수 있는 마크다운
내보내기(export)는 대시보드에서 언제든 생성할 수 있습니다.

스키마는 앱 시작 시 CREATE TABLE IF NOT EXISTS 로 보장되며, 마이그레이션도
같은 자리에서 순서대로 실행됩니다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import config


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc(s: str | None) -> str:
    """저장된 ISO 문자열을 사람이 읽기 좋게 'YYYY-MM-DD HH:MM' 로."""
    if not s:
        return "-"
    try:
        return datetime.fromisoformat(s).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return s


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        pw_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',      -- 'admin' | 'user'
        disabled INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS projects(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        slug TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS api_keys(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL DEFAULT '',
        key_hash TEXT UNIQUE NOT NULL,          -- SHA-256, 평문은 저장하지 않음
        key_prefix TEXT NOT NULL,               -- 표시용 앞 8자 (jv_4f2a…)
        created_at TEXT NOT NULL,
        revoked_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS memories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        category TEXT NOT NULL,                 -- knowledge | commands | pitfalls | decisions
        title TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',       -- L0: 한 줄 요약
        overview TEXT NOT NULL DEFAULT '',      -- L1: 개요
        content TEXT NOT NULL DEFAULT '',       -- L2: 전문
        status TEXT NOT NULL DEFAULT 'fresh',   -- fresh | established | contested | superseded
        trust REAL NOT NULL DEFAULT 0.0,
        keywords TEXT NOT NULL DEFAULT '[]',    -- 검색용 키워드 (JSON)
        source TEXT NOT NULL DEFAULT 'session', -- session | manual
        corrects INTEGER,                       -- 이 지식이 대체한 지식 id (교정)
        superseded_by INTEGER,
        session_ref TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_used_at TEXT,
        use_count INTEGER NOT NULL DEFAULT 0,
        correct_count INTEGER NOT NULL DEFAULT 0,
        wrong_count INTEGER NOT NULL DEFAULT 0,
        evidence TEXT NOT NULL DEFAULT '[]',    -- 최근 결과 기록 (JSON)
        embedding TEXT                           -- 선택: 의미 검색 벡터 (JSON)
    )""",
    """CREATE TABLE IF NOT EXISTS sessions(
        id TEXT PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        agent TEXT NOT NULL DEFAULT 'unknown',
        started_at TEXT NOT NULL,
        ended_at TEXT,
        question_count INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        kind TEXT NOT NULL,                     -- session | memory | key | project
        detail TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_mem_proj ON memories(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status)",
    "CREATE INDEX IF NOT EXISTS idx_events_proj ON events(project_id)",
]


class DB:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def init(self) -> None:
        with self._lock, sqlite3.connect(self.path) as con:
            for statement in SCHEMA:
                con.execute(statement)
            # 컬럼 추가 마이그레이션 (기존 DB 에 새 컬럼이 없을 때)
            cols = {r[1] for r in con.execute("PRAGMA table_info(memories)")}
            if "embedding" not in cols:
                con.execute("ALTER TABLE memories ADD COLUMN embedding TEXT")
            if "superseded_by" not in cols:
                con.execute("ALTER TABLE memories ADD COLUMN superseded_by INTEGER")

    @contextmanager
    def conn(self):
        with self._lock:
            con = sqlite3.connect(self.path, timeout=30)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("PRAGMA journal_mode = WAL")
            try:
                yield con
                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()

    # ---------------- 질의 도우미 ---------------- #
    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.conn() as con:
            return [dict(r) for r in con.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        with self.conn() as con:
            r = con.execute(sql, args).fetchone()
            return dict(r) if r else None

    def execute(self, sql: str, args: tuple = ()) -> int:
        with self.conn() as con:
            cur = con.execute(sql, args)
            return cur.lastrowid

    # ---------------- json 컬럼 ---------------- #
    @staticmethod
    def jloads(value: str) -> list:
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return []

    @staticmethod
    def jdumps(value: list) -> str:
        return json.dumps(value, ensure_ascii=False)


def log_event(project_id: int, kind: str, detail: str = "") -> None:
    db.execute(
        "INSERT INTO events(project_id, kind, detail, created_at) VALUES(?,?,?,?)",
        (project_id, kind, detail[:500], now()),
    )


# 모듈 편의 함수 — 코드 전반에서 `from .. import db` 후 db.rows(...) 처럼 쓴다
def rows(sql: str, args: tuple = ()) -> list[dict]:
    return db.rows(sql, args)


def one(sql: str, args: tuple = ()) -> dict | None:
    return db.one(sql, args)


def execute(sql: str, args: tuple = ()) -> int:
    return db.execute(sql, args)


def jloads(value: str) -> list:
    return DB.jloads(value)


def jdumps(value: list) -> str:
    return DB.jdumps(value)


db = DB(config.data_dir / "index.db")