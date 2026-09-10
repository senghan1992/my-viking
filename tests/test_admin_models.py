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
    assert "http(s)://" in unquote(r.headers["location"])
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

# ── 커스텀 모델 등록 (registered) ───────────────────────── #
def test_register_custom_model(client, user1, tmp_path):
    c, _ = client
    r = c.post("/admin/models/register", data={
        "kind": "llm", "name": "회사 게이트웨이", "base_url": "https://gw.example.com/v1",
        "model": "deepseek-chat", "api_key": "sk-reg-5555",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "등록했습니다" in unquote(r.headers["location"])

    import json
    from app import model_settings
    data = json.loads((tmp_path / "models.json").read_text())
    regs = data["registered"]
    assert len(regs) == 1
    assert regs[0]["name"] == "회사 게이트웨이"
    assert regs[0]["base_url"] == "https://gw.example.com/v1"  # 끝 '/' 제거
    assert regs[0]["model"] == "deepseek-chat"
    assert regs[0]["kind"] == "llm"
    assert regs[0]["api_key"] == "sk-reg-5555"

    # 드롭다운에 노출 + 키 마스킹
    html = c.get("/admin/models").text
    assert "회사 게이트웨이" in html
    assert "https://gw.example.com/v1" in html
    assert "sk-reg-5555" not in html
    assert "••••••••5555" in html

    # 기존 설정 저장해도 registered 유지
    c.post("/admin/models", data={"llm_model": "deepseek-r1"}, follow_redirects=False)
    data = json.loads((tmp_path / "models.json").read_text())
    assert len(data["registered"]) == 1


def test_register_validation(client, user1):
    c, _ = client
    r = c.post("/admin/models/register", data={
        "kind": "llm", "name": "", "base_url": "gw.example.com", "model": "m"}, follow_redirects=False)
    assert r.status_code == 303
    assert "이름을 입력" in unquote(r.headers["location"])
    r = c.post("/admin/models/register", data={
        "kind": "llm", "name": "x", "base_url": "gw.example.com", "model": "m"}, follow_redirects=False)
    assert "http(s)://" in unquote(r.headers["location"])


def test_delete_registered(client, user1):
    c, _ = client
    c.post("/admin/models/register", data={
        "kind": "embed", "name": "임베딩테스트", "base_url": "https://e.example.com/v1",
        "model": "emb-1"}, follow_redirects=False)
    from app import model_settings
    rid = model_settings.load_registered()[0]["id"]
    r = c.post(f"/admin/models/registered/{rid}/delete", follow_redirects=False)
    assert r.status_code == 303 and "지웠습니다" in unquote(r.headers["location"])
    assert model_settings.load_registered() == []


def test_dropdown_catalog_default(client, user1):
    """pi 에서 가져온 Databricks 카탈로그가 드롭다운에 미리 붙어 있다."""
    c, _ = client
    html = c.get("/admin/models").text
    assert "data-pick=\"llm\"" in html
    assert "사전 등록 · 나의 Databricks" in html
    assert "DeepSeek V4 Flash 0731" in html          # 기본 모델
    assert "/serving-endpoints/databricks-deepseek-v4-flash-0731/invocations" in html
    assert "data-pick=\"embed\"" in html             # 임베딩용 드롭다운도 존재


# ── 엔드포인트 URL 판단 (Databricks 직접 호출 지원) ──────── #
def test_chat_url_direct_vs_appended():
    from app.engine.llm import chat_url, embed_url
    # OpenAI 호환 → /chat/completions 붙임
    assert chat_url("https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions"
    assert chat_url("https://api.deepseek.com/v1/") == "https://api.deepseek.com/v1/chat/completions"
    # Databricks Serving → 그대로 (뒤에 붙이면 404)
    assert chat_url("https://h.cloud.databricks.com/serving-endpoints/databricks-kimi-k3/invocations") \
        == "https://h.cloud.databricks.com/serving-endpoints/databricks-kimi-k3/invocations"
    # '#' 로 끝나는 pi 스타일 주소도 정리 후 판단
    assert chat_url("https://h.cloud.databricks.com/serving-endpoints/x/invocations#") \
        == "https://h.cloud.databricks.com/serving-endpoints/x/invocations"
    # 완전한 경로를 직접 써도 중복 붙이지 않음
    assert chat_url("https://x.example.com/v1/chat/completions") == "https://x.example.com/v1/chat/completions"
    # 임베딩
    assert embed_url("https://api.openai.com/v1") == "https://api.openai.com/v1/embeddings"
    assert embed_url("https://h.cloud.databricks.com/serving-endpoints/emb/invocations") \
        == "https://h.cloud.databricks.com/serving-endpoints/emb/invocations"


# ── 첫 실행 시드 (models_seed.json) ─────────────────────────── #
def test_seed_if_empty_writes_catalog(tmp_path, monkeypatch):
    """VIKING_SEED_MODELS=true + 빈 data_dir → llm/registered 기본 시드. 두 번째는 스킵."""
    from app import model_settings

    monkeypatch.setenv("VIKING_SEED_MODELS", "true")
    monkeypatch.setattr(model_settings, "file_path", lambda: tmp_path / "models.json")

    assert model_settings.seed_if_empty() is True
    p = tmp_path / "models.json"
    assert p.exists()
    data = model_settings._load_raw()
    assert data.get("llm_model") == "databricks-deepseek-v4-flash-0731"
    # 키는 저장소가 아닌 환경변수에서 — 시드 파일엔 api_key 가 없다
    assert "api_key" not in data
    regs = model_settings.load_registered()
    assert len(regs) > 40                   # 사전 카탈로그 전체
    assert all(r["kind"] == "llm" and r["endpoint"] == "direct" for r in regs)

    # 환경변수로 키를 주면 시드가 그걸 넣는다
    monkeypatch.setenv("VIKING_LLM_API_KEY", "sk-env-key-0000")
    fresh = tmp_path / "models2.json"
    monkeypatch.setattr(model_settings, "file_path", lambda: fresh)
    assert model_settings.seed_if_empty() is True
    assert model_settings._load_raw().get("llm_api_key") == "sk-env-key-0000"
    monkeypatch.delenv("VIKING_LLM_API_KEY")

    # 이미 설정이 있으면 시드하지 않는다
    assert model_settings.seed_if_empty() is False

    # 관리자가 하나라도 직접 등록한 뒤에는 다시 채우지 않는다
    model_settings.add_registered("llm", "내 모델", "https://x.example.com/v1", "my-model")
    monkeypatch.setenv("VIKING_SEED_MODELS", "false")
    assert model_settings.seed_if_empty() is False
