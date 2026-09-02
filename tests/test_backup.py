"""서버 밖 백업: 스냅샷·회전·복원, 그리고 Google Drive 클라이언트."""

import json
import tarfile
import urllib.parse

import pytest

from jarvis import Config, Jarvis
from jarvis.backup import (
    BackupManager,
    BackupSettings,
    GoogleDrive,
    LocalFolder,
    make_snapshot,
    restore_snapshot,
)


@pytest.fixture()
def remote_dir(tmp_path):
    return tmp_path / "remote"


@pytest.fixture()
def manager(home, remote_dir, jarvis):
    m = BackupManager(home)
    m.configure(provider="local", local_path=str(remote_dir), keep=2)
    return m


# --------------------------------------------------------------------------
# snapshot / restore
# --------------------------------------------------------------------------
def test_snapshot_and_restore_roundtrip(home, tmp_path, jarvis):
    jarvis.init_project("app", template="coding")
    jarvis.remember("app", "commands", "테스트", "pytest -q 로 돌린다")

    snap = make_snapshot(home, tmp_path)
    assert snap.exists() and snap.name.startswith("myviking-")
    with tarfile.open(snap) as tar:
        names = tar.getnames()
    assert "index.db" in names
    assert any(n.startswith("projects") for n in names)

    # 백업 이후의 변경은 복원하면 사라져야 한다 (그게 복원이다).
    jarvis.remember("app", "commands", "빌드", "make build")
    restore_snapshot(snap, home)

    fresh = Jarvis(config=Config(home=home))
    titles = [m["title"] for m in fresh.memories("app")]
    assert "테스트" in titles and "빌드" not in titles


def test_restore_rejects_a_foreign_archive(home, tmp_path):
    bogus = tmp_path / "myviking-x.tar.gz"
    inner = tmp_path / "readme.txt"
    inner.write_text("not a backup", encoding="utf-8")
    with tarfile.open(bogus, "w:gz") as tar:
        tar.add(inner, arcname="readme.txt")
    with pytest.raises(ValueError):
        restore_snapshot(bogus, home)


def test_snapshot_is_consistent_while_the_db_is_open(home, tmp_path, jarvis):
    """서버가 살아 있는 동안(연결이 열려 있는 동안) 찍어도 읽을 수 있어야 한다."""
    jarvis.init_project("app", template="coding")
    snap = make_snapshot(home, tmp_path)  # jarvis 의 연결이 열려 있는 상태
    import sqlite3

    with tarfile.open(snap) as tar:
        tar.extract("index.db", tmp_path / "out", filter="data")
    conn = sqlite3.connect(str(tmp_path / "out" / "index.db"))
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


# --------------------------------------------------------------------------
# manager: run / rotate / restore / status
# --------------------------------------------------------------------------
def test_run_uploads_and_rotates(manager, remote_dir, monkeypatch):
    # 타임스탬프가 초 단위라 같은 초에 찍히면 이름이 겹친다 — 스탬프를 조작한다.
    import jarvis.backup as bk

    stamps = iter(["20260901T000000Z", "20260901T000001Z", "20260901T000002Z"])

    real = bk.make_snapshot

    def stamped(home, dest):
        p = real(home, dest)
        renamed = p.with_name(f"myviking-{next(stamps)}.tar.gz")
        p.rename(renamed)
        return renamed

    monkeypatch.setattr(bk, "make_snapshot", stamped)

    for _ in range(3):
        manager.run()
    names = [f["name"] for f in manager.list_remote()]
    # keep=2: 최신 둘만 남고 가장 오래된 것은 지워졌다.
    assert names == [
        "myviking-20260901T000002Z.tar.gz",
        "myviking-20260901T000001Z.tar.gz",
    ]

    st = manager.status()
    assert st["last_status"] == "ok"
    assert st["last_name"] == "myviking-20260901T000002Z.tar.gz"
    assert st["last_size"] > 0
    assert st["due"] is False  # 방금 돌았으니


