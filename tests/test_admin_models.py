"""관리자 → 모델 설정: 웹으로 LLM/임베딩 키 관리 (env 없이도 가능)."""
from conftest import create_key, create_project
from urllib.parse import unquote


def auth_h(c, raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


# ── 접근 제어 ─────────────────────────────────────────── #
def test_models_page_admin_only(client, user1, user2):
    c, _ = client
    # user2 가입으로 쿠키가 user2 것으로 덮였을 수 있으니 관리자로 다시 로그인
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    assert c.get("/admin/models").status_code == 200
    c.post("/logout")
    c.post("/login", data={"email": "b@test.com", "password": "password2"})
    assert c.get("/admin/models").status_code == 403


# ── 저장 → 즉시 적용(핫스왑) ───────────────────────────── #
def test_save_web_settings_hotswap(client, user1, tmp_path):
    c, _ = client
    from app.config import config
    assert config.llm_api_key == ""  # env 없음 (conftest)

    r = c.post("/admin/models", data={
        "llm_base_url": "https://api.deepseek.com/v1/",
        "llm_api_key": "sk-test-key-1234",
        "llm_model": "deepseek-chat",
        "embed_model": "text-embedding-3-small",
    }, follow_redirects=False)
    assert r.status_code == 303
    # 핫스왑: 재시작 없이 config 에 반영
    assert config.llm_base_url == "https://api.deepseek.com/v1"
    assert config.llm_api_key == "sk-test-key-1234"
    assert config.llm_model == "deepseek-chat"
    # 임베딩 주소 미지정 → LLM 과 동일
    assert config.embed_base_url == "https://api.deepseek.com/v1"

    # 파일 저장 + 권한 0600
    p = tmp_path / "models.json"
    assert p.exists()
    assert (p.stat().st_mode & 0o777) == 0o600

    # 키는 화면에 마스킹만 (평문 노출 금지)
    html = c.get("/admin/models").text
    assert "sk-test-key-1234" not in html
    assert "1234" in html


# ── 빈 키 = 기존 유지 / 체크박스 = 지우기 ────────────────── #
def test_empty_key_keeps_existing(client, user1):
    c, _ = client
    c.post("/admin/models", data={"llm_api_key": "sk-keep-9999"}, follow_redirects=False)
    from app.config import config
    assert config.llm_api_key == "sk-keep-9999"

    # 키를 비워서 저장 → 기존 키 유지
    c.post("/admin/models", data={"llm_base_url": "https://api.openai.com/v1"}, follow_redirects=False)
    assert config.llm_api_key == "sk-keep-9999"

    # 명시적 지우기 → env 로 복귀 (여기선 env 도 없으므로 빈 값)
    c.post("/admin/models", data={"llm_clear_key": "on"}, follow_redirects=False)
    assert config.llm_api_key == ""


# ── 잘못된 주소 검증 ───────────────────────────────────── #
def test_invalid_base_url_rejected(client, user1):
    c, _ = client
    r = c.post("/admin/models", data={"llm_base_url": "api.openai.com"}, follow_redirects=False)
    assert r.status_code == 303
    assert "http(s)://" in r.headers["location"]
    from app.config import config
    assert config.llm_base_url == "https://api.openai.com/v1"  # 저장 안 됨


# ── 연결 테스트 ────────────────────────────────────────── #
def test_models_test_endpoint(client, user1, monkeypatch):
    c, _ = client
    from app.engine import llm
    monkeypatch.setattr(llm, "test_llm", lambda: (True, ""))
    monkeypatch.setattr(llm, "test_embed", lambda: (False, "401 인증 실패"))
    r = c.post("/admin/models/test", follow_redirects=False)
    assert r.status_code == 303
    loc = unquote(r.headers["location"])
    assert "LLM ✓" in loc
    assert "임베딩 ✗" in loc and "401" in loc


# ── 전체 재색인 ────────────────────────────────────────── #
def test_reindex_without_embed_config(client, user1):
    c, _ = client
    r = c.post("/admin/models/reindex", follow_redirects=False)
    assert r.status_code == 303
    assert "재색인하지 않았습니다" in unquote(r.headers["location"])


def test_reindex_embeds_all_memories(client, user1, monkeypatch):
    c, _ = client
    from app.engine import llm
    monkeypatch.setattr(llm, "embed", lambda text: [1.0, 0.0])

    # 임베딩 활성화 (웹 설정으로)
    c.post("/admin/models", data={"embed_api_key": "sk-embed-1"}, follow_redirects=False)

    # 지식 2권 생성 (에이전트 루프로)
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "재색인 프로젝트")
    raw = create_key(c, slug)
    h = auth_h(c, raw)
    for i in range(2):
        c.post(f"/api/v1/projects/{slug}/commit", headers=h, json={
            "question": f"질문 {i}", "answer": f"답변 {i} — 이중 결제 방지", "session_id": "s1"})

    r = c.post("/admin/models/reindex", follow_redirects=False)
    assert r.status_code == 303
    assert "2권" in unquote(r.headers["location"])
    from app import db
    n = db.one("SELECT COUNT(*) AS n FROM memories WHERE embedding IS NOT NULL")["n"]
    assert n >= 2