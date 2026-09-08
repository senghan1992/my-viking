"""엔진 단위 테스트 — 토큰화·마스킹·티어·검색 점수."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine.redact import redact
from app.engine.tiers import make_overview, make_summary
from app.engine.tokens import keywords, tokenize


def test_tokenize_korean_strips_particles():
    toks = tokenize("결제 재시도를 해야 한다")
    assert "재시도" in toks
    assert "결제" in toks


def test_tokenize_english():
    toks = tokenize("how to deploy with docker compose")
    assert "deploy" in toks and "docker" in toks


def test_keywords_top_first():
    kws = keywords("배포 배포 배포 방법 문서")
    assert kws and kws[0] == "배포"


def test_redact_known_key_formats():
    assert "sk-…" in redact("키는 sk-abc123DEF456ghi789JKL0123456789 이다")
    assert "AKIA…" in redact("AKIAIOSFODNN7EXAMPLE 사용")
    assert "gh_…" in redact("ghp_abcdefghijklmnopqrstuvwxyz1234567890")


def test_redact_assignments():
    out = redact('export OPENAI_API_KEY="sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"')
    assert "sk-aa" not in out
    assert "[REDACTED]" in out
    out = redact("password = hunter2hunter2")
    assert "hunter2" not in out


def test_redact_url_and_jwt():
    assert "[REDACTED]" in redact("https://user:pass123@example.com/api")
    assert "JWT" in redact("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")


def test_redact_private_key_block():
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSj\n-----END PRIVATE KEY-----"
    out = redact(pem)
    assert "MIIEvQ" not in out


def test_summary_extracts_first_sentence():
    s = make_summary("제목", "첫 번째 문장이다. 두 번째 문장은 안 씀.")
    assert "첫 번째 문장" in s
    assert "두 번째" not in s


def test_overview_truncates():
    body = " ".join(f"단어{i}" for i in range(500))
    overview = make_overview(body, max_chars=100)
    assert len(overview) <= 101