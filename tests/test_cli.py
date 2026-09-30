"""jv CLI — 훅 설정 JSON, 마스킹, 트랜스크립트 추출 테스트."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jv.cli import _last_exchange, mask, _content_text


def test_mask():
    out = mask("키는 sk-abc123DEF456ghi789JKL0123456789")
    assert "sk-abc" not in out
    assert out != ""


def test_hook_settings_json_shape(tmp_path, monkeypatch):
    """hook install 이 만드는 .claude/settings.local.json 형태 확인."""
    import jv.cli as cli

    monkeypatch.setattr(cli, "_self_command", lambda: "jv")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: {"version": "1.0.0"})

    class Args:
        url = "https://viking.example.com"
        key = "jv_0123456789abcdef01234567"
        project = "my-app"
        timeout = "15"
        cwd = str(tmp_path)

    cli.hook_install(Args())
    path = tmp_path / ".claude" / "settings.local.json"
    data = json.loads(path.read_text())
    assert {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"} <= set(data["hooks"])
    cmd = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    # 주소를 박지 않는다 — 폴더 연결 파일이 기준(git 의 HEAD 처럼). 그래서 switch/disconnect 가 즉시 통한다.
    assert "MYVIKING_URL" not in cmd
    assert "jv hook user-prompt-submit" in cmd
    # hook install 은 폴더 연결(=연결 저장소 + 링크)도 만든다
    assert (tmp_path / ".myviking-connection.json").exists()
    conns = json.loads((tmp_path / ".myviking" / "connections.json").read_text())["connections"]
    assert conns[0]["project"] == "my-app"


# ══════════════ git 같은 표면: status / connect / disconnect / switch ══════════════ #
def _stub_api(monkeypatch, name="데이터자판기"):
    import jv.cli as cli
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: {"project_name": name, "orientation": "x"})


def test_connect_by_saved_name_and_disconnect(tmp_path, monkeypatch, capsys):
    """`jv connect <이름>` 은 키를 다시 묻지 않고, `jv disconnect` 는 폴더 흔적을 모두 지운다."""
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "app1"
    folder.mkdir()
    _stub_api(monkeypatch)

    class Args:
        url = "https://viking.example.com"
        key = "jv_0123456789abcdef01234567"
        project = "p921a95"
        name = ""
        agent = ""
        timeout = "15"
        cwd = str(folder)

    cli.connect(Args())            # 1) 새 연결
    assert (folder / ".myviking-connection.json").exists()

    other = tmp_path / "app2"       # 2) 저장된 이름으로 다른 폴더 연결 (키 재입력 없음)
    other.mkdir()
    a2 = Args()
    a2.url, a2.key, a2.project, a2.cwd = "", "", "", str(other)
    a2.name = "데이터자판기"
    cli.connect(a2)
    capsys.readouterr()
    assert (other / ".myviking-connection.json").exists()

    # 3) 폴더 해제 — 링크 + 폴더 훅 제거, 저장된 연결은 남음
    a2.name = ""
    folder2 = tmp_path / "app3"
    folder2.mkdir()
    (folder2 / ".claude").mkdir()
    hook_file = folder2 / ".claude" / "settings.local.json"
    hook_file.write_text(json.dumps({"hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": "jv hook session-start --timeout 15"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "echo other-hook"}]}],
    }}, ensure_ascii=False), encoding="utf-8")
    _link_file = folder2 / ".myviking-connection.json"
    _link_file.write_text(json.dumps({"connection": "https://viking.example.com|p921a95"}), encoding="utf-8")

    a3 = Args()
    a3.cwd = str(folder2)
    a3.all = False
    cli.disconnect(a3)
    out = capsys.readouterr().out
    assert not _link_file.exists()
    left = json.loads(hook_file.read_text())["hooks"]
    assert "SessionStart" not in left       # jv 훅만 제거
    assert "Stop" in left                   # 남의 훅은 보존
    conns = json.loads((tmp_path / ".myviking" / "connections.json").read_text())["connections"]
    assert len(conns) == 1                  # 저장된 연결은 남음 → 언제든 다시 붙는다


def test_disable_is_machine_wide_kill_switch(tmp_path, monkeypatch):
    """jv disable → 폴더 연결이 있어도 어디에서도 쓰지 않는다 (status 로 확인 가능)."""
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "app1"
    folder.mkdir()
    _stub_api(monkeypatch)

    class Args:
        url = "https://viking.example.com"
        key = "jv_0123456789abcdef01234567"
        project = "p1"
        timeout = "15"
        cwd = str(folder)

    cli.pi_install(Args())
    assert cli._folder_conn(folder) is not None

    class Ns:
        pass
    cli._cmd_disable(Ns())
    assert cli._is_enabled() is False
    assert cli._folder_conn(folder) is None      # 전역 off → 폴더 연결이 있어도 무시

    cli._cmd_enable(Ns())
    assert cli._folder_conn(folder) is not None  # 켜면 다시 따라온다


def test_folder_conn_does_not_escape_project_root(tmp_path, monkeypatch):
    """연결은 프로젝트 단위 — 상위 공유 폴더/홈의 설정이 안으로 새지 않는다."""
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    conn = {"id": "https://v|p1", "name": "A", "url": "https://v", "key": "jv_k", "project": "p1"}
    cli._save_conns([conn])

    # 홈에 누군가 실수로 링크 파일을 놓았어도 — git 프로젝트 안에서는 무시된다
    (tmp_path / cli._PI_LINK_FILE).write_text(json.dumps({"connection": conn["id"]}))

    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    sub = proj / "src" / "deep"
    sub.mkdir(parents=True)
    assert cli._folder_conn(sub) is None            # 전역으로 보이는 링크는 없다

    # 프로젝트 루트에 두면 서브폴더까지 따라온다 (git 과 같게)
    (proj / cli._PI_LINK_FILE).write_text(json.dumps({"connection": conn["id"]}))
    assert cli._folder_conn(sub)["project"] == "p1"
    assert cli._folder_conn(proj)["project"] == "p1"

    # 옛 id(뒤에 슬래시)도 같은 연결로 본다
    (proj / cli._PI_LINK_FILE).write_text(json.dumps({"connection": "https://v/|p1"}))
    assert cli._folder_conn(sub)["project"] == "p1"


def test_link_writes_to_git_root_from_subfolder(tmp_path, monkeypatch, capsys):
    """git 저장소 안의 서브폴더에서 connect 하면 링크가 git 루트에 간다 (pi 허브와 대칭)."""
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    capsys.readouterr()
    monkeypatch.setattr(cli, "_resolve_project_by_key", lambda u, k: {"project": "p1", "project_name": "앱"})
    monkeypatch.setattr(cli, "_verify_server", lambda u, k, p: {"project_name": "앱"})
    monkeypatch.setattr(cli, "_self_command", lambda: "jv")

    class Args:
        url = "https://viking.example.com"
        key = "jv_0123456789abcdef01234567"
        project = "p1"
        name = ""
        agent = ""
        timeout = "15"
        cwd = str(sub)

    cli.connect(Args())
    assert not (sub / ".myviking-connection.json").exists()      # 서브폴더에 두지 않는다
    link = repo / ".myviking-connection.json"
    assert link.exists()                                          # git 루트에 쓴다
    assert json.loads(link.read_text())["connection"] == "https://viking.example.com|p1"
    assert ".myviking-connection.json" in (repo / ".git" / "info" / "exclude").read_text()
    assert cli._folder_conn(sub)["project"] == "p1"


def test_ambient_env_does_not_leak_to_shell_commands(tmp_path, monkeypatch, capsys):
    """쉘/프로필의 MYVIKING_* 는 이제 연결을 정하지 못한다 — 모든 폴더가 자유 사용."""
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MYVIKING_URL", "https://viking.example.com")
    monkeypatch.setenv("MYVIKING_KEY", "jv_0123456789abcdef01234567")
    monkeypatch.setenv("MYVIKING_PROJECT", "p1")
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: {"orientation": "잠겨있어서 실행 안 됨"})
    monkeypatch.setattr(cli, "_resolve_project_by_key", lambda u, k: {"project": "p1"})

    folder = tmp_path / "free"
    folder.mkdir()

    class A:
        url = ""; key = ""; project = ""; timeout = "15"
        cwd = str(folder); q = "x"; cmd = "brief"; content = ""; category = "knowledge"

    # brief: env 로는 연결이 안 된다 — 폴더 연결을 먼저 만들어야 한다
    with pytest.raises(SystemExit):
        cli.remote(A())
    err = capsys.readouterr().err
    assert "이 폴더에 myviking 연결이 없습니다" in err
    out = capsys.readouterr().out
    assert "잠겨있어서" not in out          # API 호출은 실제로 안 됐다
    assert cli._load_conns() == []         # env 만으로 저장소도 생기는 것이 아니다


def test_mcp_honors_baked_env_from_agent_config(tmp_path, monkeypatch):
    """jcode/Cursor/Codex 의 mcp.json 이 박아둔 MYVIKING_* 는 그 에이전트의 연결이라 존중한다."""
    import io
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MYVIKING_URL", "https://viking.example.com")
    monkeypatch.setenv("MYVIKING_KEY", "jv_0123456789abcdef01234567")
    monkeypatch.setenv("MYVIKING_PROJECT", "p9")
    calls = []
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: calls.append(a[0:3]) or {"orientation": "BRIEF"})

    folder = tmp_path / "agent"          # 폴더 연결 없는 곳 — 에이전트 설정(env) 만 있다
    folder.mkdir()
    lines = ["{\"jsonrpc\": \"2.0\", \"id\": 0, \"method\": \"initialize\"}",
             "{\"jsonrpc\": \"2.0\", \"id\": 1, \"method\": \"tools/call\", "
             "\"params\": {\"name\": \"viking_brief\", \"arguments\": {}}}"]
    old_stdin, old_out = sys.stdin, sys.stdout
    sys.stdin = io.StringIO("\n".join(lines) + "\n")
    sys.stdout = io.StringIO()
    try:
        class Args:
            url = ""; key = ""; project = ""; timeout = "15"
            cwd = str(folder)
        cli.mcp(Args())
        out = sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = old_stdin, old_out
    assert "BRIEF" in out                        # env 로 연결을 풀어냈다
    assert ("https://viking.example.com", "jv_0123456789abcdef01234567", "GET") in calls


def test_mcp_folder_conn_beats_baked_env(tmp_path, monkeypatch):
    """폴더 연결이 있으면 에이전트 설정(env) 이 아니라 폴더가 우선 — 프로젝트 이동 후의 새임 방지."""
    import io
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MYVIKING_URL", "https://old.example.com")
    monkeypatch.setenv("MYVIKING_KEY", "jv_0123456789abcdef01234567")
    monkeypatch.setenv("MYVIKING_PROJECT", "old")
    conn = {"id": "https://new.example.com|new", "name": "new", "url": "https://new.example.com",
            "key": "jv_ffffffffffffffffffffffff", "project": "new"}
    cli._save_conns([conn])
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / cli._PI_LINK_FILE).write_text(json.dumps({"connection": conn["id"]}))
    calls = []
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: calls.append(a[0:3]) or {"orientation": "OK"})

    lines = ["{\"jsonrpc\": \"2.0\", \"id\": 1, \"method\": \"tools/call\", "
             "\"params\": {\"name\": \"viking_brief\", \"arguments\": {}}}"]
    old_stdin, old_out = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(lines[0] + "\n")
    sys.stdout = io.StringIO()
    try:
        class Args:
            url = ""; key = ""; project = ""; timeout = "15"
            cwd = str(folder)
        cli.mcp(Args())
        out = sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = old_stdin, old_out
    assert "OK" in out
    assert ("https://new.example.com", "jv_ffffffffffffffffffffffff", "GET") in calls   # 폴더 연결 승
    assert not any(c[0] == "https://old.example.com" for c in calls)


def test_jcode_disconnect_clears_mcp_env(tmp_path, monkeypatch, capsys):
    """jcode disconnect — 폴더 링크 제거와 함께 전역 jcode MCP(env) 도 지운다 (해제 완전성)."""
    import jv.cli as cli

    home = _fake_jcode_home(tmp_path, monkeypatch)
    conn = {"id": "http://srv|p1", "name": "앱", "url": "http://srv",
            "key": "jv_0123456789abcdef01234567", "project": "p1"}
    cli._save_conns([conn])
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / cli._PI_LINK_FILE).write_text(json.dumps({"connection": conn["id"]}))
    cli._jcode_mcp_file().write_text(json.dumps(cli._mcp_merge(conn), ensure_ascii=False), encoding="utf-8")
    assert "myviking" in json.loads(cli._jcode_mcp_file().read_text())["servers"]

    class Args:
        cwd = str(proj)
    cli.jcode_disconnect(Args())
    out = capsys.readouterr().out
    assert not (proj / cli._PI_LINK_FILE).exists()
    mcp = json.loads(cli._jcode_mcp_file().read_text())
    assert "myviking" not in mcp.get("servers", {})          # 다른 폴더로 새는 길이 닫힘
    assert "jcode MCP" in out and "제거" in out


def test_disconnect_clears_mcp_env_when_jcode_installed(tmp_path, monkeypatch, capsys):
    """최상위 disconnect — git 루트 링크 제거 + jcode 연동이라면 전역 MCP 도 지운다."""
    import jv.cli as cli

    home = _fake_jcode_home(tmp_path, monkeypatch)
    conn = {"id": "http://srv|p1", "name": "앱", "url": "http://srv",
            "key": "jv_0123456789abcdef01234567", "project": "p1"}
    cli._save_conns([conn])
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "src"
    sub.mkdir()
    (repo / cli._PI_LINK_FILE).write_text(json.dumps({"connection": conn["id"]}))   # git 루트
    cli._jcode_mcp_file().write_text(json.dumps(cli._mcp_merge(conn), ensure_ascii=False), encoding="utf-8")

    class Args:
        cwd = str(sub)
        all = False
    cli.disconnect(Args())
    out = capsys.readouterr().out
    assert not (repo / cli._PI_LINK_FILE).exists()        # 서브폴더에서 해도 git 루트가 정리됨
    assert "myviking" not in json.loads(cli._jcode_mcp_file().read_text()).get("servers", {})
    assert "새지 않습니다" in out


def _fake_jcode_home(tmp_path, monkeypatch):
    """jcode 연동 마커·config.toml·런처를 가짜 홈에 설치해 _jcode_integration_installed()=True 로 만든다."""
    import jv.cli as cli

    home = tmp_path / "home"
    (home / ".jcode").mkdir(parents=True)
    (home / ".myviking").mkdir()
    monkeypatch.setenv("HOME", str(home))
    launcher = home / ".jcode" / "myviking-hook.sh"
    launcher.write_text("#!/bin/bash\n")
    monkeypatch.setattr(cli, "_jcode_hook_cmd", lambda: str(launcher))
    cfg = home / ".jcode" / "config.toml"
    cfg.write_text(f'[hooks]\nturn_end = "{launcher}"\n', encoding="utf-8")
    cli._jcode_marker().write_text(
        json.dumps({"version": cli._JCODE_VERSION, "launcher": str(launcher), "events": ["turn_end"]}))
    return home


def test_hub_template_is_project_scoped():
    """pi 허브(hub v8)는 전역 '현재 연결' 을 모른다 — 폴더 설정이 곧 연결."""
    import jv.cli as cli

    src = cli._PI_EXT_TEMPLATE
    assert cli._PI_HUB_VERSION in src and cli._PI_HUB_VERSION == "myviking-hub-v8"
    # 1) 세션 시작: 이 프로젝트의 설정을 자동 적용한다 (전역 폴백 아님)
    start = src[src.index('pi.on("session_start"'):src.index('pi.on("before_agent_start"')]
    assert "linkInfo(ctx.cwd)" in start and "setActive(ctx, info.conn)" in start
    assert "linkConn(ctx.cwd)" not in start
    # 2) /myviking use 의 '저장된 첫 연결' 전역 폴백 금지 (다른 프로젝트로 새던 길)
    use = src[src.index('if (word === "use")'):src.index('if (word === "remove")')]
    assert "conns[0]" not in use and "linkInfo(ctx.cwd)" in use
    # 3) connect·switch 는 이 프로젝트에 묶는다 (폴더 링크 쓰기)
    assert src.count("setFolderLink(ctx.cwd") >= 2
    # 4) 링크 탐색은 git 루트까지만
    find = src[src.index("function findLink"):src.index("function projectFolderOf")]
    assert '".git"' in find


def test_hook_handler_is_silent_noop_without_connection(tmp_path, monkeypatch, capsys):
    """폴더에 연결이 없으면 훅은 조용히 {} 만吐한다 (에이전트 세션을 막지 않는다)."""
    import io
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "app1"
    folder.mkdir()
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"session_id": "s1"}'))

    class Args:
        event = "session-start"
        url = ""
        key = ""
        project = ""
        timeout = "15"
        cwd = str(folder)

    cli.hook_handler(Args())
    assert json.loads(capsys.readouterr().out) == {}


def test_status_shows_connection_and_agents(tmp_path, monkeypatch, capsys):
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "app1"
    folder.mkdir()
    (tmp_path / ".pi").mkdir()
    _stub_api(monkeypatch)
    (tmp_path / ".pi" / "agent" / "extensions").mkdir(parents=True)
    cli._install_hub_extension("pi")

    class Args:
        url = "https://viking.example.com"
        key = "jv_0123456789abcdef01234567"
        project = "p1"
        timeout = "15"
        cwd = str(folder)

    cli.pi_install(Args())
    capsys.readouterr()

    class S:
        cwd = str(folder)
        offline = False
    cli.status(S())
    out = capsys.readouterr().out
    assert "연결: 데이터자판기" in out
    assert "pi" in out and "확장 설치됨" in out
    assert "저장된 연결 1개" in out


def test_content_text_plain_and_tool():
    assert _content_text("그냥 텍스트") == "그냥 텍스트"
    out = _content_text([{"type": "text", "text": "안녕"},
                         {"type": "tool_use", "name": "Write", "input": {}}])
    assert "안녕" in out and "Write" in out


def test_last_exchange_from_transcript(tmp_path):
    t = tmp_path / "transcript.jsonl"
    t.write_text(
        "\n".join([
            json.dumps({"type": "user", "message": {"role": "user", "content": "첫 질문"}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "첫 답"}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": "마지막 질문"}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "마지막 답"}}),
        ]),
        encoding="utf-8",
    )
    qa = _last_exchange(t)
    assert qa == ("마지막 질문", "마지막 답")


def test_touched_files(tmp_path):
    from jv.cli import _touched_files

    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"message": {"content": [
        {"type": "tool_use", "name": "Write", "input": {"file_path": "src/a.py"}},
        {"type": "tool_use", "name": "Write", "input": {"file_path": "src/a.py"}},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "src/b.py"}},
    ]}}), encoding="utf-8")
    assert _touched_files(t) == ["src/a.py"]


def test_session_start_hook_output_schema(monkeypatch):
    import jv.cli as cli

    monkeypatch.setattr(cli, "_api", lambda *a, **kw: {"orientation": "BRIEF-TEXT"})

    class Args:
        pass

    out = cli._hook_session_start("u", "k", "p", "s1")
    hs = out["hookSpecificOutput"]
    assert hs["hookEventName"] == "SessionStart"
    assert "BRIEF-TEXT" in hs["additionalContext"]

# ══════════════════ pi 확장 (허브 + 프로젝트 연결) ══════════════════ #
# ══════════════════ pi 확장 (허브 + 프로젝트 연결) ══════════════════ #
def test_pi_install_saves_conn_and_links_folder(tmp_path, monkeypatch, capsys):
    """jv pi install: 연결 저장(0600) + 폴더 링크 + 전역 허브 확장 설치. 프로젝트 고정 없음."""
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "app1"
    folder.mkdir()
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: {"project_name": "데이터자판기"})

    class Args:
        url = "https://viking.example.com"
        key = "jv_0123456789abcdef01234567"
        project = "p921a95"
        timeout = "15"
        cwd = str(folder)

    cli.pi_install(Args())

    # 1) 연결 저장소 — 키 포함, 0600
    conns_p = tmp_path / ".myviking" / "connections.json"
    assert conns_p.exists()
    assert (conns_p.stat().st_mode & 0o777) == 0o600
    conns = json.loads(conns_p.read_text())["connections"]
    assert conns[0]["key"] == "jv_0123456789abcdef01234567"
    assert conns[0]["id"] == "https://viking.example.com|p921a95"
    assert conns[0]["name"] == "데이터자판기"

    # 2) 폴더 링크 — 비밀 없음
    link = folder / ".myviking-connection.json"
    assert json.loads(link.read_text()) == {"connection": "https://viking.example.com|p921a95"}

    # 3) 허브 확장 — 전역 1개, 프로젝트/키가 박히지 않음
    path = tmp_path / ".pi" / "agent" / "extensions" / "myviking.ts"
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    src = path.read_text()
    assert "registerTool" in src and "viking_search" in src and "viking_remember" in src
    assert "session_start" in src and "sendMessage" in src
    assert "registerCommand" in src and "myviking" in src
    assert "activeByThread" in src and "myviking use" in src
    assert "before_agent_start" in src and "turn_end" in src and "/commit" in src  # 자동 증류
    # 프로젝트 고정/키 박힘 금지
    assert "viking.example.com" not in src
    assert "jv_0123456789abcdef01234567" not in src
    assert "p921a95" not in src
    # 생성물 무결성
    assert "@CREATED@" not in src and "@URL@" not in src and "@KEY@" not in src and "@PROJECT@" not in src
    assert cli._PI_HUB_VERSION in src                # 버전 마커 — 없으면 오래된 확장으로 간주
    assert 'Authorization: "Bearer " + c.key' in src
    assert "Bearer ${" not in src
    assert src.count("{") == src.count("}")

    out = capsys.readouterr().out
    assert "데이터자판기" in out and "연결 저장" in out and "이 폴더 기본 연결" in out

    # list — 저장된 연결이 보인다
    class LArgs:
        cwd = str(folder)

    cli.pi_list(LArgs())
    assert "데이터자판기" in capsys.readouterr().out

    # switch — 다른 폴더를 저장된 연결로 바꿔 연결 (git checkout 느낌)
    other = tmp_path / "app2"
    other.mkdir()

    class SArgs:
        name = "데이터자판기"
        cwd = str(other)

    cli.pi_switch(SArgs())
    assert json.loads((other / ".myviking-connection.json").read_text())["connection"] \
        == "https://viking.example.com|p921a95"

    # check — 폴더 연결 + 서버 인증까지 확인
    cli.pi_check(SArgs())
    out = capsys.readouterr().out
    assert "허브 확장" in out and "폴더 연결" in out

    # disconnect — 연결 해제 (자유 사용)
    class DArgs:
        cwd = str(other)

    cli.pi_disconnect(DArgs())
    assert not (other / ".myviking-connection.json").exists()

    # remove — 저장된 연결 삭제 (키 포함). 가리키던 폴더 링크도 함께 해제
    cli.pi_switch(SArgs())                      # other 을 다시 연결
    class RArgs:
        name = "데이터자판기"
        cwd = str(other)

    cli.pi_remove(RArgs())
    assert json.loads(conns_p.read_text())["connections"] == []
    assert not (other / ".myviking-connection.json").exists()
    out = capsys.readouterr().out
    assert "연결 삭제" in out and "키도 함께 제거" in out and "링크도 함께 해제" in out

    # rm 별칭으로도 동작 (연결 하나 다시 만들고 삭제)
    class Args2:
        url = "https://viking.example.com"
        key = "jv_0123456789abcdef01234567"
        project = "p921a95"
        timeout = "15"
        cwd = str(folder)

    cli.pi_install(Args2())
    cli.pi_remove(RArgs())
    assert json.loads(conns_p.read_text())["connections"] == []

    # uninstall
    cli.pi_uninstall(SArgs())
    assert not path.exists()


def test_connect_installs_all_detected_agents(tmp_path, monkeypatch, capsys):
    """jv connect: 저장 + 폴더 연결 + 감지된 에이전트(Claude Code·pi·jcode) 전부 설치 + MCP 출력."""
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".pi").mkdir()
    (tmp_path / ".jcode").mkdir()
    folder = tmp_path / "app1"
    folder.mkdir()
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: {"project_name": "데이터자판기"})
    monkeypatch.setattr(cli, "_self_command", lambda: "jv")

    class Args:
        url = "https://viking.example.com"
        key = "jv_0123456789abcdef01234567"
        project = "p921a95"
        timeout = "15"
        cwd = str(folder)
        agent = ""

    cli.connect(Args())
    out = capsys.readouterr().out

    # 1) 연결 저장 + 폴더 링크
    assert (folder / ".myviking-connection.json").exists()
    # 2) Claude Code 훅
    hook_file = folder / ".claude" / "settings.local.json"
    assert hook_file.exists()
    assert "hooks" in json.loads(hook_file.read_text())
    assert "✓ 훅 설치" in out
    # 3) pi 허브 확장
    assert "pi 허브 확장" in out
    # 4) jcode 연동 (훅 등록)
    assert "jcode 연동" in out
    # 5) MCP 설정 출력 (키 포함 env)
    assert "mcpServers" in out
    assert "MYVIKING_KEY" in out


def test_connect_agent_filter_only_jcode(tmp_path, monkeypatch, capsys):
    """--agent jcode: 해당 에이전트만 설치한다."""
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".jcode").mkdir()
    folder = tmp_path / "app1"
    folder.mkdir()
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: {"project_name": "앱"})

    class Args:
        url = "https://viking.example.com"
        key = "jv_0123456789abcdef01234567"
        project = "p1"
        timeout = "15"
        cwd = str(folder)
        agent = "jcode"

    cli.connect(Args())
    out = capsys.readouterr().out
    assert "jcode 연동" in out
    assert "훅 설치" not in out       # Claude Code 는 건너뜀
    assert "pi 허브 확장" not in out  # pi 없음 → 건너뜀
    assert not (folder / ".claude").exists()  # 폴더 안 .claude 를 만들지 않는다


def test_pi_install_requires_key_and_project(tmp_path, monkeypatch):
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))

    class Args:
        url = "https://viking.example.com"
        key = ""
        project = "my-app"
        timeout = "15"
        cwd = str(tmp_path)

    import pytest
    with pytest.raises(SystemExit):
        cli.pi_install(Args())
    assert not (tmp_path / ".pi").exists()
    assert not (tmp_path / ".myviking").exists()


def test_pi_install_detects_project_from_key(tmp_path, monkeypatch, capsys):
    """--project 없이 키만으로 연결 — GET /api/v1/me 가 슬러그/이름을 알려 준다."""
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "app1"
    folder.mkdir()

    def fake_api(url, key, method, path, **kw):
        if path == "/me":
            return {"project": "pX99", "project_name": "자동 식별 프로젝트"}
        if path == "/projects/pX99/brief":
            return {"project": "pX99", "project_name": "자동 식별 프로젝트", "orientation": "x"}
        raise AssertionError(f"예상 밖 경로: {path}")

    monkeypatch.setattr(cli, "_api", fake_api)

    class Args:
        url = "https://viking.example.com"
        key = "jv_0123456789abcdef01234567"
        project = ""
        timeout = "15"
        cwd = str(folder)

    cli.pi_install(Args())
    conns = json.loads((tmp_path / ".myviking" / "connections.json").read_text())["connections"]
    assert conns[0]["project"] == "pX99"
    assert conns[0]["name"] == "자동 식별 프로젝트"
    link = json.loads((folder / ".myviking-connection.json").read_text())
    assert link["connection"] == "https://viking.example.com|pX99"
    out = capsys.readouterr().out
    assert "자동 식별 프로젝트" in out


def test_pi_install_bad_key_fails_with_project_hint(tmp_path, monkeypatch):
    """키가 틀리면 /me 에서 막히고 — '--project 를 함께 주세요' 안내로 종료."""
    import pytest
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "app2"
    folder.mkdir()

    def bad_api(url, key, method, path, **kw):
        if path == "/me":
            raise SystemExit("jv: 서버 응답 오류 (401) — 키/주소를 확인하세요.")
        raise AssertionError(f"예상 밖 경로: {path}")

    monkeypatch.setattr(cli, "_api", bad_api)

    class Args:
        url = "https://viking.example.com"
        key = "jv_wrong"
        project = ""
        timeout = "15"
        cwd = str(folder)

    with pytest.raises(SystemExit) as ei:
        cli.pi_install(Args())
    assert ei.value.code == 2
    assert not (tmp_path / ".myviking").exists()
    assert not (folder / ".myviking-connection.json").exists()


# ══════════════════ omp (Oh My Pi) — pi 와 허브/연결 저장소 공유 ══════════════════ #
def test_omp_install_uses_own_extensions_dir_shares_connections(tmp_path, monkeypatch, capsys):
    """jv omp install: 같은 TS 확장을 ~/.omp/agent/extensions/ 에 둔다 (~/.pi 건 안 똹음).

    연결 저장소(~/.myviking/connections.json)와 폴더 링크는 pi 와 완전히 공유한다.
    """
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "app1"
    folder.mkdir()
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: {"project_name": "데이터자판기"})

    class Args:
        url = "https://viking.example.com"
        key = "jv_0123456789abcdef01234567"
        project = "p921a95"
        timeout = "15"
        cwd = str(folder)

    cli.omp_install(Args())

    omp_path = tmp_path / ".omp" / "agent" / "extensions" / "myviking.ts"
    pi_path = tmp_path / ".pi" / "agent" / "extensions" / "myviking.ts"
    assert omp_path.exists() and (omp_path.stat().st_mode & 0o777) == 0o600
    assert not pi_path.exists()  # omp 설치로 pi 폴더가 생기지 않아야 함

    # 연결 저장소와 폴더 링크는 pi 와 동일한 위치 사용
    conns_p = tmp_path / ".myviking" / "connections.json"
    assert conns_p.exists()
    link = folder / ".myviking-connection.json"
    assert json.loads(link.read_text()) == {"connection": "https://viking.example.com|p921a95"}

    out = capsys.readouterr().out
    assert "omp 허브 확장" in out

    # pi_check 로 omp 전용 허브를 검사하려면 flavor="omp" 가 필요 — pi 용은 별개로 남음
    class CArgs:
        cwd = str(folder)

    cli.pi_check(CArgs())               # flavor="pi" 기본값 → pi 허브가 없으니 경고
    out = capsys.readouterr().out
    assert "설치되어 있지 않습니다" in out

    monkeypatch.setattr(cli, "_verify_server", lambda *a, **kw: {"ok": True})
    cli.omp_check(CArgs())              # flavor="omp" → 정상 확인
    out = capsys.readouterr().out
    assert "omp 허브 확장" in out and "폴더 연결" in out


def test_omp_and_pi_share_folder_link_and_conns_store(tmp_path, monkeypatch):
    """jv pi install 로 연결해도 jv omp switch/list 가 같은 저장소를 보고 쓴다."""
    import jv.cli as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "app1"
    folder.mkdir()
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: {"project_name": "데이터자판기"})

    class Args:
        url = "https://viking.example.com"
        key = "jv_0123456789abcdef01234567"
        project = "p921a95"
        timeout = "15"
        cwd = str(folder)

    cli.pi_install(Args())              # pi 로 설치
    assert (tmp_path / ".pi" / "agent" / "extensions" / "myviking.ts").exists()
    assert not (tmp_path / ".omp" / "agent" / "extensions" / "myviking.ts").exists()

    # omp 로따 같은 폴더 링크와 연결 저장소를 보고 있다 (omp_list 는 pi_list 와 같은 함수)
    assert cli.omp_list is cli.pi_list
    assert cli.omp_switch is cli.pi_switch
    assert cli.omp_disconnect is cli.pi_disconnect
    assert cli.omp_remove is cli.pi_remove

