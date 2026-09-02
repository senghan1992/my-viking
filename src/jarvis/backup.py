"""Off-site backups — the copy that survives losing the volume.

The compose volume already survives ``docker compose down && up``; what it does
not survive is the volume being deleted or the host dying, and by then the
store holds months of accumulated project knowledge. So the server ships its
own backup loop: snapshot → upload → rotate, on a schedule, to a remote you
connect *after* the service is already running.

Two remotes:

* **gdrive** — a personal Google Drive, connected with the OAuth *device*
  flow: the server shows a code, you approve it on any browser, done. No
  redirect URL, no browser on the server, and — deliberately — no Google SDK:
  the whole exchange is a handful of form POSTs, so the core dependency set
  (PyYAML alone) stays intact. The ``drive.file`` scope only reaches files this
  app created, so a leaked token cannot read the rest of the Drive.
* **local** — a directory, for people who mount their own network share.

Snapshots are consistent while the server is running: the SQLite backup API
copies the index mid-write safely, and the content files ride along best-effort
(they are re-derivable from the index via ``jv reindex`` in the worst case).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from .config import jarvis_home

SETTINGS_NAME = "backup.yaml"
SNAPSHOT_PREFIX = "myviking-"

GOOGLE_DEVICE_URL = "https://oauth2.googleapis.com/device/code"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_API = "https://www.googleapis.com/drive/v3"
GOOGLE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
# drive.file sees only what this app created. A backup token that could read
# someone's whole Drive would turn a convenience into a liability.
GOOGLE_SCOPE = "https://www.googleapis.com/auth/drive.file"

Http = Callable[..., tuple[int, bytes]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def folder_url(folder_id: str) -> str:
    """The folder's address in the Drive web UI — so a person can open the
    destination and see the archives with their own eyes. Trust needs a URL."""
    return f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else ""


def _urllib_http(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 120.0,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return int(res.getcode() or 0), res.read()
    except urllib.error.HTTPError as exc:
        # API errors carry their explanation in the body; surface it, don't raise.
        return int(exc.code), exc.read()


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------
@dataclass
class BackupSettings:
    provider: str = "none"  # none | gdrive | local
    every_hours: float = 12.0
    keep: int = 14
    folder: str = "MyViking-Backups"
    local_path: str = ""
    # client_id / client_secret / refresh_token / folder_id
    gdrive: dict[str, str] = field(default_factory=dict)
    last_run: str = ""
    last_status: str = ""
    last_name: str = ""
    last_size: int = 0

    @classmethod
    def load(cls, home: Path) -> "BackupSettings":
        path = home / SETTINGS_NAME
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, home: Path) -> Path:
        home.mkdir(parents=True, exist_ok=True)
        path = home / SETTINGS_NAME
        path.write_text(
            yaml.safe_dump(asdict(self), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        # Holds OAuth secrets: nobody but the service user gets to read it.
        os.chmod(path, 0o600)
        return path

    def due(self) -> bool:
        if self.provider == "none" or self.every_hours <= 0:
            return False
        if not self.last_run:
            return True
        try:
            last = datetime.fromisoformat(self.last_run)
        except ValueError:
            return True
        age = (datetime.now(timezone.utc) - last).total_seconds()
        return age >= self.every_hours * 3600


# --------------------------------------------------------------------------
# snapshot
# --------------------------------------------------------------------------
def make_snapshot(home: Path, dest_dir: Path) -> Path:
    """One self-contained archive of everything under ``home``.

    The SQLite file is copied with the backup API rather than ``shutil`` — a
    plain copy of a database mid-transaction is silently corrupt, and this
    runs inside a live server.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = Path(dest_dir) / f"{SNAPSHOT_PREFIX}{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(dir=dest_dir) as td:
        db_copy = Path(td) / "index.db"
        live = home / "index.db"
        if live.exists():
            src = sqlite3.connect(str(live))
            dst = sqlite3.connect(str(db_copy))
            try:
                with dst:
                    src.backup(dst)
            finally:
                src.close()
                dst.close()
        with tarfile.open(dest, "w:gz") as tar:
            if db_copy.exists():
                tar.add(db_copy, arcname="index.db")
            for item in ("jarvis.yaml", SETTINGS_NAME, "projects"):
                p = home / item
                if p.exists():
                    tar.add(p, arcname=item)
    return dest


