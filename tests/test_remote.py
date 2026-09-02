"""셸 브리지: MCP 도 훅도 없는 에이전트가 명령 몇 개로 같은 루프를 돈다."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from jarvis.remote import RemoteClient, format_context  # noqa: E402
from jarvis.server import create_app  # noqa: E402


class ClientTransport:
    def __init__(self, client, key=""):
        self.client = client
        self.key = key

    def request(self, method, path, body=None, params=None):
        headers = {"authorization": f"Bearer {self.key}"} if self.key else {}
        res = self.client.request(method, path, json=body, params=params, headers=headers)
        if res.status_code >= 400:
            raise RuntimeError(f"{res.status_code}: {res.text[:200]}")
        return res.json()


@pytest.fixture()
def http(home):
    return TestClient(create_app(home=str(home)))


@pytest.fixture()
def rc(http):
    return RemoteClient(ClientTransport(http))


def _seed(http):
    http.post("/projects", json={"project": "backend", "template": "coding"})
    http.post("/aliases", json={"alias": "git@github.com:me/backend.git", "project": "backend"})
    http.post("/memories", json={
        "project": "backend", "category": "pitfalls",
        "title": "PG 재시도 금지", "statement": "결제 승인 실패 시 재시도하면 이중 결제.",
    })


# --------------------------------------------------------------------------
# the loop, end to end
# --------------------------------------------------------------------------
def test_full_loop_resolves_by_repo(rc, http):
    """회사에서든 집에서든: 이름 없이 git remote 만으로 같은 프로젝트."""
    _seed(http)

    # https 형태로 물어도 ssh 형태로 등록한 프로젝트가 잡힌다.
    assert rc.resolve_or_fail(repo="https://github.com/me/backend") == "backend"

    prepared = rc.context(
        "결제 승인 실패 처리 어떻게 해?",
        repo="git@github.com:me/backend.git",
        agent="pi@laptop",
    )
    assert prepared["trace_id"].startswith("tr_")
    assert any(w["title"] == "PG 재시도 금지" for w in prepared["warnings"])

    rc.commit("backend", "결제 승인 실패 처리 어떻게 해?", "재시도하지 않습니다.",
              trace_id=prepared["trace_id"], agent="pi@laptop")
    res = rc.score(prepared["trace_id"], 1.0, comment="정확")
    assert "memories_adjusted" in res

    # 기록이 트레이스에 남고, 에이전트도 등록됐다.
    traces = http.get("/traces", params={"project": "backend"}).json()
    assert traces[0]["output"] == "재시도하지 않습니다."
    agents = http.get("/agents").json()
    assert agents[0]["name"] == "pi@laptop"


def test_brief_reads_established_knowledge(rc, http):
    _seed(http)
    brief = rc.brief("backend")
    assert any(w["title"] == "PG 재시도 금지" for w in brief["warnings"])


def test_resolve_failure_names_the_candidates(rc, http):
    _seed(http)
    with pytest.raises(RuntimeError) as err:
        rc.resolve_or_fail(repo="https://github.com/me/unknown-repo-xyz")
    assert "backend" in str(err.value)


# --------------------------------------------------------------------------
# output formatting — the agent reads this
# --------------------------------------------------------------------------
def test_format_context_leads_with_trace_and_warnings(rc, http):
    _seed(http)
    prepared = rc.context("결제 실패 처리?", project="backend", agent="pi@laptop")
    text = format_context(prepared)
    assert text.startswith(f"[MyViking · backend] trace_id={prepared['trace_id']}")
    assert "⚠ 주의" in text and "PG 재시도 금지" in text
    assert "jv remote commit" in text  # 다음 행동이 출력에 붙어 있다


def test_format_context_reuses_cached_answer(rc, http):
    _seed(http)
    rc.commit("backend", "배포는 어떻게 해?", "make deploy 입니다.")
    prepared = rc.context("배포는 어떻게 해?", project="backend")
    text = format_context(prepared)
    assert "전에 같은 질문에 답했습니다" in text
    assert "make deploy 입니다." in text
    assert "jv remote score" in text


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def test_remote_cli_loop(http, monkeypatch, capsys):
    import jarvis.remote as remote_mod
    from jarvis.cli import main

    _seed(http)
    monkeypatch.setattr(
        remote_mod, "client_for", lambda url, key="", timeout=30.0: RemoteClient(ClientTransport(http))
    )

    assert main(["remote", "brief", "-p", "backend", "--url", "x"]) == 0
    out = capsys.readouterr().out
    assert "backend" in out and "PG 재시도 금지" in out

    assert main(["remote", "ctx", "결제 실패 처리?", "-p", "backend", "--url", "x"]) == 0
    out = capsys.readouterr().out
    assert "trace_id=tr_" in out
    trace_id = out.split("trace_id=")[1].split()[0]

    assert main(["remote", "remember", "commands", "테스트", "pytest -q 로 돌린다",
                 "-p", "backend", "--url", "x"]) == 0
    assert "memories/commands" in capsys.readouterr().out

    assert main(["remote", "commit", "결제 실패 처리?", "재시도 안 함",
                 "-p", "backend", "--trace", trace_id, "--url", "x"]) == 0
    assert "기록했습니다" in capsys.readouterr().out

    assert main(["remote", "score", trace_id, "1.0", "--url", "x"]) == 0
    assert "반영했습니다" in capsys.readouterr().out


def test_remote_cli_fails_loud_without_a_server(capsys, monkeypatch):
    """훅과 달리 브리지는 에이전트가 출력을 읽는다 — 조용한 실패는 금물."""
    from jarvis.cli import main

    monkeypatch.delenv("MYVIKING_URL", raising=False)
    code = main(["remote", "ctx", "질문", "--url", "http://127.0.0.1:1", "-p", "x"])
    assert code == 1
    assert "오류" in capsys.readouterr().err

    with pytest.raises(SystemExit):  # URL 자체가 없으면 설정 방법을 알려주고 종료
        main(["remote", "brief"])


# --------------------------------------------------------------------------
# connection info
# --------------------------------------------------------------------------
def test_shell_client_connection_block():
    from jarvis.connect import build

    conn = build("shell", "https://viking.example.com", "backend", key="jv_k")
    assert "pip install my-viking" in conn.setup
    assert "MYVIKING_URL=https://viking.example.com" in conn.setup
    assert "MYVIKING_KEY=jv_k" in conn.setup
    assert "jv remote ctx" in conn.instructions
    assert conn.hooks_setup == ""  # 훅은 Claude Code 전용
