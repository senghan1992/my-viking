import json

import pytest

from jarvis.cli import main


@pytest.fixture()
def jv(home):
    def run(*args, expect=0):
        code = main(["--home", str(home), *args])
        assert code == expect, f"exit {code} for {args}"
        return code

    return run


def _json(capsys):
    return json.loads(capsys.readouterr().out)


def test_init_and_project_list(jv, capsys):
    jv("init", "app", "-t", "coding", "-d", "설명")
    out = capsys.readouterr().out
    assert "coding" in out and "commands" in out
    jv("--json", "project", "list")
    rows = _json(capsys)
    assert rows[0]["project"] == "app"
    assert rows[0]["template"] == "coding"


def test_profile_shows_categories(jv, capsys):
    jv("init", "app", "-t", "research")
    capsys.readouterr()
    jv("project", "profile", "-p", "app")
    out = capsys.readouterr().out
    assert "findings" in out and "contradictions" in out


def test_prompt_lifecycle(jv, capsys):
    jv("init", "app")
    jv("prompt", "save", "-p", "app", "review", "리뷰: {{code}}")
    assert "code" in capsys.readouterr().out
    jv("prompt", "render", "-p", "app", "review", "--var", "code=print(1)")
    assert "print(1)" in capsys.readouterr().out
    jv("prompt", "save", "-p", "app", "review", "새 본문 {{code}}")
    capsys.readouterr()
    jv("--json", "prompt", "versions", "-p", "app", "review")
    assert _json(capsys)[0]["version"] == "v1"
    jv("prompt", "rollback", "-p", "app", "review", "1")
    capsys.readouterr()
    jv("prompt", "show", "-p", "app", "review")
    assert "리뷰:" in capsys.readouterr().out


def test_strict_render_fails_cleanly(jv, capsys):
    jv("init", "app")
    jv("prompt", "save", "-p", "app", "p", "{{a}}")
    capsys.readouterr()
    jv("prompt", "render", "-p", "app", "p", "--strict", expect=1)
    assert "누락" in capsys.readouterr().err


def test_link_warns_when_a_remote_server_is_configured(jv, capsys, monkeypatch, tmp_path):
    """MYVIKING_URL 이 걸린 머신에서 jv link 는 로컬 저장소에만 심긴다 — 서버는
    모른다. 조용히 성공하면 사용자는 연결됐다 착각한다. 경고하고 방향을 알린다."""
    monkeypatch.setenv("MYVIKING_URL", "http://server:8787")
    jv("link", "-p", "app", "--repo", "github.com/me/app", "--path", str(tmp_path))
    err = capsys.readouterr().err
    assert "MYVIKING_URL" in err
    assert "jv remote link" in err


