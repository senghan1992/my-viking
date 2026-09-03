"""자동 캡처 훅: 에이전트가 도구를 잊어도 기록이 남고, 새 세션이 브리핑을 받는다."""

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jarvis.connect import hook_settings  # noqa: E402
from jarvis.hooks import _last_exchange, _orientation, run  # noqa: E402
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
    # A git checkout (remote-less): the folder name may serve as the project.
    (d / ".git").mkdir()
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
    # With accumulated work the closing line says to start from it; with none
    # yet, it must not (that read as a contradiction to live users).
    assert "다시 조사하지" in ctx or "아직 기록이 없습니다" in ctx


def test_session_start_announces_a_freshly_invented_project(transport, repo_dir, state_dir):
    """매칭되는 프로젝트가 없어 새로 만든 경우, 조용히 넘어가지 말고 알린다 —
    오타 난 디렉터리나 미연결 체크아웃이 유령 프로젝트를 낳는 걸 막는다."""
    out = run(
        "session-start",
        {"session_id": "s-new", "cwd": str(repo_dir)},
        transport,
        state_dir=state_dir,
    )
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "새 프로젝트" in ctx
    assert "jv remote link" in ctx


def test_http_transport_surfaces_the_server_reason(monkeypatch):
    """서버가 거절 이유를 JSON body 로 설명하는데, urlopen 은 그걸 버리고
    'HTTP Error 403' 만 남긴다. 브리지·훅 로그에 진짜 이유가 보여야 한다."""
    import io
    import urllib.error

    from jarvis.hooks import HttpTransport

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", {},
            io.BytesIO(json.dumps({"detail": "이 키는 'b' 에 접근할 수 없습니다"}).encode()),
        )

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(RuntimeError, match="이 키는 'b'"):
        HttpTransport("http://x").request("GET", "/projects/b/brief")


def test_orientation_surfaces_established_knowledge():
    """확립된 지식(brief.know)이 세션 시작 브리핑에 실제로 나와야 한다 —
    돌아온 세션이 알아야 할 핵심이 orientation 에서 빠지면 안 된다."""
    brief = {
        "know": [
            {"uri": "backend/memories/commands/deploy", "category": "commands",
             "title": "배포", "abstract": "scripts/deploy.sh 로 배포한다", "confidence": 0.9},
        ],
        "recently_learned": [],
    }
    out = _orientation("backend", brief)
    assert "확립된 지식" in out
    assert "scripts/deploy.sh" in out


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


def _write_edit_transcript(path, question, files):
    """질문 + 파일을 실제로 편집한 어시스턴트 턴."""
    tool_uses = []
    for f in files:
        name = "NotebookEdit" if f.endswith(".ipynb") else "Edit"
        key = "notebook_path" if f.endswith(".ipynb") else "file_path"
        tool_uses.append({"type": "tool_use", "name": name, "input": {key: f}})
    lines = [
        {"type": "user", "message": {"role": "user", "content": question}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "고쳤습니다."}, *tool_uses],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")


def test_touched_files_reads_the_edits_the_agent_made(tmp_path):
    from jarvis.hooks import _touched_files

    p = tmp_path / "t.jsonl"
    # 같은 파일을 두 번 고쳐도 한 번, Bash 같은 비편집 도구는 빠진다.
    _write_edit_transcript(p, "리팩터링 해줘", ["src/auth.py", "src/auth.py", "notes.ipynb"])
    assert _touched_files(p) == ["src/auth.py", "notes.ipynb"]


def test_stop_records_which_files_the_work_touched(
    transport, client, repo_dir, state_dir, tmp_path
):
    """무엇을 물었는지만큼 무엇을 건드렸는지도 남아, 새 세션이 파일을 안다."""
    client.post("/projects", json={"project": "backend", "template": "coding"})
    client.post("/aliases", json={"alias": str(repo_dir), "project": "backend", "kind": "path"})

    transcript = tmp_path / "t.jsonl"
    _write_edit_transcript(transcript, "인증 버그 고쳐줘", ["src/auth.py", "src/models.py"])
    run(
        "user-prompt-submit",
        {"session_id": "s1", "cwd": str(repo_dir), "prompt": "인증 버그 고쳐줘"},
        transport,
        state_dir=state_dir,
    )
    run(
        "stop",
        {"session_id": "s1", "cwd": str(repo_dir), "transcript_path": str(transcript)},
        transport,
        state_dir=state_dir,
    )

    # 변경한 파일이 다음 세션의 오리엔테이션에 뜬다.
    brief = client.get("/projects/backend/brief").json()
    text = _orientation("backend", brief)
    assert "auth.py" in text and "models.py" in text


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


def test_installed_events_reports_missing_when_settings_absent(tmp_path):
    from jarvis.hooks import installed_events

    res = installed_events(tmp_path)
    assert res["exists"] is False
    assert res["installed"] == []
    assert set(res["missing"]) == {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}


def test_installed_events_sees_the_wired_hooks(tmp_path):
    from jarvis.connect import hook_settings
    from jarvis.hooks import installed_events

    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(hook_settings("http://localhost:8787")), encoding="utf-8"
    )

    res = installed_events(tmp_path)
    assert res["exists"] is True
    assert set(res["installed"]) == {
        "SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"
    }
    assert res["missing"] == []


