"""보안 — 비밀번호 해시, 세션 쿠키 서명, API 키 발급/검증.

- 비밀번호: PBKDF2-SHA256 (표준 라이브러리만 사용, 취약점 없음)
- 쿠키 세션: HMAC 서명된 {uid, exp} 토큰 (서버 시크릿으로 서명)
- API 키:  jv_ + 24자리 랜덤. 저장은 SHA-256 해시뿐 — 평문은 발급 순간 한 번만.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from .config import config

# ── 비밀번호 ───────────────────────────────────────────── #
_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS
        )
        return hmac.compare_digest(check.hex(), digest)
    except ValueError:
        return False


# ── 세션 쿠키 (HMAC 서명 토큰) ──────────────────────────── #
def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def sign_session(uid: int, ttl_hours: int = 24 * 30) -> str:
    payload = json.dumps({"uid": uid, "exp": int(time.time()) + ttl_hours * 3600})
    body = _b64encode(payload.encode())
    sig = hmac.new(config.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_session(token: str) -> int | None:
    """유효하면 uid, 아니면 None."""
    try:
        body, sig = token.rsplit(".", 1)
        expected = hmac.new(config.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64decode(body))
        if payload["exp"] < time.time():
            return None
        return int(payload["uid"])
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


# ── API 키 (에이전트 Bearer 인증) ───────────────────────── #
def generate_api_key() -> tuple[str, str]:
    """평문 키와 (hash, prefix) 반환. 평문은 즉시 사용자에게 보여주고 버려야 함."""
    raw = "jv_" + secrets.token_hex(12)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return raw, digest, raw[:10]


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()