def test_link_is_quiet_without_a_remote_server(jv, capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("MYVIKING_URL", raising=False)
    jv("link", "-p", "app", "--repo", "github.com/me/app", "--path", str(tmp_path))
    assert "MYVIKING_URL" not in capsys.readouterr().err


def test_mem_add_list_and_forget(jv, capsys):
    jv("init", "app", "-t", "coding")
    jv("mem", "add", "-p", "app", "commands", "테스트", "pytest -q 로 돌린다")
    uri = capsys.readouterr().out.split("기록: ")[1].strip()
    jv("--json", "mem", "list", "-p", "app")
    assert _json(capsys)[0]["title"] == "테스트"
    jv("mem", "forget", uri)
    assert "보관함" in capsys.readouterr().out
    jv("--json", "mem", "list", "-p", "app")
    assert _json(capsys) == []


def test_mem_add_rejects_unknown_category(jv, capsys):
    jv("init", "app", "-t", "coding")
    capsys.readouterr()
    jv("mem", "add", "-p", "app", "없는것", "t", "s", expect=1)
    assert "카테고리" in capsys.readouterr().err


def test_ask_commit_ask_shows_cache(jv, capsys):
    jv("init", "app", "-t", "coding")
    jv("mem", "add", "-p", "app", "commands", "테스트", "pytest -q 로 돌린다")
    capsys.readouterr()
    jv("ask", "-p", "app", "테스트 어떻게 돌려?")
    out = capsys.readouterr()
    assert "=== SYSTEM ===" in out.out and "pytest" in out.out
    assert "절감" in out.err

    jv("commit", "-p", "app", "테스트 어떻게 돌려?", "pytest -q 실행", "--tokens-in", "900")
    assert "세션 기록" in capsys.readouterr().out

    jv("ask", "-p", "app", "테스트 어떻게 돌려?")
    out = capsys.readouterr().out
    assert "캐시 적중" in out and "pytest -q 실행" in out


def test_ask_context_only(jv, capsys):
    jv("init", "app", "-t", "coding")
    jv("mem", "add", "-p", "app", "commands", "테스트", "pytest -q 로 돌린다")
    capsys.readouterr()
    jv("ask", "-p", "app", "테스트", "--no-cache", "--context-only")
    out = capsys.readouterr().out
    assert "=== SYSTEM ===" not in out
    assert "pytest" in out


def test_browse_commands(jv, capsys):
    jv("init", "app", "-t", "coding")
    jv("mem", "add", "-p", "app", "commands", "빌드", "make build 로 빌드한다")
    capsys.readouterr()
    jv("ls", "jarvis://projects/app/memories")
    assert "commands" in capsys.readouterr().out
    jv("tree", "jarvis://projects/app", "-L", "2")
    assert "memories/" in capsys.readouterr().out
    jv("find", "-p", "app", "빌드", "--trace")
    out = capsys.readouterr().out
    assert "memories/commands" in out and "검색 경로" in out
    jv("grep", "make build")
    assert "make build" in capsys.readouterr().out
    jv("read", "jarvis://projects/app/memories/commands/빌드", "-t", "0")
    assert "make build" in capsys.readouterr().out


def test_report_and_stats(jv, capsys):
    jv("init", "app", "-t", "coding")
    jv("commit", "-p", "app", "질문", "답변", "--tokens-in", "500", "--tokens-out", "20")
    jv("ask", "-p", "app", "질문")
    capsys.readouterr()
    jv("--json", "report", "-p", "app")
    rep = _json(capsys)
    assert rep["saved_cache"] == 520
    jv("stats", "-p", "app")
    assert "coding" in capsys.readouterr().out


def test_config_set_roundtrip(jv, capsys):
    jv("--json", "config", "--set", "budget.total=1234", "--set", "llm.provider=openai")
    cfg = _json(capsys)
    assert cfg["budget"]["total"] == 1234
    assert cfg["llm"]["provider"] == "openai"


def test_config_rejects_unknown_key(jv):
    with pytest.raises(SystemExit):
        main(["config", "--set", "nope.nope=1"])


def test_reindex_and_cache_clear(jv, capsys):
    jv("init", "app", "-t", "coding")
    jv("mem", "add", "-p", "app", "commands", "빌드", "make build")
    jv("commit", "-p", "app", "질문", "답변")
    capsys.readouterr()
    jv("reindex", "-p", "app")
    assert "재색인" in capsys.readouterr().out
    jv("cache", "-p", "app", "--clear")
    assert "삭제" in capsys.readouterr().out


def test_project_delete_requires_confirmation(jv, capsys):
    jv("init", "temp")
    capsys.readouterr()
    jv("project", "delete", "-p", "temp", expect=1)
    assert "--yes" in capsys.readouterr().out
    jv("project", "delete", "-p", "temp", "--yes")
    assert "삭제" in capsys.readouterr().out


def test_distill_reports_what_it_learned(jv, capsys):
    jv("init", "app", "-t", "coding")
    jv("commit", "-p", "app", "앞으로 항상 한글로 답해줘", "네, 한글로 답합니다", "--no-distill")
    capsys.readouterr()
    jv("distill", "-p", "app")
    out = capsys.readouterr().out
    assert "세션 1건" in out
    assert "memories/conventions" in out


# ----- server operations ---------------------------------------------------
def test_key_lifecycle(jv, capsys):
    jv("key", "create", "laptop")
    out = capsys.readouterr().out
    assert "jv_" in out
    assert "다시 볼 수 없습니다" in out
    assert "서버 전체가 인증을 요구" in out  # first key flips the posture

    jv("--json", "key", "list")
    rows = _json(capsys)
    assert rows[0]["name"] == "laptop"
    assert "key" not in rows[0]

    jv("key", "revoke", rows[0]["id"])
    assert "폐기" in capsys.readouterr().out
    jv("key", "revoke", rows[0]["id"], expect=1)


def test_key_can_be_scoped_to_projects(jv, capsys):
    jv("init", "app")
    jv("key", "create", "ci", "--project", "app")
    capsys.readouterr()
    jv("--json", "key", "list")
    assert _json(capsys)[0]["projects"] == "app"


def test_link_binds_repo_and_path(jv, capsys, tmp_path):
    jv(
        "--json",
        "link",
        "-p",
        "backend",
        "--repo",
        "git@github.com:me/backend.git",
        "--path",
        str(tmp_path),
    )
    data = _json(capsys)
    assert data["project"] == "backend"
    assert any("repo" in b for b in data["bound"])
    assert any("path" in b for b in data["bound"])


def test_link_infers_project_from_repo(jv, capsys, tmp_path):
    jv("--json", "link", "--repo", "https://github.com/me/payments.git", "--path", str(tmp_path))
    assert _json(capsys)["project"] == "payments"


def test_agent_config_includes_endpoint_and_instructions(jv, capsys):
    jv("agent", "config", "--client", "claude-code", "--url", "https://vk.example.com", "-p", "app")
    out = capsys.readouterr().out
    assert "claude mcp add --transport http myviking https://vk.example.com/mcp" in out
    assert "jarvis_context" in out and "jarvis_score" in out
    assert 'project="app"' in out


def test_agent_config_embeds_key_per_client(jv, capsys):
    jv("agent", "config", "--client", "claude-code", "--url", "http://h:1", "--key", "jv_X")
    assert 'Authorization: Bearer jv_X' in capsys.readouterr().out
    jv("agent", "config", "--client", "cursor", "--url", "http://h:1", "--key", "jv_X")
    cursor = capsys.readouterr().out
    assert '"url": "http://h:1/mcp"' in cursor
    assert "Bearer jv_X" in cursor
    jv("agent", "config", "--client", "codex", "--url", "http://h:1", "--key", "jv_X")
    assert "[mcp_servers.myviking]" in capsys.readouterr().out


# ----- observability -------------------------------------------------------
def _one_traced_task(jarvis, project="app"):
    jarvis.init_project(project, template="coding")
    jarvis.remember(project, "commands", "테스트", "pytest -q 로 돌린다")
    prepared = jarvis.prepare(project, "테스트 실행", agent="cli-test")
    jarvis.commit(
        project,
        "테스트 실행",
        "pytest -q",
        trace_id=prepared.trace_id,
        latency_ms=1800,
        tokens_in=900,
        tokens_out=20,
    )
    return prepared.trace_id


def test_traces_trace_and_score(jv, jarvis, capsys):
    trace_id = _one_traced_task(jarvis)
    capsys.readouterr()

    jv("--json", "traces", "-p", "app")
    rows = _json(capsys)
    assert rows[0]["id"] == trace_id
    # total = 조립 + 생성. 이 합이 맞아야 지연 귀속이 의미를 갖는다.
    assert rows[0]["total_ms"] == rows[0]["latency_ms"] + 1800

    jv("trace", trace_id)
    detail = capsys.readouterr().out
    assert "retrieval" in detail and "generation" in detail
    assert "memories/commands/테스트" in detail

    jv("score", trace_id, "1.0", "--comment", "정확")
    scored = capsys.readouterr().out
    assert "신뢰도 조정" in scored
    assert "memories/commands/테스트" in scored


def test_score_on_unknown_trace_fails_cleanly(jv, capsys):
    jv("score", "tr_nope", "1.0", expect=1)
    assert "없는 트레이스" in capsys.readouterr().err


def test_metrics_splits_context_from_answer(jv, jarvis, capsys):
    _one_traced_task(jarvis)
    capsys.readouterr()
    jv("--json", "metrics", "-p", "app")
    m = _json(capsys)
    assert m["answer_ms"]["p50"] >= 1800
    assert m["context_ms"]["p50"] <= m["answer_ms"]["p50"]
    assert {s["type"] for s in m["steps"]} >= {"retrieval", "generation"}


def test_metrics_says_so_when_no_scores_exist(jv, jarvis, capsys):
    _one_traced_task(jarvis)
    capsys.readouterr()
    jv("metrics", "-p", "app")
    assert "점수: 아직 없음" in capsys.readouterr().out


def test_impact_marks_unscored_memories_as_unproven(jv, jarvis, capsys):
    _one_traced_task(jarvis)
    capsys.readouterr()
    jv("impact", "-p", "app")
    out = capsys.readouterr().out
    assert "미검증" in out
    assert "memories/commands/테스트" in out


def test_traces_can_filter_slow_ones(jv, jarvis, capsys):
    _one_traced_task(jarvis)
    capsys.readouterr()
    jv("--json", "traces", "-p", "app", "--slower-than", "999999")
    assert _json(capsys) == []


def test_agent_list_shows_connected_agents(jv, jarvis, capsys):
    _one_traced_task(jarvis)
    capsys.readouterr()
    jv("--json", "agent", "list")
    rows = _json(capsys)
    assert rows[0]["name"] == "cli-test"
    assert rows[0]["projects"] == ["app"]


def test_hook_command_fails_open(jv, monkeypatch, capsys, tmp_path):
    """훅은 서버가 죽어 있어도, stdin 이 깨져 있어도 코딩 세션을 막으면 안 된다."""
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("깨진 json"))
    jv(
        "hook",
        "stop",
        "--url",
        "http://127.0.0.1:1",  # nothing listens here
        "--state-dir",
        str(tmp_path / "state"),
        expect=0,
    )
    assert capsys.readouterr().out == ""


