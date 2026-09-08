"""웹 대시보드 '모델' 탭 — 제공자 설정 저장·핫스왑·키 마스킹·연결 테스트.

요구: .env 말고 웹에서도 LLM/임베딩 제공자(openai·grok·custom 등)를 연결할 수 있어야
한다. 키는 저장 파일(0600)에만 남고 API 응답에는 절대 평문이 나오면 안 된다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import httpx
import pytest

from jarvis.config import Config
from jarvis.server import create_app


@pytest.fixture()
def client(home):
    return TestClient(create_app(home=str(home)))


def _hdr(key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {key}"}


def _resp(data):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return data

    return FakeResp()


def test_settings_lists_the_catalog_without_leaking_keys(client):
    d = client.get("/settings").json()
    llm_names = {p["provider"] for p in d["llm_catalog"]}
    assert {"anthropic", "openai", "grok", "deepseek", "gemini", "volcengine", "ollama", "custom"} <= llm_names
    assert "custom" in {p["provider"] for p in d["embed_catalog"]}
    assert d["llm"]["has_key"] is False and d["embed"]["has_key"] is False


def test_web_save_persists_and_masks_the_key(monkeypatch, tmp_path):
    app = create_app(home=str(tmp_path))
    c = TestClient(app)
    r = c.post("/settings/llm", json={
        "provider": "grok", "model": "grok-3", "api_key": "xai-web-key",
        "base_url": "", "path": "", "max_output_tokens": 4096,
    })
    assert r.status_code == 200
    d = r.json()["llm"]
    assert d["provider"] == "grok" and d["max_output_tokens"] == 4096 and d["has_key"]
    assert d["key"].endswith("-key") and d["key"] != "xai-web-key"
    assert "xai-web-key" not in r.text, "평문 키가 API 응답에 나오면 안 된다"
    # 저장 파일: 0600 + 재로드 시 그대로
    path = tmp_path / "jarvis.yaml"
    assert "xai-web-key" in path.read_text(encoding="utf-8")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    cfg = Config.load(tmp_path)
    assert cfg.llm.provider == "grok" and cfg.api_key("llm") == "xai-web-key"


def test_web_setting_blocks_env_override_until_reset(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("JARVIS_LLM_MODEL", "gpt-env")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    c = TestClient(create_app(home=str(tmp_path)))
    # 웹으로 grok 저장 → env 가 덮어쓰지 않는다
    c.post("/settings/llm", json={"provider": "grok", "model": "grok-3", "api_key": "web-key"})
    cfg = Config.load(tmp_path)  # load 가 env 를 적용하지만 web_set 이 우선
    assert cfg.llm.provider == "grok" and cfg.llm.model == "grok-3"
    # 초기화 → env 부트스트랩 복귀
    c.post("/settings/reset")
    cfg = Config.load(tmp_path)
    assert cfg.llm.provider == "openai" and cfg.llm.model == "gpt-env"


def test_empty_key_clears_stored_and_env_fallback_fills(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key-123")
    c = client
    c.post("/settings/llm", json={"provider": "openai", "model": "gpt-4o", "api_key": "stored-key"})
    d = c.get("/settings").json()["llm"]
    assert d["key"] == "⋯-key"  # 마스킹: 뒤 4자리만
    # 키 지우기("") → 저장 키 제거 → env 폴백이 채운다
    c.post("/settings/llm", json={"provider": "openai", "model": "gpt-4o", "api_key": ""})
    d = c.get("/settings").json()["llm"]
    assert d["has_key"] and d["key"] == "⋯-123"


def test_scoped_key_cannot_change_settings(client):
    admin = client.post("/keys", json={"name": "admin"}).json()["key"]
    client.post("/projects", json={"project": "alpha"})
    scoped = client.post("/keys", json={"name": "only", "projects": ["alpha"]}, headers=_hdr(admin)).json()["key"]
    r = client.post("/settings/llm", json={"provider": "openai"}, headers=_hdr(scoped))
    assert r.status_code == 403


def test_embed_save_swaps_provider_and_reports_probe_error(client, monkeypatch):
    def fake_post(url, **kw):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(httpx, "post", fake_post)  # jarvis.llm 과 같은 객체
    r = client.post("/settings/embed", json={"provider": "openai", "model": "text-embedding-3-small", "api_key": "k"})
    assert r.status_code == 200
    d = r.json()["embed"]
    assert d["provider"] == "openai" and d["configured"] and not d["ready"]
    assert "endpoint down" in d["probe_error"]

    r = client.post("/settings/embed", json={"provider": "hashing"})
    assert r.json()["embed"]["provider"] == "hashing"


def test_llm_test_endpoint_calls_the_provider(client, monkeypatch):
    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        return _resp({"choices": [{"message": {"content": "연결"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    monkeypatch.setattr(httpx, "post", fake_post)
    r = client.post("/settings/llm/test", json={"provider": "openai", "model": "gpt-4o", "api_key": "k"})
    d = r.json()
    assert d["ok"] and "/chat/completions" in seen["url"] and d["text"] == "연결"


def test_custom_provider_hits_the_given_endpoint(client, monkeypatch):
    """Databricks 식: base_url 이 곧 엔드포인트이고 path=/, 키는 Bearer."""
    seen = {}

    def fake_post(url, **kw):
        seen.update(url=url, headers=kw.get("headers", {}))
        return _resp({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    r = client.post("/settings/llm/test", json={
        "provider": "custom", "model": "deepseek-x",
        "base_url": "https://gw.example.com/serving-endpoints/deepseek/invocations",
        "path": "/", "api_key": "dapi-test",
    })
    assert r.json()["ok"]
    assert seen["url"] == "https://gw.example.com/serving-endpoints/deepseek/invocations"
    assert seen["headers"].get("Authorization") == "Bearer dapi-test"