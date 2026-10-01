"""핵심 루프 (서기 컨셉): 작업 세션의 관찰 → inbox → 서기 등재 → 검색·주입 → 적응.

중요: /commit 은 더 이상 지식을 만들지 않는다(관찰로만 저장). 지식을 등재하는 주체는
서기 agent 이며, 서버 API 로는 그 결과(remember + ack)만 들어온다.
"""
from conftest import create_key, create_project


def auth_h(c, raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


def new_project(c, name="결제 시스템"):
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, name, "PG 연동 프로젝트")
    raw = create_key(c, slug, "내 노트북")
    return slug, auth_h(c, raw)


def test_observe_does_not_create_knowledge(client, user1):
    """작업 세션의 관찰은 지식으로 승격되지 않는다 — 판단은 서기의 몫."""
    c, _ = client
    slug, h = new_project(c)

    r = c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
        "session_id": "s1", "agent": "pi", "transcript": "/root/.pi/sessions/x.jsonl",
        "items": [
            {"kind": "prompt", "text": "PG 결제 실패 시 재시도 안 하는 법?"},
            {"kind": "reply", "text": "승인 실패 응답에서 재시도하면 이중 결제가 된다. code != 0000 이면 raise 한다."},
        ],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["logged"], "관찰 id 가 반환되어야 한다"
    assert body["pending"] == 2
    from app import db

    assert db.one("SELECT COUNT(*) AS n FROM memories")["n"] == 0, "관찰만으로는 지식이 생기지 않는다"
    row = db.one("SELECT transcript, state FROM observations ORDER BY id DESC LIMIT 1")
    assert row["state"] == "open" and row["transcript"].endswith(".jsonl")


def test_secretary_files_knowledge_and_acks(client, user1):
    """서기: inbox 로 읽고 → remember 로 등재 → ack 로 관찰을 처리한다."""
    c, _ = client
    slug, h = new_project(c)
    obs = c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
        "session_id": "s1", "agent": "pi",
        "items": [{"kind": "prompt", "text": "재시도 정책이 뭐였지?"},
                  {"kind": "reply", "text": "중복 결제 방지를 위해 실패 응답은 재시도하지 않는다."}],
    }).json()

    feed = c.get(f"/api/v1/projects/{slug}/inbox?worker=sec1", headers=h).json()
    assert feed["pending"] == 2
    assert feed["sessions"][0]["observations"], "세션별로 관찰이 묶여야 한다"

    filed = c.post(f"/api/v1/projects/{slug}/remember", headers=h, json={
        "title": "결제 실패 응답은 재시도하지 않는다",
        "content": "PG 승인 실패(code != 0000)에 재시도하면 이중 결제가 된다. 실패 응답은 재시도 없이 raise 한다.",
        "category": "pitfalls", "source": "secretary", "occurrences": 2,
        "observation_ids": obs["logged"], "reason": "두 세션에서 같은 함정이 반복됨",
    }).json()
    mid = filed["memory_id"]

    # 관찰이 filed 로 처리됐고, 어떤 서기가 언제 정리했는지 계보가 남는다
    from app import db

    rows = db.rows("SELECT state, memory_id, worker FROM observations WHERE project_id=(SELECT id FROM projects WHERE slug=?)", (slug,))
    assert all(r["state"] == "filed" for r in rows)
    assert all(r["memory_id"] == mid for r in rows)
    mem = db.one("SELECT source, occurrences FROM memories WHERE id=?", (mid,))
    assert mem["source"] == "secretary" and mem["occurrences"] >= 2

    # 이제 관련 질문에 그 지식이 주입된다
    prep = c.post(f"/api/v1/projects/{slug}/prepare", headers=h,
                  json={"prompt": "결제가 실패했는데 재시도해도 되나?", "session_id": "s1"}).json()
    assert prep["items"], "방금 등재한 지식이 검색되어야 한다"
    assert prep["items"][0]["status"] == "fresh"           # 서기가 등재 → 아직 검증 전
    assert "⟨검증 전⟩" in prep["injection"]

    # 사람이 확인하면 확립
    assert c.post(f"/api/v1/projects/{slug}/score", headers=h,
                  json={"memory_id": mid, "outcome": "good"}).json()["status"] == "established"


def test_inbox_reveals_repeated_requests(client, user1):
    """서버가 결정론적으로 세는 '여러 번 요청한 항목' — 서기가 규칙으로 승격할 1순위."""
    c, _ = client
    slug, h = new_project(c)
    for turn in range(3):
        c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
            "session_id": f"s{turn}", "agent": "pi",
            "items": [{"kind": "prompt", "text": "로컬에서 DB 마이그레이션 어떻게 실행해?"}],
        })

    feed = c.get(f"/api/v1/projects/{slug}/inbox?claim=false", headers=h).json()
    assert feed["repeats"], "반복 요청이 inbox 에 보여야 한다"
    assert feed["repeats"][0]["hits"] >= 3
    assert "DB" in feed["repeats"][0]["text"] or "마이그레이션" in feed["repeats"][0]["text"]


