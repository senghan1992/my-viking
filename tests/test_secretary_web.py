"""웹 화면 — 사람이 서기의 일을 확인하는 자리 (관찰함·반복 요청·설정)."""
from conftest import create_key, create_project


def auth_h(c, raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


def login(c):
    c.post("/login", data={"email": "a@test.com", "password": "password1"})


def test_secretary_page_shows_inbox(client, user1):
    c, _ = client
    login(c)
    slug = create_project(c, "정리 화면")
    raw = create_key(c, slug)
    h = auth_h(c, raw)
    c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
        "session_id": "s1", "agent": "pi", "transcript": "/root/.pi/agent/sessions/x.jsonl",
        "items": [{"kind": "prompt", "text": "마이그레이션은 무엇으로 실행하나?"},
                  {"kind": "error", "text": "alembic: command not found"}],
    })
    c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
        "session_id": "s2", "items": [{"kind": "prompt", "text": "마이그레이션 무엇으로 실행"}]})

    page = c.get(f"/projects/{slug}/secretary")
    assert page.status_code == 200
    assert "정리 대기 관찰" in page.text
    assert "마이그레이션" in page.text
    assert "alembic: command not found" in page.text
    assert "jv secretary once" in page.text          # 복사할 실행 명령
    assert "x.jsonl" in page.text                    # 세션 공유: 트랜스크립트 위치


def test_secretary_page_lists_repeat_clusters(client, user1):
    """여러 번 요청된 것이 화면에 보인다 — 서버가 결정한론적으로 센 결과."""
    c, _ = client
    login(c)
    slug = create_project(c, "반복 목격")
    raw = create_key(c, slug)
    h = auth_h(c, raw)
    for i in range(3):
        c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
            "session_id": f"s{i}", "items": [{"kind": "prompt", "text": "포팅은 alembic 으로 실행"}]})
    page = c.get(f"/projects/{slug}/secretary")
    assert "여러 번 요청한 것" in page.text
    assert "×3" in page.text


def test_project_page_advertises_secretary_backlog(client, user1):
    c, _ = client
    login(c)
    slug = create_project(c, "서가 머리말")
    raw = create_key(c, slug)
    c.post(f"/api/v1/projects/{slug}/observe", headers=auth_h(c, raw),
           json={"session_id": "s3", "items": [{"kind": "note", "text": "관찰 하나"}]})
    page = c.get(f"/projects/{slug}")
    assert "정리 대기 1건" in page.text
    assert "서기가 등재" in page.text                  # 출처별 칸
    assert f"/projects/{slug}/secretary" in page.text   # 관찰함으로 가는 길


def test_dashboard_counts_pending(client, user1):
    c, _ = client
    login(c)
    slug = create_project(c, "숫자 세는 대시보드")
    raw = create_key(c, slug)
    c.post(f"/api/v1/projects/{slug}/observe", headers=auth_h(c, raw),
           json={"session_id": "s4", "items": [{"kind": "note", "text": "첫째"}, {"kind": "note", "text": "둘째"}]})
    page = c.get("/")
    assert "서기 대기 관찰" in page.text


def test_auto_distill_setting_roundtrip(client, user1):
    """설정에 '레거시 자동 증류'를 켜면 /commit 이 옛 방식으로 지식을 만든다."""
    c, _ = client
    login(c)
    slug = create_project(c, "레거시 스위치")
    raw = create_key(c, slug)
    h = auth_h(c, raw)

    r = c.post(f"/api/v1/projects/{slug}/commit", headers=h,
               json={"question": "규칙?", "answer": "답", "session_id": "s5"})
    assert r.json()["mode"] == "observe"

    c.post(f"/projects/{slug}/settings", data={"name": "레거시 스위치", "auto_distill": "1"},
           follow_redirects=False)
    from app import db

    assert db.one("SELECT auto_distill FROM projects WHERE slug=?", (slug,))["auto_distill"] == 1

    r = c.post(f"/api/v1/projects/{slug}/commit", headers=h,
               json={"question": "두 번째 규칙?", "answer": "두 번째 답", "session_id": "s5"})
    assert r.json()["mode"] == "distill"
    assert r.json()["distilled"]["created"] >= 1


def test_secretary_clear_from_dashboard(client, user1):
    """사람이 대기 관찰을 직접 비울 수 있다 (서기를 기다리지 않아도)."""
    c, _ = client
    login(c)
    slug = create_project(c, "비우기")
    raw = create_key(c, slug)
    h = auth_h(c, raw)
    c.post(f"/api/v1/projects/{slug}/observe", headers=h,
           json={"session_id": "s6", "items": [{"kind": "note", "text": "지워질 관찰"}]})

    r = c.post(f"/projects/{slug}/secretary/clear", follow_redirects=False)
    assert r.status_code == 303
    st = c.get(f"/api/v1/projects/{slug}/secretary/status", headers=h).json()
    assert st["pending"] == 0 and st["skipped_total"] == 1


def test_knowledge_keeps_secretary_provenance(client, user1):
    """지식 한 권이 어디서 왔는지(서기·반복 횟수·evidence) 사람이 확인할 수 있다."""
    c, _ = client
    login(c)
    slug = create_project(c, "계보")
    raw = create_key(c, slug)
    h = auth_h(c, raw)
    obs = c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
        "session_id": "s7", "items": [{"kind": "prompt", "text": "배포 절차?"}]}).json()
    mid = c.post(f"/api/v1/projects/{slug}/remember", headers=h, json={
        "title": "배포 절차", "content": "staging 먼저, 그 다음 prod", "category": "commands",
        "source": "secretary", "occurrences": 3, "observation_ids": obs["logged"],
        "reason": "3번 요청됨"}).json()["memory_id"]

    from app import db

    row = db.one("SELECT source, occurrences, evidence FROM memories WHERE id=?", (mid,))
    assert row["source"] == "secretary" and row["occurrences"] >= 3
    assert "3번 요청됨" in row["evidence"]
    detail = c.get(f"/projects/{slug}/memories/{mid}").json()
    assert detail["title"] == "배포 절차"
    assert "배포 절차" in c.get(f"/projects/{slug}").text
