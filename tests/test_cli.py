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