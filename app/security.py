"""보안 — 비밀번호 해시, 세션 쿠키 서명, API 키 발급/검증.

- 비밀번호: PBKDF2-SHA256 (표준 라이브러리만 사용, 취약점 없음)
- 쿠키 세션: HMAC 서명된 {uid, exp} 토큰 (서버 시크릿으로 서명)
- API 키:  jv_ + 24자리 랜덤. 인증은 SHA-256 해시로만 판단한다.
  평문은 서버 시크릿(VIKING_SECRET)으로 봉인(seal)해 따로 보관 — 발급 화면이 아니라
  프로젝트 주인만(로그인 세션) 언제든 다시 복사할 수 있다. DB 백업만으로는 평문이 안 나온다.
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


# ── 키 평문 봉인 (발급 후에도 주인이 다시 복사할 수 있게) ───────── #
# 표준 라이브러리로 만든 encrypt-then-MAC: HMAC-SHA256 스트림 암호 + 자른 태그.
# 인증 실패(위조/손상)는 None 을 반환한다. 키 해시는 인증에 쓰고, 이 값은 표시/복사용뿐이다.
_KEK_LABEL = b"myviking.apikey.v1"
_NONCE = 12
_TAG = 16


def _keyring(label: bytes) -> bytes:
    return hmac.new(config.secret.encode(), _KEK_LABEL + label, hashlib.sha256).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def seal_api_key(raw: str) -> str:
    """평문 키를 서버 시크릿으로 봉인해 문자열로 (DB 에 0600 파일과 별개 값으로 저장)."""
    enc, mac = _keyring(b"enc"), _keyring(b"mac")
    nonce = secrets.token_bytes(_NONCE)
    stream = _keystream(enc, nonce, len(raw))
    body = bytes(b ^ s for b, s in zip(raw.encode(), stream))
    tag = hmac.new(mac, nonce + body, hashlib.sha256).digest()[:_TAG]
    return "v1." + base64.urlsafe_b64encode(nonce + body + tag).decode().rstrip("=")


def unseal_api_key(sealed: str | None) -> str | None:
    """봉인된 키를 평문으로. 없거나 위조/손상이면 None (발급 이전 구버전 키 포함)."""
    if not sealed or not sealed.startswith("v1."):
        return None
    try:
        blob = base64.urlsafe_b64decode(sealed[3:] + "=" * (-len(sealed[3:]) % 4))
        nonce, tag = blob[:_NONCE], blob[-_TAG:]
        body = blob[_NONCE:-_TAG]
        mac = _keyring(b"mac")
        if not hmac.compare_digest(tag, hmac.new(mac, nonce + body, hashlib.sha256).digest()[:_TAG]):
            return None
        stream = _keystream(_keyring(b"enc"), nonce, len(body))
        return bytes(b ^ s for b, s in zip(body, stream)).decode()
    except (ValueError, TypeError, IndexError, UnicodeDecodeError):
        return None