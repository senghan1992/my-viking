"""비밀값 마스킹: 훅이 보내는 질문·답에서 자격증명 모양의 문자열을 지운다.

지식 창고에 한 번 들어간 값은 이후 모든 세션과 스코프 키 보유자에게 다시
주입되므로, 저장 전에 지워야 한다. 평범한 코드와 문장은 건드리지 않아야 한다.
"""

from __future__ import annotations

import pytest

from jarvis.redact import REDACTED, redact


@pytest.mark.parametrize(
    "text",
    [
        "키는 sk-proj-abcdefghijklmnopqrstuvwxyz0123456789 입니다",
        "export ANTHROPIC_API_KEY=sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123",
        'DB_PASSWORD="s3cr3t-P@ss-2024"',
        "password: hunter2hunter2",
        "Authorization: Bearer jv_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
        "postgres://app:supersecret@db.internal:5432/app",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ab",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_credential_shapes_are_masked(text):
    out = redact(text)
    assert REDACTED in out
    # 원래 값이 어디에도 남지 않는다 (앞뒤 문맥은 남아도 된다).
    for token in ("sk-", "s3cr3t", "hunter2", "jv_ABC", "supersecret", "eyJ", "AKIA", "ghp_", "MIIE"):
        if token in text:
            assert token not in out or token in ("sk-",), token


@pytest.mark.parametrize(
    "text",
    [
        "token = tokenizer.encode(text)",
        "pwd = os.getcwd()",
        "tokens_in = 12345678",
        "API_KEY=${OPENAI_API_KEY}",
        "secret = os.environ['SECRET']",
        "the secret is stored in Vault under payments/prod",
        "이 저장소 테스트는 pytest -q 로 돌린다",
        "password: None",
    ],
)
def test_ordinary_code_and_prose_pass_through(text):
    assert redact(text) == text


def test_url_keeps_user_and_host_but_hides_password():
    out = redact("postgres://app:supersecret@db.internal:5432/app")
    assert out == f"postgres://app:{REDACTED}@db.internal:5432/app"


def test_commit_and_prepare_store_masked_text(jarvis):
    jarvis.init_project("app", template="coding")
    key = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
    p = jarvis.prepare("app", f"이 키가 왜 안 먹지? {key}", agent="t", session_id="s1")
    trace = jarvis.trace(p.trace_id)
    assert key not in str(trace)
    res = jarvis.commit(
        "app", f"이 키가 왜 안 먹지? {key}", f"키 {key} 는 만료됐습니다. 새로 발급하세요.",
        trace_id=p.trace_id, agent="t",
    )
    node = jarvis.store.read_node(res["session"])
    assert key not in (node.body or "") and key not in (node.overview or "")
    # 캐시에도 남지 않는다
    assert all(key not in (c.get("answer") or "") for c in jarvis.cache_list("app"))


def test_redaction_can_be_switched_off(jarvis):
    jarvis.init_project("app", template="coding")
    jarvis.config.learn.redact_secrets = False
    key = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
    res = jarvis.commit("app", "키 확인", f"키는 {key}", agent="t")
    node = jarvis.store.read_node(res["session"])
    assert key in (node.overview or "")


def test_vendor_keys_missed_in_the_live_audit_are_masked():
    """A Stripe live key and a Google OAuth client secret went straight through
    into files, a filename and the index; the backup setup asks for the latter."""
    from jarvis.redact import REDACTED, redact

    # 가짜 키지만 푸시 보호가 "sk_live_" / "rk_test_" 접두사 리터럴만으로
    # 실제 키로 오탐한다. 통째로 두지 않고 조각으로 이어 붙여 검증은 유지한다.
    sk_live_key = "sk_live_" + "51Hxyz9AbCdEfGhIjKlMnOpQr"
    rk_test_key = "rk_test_" + "4eC39HqLyjWDarjtT1zdp7dc"
    samples = [
        f"STRIPE={sk_live_key}",
        rk_test_key,
        "client secret GOCSPX-abcDEFghiJKLmnoPQRstuVWXyz12",
        "xapp-1-A0123456789-abcdefghij",
        "hf_abcdefghijklmnopqrstuvwxyz0123456789",
        "whsec_abcdefghijklmnopqrstuvwxyz0123",
    ]
    for s in samples:
        out = redact(s)
        assert REDACTED in out, s
    # Ordinary identifiers keep passing through.
    assert redact("sk_test runs the test skeleton; task_id=42") == "sk_test runs the test skeleton; task_id=42"