def test_hook_command_does_not_create_a_local_store(home, monkeypatch, tmp_path):
    """훅 수신기는 에이전트 머신에서 매 프롬프트마다 돈다. 로컬 DB 를 만들면 안 된다."""
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    code = main(
        ["--home", str(home), "hook", "session-end",
         "--url", "http://127.0.0.1:1", "--state-dir", str(tmp_path / "state")]
    )
    assert code == 0
    assert not (home / "index.db").exists()


def test_agent_hooks_install_is_idempotent_and_preserves_others(jv, tmp_path, capsys):
    import json as _json

    repo = tmp_path / "repo"
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(_json.dumps({
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]},
        "model": "opus",
    }), encoding="utf-8")

    jv("agent", "hooks", "--install", "--path", str(repo), "--url", "http://v:8787", "--key", "jv_k")
    jv("agent", "hooks", "--install", "--path", str(repo), "--url", "http://v:8787", "--key", "jv_k")
    capsys.readouterr()

    data = _json.loads(settings.read_text(encoding="utf-8"))
    assert data["model"] == "opus"  # untouched
    stop_cmds = [h["command"] for m in data["hooks"]["Stop"] for h in m["hooks"]]
    assert stop_cmds.count("echo mine") == 1
    assert sum("jv hook stop" in c for c in stop_cmds) == 1  # not duplicated
    assert set(data["hooks"]) == {"Stop", "SessionStart", "UserPromptSubmit", "SessionEnd"}


