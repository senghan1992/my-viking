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