def test_brief_reports_secretary_backlog(client, user1):
    c, _ = client
    slug, h = new_project(c)
    c.post(f"/api/v1/projects/{slug}/observe", headers=h,
           json={"session_id": "s9", "items": [{"kind": "note", "text": "이건 기록할 가치 있음"}]})
    brief = c.get(f"/api/v1/projects/{slug}/brief?session_id=s9&agent=pi", headers=h).json()
    assert "서기" in brief["orientation"]
    assert brief["pending_observations"] == 1
    assert "scribe secretary once" in brief["orientation"]


def test_secretary_run_is_audited(client, user1):
    """서기가 처리한 관찰·등재·버림 수가 실행 기록으로 남는다."""
    c, _ = client
    slug, h = new_project(c)
    obs = c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
        "session_id": "s1", "agent": "pi",
        "items": [{"kind": "prompt", "text": "일회성 파일 rename 요청"},
                  {"kind": "note", "text": "단순 오타 수정"}],
    }).json()
    r = c.post(f"/api/v1/projects/{slug}/inbox/ack", headers=h, json={
        "worker": "sec-run-1", "found": 2, "report": "일회성 작업만 있어 남긴 것 없음",
        "results": [{"ids": obs["logged"], "action": "skipped", "reason": "재발 가치 없음"}],
    })
    assert r.status_code == 200
    st = c.get(f"/api/v1/projects/{slug}/secretary/status", headers=h).json()
    assert st["pending"] == 0 and st["skipped_total"] == 2
    assert st["last_run"]["report"].startswith("일회성")


def test_implicit_feedback_demotes_bad_knowledge(client, user1):
    """불만 섞인 다음 질문 → 직전에 주입된 지식을 '검증 필요'로 강등 (적응 루프)."""
    c, _ = client
    slug, h = new_project(c)
    c.post(f"/api/v1/projects/{slug}/remember", headers=h,
           json={"title": "배포는 이렇게", "content": "npm run deploy:prod 로 배포한다", "source": "manual"})
    mid = c.get(f"/api/v1/projects/{slug}/search?q=배포", headers=h).json()["items"][0]["id"]

    c.post(f"/api/v1/projects/{slug}/prepare", headers=h, json={"prompt": "배포 어떻게?", "session_id": "s2"})
    c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
        "session_id": "s2", "agent": "pi",
        "items": [{"kind": "prompt", "text": "그래도 배포가 안 되는데?"},
                  {"kind": "reply", "text": "절차가 바뀌었다. CI 파이프라인을 쓴다."}],
    })
    row = c.get(f"/api/v1/projects/{slug}/search?q=배포", headers=h).json()
    assert all(i["id"] != mid for i in row["items"]), "강등된 지식은 일반 검색에서 빠진다"
    assert any(w["id"] == mid for w in row["warnings"]), "검증 필요 경고로 나온다"


def test_commit_is_observe_by_default(client, user1):
    """레거시 /commit 도 기본으로는 관찰만 저장한다 (auto_distill OFF)."""
    c, _ = client
    slug, h = new_project(c)
    r = c.post(f"/api/v1/projects/{slug}/commit", headers=h, json={
        "question": "포인트 적립 규칙이 뭐야?", "answer": "결제 금액의 1%를 적립한다.",
        "session_id": "s3", "agent": "claude"}).json()
    assert r["mode"] == "observe"
    assert r["distilled"]["created"] == 0
    from app import db

    assert db.one("SELECT COUNT(*) AS n FROM memories")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM observations")["n"] == 2


def test_legacy_auto_distill_still_works(client, user1):
    """auto_distill 을 켠 프로젝트는 옛 방식으로 서버가 즉시 증류한다 (pi 외 도구용)."""
    c, _ = client
    slug, h = new_project(c)
    from app import db

    db.execute("UPDATE projects SET auto_distill=1 WHERE slug=?", (slug,))
    r = c.post(f"/api/v1/projects/{slug}/commit", headers=h, json={
        "question": "PG 결제 실패 시 재시도 안 하는 법?",
        "answer": "실패 응답에 재시도하면 이중 결제가 된다. code != 0000 이면 raise 한다.",
        "session_id": "s4", "agent": "claude", "files": ["payment/gateway.py"]}).json()
    assert r["mode"] == "distill"
    assert r["distilled"]["created"] >= 1


def test_observe_redacts_secrets(client, user1):
    c, _ = client
    slug, h = new_project(c)
    obs = c.post(f"/api/v1/projects/{slug}/observe", headers=h, json={
        "session_id": "s5", "items": [{"kind": "reply",
                                       "text": "export OPENAI_API_KEY=sk-abc123def456ghi789jkl0123456789 하고 쓴다"}],
    }).json()
    from app import db

    row = db.one("SELECT text FROM observations WHERE id=?", (obs["logged"][0],))
    assert "sk-abc" not in row["text"] and "[REDACTED]" in row["text"]


