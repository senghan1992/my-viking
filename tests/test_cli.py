"""jv CLI — 훅 설정 JSON, 마스킹, 트랜스크립트 추출 테스트."""
import json
import sys
from pathlib import Path

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
    monkeypatch.setattr(cli, "_in_container", lambda: False)
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
    assert "MYVIKING_URL=https://viking.example.com" in cmd
    assert "jv" in cmd
    assert "user-prompt-submit" in cmd


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
