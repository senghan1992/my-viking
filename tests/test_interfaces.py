"""MCP stdio 서버와 HTTP API 의 계약 테스트."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _mcp(home, requests):
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    proc = subprocess.run(
        [sys.executable, "-m", "jarvis.mcp_server"],
        input=payload,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": SRC, "JARVIS_HOME": str(home), "PATH": "/usr/bin:/bin"},
        timeout=90,
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def _call(name, args, rid=1):
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }


def _payload(msg):
    return json.loads(msg["result"]["content"][0]["text"])


def test_mcp_handshake_and_tool_list(home):
    out = _mcp(
        home,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ],
    )
    assert out[0]["result"]["serverInfo"]["name"] == "myviking"
    tools = {t["name"] for t in out[1]["result"]["tools"]}
    assert tools == {
        "jarvis_brief",
        "jarvis_history",
        "jarvis_context",
        "jarvis_remember",
        "jarvis_commit",
        "jarvis_score",
        "jarvis_browse",
        "jarvis_prompt",
        "jarvis_profile",
    }
    for tool in out[1]["result"]["tools"]:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


def test_mcp_full_loop(home):
    out = _mcp(
        home,
        [
            _call("jarvis_profile", {"op": "init", "project": "app", "template": "coding"}, 1),
            _call(
                "jarvis_remember",
                {
                    "project": "app",
                    "category": "commands",
                    "title": "테스트 실행",
                    "statement": "pytest -q 로 돌린다",
                },
                2,
            ),
            _call("jarvis_context", {"project": "app", "question": "테스트 실행 방법"}, 3),
            _call(
                "jarvis_commit",
                {"project": "app", "question": "테스트 실행 방법", "answer": "pytest -q"},
                4,
            ),
            _call("jarvis_context", {"project": "app", "question": "테스트 실행 방법"}, 5),
        ],
    )
    assert _payload(out[0])["profile"]["template"] == "coding"
    assert _payload(out[1])["uri"].endswith("commands/테스트-실행")
    ctx = _payload(out[2])
    assert ctx["reused"] is False
    assert "pytest" in ctx["context"]
    assert ctx["trace_id"].startswith("tr_")
    assert _payload(out[3])["session"]
    hit = _payload(out[4])
    assert hit["reused"] is True
    assert hit["answer"] == "pytest -q"


def test_mcp_errors_are_returned_not_crashes(home):
    out = _mcp(
        home,
        [
            _call("jarvis_browse", {"op": "read", "uri": "jarvis://projects/x/memories/a/b"}, 1),
            _call("nope", {}, 2),
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        ],
    )
    assert out[0]["result"]["isError"] is True
    assert out[1]["result"]["isError"] is True
    # The server stays alive after tool errors.
    assert out[2]["result"]["tools"]


def test_mcp_ignores_malformed_lines(home):
    proc = subprocess.run(
        [sys.executable, "-m", "jarvis.mcp_server"],
        input='not json\n\n{"jsonrpc":"2.0","id":9,"method":"ping"}\n',
        capture_output=True,
        text=True,
        env={"PYTHONPATH": SRC, "JARVIS_HOME": str(home), "PATH": "/usr/bin:/bin"},
        timeout=90,
    )
    lines = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
    assert lines == [{"jsonrpc": "2.0", "id": 9, "result": {}}]


# --------------------------------------------------------------------------
@pytest.fixture()
def client(home):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from jarvis.server import create_app

    return TestClient(create_app(home=str(home)))


def test_http_health_and_loop(client):
    assert client.get("/health").json()["ok"] is True

    r = client.post("/projects", json={"project": "app", "template": "coding"})
    assert r.status_code == 200
    assert r.json()["profile"]["template"] == "coding"

    r = client.post(
        "/memories",
        json={
            "project": "app",
            "category": "commands",
            "title": "테스트 실행",
            "statement": "pytest -q 로 돌린다",
        },
    )
    assert r.status_code == 200

    r = client.post(
        "/prepare",
        json={"project": "app", "question": "테스트 실행 방법", "agent": "test-agent"},
    )
    body = r.json()
    assert body["cache_hit"] is None
    assert "pytest" in body["system"]
    trace_id = body["trace_id"]
    assert trace_id.startswith("tr_")

    r = client.post(
        "/commit",
        json={
            "project": "app",
            "question": "테스트 실행 방법",
            "answer": "pytest -q",
            "tokens_in": 800,
            "tokens_out": 10,
            "trace_id": trace_id,
            "latency_ms": 1500,
        },
    )
    assert r.json()["session"]

    r = client.post("/prepare", json={"project": "app", "question": "테스트 실행 방법"})
    assert r.json()["cache_hit"]["answer"] == "pytest -q"

    rep = client.get("/report", params={"project": "app"}).json()
    assert rep["saved_cache"] == 810


def test_http_rejects_unknown_category(client):
    client.post("/projects", json={"project": "app", "template": "coding"})
    r = client.post(
        "/memories",
        json={"project": "app", "category": "없는것", "title": "t", "statement": "s"},
    )
    assert r.status_code == 400


def test_http_404_on_missing_prompt(client):
    client.post("/projects", json={"project": "app"})
    r = client.post("/prompts/render", json={"project": "app", "name": "nope"})
    assert r.status_code == 404


def test_http_browse_endpoints(client):
    client.post("/projects", json={"project": "app", "template": "coding"})
    client.post(
        "/memories",
        json={
            "project": "app",
            "category": "commands",
            "title": "빌드",
            "statement": "make build 로 빌드한다",
        },
    )
    assert client.get("/ls", params={"uri": "jarvis://projects/app/memories"}).json()["dirs"]
    assert client.get("/find", params={"q": "빌드", "project": "app"}).json()["results"]
    assert client.get("/grep", params={"term": "make build"}).json()
    tree = client.get("/tree", params={"uri": "jarvis://projects/app", "depth": 2}).json()
    assert tree["name"] == "app"


def test_mcp_resolves_project_from_git_remote(home):
    """An agent knows its git remote, not what you named the project here."""
    out = _mcp(
        home,
        [
            _call("jarvis_profile", {"op": "init", "project": "backend"}, 1),
            _call(
                "jarvis_remember",
                {
                    "project": "backend",
                    "repo": "git@github.com:me/backend.git",
                    "category": "facts",
                    "title": "포트",
                    "statement": "8080 포트를 쓴다",
                },
                2,
            ),
            # No project at all: the remote alone must resolve it.
            _call(
                "jarvis_context",
                {"repo": "https://github.com/me/backend", "question": "포트 뭐 쓰지?"},
                3,
            ),
        ],
    )
    assert _payload(out[1])["uri"].startswith("jarvis://projects/backend/")
    ctx = _payload(out[2])
    assert ctx["project"] == "backend"
    assert "8080" in ctx["context"]


def test_mcp_score_feeds_back_into_memory(home):
    out = _mcp(
        home,
        [
            _call("jarvis_profile", {"op": "init", "project": "app", "template": "coding"}, 1),
            _call(
                "jarvis_remember",
                {
                    "project": "app",
                    "category": "commands",
                    "title": "배포",
                    "statement": "make deploy 로 배포한다",
                    "confidence": 0.6,
                },
                2,
            ),
            _call("jarvis_context", {"project": "app", "question": "배포 어떻게 해?"}, 3),
        ],
    )
    trace_id = _payload(out[2])["trace_id"]
    out2 = _mcp(
        home,
        [
            _call(
                "jarvis_score",
                {"trace_id": trace_id, "value": 0.0, "comment": "그런 명령 없음"},
                1,
            ),
            _call("jarvis_browse", {"op": "read", "uri": "jarvis://projects/app/memories/commands/배포"}, 2),
        ],
    )
    scored = _payload(out2[0])
    assert scored["memories_adjusted"]
    # A zero score must actually cost the memory confidence, not just log a number.
    assert out2[1]["result"]["isError"] or _payload(out2[1])["confidence"] < 0.6


def test_mcp_unresolvable_project_says_what_exists(home):
    out = _mcp(home, [_call("jarvis_context", {"question": "무엇이든"}, 1)])
    assert out[0]["result"]["isError"] is True
    assert "프로젝트를 특정할 수 없습니다" in out[0]["result"]["content"][0]["text"]


def test_every_post_route_parses_its_body(client):
    """Guard against route signatures whose annotations FastAPI cannot resolve.

    A name imported inside create_app() is invisible to FastAPI under postponed
    annotations, and the symptom is a 422 claiming a body field is a missing
    query parameter. This walks the real routes so the failure cannot return
    quietly on some endpoint nobody tested.
    """
    app = client.app
    client.post("/projects", json={"project": "app", "template": "coding"})
    samples: dict[str, dict] = {
        "/projects": {"project": "app"},
        "/prompts": {"project": "app", "name": "p", "template": "x"},
        "/prompts/render": {"project": "app", "name": "p"},
        "/memories": {"project": "app", "category": "commands", "title": "t", "statement": "s"},
        "/resources": {"project": "app", "name": "r", "text": "본문"},
        "/prepare": {"project": "app", "question": "질문"},
        "/commit": {"project": "app", "question": "질문", "answer": "답변"},
        "/scores": {"trace_id": "nope", "value": 1.0},
        "/feedback": {"project": "app", "uri": "jarvis://projects/app/memories/commands/t"},
        "/aliases": {"alias": "github.com/me/app", "project": "app"},
        "/resolve": {"project": "app"},
        "/keys": {"name": "test-key"},
        "/preferences": {"statement": "답변은 한글로 한다"},
        "/memories/confirm": {"uri": "jarvis://projects/app/memories/commands/t"},
        "/reindex": None,
        "/backup/config": {"keep": 5},
        # Empty credentials are rejected before any network call is attempted,
        # so this exercises body parsing without dialing Google from a test.
        "/backup/connect/start": {"client_id": "", "client_secret": ""},
        "/backup/connect/poll": None,
        "/backup/disconnect": None,
        "/backup/run": None,
        "/settings/llm": {"provider": "openai"},
        "/settings/embed": {"provider": "hashing"},
        "/settings/reset": None,
        "/settings/llm/test": {"provider": "openai", "model": "gpt-4o"},
        "/settings/embed/test": {"provider": "openai", "model": "text-embedding-3-small"},
    }
    posts = [
        r.path
        for r in app.routes
        if "POST" in getattr(r, "methods", set()) and "{" not in r.path and r.path != "/mcp"
    ]
    unchecked = [p for p in posts if p not in samples]
    assert not unchecked, f"샘플 본문이 없는 POST 경로: {unchecked}"

    for path in posts:
        body = samples[path]
        res = client.post(path, json=body) if body is not None else client.post(path)
        assert res.status_code != 422, f"{path} 가 본문을 해석하지 못했습니다: {res.text[:200]}"