def test_manager_restore_pulls_latest(manager, home, jarvis):
    jarvis.init_project("app", template="coding")
    jarvis.remember("app", "commands", "테스트", "pytest -q")
    manager.run()
    jarvis.remember("app", "commands", "빌드", "make build")

    res = manager.restore()
    assert res["restored"].startswith("myviking-")
    fresh = Jarvis(config=Config(home=home))
    titles = [m["title"] for m in fresh.memories("app")]
    assert "테스트" in titles and "빌드" not in titles


def test_failed_run_is_recorded_not_silent(home, jarvis, tmp_path):
    m = BackupManager(home)
    m.configure(provider="local", local_path="")  # 경로 없는 local → 실패해야 함
    with pytest.raises(RuntimeError):
        m.run()

    # 업로드가 죽어도 last_status 에 남는다 — 조용히 안 도는 백업이 최악이다.
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    m.configure(provider="local", local_path=str(blocker / "sub"))  # 부모가 파일
    with pytest.raises(Exception):
        m.run()
    assert m.status()["last_status"].startswith("error")


def test_due_schedule(home):
    s = BackupSettings()
    assert s.due() is False  # provider none 이면 절대 돌지 않는다
    s.provider = "local"
    assert s.due() is True  # 한 번도 안 돌았으면 지금
    s.last_run = "2026-01-01T00:00:00+00:00"
    assert s.due() is True  # 오래됐으면 지금
    from datetime import datetime, timezone

    s.last_run = datetime.now(timezone.utc).isoformat()
    assert s.due() is False


def test_settings_file_is_private(home, manager):
    import stat

    mode = (home / "backup.yaml").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600


# --------------------------------------------------------------------------
# Google Drive 클라이언트 (가짜 HTTP 로 전 구간)
# --------------------------------------------------------------------------
class FakeGoogle:
    """디바이스 플로우·토큰 갱신·파일 API 를 흉내내는 HTTP 함수."""

    def __init__(self):
        self.approved = False
        self.files = {}  # id -> {name, size, parents}
        self.next_id = 0
        self.access = "tok-1"
        self.expired_once = False

    def __call__(self, method, url, headers=None, data=None, timeout=None):
        if url.endswith("/device/code"):
            return 200, json.dumps({
                "device_code": "dev-1", "user_code": "ABCD-EFGH",
                "verification_url": "https://google.com/device",
                "interval": 1, "expires_in": 600,
            }).encode()
        if url.endswith("/token"):
            form = urllib.parse.parse_qs((data or b"").decode())
            if form.get("grant_type") == ["urn:ietf:params:oauth:grant-type:device_code"]:
                if not self.approved:
                    return 428, json.dumps({"error": "authorization_pending"}).encode()
                return 200, json.dumps({
                    "access_token": self.access, "refresh_token": "refresh-1",
                    "expires_in": 3600,
                }).encode()
            return 200, json.dumps({"access_token": self.access, "expires_in": 3600}).encode()

        # Drive API — 토큰 검사 (만료 시나리오 지원)
        auth = (headers or {}).get("Authorization", "")
        if auth != f"Bearer {self.access}":
            return 401, b"{}"

        if "upload/drive/v3/files" in url:
            self.next_id += 1
            fid = f"f{self.next_id}"
            body = data or b""
            meta = json.loads(body.split(b"\r\n\r\n")[1].split(b"\r\n--")[0])
            self.files[fid] = {"name": meta["name"], "size": len(body),
                               "parents": meta.get("parents", [])}
            return 200, json.dumps({"id": fid}).encode()
        if method == "GET" and "/drive/v3/files?" in url:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("q", [""])[0]
            if "folder" in q:
                found = [
                    {"id": fid, "name": f["name"]}
                    for fid, f in self.files.items()
                    if f.get("folder") and f["name"] in q
                ]
                return 200, json.dumps({"files": found}).encode()
            found = [
                {"id": fid, "name": f["name"], "size": str(f["size"])}
                for fid, f in self.files.items()
                if f["name"].startswith("myviking-")
            ]
            return 200, json.dumps({"files": found}).encode()
        if method == "POST" and url.startswith("https://www.googleapis.com/drive/v3/files"):
            meta = json.loads((data or b"{}").decode())
            self.next_id += 1
            fid = f"f{self.next_id}"
            self.files[fid] = {"name": meta["name"], "size": 0, "folder": True}
            return 200, json.dumps({"id": fid}).encode()
        if method == "DELETE":
            fid = url.rstrip("/").split("/")[-1]
            self.files.pop(fid, None)
            return 204, b""
        if method == "GET" and "alt=media" in url:
            return 200, b"content"
        return 404, b"{}"


