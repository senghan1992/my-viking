"""관찰함·서기 API의 경계 — 선점 회수, 처리 이유, 반복 누적, 읽기 전용.

컨셉상 서버가 하면 안 되는 것(판단)을 하지 않으면서, 서기가 같은 일을 두 번 하지
않도록 하는 장치들이다.
"""
from datetime import datetime, timedelta, timezone

from conftest import create_key, create_project


def auth_h(c, raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


def setup(client, user1, name="경계 테스트"):
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, name)
    raw = create_key(c, slug)
    return c, slug, auth_h(c, raw)


def test_ack_keeps_the_reason_why_it_was_skipped(client, user1):
    c, slug, h = setup(client, user1)
    obs = c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
        "session_id": "s1", "items": [{"kind": "prompt", "text": "단순 오타 고쳐줘"}]}).json()
    c.post(f"/api/v1/projects/{slug}/inbox/ack", headers=h, json={
        "worker": "sec-1", "found": 1,
        "results": [{"ids": obs["logged"], "action": "skipped", "reason": "일회성 작업 — 재발 가치 없음"}],
    })
    from app import db

    row = db.one("SELECT state, note, worker FROM observations WHERE id=?", (obs["logged"][0],))
    assert row["state"] == "skipped"
    assert "일회성" in row["note"], "버린 이유가 남아야 다음에 같은 판단을 안 한다"
    assert row["worker"] == "sec-1"


def test_stale_claim_returns_to_the_queue(client, user1):
    """서기가 선점만 해두고 죽으면 관찰은 다시 대기으로 돌아온다 (일이 사라지지 않는다)."""
    c, slug, h = setup(client, user1)
    obs = c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
        "session_id": "s2", "items": [{"kind": "note", "text": "잊혀질 뻔한 관찰"}]}).json()

    c.get(f"/api/v1/projects/{slug}/inbox?worker=ghost", headers=h)  # 선점
    from app import db

    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    db.execute("UPDATE observations SET processed_at=? WHERE id=?", (old, obs["logged"][0]))

    feed = c.get(f"/api/v1/projects/{slug}/inbox?worker=fresh-scribe", headers=h).json()
    shown = [o["id"] for s in feed["sessions"] for o in s["observations"]]
    assert obs["logged"][0] in shown, "2시간 전 선점은 회수되어 새 서기에게 보여야 한다"
    row = db.one("SELECT worker FROM observations WHERE id=?", (obs["logged"][0],))
    assert row["worker"] == "fresh-scribe"


def test_read_only_inbox_shows_claimed_work(client, user1):
    """사람이 화면/CLI 로 볼 때는 남이 선점한 것도 보인다 (대기 수와 목록이 어긋나면 혼란)."""
    c, slug, h = setup(client, user1)
    c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
        "session_id": "s3", "items": [{"kind": "note", "text": "선점된 관찰"}]})
    c.get(f"/api/v1/projects/{slug}/inbox?worker=scribe-a", headers=h)

    peek = c.get(f"/api/v1/projects/{slug}/inbox?claim=false", headers=h).json()
    assert peek["sessions"], "읽기 전용 조회는 선점 상태를 가리지 않는다"
    assert peek["sessions"][0]["observations"][0]["state"] == "claimed"


def test_seen_accumulates_without_editing_content(client, user1):
    """지식 본문은 그대로 두고 '또 요청받았다' 횟수만 올린다."""
    c, slug, h = setup(client, user1)
    mid = c.post(f"/api/v1/projects/{slug}/remember", headers=h,
                 json={"title": "배포 절차", "content": "staging 먼저", "source": "secretary",
                       "occurrences": 1}).json()["memory_id"]
    before = c.get(f"/api/v1/projects/{slug}/search?q=배포", headers=h).json()["items"][0]["text"]

    c.post(f"/api/v1/projects/{slug}/seen", headers=h, json={"memory_id": mid, "occurrences": 2})
    from app import db

    row = db.one("SELECT occurrences, content, last_seen_at FROM memories WHERE id=?", (mid,))
    assert row["occurrences"] == 3
    assert row["content"] == "staging 먼저"
    assert row["last_seen_at"] is not None
    assert c.get(f"/api/v1/projects/{slug}/search?q=배포", headers=h).json()["items"][0]["text"] == before


def test_secretary_can_establish_contested_knowledge(client, user1):
    """검증 필요로 내려간 지식을 서기가 이번 세션 증거로 되돌릴 수 있다."""
    c, slug, h = setup(client, user1)
    mid = c.post(f"/api/v1/projects/{slug}/remember", headers=h,
                 json={"title": "이중 결제 방지", "content": "실패 응답은 재시도 금지", "confirmed": True,
                       "source": "secretary"}).json()["memory_id"]
    c.post(f"/api/v1/projects/{slug}/score", headers=h, json={"memory_id": mid, "outcome": "bad"})
    assert c.get(f"/api/v1/projects/{slug}/search?q=재시도", headers=h).json()["warnings"]

    r = c.post(f"/api/v1/projects/{slug}/score", headers=h,
               json={"memory_id": mid, "outcome": "good", "note": "서기: 이번 세션에서 실제로 막혔다"}).json()
    assert r["status"] == "established"


def test_observe_of_empty_text_is_ignored(client, user1):
    c, slug, h = setup(client, user1)
    r = c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
        "session_id": "s4", "items": [{"kind": "prompt", "text": "   "}, {"kind": "reply", "text": "실제 답"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["logged"] and len(body["logged"]) == 1, "빈 관찰은 넣지 않는다"


def test_inbox_is_capped_and_ordered(client, user1):
    """limit 으로 읽는 양을 조절하고, 오래된 것부터(세션 흐름 순) 읽힌다."""
    c, slug, h = setup(client, user1)
    ids = []
    for i in range(6):
        r = c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
            "session_id": f"s{i}", "items": [{"kind": "prompt", "text": f"관찰 {i}번 요청"}]})
        ids += r.json()["logged"]
    feed = c.get(f"/api/v1/projects/{slug}/inbox?limit=3&claim=false", headers=h).json()
    shown = [o["id"] for s in feed["sessions"] for o in s["observations"]]
    assert len(shown) == 3
    assert shown == sorted(shown), "오래된 관찰부터 읽혀야 세션 흐름이 유지된다"
    assert feed["pending"] == 6, "읽지 못한 것도 대기 수에는 포함된다"
