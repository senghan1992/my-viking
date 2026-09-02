"""원격 서버로서의 동작: 인증, HTTP MCP 전송, 대시보드, 프로젝트 해석."""

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jarvis.server import create_app  # noqa: E402


@pytest.fixture()
def app(home):
    return create_app(home=str(home))


@pytest.fixture()
def client(app):
    return TestClient(app)


def rpc(client, method, params=None, rid=1, key=""):
    headers = {"authorization": f"Bearer {key}"} if key else {}
    res = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}},
        headers=headers,
    )
    return res


def call(client, name, args, rid=1, key=""):
    res = rpc(client, "tools/call", {"name": name, "arguments": args}, rid, key)
    body = res.json()
    return json.loads(body["result"]["content"][0]["text"]), body["result"]["isError"]


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------
def test_open_until_first_key_is_created(client):
    assert client.get("/health").json()["auth_required"] is False
    assert client.get("/projects").status_code == 200

    made = client.post("/keys", json={"name": "laptop"}).json()
    assert made["key"].startswith("jv_")
    assert client.get("/health").json()["auth_required"] is True

    # Now the same call must fail without the key.
    assert client.get("/projects").status_code == 401
    ok = client.get("/projects", headers={"authorization": f"Bearer {made['key']}"})
    assert ok.status_code == 200


def test_health_and_dashboard_stay_reachable_without_a_key(client):
    client.post("/keys", json={"name": "k"})
    assert client.get("/health").status_code == 200
    page = client.get("/")
    assert page.status_code == 200
    assert "MyViking" in page.text


def test_wrong_key_is_rejected(client):
    client.post("/keys", json={"name": "k"})
    res = client.get("/projects", headers={"authorization": "Bearer jv_wrong"})
    assert res.status_code == 401


def test_x_api_key_header_also_works(client):
    key = client.post("/keys", json={"name": "k"}).json()["key"]
    assert client.get("/projects", headers={"x-api-key": key}).status_code == 200


def test_revoked_key_stops_working(client):
    made = client.post("/keys", json={"name": "k"}).json()
    hdr = {"authorization": f"Bearer {made['key']}"}
    assert client.get("/projects", headers=hdr).status_code == 200
    client.delete(f"/keys/{made['id']}", headers=hdr)
    # No active keys remain, so the server returns to open mode rather than
    # locking everyone out of their own data.
    assert client.get("/health").json()["auth_required"] is False


def test_project_scoped_key_cannot_read_other_projects(client):
    client.post("/projects", json={"project": "a"})
    client.post("/projects", json={"project": "b"})
    key = client.post("/keys", json={"name": "only-a", "projects": ["a"]}).json()["key"]
    hdr = {"authorization": f"Bearer {key}"}
    assert client.post("/prepare", json={"project": "a", "question": "q"}, headers=hdr).status_code == 200
    assert client.post("/prepare", json={"project": "b", "question": "q"}, headers=hdr).status_code == 403


def test_plaintext_key_is_never_returned_again(client):
    made = client.post("/keys", json={"name": "k"}).json()
    listed = client.get("/keys", headers={"authorization": f"Bearer {made['key']}"}).json()
    assert listed[0]["id"] == made["id"]
    assert "key" not in listed[0]
    assert made["key"] not in json.dumps(listed)


# --------------------------------------------------------------------------
# remote MCP transport
# --------------------------------------------------------------------------
def test_mcp_http_handshake_returns_session_id(client):
    res = rpc(client, "initialize")
    assert res.status_code == 200
    assert res.json()["result"]["serverInfo"]["name"] == "myviking"
    assert res.headers.get("Mcp-Session-Id")


def test_mcp_http_exposes_the_same_tools_as_stdio(client):
    from jarvis.mcp_core import tool_names

    listed = {t["name"] for t in rpc(client, "tools/list").json()["result"]["tools"]}
    assert listed == set(tool_names())
    assert listed == {t["name"] for t in client.get("/mcp/tools").json()}