def restore_snapshot(archive: Path, home: Path) -> None:
    """Replace ``home`` with the snapshot's contents.

    Extracts fully before touching anything, so a truncated download cannot
    leave the store half-replaced. A running server keeps serving its old,
    already-open database until restarted — restore, then restart.
    """
    home.mkdir(parents=True, exist_ok=True)
    staging = home / "_restore-tmp"  # inside the volume: /data is the writable disk
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        with tarfile.open(archive) as tar:
            tar.extractall(staging, filter="data")
        if not (staging / "index.db").exists():
            raise ValueError("index.db 가 없는 아카이브입니다 — MyViking 백업이 아닙니다")
        for stale in ("index.db-wal", "index.db-shm"):
            (home / stale).unlink(missing_ok=True)
        for item in ("index.db", "jarvis.yaml", SETTINGS_NAME):
            src = staging / item
            if src.exists():
                shutil.move(str(src), str(home / item))
        if (staging / "projects").exists():
            shutil.rmtree(home / "projects", ignore_errors=True)
            shutil.move(str(staging / "projects"), str(home / "projects"))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# --------------------------------------------------------------------------
# remotes
# --------------------------------------------------------------------------
class LocalFolder:
    """A directory remote — a mounted NAS/host path, and the test double."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def upload(self, file: Path, name: str) -> str:
        self.path.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, self.path / name)
        return name

    def list(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = [
            {"id": f.name, "name": f.name, "size": f.stat().st_size}
            for f in self.path.glob(f"{SNAPSHOT_PREFIX}*.tar.gz")
        ]
        return sorted(out, key=lambda d: d["name"], reverse=True)

    def delete(self, file_id: str) -> None:
        (self.path / file_id).unlink(missing_ok=True)

    def download(self, file_id: str, dest: Path) -> Path:
        shutil.copyfile(self.path / file_id, dest)
        return dest


class GoogleDrive:
    """Google Drive over plain HTTP: device-flow auth, upload, list, delete.

    ``creds`` is the mutable dict from :class:`BackupSettings` — token refreshes
    land back in it, and ``on_change`` persists them.
    """

    def __init__(
        self,
        creds: dict[str, str],
        on_change: Callable[[], None] | None = None,
        http: Http | None = None,
    ):
        self.creds = creds
        self.on_change = on_change or (lambda: None)
        self.http = http or _urllib_http
        self._access = ""
        self._access_until = 0.0

    # ----- device flow --------------------------------------------------
    def device_start(self) -> dict[str, Any]:
        status, body = self.http(
            "POST",
            GOOGLE_DEVICE_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=urllib.parse.urlencode(
                {"client_id": self.creds.get("client_id", ""), "scope": GOOGLE_SCOPE}
            ).encode(),
        )
        data = _jsonb(body)
        if status != 200:
            raise RuntimeError(f"기기 코드 발급 실패: {data.get('error', status)}")
        # device_code stays server-side; the person only ever sees user_code.
        # The code and URL are kept too, so a dashboard reloaded mid-flow can
        # show them again instead of forcing a restart.
        self.creds["_device_code"] = data["device_code"]
        self.creds["_poll_interval"] = str(data.get("interval", 5))
        self.creds["_user_code"] = data["user_code"]
        self.creds["_verification_url"] = (
            data.get("verification_url") or data.get("verification_uri", "")
        )
        self.on_change()
        return {
            "user_code": data["user_code"],
            "verification_url": self.creds["_verification_url"],
            "expires_in": data.get("expires_in", 1800),
            "interval": data.get("interval", 5),
        }

    def device_poll(self) -> dict[str, Any]:
        device_code = self.creds.get("_device_code", "")
        if not device_code:
            return {"status": "error", "error": "진행 중인 연결이 없습니다"}
        status, body = self.http(
            "POST",
            GOOGLE_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=urllib.parse.urlencode(
                {
                    "client_id": self.creds.get("client_id", ""),
                    "client_secret": self.creds.get("client_secret", ""),
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                }
            ).encode(),
        )
        data = _jsonb(body)
        if status == 200 and data.get("refresh_token"):
            self.creds["refresh_token"] = data["refresh_token"]
            self._drop_pending()
            self._access = data.get("access_token", "")
            self._access_until = time.time() + float(data.get("expires_in", 0)) - 60
            self.on_change()
            return {"status": "ok"}
        err = data.get("error", f"http {status}")
        if err in ("authorization_pending", "slow_down"):
            return {"status": "pending", "error": err}
        self._drop_pending()
        self.on_change()
        return {"status": "error", "error": err}

    def _drop_pending(self) -> None:
        for key in ("_device_code", "_poll_interval", "_user_code", "_verification_url"):
            self.creds.pop(key, None)

    def about(self) -> dict[str, Any]:
        """Whose Drive this token reaches — shown so the person can verify the
        connection landed on the account they meant."""
        data = self._api("GET", f"{GOOGLE_API}/about?fields=user")
        return data.get("user") or {}

    # ----- tokens --------------------------------------------------------
    def _token(self, force: bool = False) -> str:
        if not force and self._access and time.time() < self._access_until:
            return self._access
        refresh = self.creds.get("refresh_token", "")
        if not refresh:
            raise RuntimeError("Google Drive 가 연결되지 않았습니다 (refresh_token 없음)")
        status, body = self.http(
            "POST",
            GOOGLE_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=urllib.parse.urlencode(
                {
                    "client_id": self.creds.get("client_id", ""),
                    "client_secret": self.creds.get("client_secret", ""),
                    "refresh_token": refresh,
                    "grant_type": "refresh_token",
                }
            ).encode(),
        )
        data = _jsonb(body)
        if status != 200 or "access_token" not in data:
            raise RuntimeError(f"토큰 갱신 실패: {data.get('error', status)}")
        self._access = data["access_token"]
        self._access_until = time.time() + float(data.get("expires_in", 3600)) - 60
        return self._access

    def _api(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": content_type,
        }
        status, body = self.http(method, url, headers=headers, data=data)
        if status == 401:
            # One retry on a freshly forced token: access tokens expire midway
            # through long-running servers and that is routine, not an error.
            headers["Authorization"] = f"Bearer {self._token(force=True)}"
            status, body = self.http(method, url, headers=headers, data=data)
        if status >= 400:
            raise RuntimeError(f"Drive API 오류 {status}: {body[:200].decode('utf-8', 'replace')}")
        return _jsonb(body)

    # ----- files ----------------------------------------------------------
    def ensure_folder(self, name: str) -> str:
        cached = self.creds.get("folder_id", "")
        if cached:
            return cached
        safe = name.replace("'", "\\'")
        q = urllib.parse.quote(
            f"name='{safe}' and mimeType='application/vnd.google-apps.folder'"
            " and trashed=false"
        )
        found = self._api("GET", f"{GOOGLE_API}/files?q={q}&fields=files(id,name)")
        files = found.get("files", [])
        if files:
            folder_id = files[0]["id"]
        else:
            made = self._api(
                "POST",
                f"{GOOGLE_API}/files?fields=id",
                data=json.dumps(
                    {"name": name, "mimeType": "application/vnd.google-apps.folder"}
                ).encode(),
            )
            folder_id = made["id"]
        self.creds["folder_id"] = folder_id
        self.on_change()
        return folder_id

    def upload(self, file: Path, name: str) -> str:
        folder_id = self.creds.get("folder_id") or ""
        meta: dict[str, Any] = {"name": name}
        if folder_id:
            meta["parents"] = [folder_id]
        boundary = f"jvb{uuid.uuid4().hex}"
        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(meta)}\r\n"
            f"--{boundary}\r\n"
            "Content-Type: application/gzip\r\n\r\n"
        ).encode() + file.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        made = self._api(
            "POST",
            GOOGLE_UPLOAD + "&fields=id",
            data=body,
            content_type=f"multipart/related; boundary={boundary}",
        )
        return str(made.get("id", ""))

    def list(self) -> list[dict[str, Any]]:
        folder_id = self.creds.get("folder_id") or ""
        terms = ["trashed=false", f"name contains '{SNAPSHOT_PREFIX}'"]
        if folder_id:
            terms.append(f"'{folder_id}' in parents")
        q = urllib.parse.quote(" and ".join(terms))
        data = self._api(
            "GET",
            f"{GOOGLE_API}/files?q={q}&fields=files(id,name,size)&pageSize=100",
        )
        out = [
            {"id": f["id"], "name": f["name"], "size": int(f.get("size") or 0)}
            for f in data.get("files", [])
        ]
        return sorted(out, key=lambda d: d["name"], reverse=True)

    def delete(self, file_id: str) -> None:
        self._api("DELETE", f"{GOOGLE_API}/files/{urllib.parse.quote(file_id)}")

    def download(self, file_id: str, dest: Path) -> Path:
        headers = {"Authorization": f"Bearer {self._token()}"}
        status, body = self.http(
            "GET", f"{GOOGLE_API}/files/{urllib.parse.quote(file_id)}?alt=media",
            headers=headers,
        )
        if status == 401:
            headers["Authorization"] = f"Bearer {self._token(force=True)}"
            status, body = self.http(
                "GET",
                f"{GOOGLE_API}/files/{urllib.parse.quote(file_id)}?alt=media",
                headers=headers,
            )
        if status >= 400:
            raise RuntimeError(f"다운로드 실패 {status}")
        dest.write_bytes(body)
        return dest


def _jsonb(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# --------------------------------------------------------------------------
# manager
# --------------------------------------------------------------------------
class BackupManager:
    """Settings, schedule and the run itself, behind one object.

    Settings are re-read from disk on every operation so the CLI inside the
    container and the server's own loop see each other's changes without a
    restart.
    """

    def __init__(self, home: Path | str | None = None, http: Http | None = None):
        self.home = Path(home) if home else jarvis_home()
        self.http = http

    def settings(self) -> BackupSettings:
        return BackupSettings.load(self.home)

    def configure(self, **changes: Any) -> BackupSettings:
        s = self.settings()
        for key, value in changes.items():
            if value is None or not hasattr(s, key):
                continue
            setattr(s, key, value)
        s.save(self.home)
        return s

    def _remote(self, s: BackupSettings):
        if s.provider == "local":
            if not s.local_path:
                raise RuntimeError("local 백업 경로가 설정되지 않았습니다 (jv backup config --path)")
            return LocalFolder(Path(s.local_path))
        if s.provider == "gdrive":
            def persist() -> None:
                s.save(self.home)

            return GoogleDrive(s.gdrive, on_change=persist, http=self.http)
        raise RuntimeError("백업 대상이 설정되지 않았습니다 (jv backup config --provider)")

    # ----- google drive connection ---------------------------------------
    def connect_start(self, client_id: str, client_secret: str) -> dict[str, Any]:
        if not client_id.strip() or not client_secret.strip():
            raise RuntimeError("client_id 와 client_secret 이 필요합니다")
        s = self.settings()
        s.gdrive["client_id"] = client_id.strip()
        s.gdrive["client_secret"] = client_secret.strip()
        s.gdrive.pop("refresh_token", None)
        s.gdrive.pop("folder_id", None)
        s.save(self.home)
        drive = GoogleDrive(s.gdrive, on_change=lambda: s.save(self.home), http=self.http)
        return drive.device_start()

    def connect_poll(self) -> dict[str, Any]:
        s = self.settings()
        drive = GoogleDrive(s.gdrive, on_change=lambda: s.save(self.home), http=self.http)
        result = drive.device_poll()
        if result["status"] == "ok":
            s.provider = "gdrive"
            s.save(self.home)
            # Resolve the destination *now*, not at the first scheduled run:
            # "어디로 연결됐나" is the first thing the person will ask, and the
            # answer should be on screen the moment the flow finishes.
            try:
                drive.ensure_folder(s.folder)
                user = drive.about()
                if user.get("emailAddress"):
                    s.gdrive["account_email"] = str(user["emailAddress"])
                    s.save(self.home)
            except Exception:
                pass  # enrichment only — the connection itself already stands
            result = {
                **result,
                "account": s.gdrive.get("account_email", ""),
                "folder": s.folder,
                "folder_url": folder_url(s.gdrive.get("folder_id", "")),
            }
        return result

    def disconnect(self) -> dict[str, Any]:
        """Stop backing up and forget the Drive token. Archives already
        uploaded stay where they are — this only severs the connection.
        (``local_path`` is kept: pointing at the directory again is one
        config call, and the path holds no secret.)"""
        s = self.settings()
        s.gdrive = {}
        s.provider = "none"
        s.save(self.home)
        return self.status()

    # ----- the run ---------------------------------------------------------
    def run(self) -> dict[str, Any]:
        s = self.settings()
        remote = self._remote(s)
        if isinstance(remote, GoogleDrive):
            remote.ensure_folder(s.folder)
        try:
            with tempfile.TemporaryDirectory() as td:
                snap = make_snapshot(self.home, Path(td))
                size = snap.stat().st_size
                remote.upload(snap, snap.name)
                name = snap.name
            deleted = self._rotate(remote, s.keep)
        except Exception as exc:
            s = self.settings()  # 실패도 기록: 조용히 안 도는 백업이 최악이다
            s.last_run = _now_iso()
            s.last_status = f"error: {type(exc).__name__}: {exc}"
            s.save(self.home)
            raise
        s = self.settings()
        s.last_run = _now_iso()
        s.last_status = "ok"
        s.last_name = name
        s.last_size = size
        s.save(self.home)
        return {"name": name, "size": size, "deleted": deleted}

    def _rotate(self, remote: Any, keep: int) -> list[str]:
        if keep <= 0:
            return []
        files = remote.list()  # already newest-first by name (UTC timestamps sort)
        stale = files[keep:]
        for f in stale:
            remote.delete(f["id"])
        return [f["name"] for f in stale]

    def run_if_due(self) -> dict[str, Any] | None:
        return self.run() if self.settings().due() else None

    # ----- reads / restore ---------------------------------------------------
    def list_remote(self) -> list[dict[str, Any]]:
        s = self.settings()
        remote = self._remote(s)
        if isinstance(remote, GoogleDrive):
            remote.ensure_folder(s.folder)
        return remote.list()

    def restore(self, name: str = "") -> dict[str, Any]:
        s = self.settings()
        remote = self._remote(s)
        if isinstance(remote, GoogleDrive):
            remote.ensure_folder(s.folder)
        files = remote.list()
        if not files:
            raise RuntimeError("원격에 백업이 없습니다")
        chosen = files[0] if not name else next(
            (f for f in files if f["name"] == name), None
        )
        if chosen is None:
            raise RuntimeError(f"'{name}' 백업을 찾지 못했습니다")
        with tempfile.TemporaryDirectory(dir=self.home) as td:
            archive = remote.download(chosen["id"], Path(td) / chosen["name"])
            restore_snapshot(archive, self.home)
        return {"restored": chosen["name"], "size": chosen.get("size", 0)}

    def status(self) -> dict[str, Any]:
        s = self.settings()
        return {
            "provider": s.provider,
            "connected": s.provider == "local"
            or bool(s.gdrive.get("refresh_token")),
            "pending_auth": bool(s.gdrive.get("_device_code")),
            # A flow interrupted mid-approval (dashboard reloaded, terminal
            # closed) can resume: re-show the same code instead of restarting.
            "pending_user_code": s.gdrive.get("_user_code", ""),
            "pending_verification_url": s.gdrive.get("_verification_url", ""),
            # Where exactly the archives land — account and a clickable folder.
            "account": s.gdrive.get("account_email", ""),
            "folder_id": s.gdrive.get("folder_id", ""),
            "folder_url": folder_url(s.gdrive.get("folder_id", "")),
            "every_hours": s.every_hours,
            "keep": s.keep,
            "folder": s.folder,
            "local_path": s.local_path,
            "last_run": s.last_run,
            "last_status": s.last_status,
            "last_name": s.last_name,
            "last_size": s.last_size,
            "due": s.due(),
        }


def start_backup_loop(home: str | None, check_every: float = 900.0) -> None:
    """Run due backups inside the server process — one container, no cron.

    A fresh manager per tick picks up settings changed through the UI or the
    CLI without a restart. Failures are recorded in ``last_status`` and
    printed, never raised: the server must outlive its backup remote.
    """
    if check_every <= 0:
        return
    import threading

    def loop() -> None:
        while True:
            time.sleep(check_every)
            try:
                result = BackupManager(home).run_if_due()
                if result:
                    print(
                        f"[backup] {result['name']} ({result['size']}B)"
                        + (f" · 회전 {len(result['deleted'])}건" if result["deleted"] else ""),
                        flush=True,
                    )
            except Exception as exc:  # pragma: no cover - best effort
                print(f"[backup] 실패: {type(exc).__name__}: {exc}", flush=True)

    threading.Thread(target=loop, name="jarvis-backup", daemon=True).start()