def test_device_flow_lands_a_refresh_token(home):
    fake = FakeGoogle()
    m = BackupManager(home, http=fake)
    info = m.connect_start("cid", "sec")
    assert info["user_code"] == "ABCD-EFGH"
    assert "google.com/device" in info["verification_url"]

    assert m.connect_poll()["status"] == "pending"
    fake.approved = True
    assert m.connect_poll()["status"] == "ok"

    s = m.settings()
    assert s.provider == "gdrive"
    assert s.gdrive["refresh_token"] == "refresh-1"
    assert "_device_code" not in s.gdrive  # 임시 코드는 지워진다


def test_gdrive_upload_creates_folder_and_survives_token_expiry(home, tmp_path, jarvis):
    fake = FakeGoogle()
    m = BackupManager(home, http=fake)
    m.connect_start("cid", "sec")
    fake.approved = True
    m.connect_poll()

    jarvis.init_project("app", template="coding")
    res = m.run()
    assert res["name"].startswith("myviking-")
    names = [f["name"] for f in fake.files.values()]
    assert "MyViking-Backups" in names  # 폴더가 만들어졌고
    assert any(n.startswith("myviking-") for n in names)  # 그 안에 올라갔다

    # 액세스 토큰이 만료돼도 (401) 갱신 후 재시도로 살아난다.
    fake.access = "tok-2"
    assert [f["name"] for f in m.list_remote()]


def test_gdrive_poll_reports_denial(home):
    fake = FakeGoogle()

    def denying(method, url, headers=None, data=None, timeout=None):
        if url.endswith("/token"):
            return 403, json.dumps({"error": "access_denied"}).encode()
        return fake(method, url, headers=headers, data=data)

    m = BackupManager(home, http=denying)
    m.connect_start("cid", "sec")
    res = m.connect_poll()
    assert res == {"status": "error", "error": "access_denied"}


# --------------------------------------------------------------------------
# HTTP API
# --------------------------------------------------------------------------
def test_backup_endpoints(home, remote_dir, jarvis):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from jarvis.server import create_app

    client = TestClient(create_app(home=str(home)))
    st = client.get("/backup/status").json()
    assert st["provider"] == "none" and st["connected"] is False

    client.post("/backup/config", json={"provider": "local", "local_path": str(remote_dir)})
    run = client.post("/backup/run")
    assert run.status_code == 200
    assert run.json()["name"].startswith("myviking-")
    assert len(client.get("/backup/list").json()) == 1
    assert client.get("/backup/status").json()["last_status"] == "ok"


def test_backup_endpoints_require_key_once_auth_is_on(home):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from jarvis.server import create_app

    client = TestClient(create_app(home=str(home)))
    made = client.post("/keys", json={"name": "k"}).json()
    # 백업 설정에는 OAuth 시크릿이 들어간다 — 키 없이 보이면 안 된다.
    assert client.get("/backup/status").status_code == 401
    ok = client.get("/backup/status", headers={"authorization": f"Bearer {made['key']}"})
    assert ok.status_code == 200