def test_mcp_http_full_loop(client):
    call(client, "jarvis_profile", {"op": "init", "project": "app", "template": "coding"})
    call(
        client,
        "jarvis_remember",
        {
            "project": "app",
            "category": "commands",
            "title": "테스트",
            "statement": "pytest -q 로 돌린다",
        },
    )
    ctx, err = call(client, "jarvis_context", {"project": "app", "question": "테스트 실행"})
    assert not err
    assert "pytest" in ctx["context"]
    trace_id = ctx["trace_id"]

    call(
        client,
        "jarvis_commit",
        {
            "project": "app",
            "question": "테스트 실행",
            "answer": "pytest -q",
            "trace_id": trace_id,
            "latency_ms": 900,
        },
    )
    scored, err = call(client, "jarvis_score", {"trace_id": trace_id, "value": 1.0})
    assert not err
    assert scored["memories_adjusted"]

    trace = client.get(f"/traces/{trace_id}").json()
    assert trace["total_ms"] >= 900
    assert [o["type"] for o in trace["observations"]][:2] == ["cache", "retrieval"]

    again, _ = call(client, "jarvis_context", {"project": "app", "question": "테스트 실행"})
    assert again["reused"] is True


def test_mcp_http_notifications_get_no_body(client):
    res = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert res.status_code == 202


def test_mcp_http_batch(client):
    res = client.post(
        "/mcp",
        json=[
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ],
    )
    body = res.json()
    assert isinstance(body, list) and len(body) == 2
    assert [m["id"] for m in body] == [1, 2]


