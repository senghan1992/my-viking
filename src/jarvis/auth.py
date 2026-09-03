"""API keys.

Once this runs somewhere other than localhost it holds every project's
accumulated context, so it needs a door. Keys are stored hashed; the plaintext
is shown once at creation and never again.

Default posture: if no keys exist, the server accepts local requests and says
so loudly in ``/health``. The moment you create a key, authentication is
required — no silent half-protected state.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .db import Database

KEY_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    key_hash  TEXT NOT NULL,
    projects  TEXT DEFAULT '*',
    created   TEXT NOT NULL,
    last_used TEXT DEFAULT '',
    calls     INTEGER DEFAULT 0,
    revoked   INTEGER DEFAULT 0
);
"""

PREFIX = "jv_"


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class KeyInfo:
    id: str
    name: str
    projects: list[str]

    def allows(self, project: str) -> bool:
        return "*" in self.projects or project in self.projects


class KeyStore:
    def __init__(self, db: Database):
        self.db = db
        self.db.conn.executescript(KEY_SCHEMA)
        self.db.commit()

    # Keys live in index.db. If that file is lost (deleted, corrupted, wrong
    # volume) the server would come back *open* — on a port-forwarded box that
    # is the worst failure mode there is. So the first key also drops a marker
    # next to the DB; marker-without-keys means "auth was on, the DB is gone".
    MARKER = "auth.enabled"

    def _marker(self):
        return self.db.path.with_name(self.MARKER)

    def auth_lost(self) -> bool:
        """Auth used to be on, but no active key exists any more."""
        try:
            return self._marker().exists() and not self.any_active()
        except Exception:
            return False

    def create(self, name: str, projects: list[str] | None = None) -> tuple[str, str]:
        """Return ``(key_id, plaintext_key)``. The plaintext is not stored."""
        raw = PREFIX + secrets.token_urlsafe(32)
        try:
            self._marker().touch()
        except OSError:
            pass
        kid = "key_" + secrets.token_hex(6)
        self.db.execute(
            "INSERT INTO api_keys (id, name, key_hash, projects, created)"
            " VALUES (?,?,?,?,?)",
            (
                kid,
                name,
                _hash(raw),
                ",".join(projects or ["*"]),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        self.db.commit()
        return kid, raw

    def verify(self, raw: str) -> KeyInfo | None:
        if not raw:
            return None
        digest = _hash(raw.strip())
        for row in self.db.query("SELECT * FROM api_keys WHERE revoked = 0"):
            # Constant-time compare so a wrong key cannot be probed by timing.
            if hmac.compare_digest(digest, row["key_hash"]):
                self.db.execute(
                    "UPDATE api_keys SET last_used=?, calls=calls+1 WHERE id=?",
                    (
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        row["id"],
                    ),
                )
                self.db.commit()
                return KeyInfo(
                    id=row["id"],
                    name=row["name"],
                    projects=[p for p in (row["projects"] or "*").split(",") if p],
                )
        return None

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "projects": r["projects"],
                "created": r["created"],
                "last_used": r["last_used"],
                "calls": r["calls"],
                "revoked": bool(r["revoked"]),
            }
            for r in self.db.query("SELECT * FROM api_keys ORDER BY created DESC")
        ]

    def revoke(self, key_id: str) -> bool:
        cur = self.db.execute(
            "UPDATE api_keys SET revoked = 1 WHERE id = ? AND revoked = 0", (key_id,)
        )
        self.db.commit()
        return (cur.rowcount or 0) > 0

    def any_active(self) -> bool:
        row = self.db.one("SELECT COUNT(*) c FROM api_keys WHERE revoked = 0")
        return bool(row and row["c"])
