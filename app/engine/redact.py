"""비밀값 마스킹 — 캡처된 질문·답이 서버 저장소에 닿기 전에 지웁니다.

한 번 들어간 값은 이후 세션에 다시 주입될 수 있으므로, 서버에 남기면 안 되는
것들을 여기서 [REDACTED] 로 바꿉니다. (hooks 경유든, MCP·CLI 경유든 서버의
commit/remember 엔드포인트에서 한 번 더 걸러집니다.)
"""
from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # ── 이름=값 대입 (먼저 처리 — 값이 통째로 지워져야 안전) ──
    (
        re.compile(
            r"(?i)(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|secret|"
            r"password|passwd|pwd|client[_-]?secret|private[_-]?key)"
            r"(['\"]?)\s*[:=]\s*['\"]?[0-9A-Za-z_\-./+=]{8,}"
        ),
        r"\1=\2[REDACTED]",
    ),
    # ── Authorization 헤더 ──
    (re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer|basic|token)\s+)[0-9A-Za-z_\-./+=]+"), r"\1[REDACTED]"),
    # ── 잘 알려진 키 형식 ──
    (re.compile(r"(?i)\b(?:sk|pk)-[A-Za-z0-9_-]{16,}\b"), "sk-…"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA…"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "AIza…"),
    (re.compile(r"\bgh[pousr]_[0-9A-Za-z]{20,}\b"), "gh_…"),
    (re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"), "xox…"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "JWT…"),
    # ── URL 속 자격증명 https://user:pass@host ──
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1[REDACTED]@"),
    # ── 비공개 키 블록 ──
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "-----BEGIN PRIVATE KEY-----[REDACTED]-----END PRIVATE KEY-----",
    ),
]

# 키 인접 long-hex/base64 (위 패턴에 안 걸린 흔한 케이스)
_HEX_LONG = re.compile(r"\b[0-9a-f]{32,}\b")
_JWKS_SAFE = {"0" * 32}  # '0000…' 같은 더미는 건드리지 않음

# 가짜 키로 테스트할 때 안전한 기본값들
_ALLOWED_PLACEHOLDERS = {"your-api-key", "xxxx", "test", "example"}


def redact(text: str) -> str:
    """텍스트에서 비밀값을 [REDACTED] 로 마스킹."""
    if not text:
        return text
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)

    # 32자 이상 hex 는 키 대입문 근처거나 단독일 때만 (hash 값 오탐 방지)
    def _maybe_hex(m: re.Match) -> str:
        v = m.group(0)
        if v in _ALLOWED_PLACEHOLDERS or all(c == "0" or c == "f" for c in v[:8]):
            return v
        return "[REDACTED]"

    out = _HEX_LONG.sub(_maybe_hex, out)
    return out