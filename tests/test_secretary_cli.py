"""서기 agent (jv secretary) — 관찰함을 지식으로 정리하는 별도 pi 세션을 조립·실행한다.

컨셉 검증 포인트:
  · 작업 세션은 지식을 만들지 않는다 (자동 등재 경로는 /observe 로 대체됐다)
  · 서기는 '별도의 세션'으로 열린다 — pi 를 헤드리스 자식으로 조립한다
  · 서기 원칙(charter)과 pi 에이전트 정의가 설치된다
  · 자동 실행 스위치는 머신 설정(~/.myviking/config.json)에 있다
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jv.cli as cli


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".myviking").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli, "STATE_DIR", home / ".myviking" / "hook-state")
    return home


class _Args:
    url = ""
    key = ""
    project = ""
    cwd = ""
    limit = "60"
    task = ""
    claim = False
    dry_run = False
    foreground = False
    detach = True
    force = True


def test_secretary_work_session_has_no_auto_commit():
    """허브 확장에는 '턴 → 관찰' 경로만 있고 서버 자동 증류(/commit) 경로는 없다."""
    src = cli._PI_EXT_TEMPLATE
    assert "/observe" in src
    assert "/commit" not in src, "작업 세션이 지식을 만들면 안 된다 (서기의 일)"
    assert 'pi.on("turn_end"' in src and "digestTranscript" in src


def test_secretary_role_session_gets_scribe_tools_only():
    """MYVIKING_ROLE=secretary 로 열린 세션은 서기 도구를 열고, 관찰을 올리지 않는다."""
    src = cli._PI_EXT_TEMPLATE
    assert "MYVIKING_ROLE" in src
    assert "secretaryJobs" in src and "workerJobs" in src
    for tool in ("viking_inbox", "viking_file", "viking_ack", "viking_report", "viking_session"):
        assert f'"{tool}"' in src, f"{tool} 이 서기 도구에 없다"
    # 서기 세션은 관찰을 올리지 않아야 한다 (되먹임 방지) — observe 호출이 role 가드로 막혀 있다
    assert "if (!c || SECRETARY) return;" in src


def test_secretary_installs_charter_and_pi_agent(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    (home / ".pi" / "agent").mkdir(parents=True)

    cli.secretary_install(_Args())
    out = capsys.readouterr().out
    charter = home / ".myviking" / "secretary" / "secretary.md"
    agent = home / ".pi" / "agent" / "agents" / "myviking-secretary.md"
    assert "✓" in out
    assert charter.exists() and "반복 요청" in charter.read_text()
    assert agent.exists()
    body = agent.read_text()
    assert "name: myviking-secretary" in body
    # 서기는 코드를 고치지 못한다 — 쓰기 도구가 없다
    assert "write" not in body.split("---")[1].split("tools:")[1].splitlines()[0]


def test_secretary_charter_is_not_overwritten_by_default(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    path = cli.install_secretary_charter()
    path.write_text("내가 고친 서기 원칙")
    assert cli.install_secretary_charter() == path
    assert path.read_text() == "내가 고친 서기 원칙", "사용자 편집은 보존 (force 로만 덮어쓰기)"


def test_secretary_once_dry_run_builds_headless_pi(tmp_path, monkeypatch, capsys):
    """dry-run: 서기 세션 조립 명령이 보인다 (백그라운드 pi, 관찰함만 읽는 도구)."""
    _home(tmp_path, monkeypatch)
    folder = tmp_path / "proj"
    folder.mkdir()
    conn = {"id": "http://srv|p1", "name": "앱", "url": "http://srv",
            "key": "jv_0123456789abcdef01234567", "project": "p1"}
    cli._save_conns([conn])
    (folder / ".myviking-connection.json").write_text(json.dumps({"connection": conn["id"]}))

    monkeypatch.setattr(cli, "_api", lambda url, key, method, path, **kw: {"pending": 4, "last_run": None})

    args = argparse.Namespace(**{**vars(_Args()), "cwd": str(folder), "dry_run": True})
    cli.secretary_once(args)
    out = capsys.readouterr().out
    assert "MYVIKING_ROLE=secretary" in out
    assert "--print" in out and "--append-system-prompt" in out
    assert "viking_inbox" in out and "viking_file" in out
    assert "read,grep,find,ls" in out
    assert "대기 관찰 4건" in out


def test_secretary_once_skips_when_nothing_pending(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    folder = tmp_path / "proj"
    folder.mkdir()
    conn = {"id": "http://srv|p1", "name": "앱", "url": "http://srv",
            "key": "jv_0123456789abcdef01234567", "project": "p1"}
    cli._save_conns([conn])
    (folder / ".myviking-connection.json").write_text(json.dumps({"connection": conn["id"]}))
    monkeypatch.setattr(cli, "_api", lambda url, key, method, path, **kw: {"pending": 0})

    args = argparse.Namespace(**{**vars(_Args()), "cwd": str(folder), "force": False})
    cli.secretary_once(args)
    assert "할 일이 없습니다" in capsys.readouterr().out


def test_secretary_once_requires_pi_binary(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    folder = tmp_path / "proj"
    folder.mkdir()
    conn = {"id": "http://srv|p1", "name": "앱", "url": "http://srv",
            "key": "jv_0123456789abcdef01234567", "project": "p1"}
    cli._save_conns([conn])
    (folder / ".myviking-connection.json").write_text(json.dumps({"connection": conn["id"]}))
    monkeypatch.setattr(cli, "_api", lambda url, key, method, path, **kw: {"pending": 3})
    monkeypatch.setattr(cli, "_pi_binary", lambda: "")

    args = argparse.Namespace(**{**vars(_Args()), "cwd": str(folder)})
    try:
        cli.secretary_once(args)
        raise AssertionError("pi 가 없으면 멈춰야 한다")
    except SystemExit as e:
        assert e.code == 3
    assert "pi" in capsys.readouterr().err


def test_secretary_auto_switch_is_machine_setting(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    cli.secretary_auto(argparse.Namespace(flag="on", every="8"))
    cfg = json.loads((home / ".myviking" / "config.json").read_text())
    assert cfg["secretary"] == {"auto": True, "every": 8}
    assert (home / ".myviking" / "config.json").stat().st_mode & 0o077 == 0

    cli.secretary_auto(argparse.Namespace(flag="off", every=""))
    assert json.loads((home / ".myviking" / "config.json").read_text())["secretary"]["auto"] is False


def test_secretary_status_reads_server(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    folder = tmp_path / "proj"
    folder.mkdir()
    conn = {"id": "http://srv|p1", "name": "앱", "url": "http://srv",
            "key": "jv_0123456789abcdef01234567", "project": "p1"}
    cli._save_conns([conn])
    (folder / ".myviking-connection.json").write_text(json.dumps({"connection": conn["id"]}))

    def fake_api(url, key, method, path, **kw):
        assert path == "/projects/p1/secretary/status"
        return {"pending": 7, "filed_total": 3, "skipped_total": 2,
                "repeats": [{"hits": 4, "text": "배포 명령 다시 알려줘"}],
                "last_run": {"started_at": "2026-09-30 05:00", "found": 9, "filed": 3,
                             "merged": 1, "skipped": 5, "report": "함정 3권 등재"}}

    monkeypatch.setattr(cli, "_api", fake_api)
    cli.secretary_status(argparse.Namespace(**{**vars(_Args()), "cwd": str(folder)}))
    out = capsys.readouterr().out
    assert "대기: 7건" in out
    assert "×4 배포 명령 다시 알려줘" in out
    assert "함정 3권 등재" in out


def test_note_and_inbox_remote_commands(tmp_path, monkeypatch, capsys):
    """jv note / jv inbox — 셸·훅 에이전트도 같은 관찰함을 쓴다."""
    _home(tmp_path, monkeypatch)
    folder = tmp_path / "proj"
    folder.mkdir()
    conn = {"id": "http://srv|p1", "name": "앱", "url": "http://srv",
            "key": "jv_0123456789abcdef01234567", "project": "p1"}
    cli._save_conns([conn])
    (folder / ".myviking-connection.json").write_text(json.dumps({"connection": conn["id"]}))

    calls = []

    def fake_api(url, key, method, path, **kw):
        calls.append((method, path, kw.get("json")))
        if path.endswith("/observe"):
            return {"logged": [1], "pending": 2, "repeat_hits": 2}
        return {"pending": 2, "sessions": [{"session_id": "s1", "agent": "pi", "transcript": "/x.jsonl",
                                           "observations": [{"id": 1, "kind": "prompt", "text": "재시도?", "hits": 2}]}],
                "repeats": [{"hits": 2, "text": "재시도?"}], "recurrences": []}

    monkeypatch.setattr(cli, "_api", fake_api)
    cli.remote(argparse.Namespace(cmd="note", q="이건 기록할 가치 있음", content="", category="note",
                                  transcript="", limit="60", json=False, claim=False,
                                  outcome="skipped", reason="", source="manual", occurrences="0",
                                  **{"url": "", "key": "", "project": "", "cwd": str(folder)}))
    assert calls[0][0] == "POST" and calls[0][1] == "/projects/p1/observe"
    assert calls[0][2]["kind"] == "note"
    assert "서기에게 전달" in capsys.readouterr().out

    cli.remote(argparse.Namespace(cmd="inbox", q="", content="", category="note", transcript="",
                                  limit="60", json=False, claim=False, outcome="skipped", reason="",
                                  source="manual", occurrences="0",
                                  **{"url": "", "key": "", "project": "", "cwd": str(folder)}))
    out = capsys.readouterr().out
    assert "claim=false" in calls[-1][1]
    assert "×2" in out and "재시도?" in out and "/x.jsonl" in out


def test_secretary_effective_folder_overrides_global(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".myviking").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    work = tmp_path / "proj"
    work.mkdir()
    (home / ".myviking" / "config.json").write_text('{"secretary": {"auto": false, "every": 12}}')
    (work / ".myviking-secretary.json").write_text('{"auto": true, "every": 5}')
    eff = cli._secretary_effective(work)
    assert eff == {"auto": True, "every": 5, "source": "folder"}


def test_secretary_effective_broken_folder_falls_back(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / ".myviking").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    work = tmp_path / "proj"
    work.mkdir()
    (home / ".myviking" / "config.json").write_text('{"secretary": {"auto": true, "every": 8}}')
    (work / ".myviking-secretary.json").write_text('{broken')
    eff = cli._secretary_effective(work)
    assert eff["auto"] is True and eff["source"] == "global"


def test_status_shows_folder_secretary(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    work = tmp_path / "proj"
    work.mkdir()
    (work / ".myviking-secretary.json").write_text('{"auto": true, "every": 5}')
    args = _Args()
    args.cwd = str(work)
    cli.status(args)
    out = capsys.readouterr().out
    assert "이 폴더 서기" in out and "5" in out


def test_secretary_auto_folder_writes_folder_file(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    work = tmp_path / "proj"
    work.mkdir()
    cli.secretary_auto(argparse.Namespace(flag="on", every="5", folder=True, cwd=str(work)))
    data = json.loads((work / ".myviking-secretary.json").read_text())
    assert data == {"auto": True, "every": 5}
    assert not (home / ".myviking" / "config.json").exists()


def test_secretary_effective_broken_global_every_falls_back(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".myviking").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    work = tmp_path / "proj"
    work.mkdir()
    (home / ".myviking" / "config.json").write_text('{"secretary": {"auto": true, "every": "abc"}}')
    assert cli._secretary_effective(work) == {"auto": True, "every": 12, "source": "global"}


def test_secretary_auto_folder_git_excludes_folder_file(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    work = tmp_path / "proj"
    work.mkdir()
    (work / ".git").mkdir()
    cli.secretary_auto(argparse.Namespace(flag="on", every="5", folder=True, cwd=str(work)))
    assert (work / ".myviking-secretary.json").exists()
    assert ".myviking-secretary.json" in (work / ".git" / "info" / "exclude").read_text()