def test_installed_events_flags_a_partial_install(tmp_path):
    from jarvis.hooks import installed_events

    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "jv hook session-start"}]}
                    ],
                    # 다른 사람 훅은 있으나 우리 것은 아니다 — 설치로 세지 않는다.
                    "Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}],
                }
            }
        ),
        encoding="utf-8",
    )

    res = installed_events(tmp_path)
    assert res["installed"] == ["SessionStart"]
    assert "Stop" in res["missing"] and "UserPromptSubmit" in res["missing"]


def test_installed_events_survives_broken_json(tmp_path):
    from jarvis.hooks import installed_events

    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{not json", encoding="utf-8")

    res = installed_events(tmp_path)
    assert "error" in res
    assert res["installed"] == []


def test_container_bakes_bare_jv_not_a_container_path(monkeypatch):
    """서버 컨테이너 안에서 config 를 만들면, 그 절대경로는 에이전트 머신에
    없다. 컨테이너 표식이 있으면 맨 jv 를 굽고, 에이전트 머신에서 --install 이
    로컬 경로로 다시 쓴다."""
    import shutil

    from jarvis import connect

    monkeypatch.setenv("MYVIKING_IN_CONTAINER", "1")
    settings = connect.hook_settings("http://server:8787")
    cmd = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert " jv hook session-start" in cmd

    # 컨테이너가 아니면 절대경로를 굽는다 (에이전트 머신의 비로그인 셸 PATH 문제).
    monkeypatch.delenv("MYVIKING_IN_CONTAINER", raising=False)
    monkeypatch.setattr(connect.Path, "exists", lambda self: False)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/jv")
    settings = connect.hook_settings("http://server:8787")
    cmd = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "/usr/local/bin/jv hook session-start" in cmd


def test_orientation_does_not_repeat_a_fresh_pitfall_under_warnings():
    """A pitfall learned this week showed up twice: under "최근에 정해진 것" and
    again under "⚠ 주의"."""
    pit = {"uri": "backend/memories/pitfalls/pg", "category": "pitfalls",
           "title": "PG 재시도 금지", "abstract": "재시도하면 이중 결제", "confidence": 0.8}
    out = _orientation("backend", {"recently_learned": [pit], "warnings": [pit]})
    assert out.count("이중 결제") == 1
    assert "⚠ 주의" not in out


def test_last_exchange_ignores_harness_text_seen_in_real_transcripts(tmp_path):
    """Checked against real Claude Code transcripts: the compaction summary sits
    in the user's seat as 17k chars of "This session is being continued…", task
    notifications arrive with promptSource=system, an interrupted turn leaves
    "[Request interrupted by user]", and API errors are assistant entries with
    model "<synthetic>" / isApiErrorMessage. None of it is the exchange."""
    p = tmp_path / "t.jsonl"
    rows = [
        {"type": "user", "message": {"role": "user", "content": "테스트 어떻게 돌려?"}},
        {"type": "assistant", "message": {"role": "assistant", "model": "claude-x",
                                          "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/r/a.py"}},
                                                      {"type": "text", "text": "pytest -q 로 돌립니다."}]}},
        {"type": "assistant", "isApiErrorMessage": True, "error": "server_error",
         "message": {"role": "assistant", "model": "<synthetic>",
                     "content": [{"type": "text", "text": "API Error: Server error mid-response."}]}},
        {"type": "user", "promptSource": "system",
         "message": {"role": "user", "content": "<task-notification>done</task-notification>"}},
        {"type": "user", "message": {"role": "user", "content": "This session is being continued from a previous conversation that ran out of context. Summary: ..."}},
        {"type": "user", "message": {"role": "user", "content": "[Request interrupted by user]"}},
        {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "서브에이전트 지시"}},
        {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "model": "claude-x",
                                                                 "content": [{"type": "text", "text": "서브 답"}]}},
    ]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    q, a, model = _last_exchange(p)
    assert q == "테스트 어떻게 돌려?"
    assert a == "pytest -q 로 돌립니다." and model == "claude-x"
    from jarvis.hooks import _touched_files
    assert _touched_files(p) == ["/r/a.py"]


def _project_names(client):
    data = client.get("/projects").json()
    items = data if isinstance(data, list) else data.get("projects", [])
    return {p.get("project") or p.get("name") if isinstance(p, dict) else p for p in items}


def test_plain_directory_is_not_turned_into_a_project(transport, client, tmp_path, state_dir):
    """Live audit: running the hooks in /tmp silently created a project named
    "tmp". A folder that is not a git checkout, has no alias and no --project
    gives nothing to file under — say so once and record nothing."""
    plain = tmp_path / "tmp"
    plain.mkdir()
    payload = {"session_id": "s-plain", "cwd": str(plain), "prompt": "테스트 어떻게 돌려?"}
    out = run("user-prompt-submit", payload, transport, state_dir=state_dir)
    assert out and "systemMessage" in out and "기록되지 않습니다" in out["systemMessage"]
    # Once per session only.
    assert run("user-prompt-submit", payload, transport, state_dir=state_dir) is None
    assert "tmp" not in _project_names(client)


def test_explicit_project_pins_a_plain_directory(transport, client, tmp_path, state_dir, monkeypatch):
    plain = tmp_path / "notes"
    plain.mkdir()
    monkeypatch.setenv("MYVIKING_PROJECT", "pinned")
    payload = {"session_id": "s-pinned", "cwd": str(plain)}
    out = run("session-start", payload, transport, state_dir=state_dir)
    assert out and "pinned" in out["hookSpecificOutput"]["additionalContext"]
    assert "pinned" in _project_names(client)
    from jarvis.connect import hook_settings
    cmd = hook_settings("http://x", "k", project="pinned")["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "MYVIKING_PROJECT=pinned" in cmd
