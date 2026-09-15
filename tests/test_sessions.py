"""죽은 세션 정리 — 질문 0회 세션은 브리핑·대시보드에서 숨고, 유예 후 삭제된다."""
from datetime import datetime, timedelta, timezone

from conftest import create_key, create_project


def auth_h(c, raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


def test_dead_session_not_shown_in_brief_or_dashboard(client, user1):
    """brief 만 받고 질문 없이 죽은 세션은 브리핑·대시보드에 보이지 않는다."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "주문 서비스")
    raw = create_key(c, slug)
    h = auth_h(c, raw)

    # brief 만 호출 (질문 없이 죽은 세션 — 예: 세션 시작 후 바로 종료)
    r = c.get(f"/api/v1/projects/{slug}/brief?session_id=dead1&agent=claude", headers=h)
    assert r.status_code == 200

    # 브리핑의 '최근 작업'에 죽은 세션이 나타나지 않는다
    brief = c.get(f"/api/v1/projects/{slug}/brief", headers=h).json()
    assert "최근 작업" not in brief["orientation"]

    # 대시보드에도 보이지 않는다 (기록 세션 없음 → 연결 안 됨 배너)
    page = c.get(f"/projects/{slug}").text
    assert "질문 0회" not in page
    assert "아직 에이전트가 연결되지 않았습니다" in page


def test_session_appears_in_brief_after_commit(client, user1):
    """질문·답이 기록된(commit) 세션은 질문 N회로 정상 노출된다."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "결제")
    raw = create_key(c, slug)
    h = auth_h(c, raw)

    c.get(f"/api/v1/projects/{slug}/brief?session_id=s1&agent=pi", headers=h)
    r = c.post(f"/api/v1/projects/{slug}/commit", headers=h, json={
        "question": "포인트 적립 규칙이 뭐야?",
        "answer": "결제 금액의 1%를 적립한다.",
        "session_id": "s1", "agent": "pi",
    })
    assert r.status_code == 200

    brief = c.get(f"/api/v1/projects/{slug}/brief", headers=h).json()
    assert "최근 작업" in brief["orientation"]
    assert "질문 1회" in brief["orientation"]

    page = c.get(f"/projects/{slug}").text
    assert "질문 1회" in page


def test_sweep_removes_only_stale_zero_question_sessions(client, user1):
    """스위퍼: 0회 + 유예(24h) 초과 세션만 삭제, 갓 시작한 세션·기록된 세션은 유지."""
    from app import db

    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "스위퍼")
    raw = create_key(c, slug)
    h = auth_h(c, raw)

    pid = db.one("SELECT id FROM projects WHERE slug=?", (slug,))["id"]
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds")
    fresh = db.now()

    db.execute("INSERT INTO sessions(id, project_id, agent, started_at, question_count) VALUES(?,?,?,?,?)",
               ("stale-dead", pid, "pi", old, 0))
    db.execute("INSERT INTO sessions(id, project_id, agent, started_at, question_count) VALUES(?,?,?,?,?)",
               ("fresh-dead", pid, "pi", fresh, 0))
    db.execute("INSERT INTO sessions(id, project_id, agent, started_at, question_count) VALUES(?,?,?,?,?)",
               ("stale-worked", pid, "claude", old, 3))

    # brief 한 번이면 유예 초과 0회 세션은 사라진다
    c.get(f"/api/v1/projects/{slug}/brief", headers=h)

    assert db.one("SELECT id FROM sessions WHERE id='stale-dead'") is None
    assert db.one("SELECT id FROM sessions WHERE id='fresh-dead'") is not None, "유예 안의 세션은 유지"
    assert db.one("SELECT id FROM sessions WHERE id='stale-worked'") is not None, "질문 기록 세션은 유지"