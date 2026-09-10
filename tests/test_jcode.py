"""jv jcode — J-Code 에이전트 연동 테스트 (훅 스플라이스·MCP 병합·턴 자동 기록)."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jv.cli as cli


def _fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".jcode").mkdir(parents=True)
    (home / ".myviking").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


# ── config.toml [hooks] 스플라이스 ──

def test_toml_set_hooks_adds_missing_keys():
    text = "[hooks]\npre_tool_timeout_ms = 5000\n\n[display]\nemoji = true\n"
    out, set_keys, skipped = cli._toml_set_hooks(text, "/h/bin/hook.sh")
    assert set_keys == ["session_start", "session_end", "turn_end"] and skipped == []
    assert 'turn_end = "/h/bin/hook.sh"' in out
    assert "pre_tool_timeout_ms = 5000" in out
    assert "[display]" in out  # 이후 섹션 보존


def test_toml_set_hooks_preserves_user_values():
    text = f"[hooks]\nturn_end = \"~/bin/my-notify\"\nsession_end = \"\"\n"
    out, set_keys, skipped = cli._toml_set_hooks(text, "/h/bin/hook.sh")
    assert skipped == ["turn_end"]
    assert set_keys == ["session_start", "turn_end"] or "session_start" in set_keys
    assert 'turn_end = "~/bin/my-notify"' in out  # 사용자 값 유지
    assert 'session_end = "/h/bin/hook.sh"' in out


def test_toml_set_hooks_no_hooks_section():
    text = "[display]\nemoji = true\n"
    out, set_keys, _ = cli._toml_set_hooks(text, "/h/bin/hook.sh")
    assert "[hooks]" in out and set_keys == ["session_start", "session_end", "turn_end"]


def test_toml_unset_hooks_removes_only_ours():
    text = ('[hooks]\n'
            'pre_tool_timeout_ms = 5000\n'
            'turn_end = "/h/bin/hook.sh"\n'
            'session_start = "/h/bin/hook.sh"\n'
            'session_end = "~/bin/other"\n')
    out, removed = cli._toml_unset_hooks(text, "/h/bin/hook.sh")
    assert removed == 2
    assert 'turn_end = ""' in out and 'session_start = ""' in out
    assert 'session_end = "~/bin/other"' in out


# ── MCP 병합 ──

def test_mcp_merge_preserves_others(tmp_path, monkeypatch):
    _fake_home(tmp_path, monkeypatch)
    cli._jcode_mcp_file().write_text(json.dumps(
        {"servers": {"other": {"command": "/bin/other"}}}), encoding="utf-8")
    conn = {"url": "http://s", "key": "jv_k", "project": "p1", "name": "n"}
    data = cli._mcp_merge(conn)
    assert set(data["servers"]) == {"other", "myviking"}
    assert data["servers"]["myviking"]["env"]["MYVIKING_PROJECT"] == "p1"
    cli._jcode_mcp_file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    assert cli._mcp_remove() is True
    assert "myviking" not in json.loads(cli._jcode_mcp_file().read_text())["servers"]


# ── 설치/점검 ──

class _Args:
    url = key = project = name = cwd = ""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_jcode_install_writes_all(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    (home / ".jcode" / "config.toml").write_text("[hooks]\npre_tool_timeout_ms = 5000\n", encoding="utf-8")

    calls = {}
    def fake_me(url, key):
        calls["me"] = (url, key)
        return {"project": "p1", "project_name": "앱"}
    def fake_verify(url, key, project):
        calls["verify"] = (url, key, project)
        return {"project_name": "앱"}
    monkeypatch.setattr(cli, "_resolve_project_by_key", fake_me)
    monkeypatch.setattr(cli, "_verify_server", fake_verify)

    cli.jcode_install(_Args(url="http://srv", key="jv_0123456789abcdef01234567", cwd=str(proj)))

    assert (proj / ".myviking-connection.json").exists()
    conns = json.loads((home / ".myviking" / "connections.json").read_text())["connections"]
    assert conns[0]["project"] == "p1" and conns[0]["key"] == "jv_0123456789abcdef01234567"

    cfg = (home / ".jcode" / "config.toml").read_text()
    launcher = str(home / ".jcode" / "myviking-hook.sh")
    assert f'turn_end = "{launcher}"' in cfg and "pre_tool_timeout_ms" in cfg
    assert (home / ".jcode" / "myviking-hook.sh").exists()
    skill = home / ".jcode" / "skills" / "myviking" / "SKILL.md"
    assert skill.exists() and "jv brief" in skill.read_text()
    mcp = json.loads((home / ".jcode" / "mcp.json").read_text())
    assert mcp["servers"]["myviking"]["env"]["MYVIKING_URL"] == "http://srv"
    assert json.loads((home / ".jcode" / ".myviking.json").read_text())["version"] == cli._JCODE_VERSION


def test_jcode_check_green(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    conn = {"id": "http://srv|p1", "name": "앱", "url": "http://srv",
            "key": "jv_0123456789abcdef01234567", "project": "p1"}
    cli._save_conns([conn])
    (proj / ".myviking-connection.json").write_text(json.dumps({"connection": conn["id"]}))
    monkeypatch.setattr(cli, "_verify_server", lambda u, k, p: {"project_name": "앱"})
    cli.jcode_install(_Args(url="http://srv", key=conn["key"], project="p1", cwd=str(proj)))

    monkeypatch.setattr(cli, "_verify_server", lambda u, k, p: {"project_name": "앱"})
    out = []
    monkeypatch.setattr(sys, "stdout", type("S", (), {"write": lambda self, s: out.append(s), "flush": lambda self: None})())
    cli.jcode_check(_Args(cwd=str(proj)))
    text = "".join(out)
    assert "✓ jcode 훅" in text and "✓ jcode MCP" in text and "✓ 서버 연결·인증" in text


def test_jcode_install_respects_existing_hook(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    (home / ".jcode" / "config.toml").write_text('[hooks]\nturn_end = "~/bin/이미 있음"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "_resolve_project_by_key", lambda u, k: {"project": "p1", "project_name": "앱"})
    monkeypatch.setattr(cli, "_verify_server", lambda u, k, p: {"project_name": "앱"})
    cli.jcode_install(_Args(url="http://srv", key="jv_0123456789abcdef01234567", cwd=str(proj)))
    cfg = (home / ".jcode" / "config.toml").read_text()
    assert 'turn_end = "~/bin/이미 있음"' in cfg  # 사용자 훅 유지
    assert "turn_end" in json.loads((home / ".jcode" / ".myviking.json").read_text()).get("events", []) or \
           'session_start' in (home / ".jcode" / "config.toml").read_text()


# ── 훅 이벤트 처리 (턴 자동 기록) ──

def _hook_env(monkeypatch, **kw):
    env = {"JCODE_HOOK_EVENT": "turn_end", "JCODE_HOOK_STATUS": "ok",
           "JCODE_HOOK_SESSION_ID": "session_x",
           "JCODE_HOOK_CWD": "/tmp", "JCODE_HOOK_PAYLOAD": "{}",
           "JCODE_HOOK_LAST_ASSISTANT_TEXT": "답변 답변"}
    env.update(kw)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_hook_turn_end_commits(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    conn = {"id": "http://srv|p1", "name": "앱", "url": "http://srv",
            "key": "jv_0123456789abcdef01234567", "project": "p1"}
    cli._save_conns([conn])
    (proj / ".myviking-connection.json").write_text(json.dumps({"connection": conn["id"]}))

    sent = {}
    def fake_api(url, key, method, path, **kw):
        sent.update(url=url, key=key, method=method, path=path, json=kw.get("json"))
        return {"ok": True}
    monkeypatch.setattr(cli, "_api", fake_api)

    _hook_env(monkeypatch, JCODE_HOOK_CWD=str(proj),
              JCODE_HOOK_PAYLOAD=json.dumps({"prompt": "배포는 어떻게?"}))
    cli.jcode_hook(_Args())
    assert sent["path"] == "/projects/p1/commit"
    assert sent["json"]["question"] == "배포는 어떻게?"
    assert sent["json"]["answer"] == "답변 답변"
    assert sent["json"]["agent"] == "jcode" and sent["json"]["session_id"] == "session_x"
    assert cli._jcode_log_file().exists()


def test_hook_skips_slash_cmd_and_error_status(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    conn = {"id": "http://srv|p1", "name": "앱", "url": "http://srv",
            "key": "jv_0123456789abcdef01234567", "project": "p1"}
    cli._save_conns([conn])
    (proj / ".myviking-connection.json").write_text(json.dumps({"connection": conn["id"]}))
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")))

    _hook_env(monkeypatch, JCODE_HOOK_CWD=str(proj),
              JCODE_HOOK_PAYLOAD=json.dumps({"prompt": "/model gpt"}))
    cli.jcode_hook(_Args())  # 슬래시 명령 → 미기록

    _hook_env(monkeypatch, JCODE_HOOK_PAYLOAD=json.dumps({"prompt": "질문"}), JCODE_HOOK_STATUS="error")
    cli.jcode_hook(_Args())  # 오류 턴 → 미기록

    _hook_env(monkeypatch, JCODE_HOOK_PAYLOAD=json.dumps({"prompt": "질문"}), JCODE_HOOK_LAST_ASSISTANT_TEXT="")
    cli.jcode_hook(_Args())  # 빈 답 → 미기록
    log = cli._jcode_log_file().read_text()
    assert "기록 안 함" in log


def test_hook_question_falls_back_to_session_file(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    conn = {"id": "http://srv|p1", "name": "앱", "url": "http://srv",
            "key": "jv_0123456789abcdef01234567", "project": "p1"}
    cli._save_conns([conn])
    (proj / ".myviking-connection.json").write_text(json.dumps({"connection": conn["id"]}))
    sdir = home / ".jcode" / "sessions"
    sdir.mkdir(parents=True)
    (sdir / "session_a_1_xyz.json").write_text(json.dumps({
        "id": "session_x",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "<system-reminder> 무시"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "이전 답"}]},
            {"role": "user", "content": [{"type": "text", "text": "질문 파일"}]},
        ]}), encoding="utf-8")

    sent = {}
    def fake_api(url, key, method, path, **kw):
        sent.update(json=kw.get("json"))
        return {}
    monkeypatch.setattr(cli, "_api", fake_api)
    _hook_env(monkeypatch, JCODE_HOOK_CWD=str(proj))  # payload 에 prompt 없음
    cli.jcode_hook(_Args())
    assert sent["json"]["question"] == "질문 파일"


def test_hook_no_connection_no_crash(tmp_path, monkeypatch):
    _fake_home(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_api", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")))
    _hook_env(monkeypatch, JCODE_HOOK_CWD=str(tmp_path))
    cli.jcode_hook(_Args())
    assert "연결 없음" in cli._jcode_log_file().read_text()


# ── 폴더 연결 폴백 (스킬의 jv brief/search/remember 가 인자 없이 동작) ──

def test_remote_commands_resolve_from_folder(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    conn = {"id": "http://srv|p1", "name": "앱", "url": "http://srv",
            "key": "jv_0123456789abcdef01234567", "project": "p1"}
    cli._save_conns([conn])
    (proj / ".myviking-connection.json").write_text(json.dumps({"connection": conn["id"]}))

    calls = []
    monkeypatch.setattr(cli, "_api", lambda url, key, method, path, **kw: calls.append((url, key, path)) or {"items": [], "warnings": []})
    monkeypatch.chdir(proj)
    cli.remote(_Args(cmd="search", q="배포", url="", key="", project="", cwd=""))
    assert calls and calls[0][0] == "http://srv" and calls[0][1] == "jv_0123456789abcdef01234567"


def test_folder_conn_walks_up(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    conn = {"id": "http://srv|p1", "name": "앱", "url": "http://srv",
            "key": "jv_0123456789abcdef01234567", "project": "p1"}
    cli._save_conns([conn])
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (tmp_path / "a" / ".myviking-connection.json").write_text(json.dumps({"connection": conn["id"]}))
    got = cli._folder_conn(deep)
    assert got and got["project"] == "p1"
    assert cli._folder_conn(tmp_path / "elsewhere") is None