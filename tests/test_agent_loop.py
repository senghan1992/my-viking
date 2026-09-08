"""핵심 루프: brief → prepare → commit → retrieve → 적응(trust)."""
from conftest import create_key, create_project


def auth_h(c, raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


def test_agent_loop_end_to_end(client, user1):
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "결제 시스템", "PG 연동 프로젝트")
    raw = create_key(c, slug, "내 노트북")

    h = auth_h(c, raw)
    # ── health / brief ──
    assert c.get("/api/v1/health").json()["ok"] is True
    brief = c.get(f"/api/v1/projects/{slug}/brief?session_id=s1", headers=h)
    assert brief.status_code == 200
    assert "결제 시스템" in brief.json()["orientation"]

    # ── prepare: 아직 지식 없음 → 빈 주입 ──
    prep = c.post(f"/api/v1/projects/{slug}/prepare", headers=h,
                  json={"prompt": "결제 재시도 어떻게 해?", "session_id": "s1", "agent": "claude"})
    assert prep.status_code == 200
    assert prep.json()["items"] == []

    # ── commit: 지식 한 권이 쌓인다 ──
    r = c.post(f"/api/v1/projects/{slug}/commit", headers=h, json={
        "question": "PG 결제 실패 시 재시도 안 하는 법?",
        "answer": "결제 승인 실패 시 재시도하면 이중 결제가 된다. 응답 코드가 성공이 아닐 때는 절대 재시도하지 말 것. "
                  "```python\nif resp.code != '0000': raise PaymentError()\n```",
        "session_id": "s1", "agent": "claude",
        "files": ["payment/gateway.py"],
    })
    assert r.status_code == 200
    assert r.json()["distilled"]["created"] >= 1

    # ── prepare: 이제 관련 지식이 나온다 ──
    prep = c.post(f"/api/v1/projects/{slug}/prepare", headers=h,
                  json={"prompt": "결제가 실패했는데 재시도해도 되나?", "session_id": "s1"})
    assert prep.status_code == 200
    items = prep.json()["items"]
    assert items, "방금 기록한 지식이 검색되어야 합니다"
    assert "재시도" in items[0]["title"]
    assert items[0]["status"] == "fresh"  # 아직 검증 전
    assert "⟨검증 전⟩" in prep.json()["injection"]  # 검증 전은 프롬프트에 표시

    # ── score: good → 확립 ──
    mid = items[0]["id"]
    r = c.post(f"/api/v1/projects/{slug}/score", headers=h,
               json={"memory_id": mid, "outcome": "good"})
    assert r.json()["status"] == "established"

    # ── 재검색: 이제 확립 표시 ──
    prep = c.post(f"/api/v1/projects/{slug}/prepare", headers=h,
                  json={"prompt": "재시도 금지", "session_id": "s1"})
    assert prep.json()["items"][0]["verified"] is True


def test_implicit_feedback_demotes_bad_knowledge(client, user1):
    """다음 질문이 불만이면 직전 지식을 '검증 필요'로 강등 (적응 루프)."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "배포")
    raw = create_key(c, slug)
    h = auth_h(c, raw)

    c.post(f"/api/v1/projects/{slug}/remember", headers=h,
           json={"title": "배포는 이렇게", "content": "npm run deploy:prod 로 배포한다", "confirmed": False})
    mid = c.get(f"/api/v1/projects/{slug}/search?q=배포", headers=h).json()["items"][0]["id"]

    # 준비(주입) → 다음 질문이 불만 → bad
    c.post(f"/api/v1/projects/{slug}/prepare", headers=h, json={"prompt": "배포 어떻게?", "session_id": "s2"})
    c.post(f"/api/v1/projects/{slug}/commit", headers=h, json={
        "question": "그래도 배포가 안 되는데?",
        "answer": "절차가 바뀌었다. CI 파이프라인을 쓴다.",
        "session_id": "s2",
    })
    row = c.get(f"/api/v1/projects/{slug}/search?q=배포", headers=h).json()
    mem = next((i for i in row["items"] if i["id"] == mid), None)
    assert mem is None, "강등된 지식은 일반 검색에서 빠진다"
    assert any(w["id"] == mid for w in row["warnings"]), "검증 필요 경고로 나온다"


def test_remember_and_correction(client, user1):
    """같은 제목의 확립 지식 + 사람 확인 교정 → 대체(superseded)."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "교정 프로젝트")
    raw = create_key(c, slug)
    h = auth_h(c, raw)

    r = c.post(f"/api/v1/projects/{slug}/remember", headers=h,
               json={"title": "db 마이그레이션", "content": "flask db upgrade 를 쓴다", "confirmed": True})
    mid = r.json()["memory_id"]

    # 같은 제목으로 사람이 확인(교정) → 옛것 대체
    r = c.post(f"/api/v1/projects/{slug}/remember", headers=h,
               json={"title": "db 마이그레이션", "content": "alembic upgrade head 를 쓴다", "confirmed": True})
    from app import db

    old = db.one("SELECT status, superseded_by FROM memories WHERE id=?", (mid,))
    assert old["status"] == "superseded"
    assert old["superseded_by"] == r.json()["memory_id"]
    new = db.one("SELECT * FROM memories WHERE id=?", (r.json()["memory_id"],))
    assert new["corrects"] == mid


def test_isolation_via_api_key(client, user1, user2):
    """A 의 프로젝트 키로는 B 의 프로젝트에 접근 불가."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug_a = create_project(c, "A 만의 프로젝트")
    raw_a = create_key(c, slug_a)

    c.post("/logout")
    c.post("/login", data={"email": "b@test.com", "password": "password2"})
    slug_b = create_project(c, "B 만의 프로젝트")

    r = c.get(f"/api/v1/projects/{slug_b}/brief", headers=auth_h(c, raw_a))
    assert r.status_code == 404


def test_commit_redacts_secrets(client, user1):
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "보안")
    raw = create_key(c, slug)
    h = auth_h(c, raw)

    c.post(f"/api/v1/projects/{slug}/commit", headers=h, json={
        "question": "API 키 넣는 법?",
        "answer": "export OPENAI_API_KEY=sk-abc123def456ghi789jkl0123456789 하고 쓰면 된다",
        "session_id": "s4",
    })
    row = c.get(f"/api/v1/projects/{slug}/search?q=API", headers=h).json()["items"][0]
    assert "sk-abc" not in row["text"]
    assert "[REDACTED]" in row["text"]


def test_export_markdown(client, user1):
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "내보내기")
    raw = create_key(c, slug)
    c.post(f"/api/v1/projects/{slug}/remember", headers=auth_h(c, raw),
           json={"title": "규칙", "content": "본문", "confirmed": True})
    r = c.get(f"/projects/{slug}/export.md")
    assert r.status_code == 200
    assert "# 내보내기" in r.text
    assert "규칙" in r.text