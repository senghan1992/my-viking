"""키 보관 — 봉인(seal)된 평문, 발급 후 재복사, 인증은 해시로만."""
from conftest import create_key, create_project

from app.security import generate_api_key, hash_api_key, seal_api_key, unseal_api_key


def test_seal_roundtrip_and_tamper():
    raw, digest, prefix = generate_api_key()
    sealed = seal_api_key(raw)
    assert raw not in sealed                       # 봉인 문자열에 평문이 남지 않는다
    assert unseal_api_key(sealed) == raw
    assert unseal_api_key(sealed[:-4] + "AAAA") is None   # 태그 손상
    assert unseal_api_key(None) is None
    assert unseal_api_key("jv_plaintext") is None         # 봉인 아닌 값은 열지 않는다
    assert hash_api_key(raw) == digest


def test_key_is_stored_sealed_not_plain(client, user1):
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "봉인 저장")
    raw = create_key(c, slug, "노트북")
    from app import db

    row = db.one(
        "SELECT k.key_hash, k.key_secret, k.key_prefix FROM api_keys k"
        " JOIN projects p ON p.id=k.project_id WHERE p.slug=? AND k.name=?", (slug, "노트북"))
    assert row["key_hash"] == hash_api_key(raw)          # 인증은 해시
    assert raw not in row["key_secret"]                  # 저장값은 평문이 아니다
    assert unseal_api_key(row["key_secret"]) == raw      # 주인만 다시 풀 수 있다
    assert row["key_prefix"] == raw[:10]


def test_sealed_key_still_authenticates_until_revoked(client, user1):
    """평문을 다시 볼 수 있어도 인증은 해시로만 — 폐기하면 즉시 끊긴다."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "봉인 후 사용")
    raw = create_key(c, slug, "쓸모 없는 키")
    hdr = {"Authorization": f"Bearer {raw}"}
    assert c.get(f"/api/v1/projects/{slug}/brief", headers=hdr).status_code == 200

    from app import db

    kid = db.one(
        "SELECT k.id FROM api_keys k JOIN projects p ON p.id=k.project_id"
        " WHERE p.slug=? AND k.name=?", (slug, "쓸모 없는 키"))["id"]
    c.post(f"/projects/{slug}/keys/{kid}/revoke")
    assert c.get(f"/api/v1/projects/{slug}/brief", headers=hdr).status_code == 401
    # 폐기된 키는 화면에서도 평문이 풀리지 않는다
    assert "폐기" in c.get(f"/projects/{slug}/connect").text
    assert raw not in c.get(f"/projects/{slug}/connect").text
