"""자동 캡처 훅: 에이전트가 도구를 잊어도 기록이 남고, 새 세션이 브리핑을 받는다."""

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jarvis.connect import hook_settings  # noqa: E402
from jarvis.hooks import _last_exchange, run  # noqa: E402
from jarvis.server import create_app  # noqa: E402


@pytest.fixture()
def client(home):
    return TestClient(create_app(home=str(home)))


class ClientTransport:
    """HttpTransport 와 같은 표면을 TestClient 위에 얹은 것."""

    def __init__(self, client, key=""):
        self.client = client
        self.key = key

    def request(self, method, path, body=None, params=None):
        headers = {"authorization": f"Bearer {self.key}"} if self.key else {}
        res = self.client.request(
            method, path, json=body, params=params, headers=headers
        )
        if res.status_code >= 400:
            raise RuntimeError(f"{res.status_code}: {res.text}")
        return res.json()


class BrokenTransport:
    def request(self, *a, **k):
        raise ConnectionError("server down")


@pytest.fixture()
def transport(client):
    return ClientTransport(client)


@pytest.fixture()
def repo_dir(tmp_path):
    d = tmp_path / "checkout" / "backend"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def state_dir(tmp_path):
    return tmp_path / "hook-state"


def _write_transcript(path, question, answer, model="claude-sonnet-5"):
    lines = [
        # Harness chatter that must not be mistaken for the question:
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "meta"}},
        {"type": "user", "message": {"role": "user", "content": "<command-name>/foo</command-name>"}},
        {"type": "user", "message": {"role": "user", "content": question}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": model,
                "content": [
                    {"type": "text", "text": answer},
                    {"type": "tool_use", "name": "Bash", "input": {}},
                ],
            },
        },
        # Tool results come back as user entries — also not questions.
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "ok"}],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": "확인했습니다."}],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")


# --------------------------------------------------------------------------
# session-start
# --------------------------------------------------------------------------
def test_session_start_creates_project_and_orients(transport, client, repo_dir, state_dir):
    out = run(
        "session-start",
        {"session_id": "s1", "cwd": str(repo_dir)},
        transport,
        state_dir=state_dir,
    )
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "backend" in ctx
    # The checkout resolved to (and created) a project on the server.
    names = [p["project"] for p in client.get("/projects").json()]
    assert "backend" in names


def test_session_start_briefs_previous_work(transport, client, repo_dir, state_dir):
    client.post("/projects", json={"project": "backend", "template": "coding"})
    client.post("/aliases", json={"alias": str(repo_dir), "project": "backend", "kind": "path"})
    client.post(
        "/commit",
        json={"project": "backend", "question": "결제 재시도 정책 정리", "answer": "재시도하지 않습니다."},
    )
    out = run(
        "session-start",
        {"session_id": "s2", "cwd": str(repo_dir)},
        transport,
        state_dir=state_dir,
    )
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "다시 조사하지" in ctx


# --------------------------------------------------------------------------
# user-prompt-submit
# --------------------------------------------------------------------------
def test_prompt_hook_records_trace_and_injects_context(transport, client, repo_dir, state_dir):
    client.post("/projects", json={"project": "backend", "template": "coding"})
    client.post("/aliases", json={"alias": str(repo_dir), "project": "backend", "kind": "path"})
    client.post(
        "/memories",
        json={
            "project": "backend",
            "category": "commands",
            "title": "테스트 실행",
            "statement": "테스트는 pytest -q 로 돌린다.",
        },
    )

    out = run(
        "user-prompt-submit",
        {"session_id": "s1", "cwd": str(repo_dir), "prompt": "테스트 어떻게 돌려?"},
        transport,
        state_dir=state_dir,
    )
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "[MyViking" in ctx and "trace_id=tr_" in ctx

    traces = client.get("/traces", params={"project": "backend"}).json()
    assert len(traces) == 1
    assert traces[0]["session_id"] == "s1"
    assert traces[0]["input"] == "테스트 어떻게 돌려?"


def test_prompt_hook_ignores_slash_commands(transport, client, repo_dir, state_dir):
    assert (
        run(
            "user-prompt-submit",
            {"session_id": "s1", "cwd": str(repo_dir), "prompt": "/compact"},
            transport,
            state_dir=state_dir,
        )
        is None
    )
    assert client.get("/traces").json() == []


# --------------------------------------------------------------------------
# stop — the automatic commit
# --------------------------------------------------------------------------
def test_stop_commits_the_exchange_onto_the_prompt_trace(
    transport, client, repo_dir, state_dir, tmp_path
):
    client.post("/projects", json={"project": "backend", "template": "coding"})
    client.post("/aliases", json={"alias": str(repo_dir), "project": "backend", "kind": "path"})

    question, answer = "빌드는 어떻게 해?", "make build 를 사용합니다."
    run(
        "user-prompt-submit",
        {"session_id": "s1", "cwd": str(repo_dir), "prompt": question},
        transport,
        state_dir=state_dir,
    )
    transcript = tmp_path / "t.jsonl"
    _write_transcript(transcript, question, answer)
    run(
        "stop",
        {"session_id": "s1", "cwd": str(repo_dir), "transcript_path": str(transcript)},
        transport,
        state_dir=state_dir,
    )

    # 같은 트레이스에 질문(검색)과 답변(생성)이 함께 남는다.
    traces = client.get("/traces", params={"project": "backend"}).json()
    assert len(traces) == 1
    assert answer in traces[0]["output"]

    sessions = client.get("/projects/backend/sessions").json()
    assert len(sessions) == 1

    # Stop 이 같은 턴에 다시 와도 중복 기록되지 않는다.
    run(
        "stop",
        {"session_id": "s1", "cwd": str(repo_dir), "transcript_path": str(transcript)},
        transport,
        state_dir=state_dir,
    )
    assert len(client.get("/projects/backend/sessions").json()) == 1


