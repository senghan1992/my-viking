"""배포 시나리오 리뷰(초보·중급·고수 3인)에서 실제로 재현된 결함들의 회귀 테스트.

각 테스트의 독스트링이 어떤 사람이 어떤 상황에서 겪은 문제인지 말한다.
"""

from __future__ import annotations

import sqlite3
import tarfile

import pytest
from fastapi.testclient import TestClient

from jarvis import Config, Jarvis
from jarvis.backup import make_snapshot, restore_snapshot
from jarvis.hooks import health_check, run
from jarvis.server import create_app


@pytest.fixture()
def client(home):
    return TestClient(create_app(home=str(home)))


def _hdr(key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {key}"}


def _admin_and_scoped(client, scope="alpha"):
    client.post("/projects", json={"project": "alpha"})
    client.post("/projects", json={"project": "beta"})
    admin = client.post("/keys", json={"name": "admin"}).json()["key"]
    scoped = client.post(
        "/keys", json={"name": "only", "projects": [scope]}, headers=_hdr(admin)
    ).json()["key"]
    return admin, scoped


def test_key_scope_never_widens_by_a_field_typo(client):
    """팀 리드 시뮬레이션에서 발견: {"project": "shop-api"} (단수) 를 보내자 필드가
    조용히 무시되고 전체 관리자 키가 발급됐다. 단수는 그 뜻대로 받고, 모르는 필드는
    거절하며, 두 표기를 동시에 주면 거절한다."""
    admin, _ = _admin_and_scoped(client)
    made = client.post(
        "/keys", json={"name": "eve", "project": "alpha"}, headers=_hdr(admin)
    )
    assert made.status_code == 200
    me = client.get("/me", headers=_hdr(made.json()["key"])).json()
    assert me["projects"] == ["alpha"] and not me.get("admin")

    bad = client.post(
        "/keys", json={"name": "eve", "scope": "alpha"}, headers=_hdr(admin)
    )
    assert bad.status_code == 422
    both = client.post(
        "/keys",
        json={"name": "eve", "project": "alpha", "projects": ["beta"]},
        headers=_hdr(admin),
    )
    assert both.status_code == 422


# --------------------------------------------------------------------------
# 스코프 키가 넘지 못해야 하는 선 (고수 B1/H1/H2, 중급 F2)
# --------------------------------------------------------------------------
def test_scoped_key_cannot_touch_backup_or_reindex(client):
    """프로젝트 하나짜리 키로 /backup/config 를 로컬 경로로 돌리고 /backup/run 을 치면
    전체 저장소 스냅샷이 그 경로에 떨어졌다 — 백업은 관리자 전용이어야 한다."""
    _, scoped = _admin_and_scoped(client)
    for method, path, body in [
        ("GET", "/backup/status", None),
        ("POST", "/backup/config", {"provider": "local", "local_path": "/tmp/x"}),
        ("POST", "/backup/run", None),
        ("GET", "/backup/list", None),
        ("POST", "/backup/disconnect", None),
        ("POST", "/backup/connect/poll", None),
        ("POST", "/backup/connect/start", {"client_id": "a", "client_secret": "b"}),
    ]:
        r = client.request(method, path, json=body, headers=_hdr(scoped))
        assert r.status_code == 403, (path, r.status_code, r.text)
    assert client.post("/reindex", headers=_hdr(scoped)).status_code == 403
    assert client.post("/reindex", params={"project": "beta"}, headers=_hdr(scoped)).status_code == 403
    assert client.post("/reindex", params={"project": "alpha"}, headers=_hdr(scoped)).status_code == 200


def test_scoped_key_cannot_create_projects_outside_its_scope(client):
    _, scoped = _admin_and_scoped(client)
    assert client.post("/projects", json={"project": "evil"}, headers=_hdr(scoped)).status_code == 403
    assert client.post("/projects", json={"project": "alpha"}, headers=_hdr(scoped)).status_code == 200


def test_scoped_key_cannot_hijack_another_projects_repo(client):
    """beta 의 remote 를 alpha 로 다시 가리키면 beta 체크아웃의 모든 훅 기록이 alpha 로
    흘러 들어온다 — 별칭의 현재 소유 프로젝트도 범위 안이어야 한다."""
    admin, scoped = _admin_and_scoped(client)
    client.post("/aliases", json={"alias": "github.com/org/beta", "project": "beta"}, headers=_hdr(admin))
    r = client.post("/aliases", json={"alias": "github.com/org/beta", "project": "alpha"}, headers=_hdr(scoped))
    assert r.status_code == 403
    got = client.post("/resolve", json={"repo": "github.com/org/beta"}, headers=_hdr(admin)).json()
    assert got["project"] == "beta"
    # 자기 것을 자기에게 (재)등록하는 것은 된다
    assert client.post("/aliases", json={"alias": "github.com/org/alpha", "project": "alpha"},
                       headers=_hdr(scoped)).status_code == 200


def test_bad_uri_is_a_400_not_a_traceback(client):
    for path in ("/ls", "/tree"):
        assert client.get(path, params={"uri": "jarvis://"}).status_code == 400


# --------------------------------------------------------------------------
# 인증 실패 백오프 (초보 F2, 중급 F1, 고수 M1)
# --------------------------------------------------------------------------
def test_valid_key_still_works_while_the_same_ip_is_backed_off(client):
    """사무실 NAT 뒤에서 한 사람의 폐기된 키가 분당 10회 실패를 만들면, 같은 IP 의 나머지
    팀원(과 관리자)까지 429 였다. 차단은 *실패한* 시도에만 걸려야 한다."""
    admin, _ = _admin_and_scoped(client)
    for _ in range(12):
        client.get("/projects", headers=_hdr("jv_wrong"))
    bad = client.get("/projects", headers=_hdr("jv_wrong"))
    assert bad.status_code == 429 and "초" in bad.json()["detail"]
    good = client.get("/projects", headers=_hdr(admin))
    assert good.status_code == 200


def test_trust_proxy_uses_the_last_forwarded_hop(home, monkeypatch):
    """X-Forwarded-For 의 첫 값은 클라이언트가 마음대로 쓴다. 우리 프록시가 붙인 마지막
    값만 믿어야 남의 IP 를 사칭해 잠그는 일이 없다."""
    monkeypatch.setenv("MYVIKING_TRUST_PROXY", "1")
    c = TestClient(create_app(home=str(home)))
    c.post("/keys", json={"name": "admin"})
    # 공격자가 피해자 IP 를 앞에 붙이고 실패를 쌓는다 — 마지막 홉(진짜 공격자 IP)에 기록돼야 한다
    for _ in range(12):
        c.get("/projects", headers={**_hdr("jv_wrong"), "x-forwarded-for": "203.0.113.9, 10.0.0.66"})
    victim = c.get("/projects", headers={**_hdr("jv_wrong"), "x-forwarded-for": "203.0.113.9"})
    assert victim.status_code == 401  # 피해자는 잠기지 않았다
    attacker = c.get("/projects", headers={**_hdr("jv_wrong"), "x-forwarded-for": "1.2.3.4, 10.0.0.66"})
    assert attacker.status_code == 429


# --------------------------------------------------------------------------
# 키 DB 유실·손상 (고수 H4/H6)
# --------------------------------------------------------------------------
def test_lost_key_db_does_not_reopen_the_server(home):
    """README 가 'index.db 는 지우고 reindex' 라고 해서 지웠더니 인증이 꺼진 채 열렸다.
    인증이 켜졌던 흔적(marker)이 있으면 키가 없어도 열지 말고 503 으로 알려야 한다."""
    c = TestClient(create_app(home=str(home)))
    c.post("/projects", json={"project": "alpha"})
    c.post("/keys", json={"name": "admin"})
    assert (home / "auth.enabled").exists()
    for f in home.glob("index.db*"):
        f.unlink()
    c2 = TestClient(create_app(home=str(home)))
    h = c2.get("/health").json()
    assert h["auth_required"] is False and h["degraded"] is True
    assert any("키 DB" in p for p in h["problems"])
    r = c2.get("/projects")
    assert r.status_code == 503 and "복원" in r.json()["detail"]
    # 복구: 서버에서 새 키를 만들면 다시 정상
    from jarvis.auth import KeyStore
    from jarvis.db import Database

    KeyStore(Database(home / "index.db")).create("admin2")
    c3 = TestClient(create_app(home=str(home)))
    assert c3.get("/projects").status_code == 401


def test_empty_index_is_rebuilt_from_files(home, jarvis):
    jarvis.init_project("alpha", template="coding")
    jarvis.remember("alpha", "commands", "테스트", "pytest -q")
    jarvis.store.db.conn.close()
    for f in home.glob("index.db*"):
        f.unlink()
    fresh = Jarvis(config=Config(home=home))
    assert [m["title"] for m in fresh.memories("alpha")] == ["테스트"]


def test_corrupt_index_is_moved_aside_instead_of_crash_looping(home, jarvis):
    jarvis.init_project("alpha", template="coding")
    jarvis.remember("alpha", "commands", "테스트", "pytest -q")
    jarvis.store.db.conn.close()
    for f in home.glob("index.db-*"):
        f.unlink()
    (home / "index.db").write_bytes(b"this is not a database at all" * 100)
    fresh = Jarvis(config=Config(home=home))
    assert fresh.store.db.recovered_from.endswith(fresh.store.db.path.name.replace("index.db", "")) or \
        "index.db.corrupt-" in fresh.store.db.recovered_from
    assert list(home.glob("index.db.corrupt-*"))
    assert [m["title"] for m in fresh.memories("alpha")] == ["테스트"]  # 파일에서 재색인
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(str(next(home.glob("index.db.corrupt-*")))).execute("select 1 from sqlite_master")


def test_health_hides_the_home_path_from_unauthenticated_callers(client):
    assert client.get("/health").json()["home"]  # 인증 없는 서버: 보여도 된다
    client.post("/keys", json={"name": "admin"})
    assert client.get("/health").json()["home"] == ""


# --------------------------------------------------------------------------
# 백업에 global/ 포함 (고수 H5)
# --------------------------------------------------------------------------
def test_backup_includes_global_preferences(home, tmp_path, jarvis):
    jarvis.init_project("alpha", template="coding")
    jarvis.remember_about_me("항상 한글로 답한다", title="답변 언어")
    snap = make_snapshot(home, tmp_path)
    with tarfile.open(snap) as tar:
        names = tar.getnames()
    assert any(n.startswith("global/") for n in names), names
    assert "auth.enabled" not in names  # 키를 만든 적이 없으면 marker 도 없다

    jarvis.remember_about_me("영어로 답한다", title="답변 언어")
    restore_snapshot(snap, home)
    fresh = Jarvis(config=Config(home=home))
    prefs = [m for m in fresh.memories("global") if m["title"] == "답변 언어"]
    import json as _j

    assert prefs and "한글" in _j.dumps(prefs[0], ensure_ascii=False)


# --------------------------------------------------------------------------
# 훅: 조용한 유실을 소리내게 (초보 F1/F3, 중급 F4/F5, 고수 M3)
# --------------------------------------------------------------------------
class _RefusingTransport:
    key = "jv_wrong"
    timeout = 4.0

    def request(self, method, path, body=None, params=None):
        raise RuntimeError("MyViking 401: 유효한 API 키가 필요합니다")


def test_hook_says_so_once_when_the_server_refuses_the_key(tmp_path):
    """틀린 키는 서버 다운과 달리 '설정 오류' 다. Claude Code 화면에 한 세션당 한 번은
    보여야 사람이 알아챈다 (errors.log 는 아무도 안 본다)."""
    payload = {"session_id": "s1", "cwd": str(tmp_path)}
    out = run("session-start", payload, _RefusingTransport(), state_dir=tmp_path / "st")
    assert out and "systemMessage" in out and "401" in out["systemMessage"]
    again = run("user-prompt-submit", {**payload, "prompt": "x"}, _RefusingTransport(), state_dir=tmp_path / "st")
    assert again is None  # 같은 세션에서 두 번 잔소리하지 않는다
    other = run("session-start", {"session_id": "s2", "cwd": str(tmp_path)}, _RefusingTransport(),
                state_dir=tmp_path / "st")
    assert other and "systemMessage" in other


class _NoProjectTransport:
    key = "jv_scoped"
    timeout = 4.0

    def request(self, method, path, body=None, params=None):
        if path == "/resolve":
            return {"project": "", "candidates": ["alpha"]}
        raise AssertionError("resolve 가 비면 더 진행하면 안 된다")


def test_hook_outside_key_scope_leaves_a_breadcrumb(tmp_path):
    """범위 밖 저장소에서 훅은 exit 0 · 무출력 · last-ok 갱신 · errors.log 없음 이었다 —
    정확히 '--check 가 잡아야 할 조용한 유실' 이다."""
    st = tmp_path / "st"
    out = run("session-start", {"session_id": "s", "cwd": str(tmp_path)}, _NoProjectTransport(), state_dir=st)
    # A checkout with no remote now also tells the person once, on screen.
    assert out and "systemMessage" in out and "기록되지 않습니다" in out["systemMessage"]
    assert not (st / "last-ok").exists()
    errors = (st / "errors.log").read_text(encoding="utf-8")
    assert "프로젝트를 정하지 못" in errors


class _HealthOnlyTransport:
    """/health 는 공개라 아무 키로도 200 — 키 검증은 /me 로만 가능하다."""

    def __init__(self, key, me_ok):
        self.key, self.me_ok, self.timeout = key, me_ok, 4.0

    def request(self, method, path, body=None, params=None):
        if path == "/health":
            return {"ok": True, "auth_required": True, "version": "x", "projects": 1}
        if path == "/me":
            if self.me_ok:
                return {"admin": False, "name": "지훈-노트북", "projects": ["alpha"]}
            raise RuntimeError("MyViking 401: 유효한 API 키가 필요합니다")
        raise AssertionError(path)


def test_check_verifies_the_key_not_just_reachability(tmp_path):
    ok = health_check(_HealthOnlyTransport("jv_good", True), state_dir=tmp_path)
    assert ok["server_ok"] and ok["key_ok"] is True and ok["me"]["name"] == "지훈-노트북"
    bad = health_check(_HealthOnlyTransport("jv_bad", False), state_dir=tmp_path)
    assert bad["server_ok"] and bad["key_ok"] is False and "거부" in bad["key_error"]
    none = health_check(_HealthOnlyTransport("", True), state_dir=tmp_path)
    assert none["key_ok"] is False and "키 없음" in none["key_error"]


def test_check_cli_exits_nonzero_on_a_rejected_key(client, home, tmp_path, monkeypatch, capsys):
    """초보가 설치 직후 --check 를 돌려 '연결됨 / exit 0' 을 보고 안심했지만 키가 틀려
    모든 훅이 401 이었다."""
    from jarvis import hooks as hooks_mod
    from jarvis.cli import main

    client.post("/keys", json={"name": "admin"})

    class T:
        def __init__(self, url, key, timeout=4.0):
            self.key, self.timeout = key, timeout

        def request(self, method, path, body=None, params=None):
            r = client.request(method, path, json=body, params=params,
                               headers=_hdr(self.key) if self.key else {})
            if r.status_code >= 400:
                raise RuntimeError(f"MyViking {r.status_code}: {r.json().get('detail')}")
            return r.json()

    monkeypatch.setattr(hooks_mod, "HttpTransport", T)
    code = main(["agent", "hooks", "--check", "--url", "http://x", "--key", "jv_wrong",
                 "--path", str(tmp_path), "--state-dir", str(tmp_path / "st")])
    out = capsys.readouterr().out
    assert code == 1 and "거부" in out


def test_remote_health_fails_on_a_bad_key(client, monkeypatch, capsys):
    """셸 전용 에이전트 지시문이 '의심되면 jv remote health' 라고 하는데, 틀린 키에도
    '연결됨 / exit 0' 이었다."""
    from jarvis import cli as cli_mod
    from jarvis.remote import RemoteClient

    admin = client.post("/keys", json={"name": "admin"}).json()["key"]

    class T:
        def __init__(self, key):
            self.key = key

        def request(self, method, path, body=None, params=None):
            r = client.request(method, path, json=body, params=params,
                               headers=_hdr(self.key) if self.key else {})
            if r.status_code >= 400:
                raise RuntimeError(f"MyViking {r.status_code}: {r.json().get('detail')}")
            return r.json()

    def fake_remote(args):
        rc = RemoteClient.__new__(RemoteClient)
        rc.t = T(args.key)
        return rc

    monkeypatch.setattr(cli_mod, "_remote", fake_remote)
    ns = type("A", (), {"key": "jv_wrong", "json": False, "url": "http://x"})()
    assert cli_mod.cmd_remote_health(ns) == 1
    assert "거부" in capsys.readouterr().err
    ns.key = admin
    assert cli_mod.cmd_remote_health(ns) == 0
    assert "확인됨" in capsys.readouterr().out
