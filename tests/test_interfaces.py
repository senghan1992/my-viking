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
        "jarvis_context",
        "jarvis_remember",
        "jarvis_commit",
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
    assert ctx["cache_hit"] is False
    assert "pytest" in ctx["context"]
    assert _payload(out[3])["session"]
    hit = _payload(out[4])
    assert hit["cache_hit"] is True
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

    r = client.post("/prepare", json={"project": "app", "question": "테스트 실행 방법"})
    body = r.json()
    assert body["cache_hit"] is None
    assert "pytest" in body["system"]

    r = client.post(
        "/commit",
        json={
            "project": "app",
            "question": "테스트 실행 방법",
            "answer": "pytest -q",
            "tokens_in": 800,
            "tokens_out": 10,
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