def test_agent_config_shows_hooks_for_claude_code(jv, capsys):
    jv("agent", "config", "--client", "claude-code", "--url", "http://v:8787")
    out = capsys.readouterr().out
    assert "자동 캡처 훅" in out
    assert "jv hook session-start" in out
    assert "`jarvis_commit` 은 호출하지 않는다" in out


def test_backup_cli_config_run_list_restore(jv, jarvis, home, tmp_path, capsys):
    jarvis.init_project("app", template="coding")
    jarvis.remember("app", "commands", "테스트", "pytest -q")
    remote = tmp_path / "bk"

    jv("backup", "config", "--provider", "local", "--path", str(remote), "--every", "6", "--keep", "3")
    out = capsys.readouterr().out
    assert str(remote) in out and "6.0시간" in out

    jv("backup", "run")
    assert "올렸습니다" in capsys.readouterr().out
    jv("--json", "backup", "list")
    files = _json(capsys)
    assert len(files) == 1 and files[0]["name"].startswith("myviking-")

    # 복원은 명시적 동의 없이는 거부된다 — 라이브 데이터를 덮어쓴다.
    jv("backup", "restore", expect=1)
    jv("backup", "restore", "--yes")
    assert "복원했습니다" in capsys.readouterr().out


def test_backup_status_before_setup_points_the_way(jv, capsys):
    jv("backup", "status")
    out = capsys.readouterr().out
    assert "설정 안 됨" in out
    assert "jv backup connect" in out