def test_mcp_http_bad_json(client):
    res = client.post("/mcp", content=b"not json", headers={"content-type": "application/json"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == -32700


def test_mcp_http_requires_key_when_auth_is_on(client):
    key = client.post("/keys", json={"name": "k"}).json()["key"]
    assert rpc(client, "tools/list").status_code == 401
    assert rpc(client, "tools/list", key=key).status_code == 200


def test_mcp_get_is_not_a_stream(client):
    assert client.get("/mcp").status_code == 405


# --------------------------------------------------------------------------
# project resolution across machines
# --------------------------------------------------------------------------
def test_repo_alias_resolves_from_any_url_form(client):
    client.post("/projects", json={"project": "backend"})
    client.post("/aliases", json={"alias": "github.com/me/backend", "project": "backend"})
    for form in (
        "git@github.com:me/backend.git",
        "https://github.com/me/backend",
        "https://github.com/me/backend.git",
        "ssh://git@github.com/me/backend",
    ):
        got = client.post("/resolve", json={"repo": form}).json()
        assert got["project"] == "backend", form


def test_resolve_reports_candidates_when_it_cannot_decide(client):
    client.post("/projects", json={"project": "alpha"})
    got = client.post("/resolve", json={"repo": "github.com/other/thing"}).json()
    assert got["project"] == ""
    assert "alpha" in got["candidates"]


def test_prepare_creates_and_binds_on_first_contact(client):
    body = client.post(
        "/prepare",
        json={"repo": "git@github.com:me/newrepo.git", "question": "처음 접속"},
    ).json()
    assert body["project"] == "newrepo"
    # A second call from a different URL form must land in the same project.
    again = client.post(
        "/prepare", json={"repo": "https://github.com/me/newrepo", "question": "다시"}
    ).json()
    assert again["project"] == "newrepo"


# --------------------------------------------------------------------------
# observability surface
# --------------------------------------------------------------------------
def test_metrics_separates_context_time_from_answer_time(client):
    client.post("/projects", json={"project": "app", "template": "coding"})
    body = client.post("/prepare", json={"project": "app", "question": "질문"}).json()
    client.post(
        "/commit",
        json={
            "project": "app",
            "question": "질문",
            "answer": "답변",
            "trace_id": body["trace_id"],
            "latency_ms": 3000,
        },
    )
    m = client.get("/metrics", params={"project": "app"}).json()
    assert m["answer_ms"]["p50"] >= 3000
    assert m["context_ms"]["p50"] < m["answer_ms"]["p50"]
    assert {s["type"] for s in m["steps"]} >= {"retrieval", "generation"}


def test_agents_are_registered_from_calls(client):
    client.post("/projects", json={"project": "app"})
    client.post(
        "/prepare",
        json={"project": "app", "question": "질문", "agent": "claude-code@laptop"},
    )
    agents = client.get("/agents").json()
    assert agents[0]["name"] == "claude-code@laptop"
    assert agents[0]["projects"] == ["app"]


def test_score_on_unknown_trace_is_404(client):
    assert client.post("/scores", json={"trace_id": "nope", "value": 1.0}).status_code == 404


# --------------------------------------------------------------------------
# curation over HTTP — what the dashboard's 검토 / 데이터베이스 tabs call
# --------------------------------------------------------------------------
def _review(client, project="app"):
    return client.get(
        f"/projects/{project}/review", params={"include_unconfirmed": True}
    ).json()


def _first_review(client, project="app"):
    return _review(client, project)[0]["uri"]


def _summary_total(client, project="app"):
    return client.get(
        "/review/summary", params={"project": project, "include_unconfirmed": True}
    ).json()["total"]


def _seed(client):
    client.post("/projects", json={"project": "app", "template": "coding"})
    client.post(
        "/memories",
        json={
            "project": "app",
            "category": "commands",
            "title": "테스트 실행",
            "statement": "pytest -q 로 돌린다",
        },
    )
    for q, a in [
        ("앞으로 커밋 메시지는 항상 한글로 써줘", "네, 한글로 씁니다."),
        ("앞으로 커밋 메시지는 항상 영어로 써줘", "네, 영어로 씁니다."),
        # No rule shape, so this one lands under the fallback category.
        ("웹훅 서명은 어디서 검증하지?", "webhooks/verify.py 에서 HMAC 으로 검증합니다."),
    ]:
        client.post("/commit", json={"project": "app", "question": q, "answer": a})


def test_contradiction_resolves_itself_without_a_person(client):
    """Default policy: the newer instruction supersedes, and nothing lands in a
    queue waiting to be triaged."""
    _seed(client)
    summary = client.get("/review/summary", params={"project": "app"}).json()
    assert summary["total"] == 0, summary

    conv = [
        m
        for m in client.get("/projects/app/memories").json()
        if m["category"] == "conventions"
    ]
    assert len(conv) == 1
    detail = client.get("/memories/detail", params={"uri": conv[0]["uri"]}).json()
    assert "영어" in detail["abstract"]
    assert detail["conflict"]["resolution"] == "superseded"


def test_review_queue_is_ordered_by_urgency_when_audited(client):
    _seed(client)
    queue = client.get(
        "/projects/app/review", params={"include_unconfirmed": True}
    ).json()
    assert queue
    assert queue[0]["priority"] >= queue[-1]["priority"]


def test_manual_memory_is_not_in_the_review_queue(client):
    _seed(client)
    uris = [
        i["uri"]
        for i in client.get(
            "/projects/app/review", params={"include_unconfirmed": True}
        ).json()
    ]
    assert not any("commands/테스트-실행" in u for u in uris)


def test_memory_detail_gives_the_editor_everything_it_needs(client):
    _seed(client)
    uri = _first_review(client)
    d = client.get("/memories/detail", params={"uri": uri}).json()
    for key in ("title", "category", "abstract", "body", "confidence", "origin",
                "reviewed", "sources", "tokens", "impact", "reasons", "path"):
        assert key in d, key


def test_confirm_clears_the_item(client):
    _seed(client)
    uri = _first_review(client)
    before = _summary_total(client)
    res = client.post("/memories/confirm", json={"uri": uri}).json()
    assert res["reviewed"] is True
    after = _summary_total(client)
    assert after == before - 1


def test_edit_moves_the_file_and_marks_reviewed(client):
    _seed(client)
    uri = [
        i["uri"] for i in _review(client) if i["category"] == "cases"
    ][0]
    res = client.patch(
        "/memories",
        json={
            "uri": uri,
            "title": "커밋 메시지 언어",
            "statement": "커밋 메시지는 한글로 쓴다",
            "category": "conventions",
        },
    ).json()
    assert res["uri"].endswith("conventions/커밋-메시지-언어")
    assert res["moved_from"] == uri
    assert client.get("/memories/detail", params={"uri": uri}).status_code == 404
    d = client.get("/memories/detail", params={"uri": res["uri"]}).json()
    assert d["reviewed"] is True
    assert d["abstract"] == "커밋 메시지는 한글로 쓴다"


def test_edit_rejects_an_unknown_category(client):
    _seed(client)
    uri = _first_review(client)
    res = client.patch("/memories", json={"uri": uri, "category": "없는것"})
    assert res.status_code == 400


def test_edit_and_confirm_on_missing_memory_are_404(client):
    _seed(client)
    ghost = "jarvis://projects/app/memories/commands/ghost"
    assert client.patch("/memories", json={"uri": ghost, "title": "x"}).status_code == 404
    assert client.post("/memories/confirm", json={"uri": ghost}).status_code == 404


def test_archive_from_the_ui_removes_it_from_retrieval(client):
    _seed(client)
    uri = _first_review(client)
    client.delete("/memories", params={"uri": uri, "archive": True})
    assert uri not in [i["uri"] for i in _review(client)]
    ctx = client.post("/prepare", json={"project": "app", "question": "커밋 메시지 규칙"}).json()
    assert uri not in [i["uri"] for i in (ctx.get("packed") or {}).get("items", [])]


def test_review_is_scoped_by_key(client):
    client.post("/projects", json={"project": "a", "template": "coding"})
    client.post("/projects", json={"project": "b", "template": "coding"})
    key = client.post("/keys", json={"name": "only-a", "projects": ["a"]}).json()["key"]
    hdr = {"authorization": f"Bearer {key}"}
    assert client.get("/projects/a/review", headers=hdr).status_code == 200
    assert client.get("/projects/b/review", headers=hdr).status_code == 403


def test_dashboard_lands_on_project_setup(client):
    """The only job a person has here is: make a project, take the connection
    info. That has to be the first thing on screen."""
    page = client.get("/").text
    for label in ("프로젝트", "지식", "활동"):
        assert label in page
    assert 'data-tab="projects" class="on"' in page
    # The project-creation form and the connection dialog must both be present.
    assert 'id="np-create"' in page
    assert 'id="conn-body"' in page


# --------------------------------------------------------------------------
# connection info — the one thing a person comes to the dashboard for
# --------------------------------------------------------------------------
def test_connection_gives_setup_and_instructions(client):
    client.post("/projects", json={"project": "backend", "template": "coding"})
    c = client.get(
        "/projects/backend/connection",
        params={"client": "claude-code", "base_url": "https://viking.example.com"},
    ).json()

    assert c["mcp_url"] == "https://viking.example.com/mcp"
    assert "claude mcp add --transport http" in c["setup"]
    assert c["instruction_file"] == "CLAUDE.md"
    # Wiring alone does nothing; the instruction half must be there too.
    for tool in ("jarvis_context", "jarvis_remember", "jarvis_commit", "jarvis_score"):
        assert tool in c["instructions"], tool
    assert 'project="backend"' in c["instructions"]
    assert c["has_key"] is False


def test_connection_embeds_the_key_when_given(client):
    client.post("/projects", json={"project": "backend"})
    c = client.get(
        "/projects/backend/connection",
        params={"client": "claude-code", "key": "jv_TEST", "base_url": "http://h:1"},
    ).json()
    assert "Authorization: Bearer jv_TEST" in c["setup"]
    assert c["has_key"] is True


def test_connection_covers_every_supported_client(client):
    from jarvis.connect import CLIENTS

    client.post("/projects", json={"project": "backend"})
    for name in CLIENTS:
        c = client.get(
            "/projects/backend/connection",
            params={"client": name, "base_url": "http://h:1"},
        ).json()
        assert c["mcp_url"] == "http://h:1/mcp", name
        assert c["setup"].strip(), name
        assert c["setup_kind"] in ("shell", "json", "toml"), name


def test_connection_uses_the_browsed_address_not_the_bind_address(client):
    """An agent on another machine cannot dial the address the server bound to."""
    client.post("/projects", json={"project": "backend"})
    c = client.get(
        "/projects/backend/connection",
        params={"base_url": "https://viking.example.com:8443"},
    ).json()
    assert c["mcp_url"] == "https://viking.example.com:8443/mcp"


def test_connection_rejects_unknown_client_and_project(client):
    client.post("/projects", json={"project": "backend"})
    assert client.get(
        "/projects/backend/connection", params={"client": "nope"}
    ).status_code == 400
    assert client.get("/projects/ghost/connection").status_code == 404


def test_connection_reports_bound_repos(client):
    client.post("/projects", json={"project": "backend"})
    client.post("/aliases", json={"alias": "git@github.com:me/backend.git", "project": "backend"})
    c = client.get("/projects/backend/connection").json()
    # Stored in the canonical form lookups use, so ssh- and https-form remotes
    # both resolve. Storing the raw string meant an alias could fail to match
    # even the exact remote it was created from.
    assert [a["alias"] for a in c["aliases"]] == ["github.com/me/backend"]


def test_project_cards_report_what_the_ui_shows(client):
    client.post("/projects", json={"project": "backend", "template": "coding"})
    client.post(
        "/memories",
        json={"project": "backend", "category": "commands", "title": "t", "statement": "s"},
    )
    body = client.post("/prepare", json={"project": "backend", "question": "q"}).json()
    client.post(
        "/commit",
        json={
            "project": "backend",
            "question": "q",
            "answer": "a",
            "trace_id": body["trace_id"],
        },
    )
    card = client.get("/projects").json()[0]
    for key in ("memories", "tasks", "last_active", "aliases", "warn_categories"):
        assert key in card, key
    assert card["memories"] >= 1
    assert card["tasks"] >= 1
    assert card["last_active"]


def test_worksessions_endpoint_groups_traces_into_sittings(client):
    client.post("/projects", json={"project": "app", "template": "coding"})
    client.post(
        "/memories",
        json={"project": "app", "category": "commands", "title": "t", "statement": "pytest -q"},
    )
    for q in ("첫 질문", "두번째 질문"):
        body = client.post(
            "/prepare",
            json={"project": "app", "question": q, "session_id": "s1", "agent": "a@x"},
        ).json()
        client.post(
            "/commit",
            json={"project": "app", "question": q, "answer": "답", "trace_id": body["trace_id"]},
        )
    sessions = client.get("/worksessions", params={"project": "app"}).json()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "s1"
    assert [w["question"] for w in sessions[0]["work"]] == ["첫 질문", "두번째 질문"]


def test_brief_endpoint_serves_the_session_handoff(client):
    client.post("/projects", json={"project": "app", "template": "coding"})
    client.post(
        "/memories",
        json={
            "project": "app",
            "category": "pitfalls",
            "title": "재시도 금지",
            "statement": "0000 이 아니면 재시도하지 않는다",
        },
    )
    b = client.get("/projects/app/brief").json()
    assert [w["title"] for w in b["warnings"]] == ["재시도 금지"]
    for key in ("know", "warnings", "unresolved", "recent_work", "recently_learned", "totals"):
        assert key in b, key
    assert client.get("/projects/ghost/brief").status_code == 404


def test_dashboard_shows_the_work_session_view(client):
    page = client.get("/").text
    assert 'id="sessions"' in page
    assert "작업 세션" in page


def test_repeated_auth_failures_get_backed_off(client):
    """포트포워딩으로 인터넷에 열리는 서버다 — 키 무차별 대입은 기본 전제.

    한 IP 가 1분 안에 10번 틀리면 그 다음부터는 검증조차 하지 않고 429 를
    돌려준다. 공개 경로(/health, 대시보드)는 영향을 받지 않는다."""
    client.post("/keys", json={"name": "k"})

    for _ in range(10):
        res = client.get("/projects", headers={"authorization": "Bearer jv_wrong"})
        assert res.status_code == 401
    blocked = client.get("/projects", headers={"authorization": "Bearer jv_wrong"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers

    # 차단은 인증이 필요한 경로에만 걸린다.
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200


def test_successful_auth_resets_the_failure_count(home):
    from fastapi.testclient import TestClient as TC

    from jarvis.server import create_app as ca

    client = TC(ca(home=str(home)))
    made = client.post("/keys", json={"name": "k"}).json()
    for _ in range(9):  # 문턱(10) 직전까지 실패
        client.get("/projects", headers={"authorization": "Bearer jv_wrong"})
    ok = client.get("/projects", headers={"authorization": f"Bearer {made['key']}"})
    assert ok.status_code == 200
    # 성공이 카운터를 리셋했으므로 한 번 더 틀려도 401 (429 아님)
    res = client.get("/projects", headers={"authorization": "Bearer jv_wrong"})
    assert res.status_code == 401
