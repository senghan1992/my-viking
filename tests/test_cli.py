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