def test_secretary_merge_same_title_accumulates_occurrences(client, user1):
    """같은 제목을 서기가 다시 등재하면 새 권이 아니라 갱신 + 반복 횟수 누적."""
    c, _ = client
    slug, h = new_project(c)
    body = {"title": "db 마이그레이션", "content": "flask db upgrade 를 쓴다", "source": "secretary"}
    first = c.post(f"/api/v1/projects/{slug}/remember", headers=h, json=body).json()
    assert first["superseded"] == 0 and not first.get("merged")
    second = c.post(f"/api/v1/projects/{slug}/remember", headers=h,
                    json={**body, "content": "alembic upgrade head 로 바뀌었다", "occurrences": 2}).json()
    assert second["merged"] == first["memory_id"], "같은 제목은 갱신해야 한다"

    from app import db

    assert db.one("SELECT COUNT(*) AS n FROM memories")["n"] == 1
    mem = db.one("SELECT occurrences, content FROM memories WHERE id=?", (first["memory_id"],))
    assert mem["occurrences"] >= 2 and "alembic" in mem["content"]


def test_remember_correction_supersedes(client, user1):
    """같은 제목의 확립 지식을 사람이 확인하고 다시 쓰면 옛것은 대체(superseded)."""
    c, _ = client
    slug, h = new_project(c)
    mid = c.post(f"/api/v1/projects/{slug}/remember", headers=h,
                 json={"title": "db 마이그레이션", "content": "flask db upgrade 를 쓴다", "confirmed": True}).json()["memory_id"]
    new = c.post(f"/api/v1/projects/{slug}/remember", headers=h,
                 json={"title": "db 마이그레이션", "content": "alembic upgrade head 를 쓴다", "confirmed": True}).json()

    from app import db

    old = db.one("SELECT status, superseded_by FROM memories WHERE id=?", (mid,))
    assert old["status"] == "superseded" and old["superseded_by"] == new["memory_id"]
    assert db.one("SELECT corrects FROM memories WHERE id=?", (new["memory_id"],))["corrects"] == mid


def test_me_identifies_project_from_key(client, user1):
    c, _ = client
    slug, _ = new_project(c, "키로 찾는 프로젝트")
    raw = create_key(c, slug, "그냥 내 노트북")
    r = c.get("/api/v1/me", headers=auth_h(c, raw))
    assert r.json() == {"project": slug, "project_name": "키로 찾는 프로젝트"}
    assert c.get("/api/v1/me", headers=auth_h(c, "sc_wrong")).status_code == 401


def test_isolation_via_api_key(client, user1, user2):
    """A 의 프로젝트 키로는 B 의 관찰함·지식에 접근 불가."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug_a = create_project(c, "A 만의 프로젝트")
    raw_a = create_key(c, slug_a)

    c.post("/logout")
    c.post("/login", data={"email": "b@test.com", "password": "password2"})
    slug_b = create_project(c, "B 만의 프로젝트")

    for path in (f"/api/v1/projects/{slug_b}/brief", f"/api/v1/projects/{slug_b}/inbox"):
        assert c.get(path, headers=auth_h(c, raw_a)).status_code == 404
    assert c.post(f"/api/v1/projects/{slug_b}/observe", headers=auth_h(c, raw_a),
                  json={"items": [{"kind": "note", "text": "새는 관찰"}]}).status_code == 404


def test_inbox_claim_prevents_double_work(client, user1):
    """두 서기가 같은 관찰을 동시에 정리하지 않게 선점한다."""
    c, _ = client
    slug, h = new_project(c)
    c.post(f"/api/v1/projects/{slug}/observe", headers=h,
           json={"session_id": "s7", "items": [{"kind": "note", "text": "선점 테스트"}]})

    first = c.get(f"/api/v1/projects/{slug}/inbox?worker=scribe-a", headers=h).json()
    assert first["pending"] == 1
    from app import db

    row = db.one("SELECT state, worker FROM observations ORDER BY id DESC LIMIT 1")
    assert row["state"] == "claimed" and row["worker"] == "scribe-a"

    second = c.get(f"/api/v1/projects/{slug}/inbox?worker=scribe-b", headers=h).json()
    assert second["sessions"] == [], "선점된 관찰은 다른 서기 작업함에 보이지 않는다"
    assert second["pending"] == 1, "대기 수는 유지된다 (누락 아님)"


def test_export_markdown(client, user1):
    c, _ = client
    slug, h = new_project(c, "내보내기")
    c.post(f"/api/v1/projects/{slug}/remember", headers=h,
           json={"title": "규칙", "content": "본문", "confirmed": True})
    r = c.get(f"/projects/{slug}/export.md")
    assert r.status_code == 200 and "# 내보내기" in r.text and "규칙" in r.text