def test_stop_without_prompt_hook_still_commits(transport, client, repo_dir, state_dir, tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_transcript(transcript, "이 함수 고쳐줘", "고쳤습니다.")
    run(
        "stop",
        {"session_id": "sX", "cwd": str(repo_dir), "transcript_path": str(transcript)},
        transport,
        state_dir=state_dir,
    )
    assert len(client.get("/projects/backend/sessions").json()) == 1


# --------------------------------------------------------------------------
# transcript parsing
# --------------------------------------------------------------------------
def test_last_exchange_skips_meta_and_tool_results(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, "질문입니다", "답변입니다")
    q, a, model = _last_exchange(p)
    assert q == "질문입니다"
    assert "답변입니다" in a and "확인했습니다" in a
    assert model == "claude-sonnet-5"


def test_last_exchange_tolerates_garbage(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("not json\n{\"type\": \"user\"}\n", encoding="utf-8")
    assert _last_exchange(p) == ("", "", "")
    assert _last_exchange(tmp_path / "missing.jsonl") == ("", "", "")


# --------------------------------------------------------------------------
# 훅은 절대 코딩 세션을 깨지 않는다
# --------------------------------------------------------------------------
def test_hooks_fail_open_when_server_is_down(repo_dir, state_dir, tmp_path):
    broken = BrokenTransport()
    for event, payload in [
        ("session-start", {"session_id": "s", "cwd": str(repo_dir)}),
        ("user-prompt-submit", {"session_id": "s", "cwd": str(repo_dir), "prompt": "질문"}),
        ("stop", {"session_id": "s", "cwd": str(repo_dir), "transcript_path": str(tmp_path / "no.jsonl")}),
        ("session-end", {"session_id": "s"}),
        ("unknown-event", {}),
    ]:
        assert run(event, payload, broken, state_dir=state_dir) is None


def test_session_end_clears_state(transport, client, repo_dir, state_dir):
    run(
        "user-prompt-submit",
        {"session_id": "s1", "cwd": str(repo_dir), "prompt": "질문"},
        transport,
        state_dir=state_dir,
    )
    assert any(state_dir.glob("*.json"))
    run("session-end", {"session_id": "s1"}, transport, state_dir=state_dir)
    assert not any(state_dir.glob("*.json"))


# --------------------------------------------------------------------------
# 설정 생성
# --------------------------------------------------------------------------
def test_hook_settings_covers_the_whole_loop():
    settings = hook_settings("http://viking:8787/", key="jv_abc")["hooks"]
    assert set(settings) == {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}
    cmd = settings["Stop"][0]["hooks"][0]["command"]
    assert "MYVIKING_URL=http://viking:8787" in cmd
    assert "MYVIKING_KEY=jv_abc" in cmd
    assert cmd.endswith("jv hook stop")


def test_connection_exposes_hooks_for_claude_code():
    from jarvis.connect import build

    conn = build("claude-code", "http://localhost:8787", "app")
    assert "SessionStart" in conn.hooks_setup
    assert "jarvis_remember" in conn.instructions_hooks
    # Other clients have no hook system; the fields stay empty.
    assert build("cursor", "http://localhost:8787", "app").hooks_setup == ""


# --------------------------------------------------------------------------
# fail-open 의 가시성: 실패는 breadcrumb 으로, 점검은 --check 로
# --------------------------------------------------------------------------
def test_hook_failures_leave_breadcrumbs(repo_dir, state_dir, transport):
    from jarvis.hooks import health_check

    broken = BrokenTransport()
    run("session-start", {"session_id": "s", "cwd": str(repo_dir)}, broken, state_dir=state_dir)
    run("user-prompt-submit", {"session_id": "s", "cwd": str(repo_dir), "prompt": "질문"},
        broken, state_dir=state_dir)

    res = health_check(broken, state_dir=state_dir)
    assert res["server_ok"] is False and "server_error" in res
    assert len(res["recent_failures"]) == 2
    assert "session-start" in res["recent_failures"][0]

    # 서버가 살아나서 훅이 성공하면 last_ok 가 남고 점검도 통과한다.
    run("user-prompt-submit", {"session_id": "s", "cwd": str(repo_dir), "prompt": "질문"},
        transport, state_dir=state_dir)
    res2 = health_check(transport, state_dir=state_dir)
    assert res2["server_ok"] is True
    assert "user-prompt-submit" in res2["last_ok"]
