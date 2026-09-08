"""테스트 픽스처 — 임시 데이터 디렉터리로 앱을 띄웁니다."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# 앱 import 전에 환경 설정 (config 는 모듈 로드 시점에 읽음)
TMP = tempfile.mkdtemp(prefix="myviking-test-")
os.environ["VIKING_DATA"] = TMP
os.environ["VIKING_SECRET"] = "test-secret"
os.environ["VIKING_ALLOW_SIGNUP"] = "true"
os.environ["VIKING_FIRST_USER_ADMIN"] = "true"
os.environ.pop("VIKING_LLM_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    # 테스트마다 깨끗한 DB 사용
    from app import db
    from app.config import config

    config.data_dir = tmp_path
    db.db.path = tmp_path / "index.db"
    db.db.init()

    from app.main import app

    with TestClient(app) as c:
        yield c, tmp_path


@pytest.fixture()
def user1(client):
    """가입 + 첫 사용자 (관리자 자동 승격)."""
    c, _ = client
    r = c.post("/signup", data={"name": "가나다", "email": "a@test.com", "password": "password1"}, follow_redirects=False)
    assert r.status_code == 303
    return {"name": "가나다", "email": "a@test.com"}


@pytest.fixture()
def user2(client):
    """두 번째 사용자 (일반 사용자)."""
    c, _ = client
    r = c.post("/signup", data={"name": "둘째", "email": "b@test.com", "password": "password2"}, follow_redirects=False)
    assert r.status_code == 303
    return {"name": "둘째", "email": "b@test.com"}


def create_project(c, name: str, description: str = "") -> str:
    r = c.post("/projects", data={"name": name, "description": description}, follow_redirects=False)
    assert r.status_code == 303
    from app import db

    row = db.one("SELECT slug FROM projects WHERE name=?", (name,))
    return row["slug"]


def create_key(c, slug: str, name: str = "tester") -> str:
    """키를 발급하고 평문을 응답 HTML에서 꺼내 반환 (서버는 해시만 저장)."""
    import re

    r = c.post(f"/projects/{slug}/keys", data={"name": name})
    assert r.status_code == 200
    m = re.search(r'class="key-box"[^>]*>([^<]+)', r.text)
    assert m, "키 평문이 화면에 보여야 합니다"
    raw = m.group(1).strip()
    assert raw.startswith("jv_")
    return raw