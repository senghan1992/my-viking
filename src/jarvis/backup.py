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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from .config import jarvis_home

SETTINGS_NAME = "backup.yaml"
KeyStoreMarker = "auth.enabled"  # see auth.KeyStore.MARKER — restored with the DB

SNAPSHOT_PREFIX = "myviking-"

GOOGLE_DEVICE_URL = "https://oauth2.googleapis.com/device/code"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_API = "https://www.googleapis.com/drive/v3"
GOOGLE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id"
# drive.file sees only what this app created. A backup token that could read
# someone's whole Drive would turn a convenience into a liability.
GOOGLE_SCOPE = "https://www.googleapis.com/auth/drive.file"

# Uploads go in slices of this size, so a multi-hundred-MB store never has to
# fit in memory alongside the server. Google requires multiples of 256 KiB.
UPLOAD_CHUNK = 8 * 1024 * 1024

# (status, body, response-headers). Headers matter: the resumable-upload
# session URI arrives in ``Location``.
Http = Callable[..., tuple[int, bytes, dict[str, str]]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# A heartbeat fresher than this means a server process is holding the index
# open right now. Comfortably longer than the heartbeat interval so an
# in-flight tick never reads as "down".
LIVE_WINDOW_SECONDS = 120.0


def server_heartbeat_age(home: Path) -> float | None:
    """Seconds since the running server last stamped its liveness, or None.

    Read from the index directly (read-only, no Store) so it works from the
    CLI without opening the store we may be about to replace. None means no
    heartbeat was ever written or it cannot be read — treated as "not live"."""
    db = Path(home) / "index.db"
    if not db.exists():
        return None
    # A normal (read-write-capable) connection, deliberately: SQLite cannot open
    # a WAL-mode database read-only — it needs to write the -shm index — and a
    # live server always runs in WAL. Opening ro would raise, be swallowed as
    # "not live", and the guard would never fire. We only ever SELECT here.
    try:
        conn = sqlite3.connect(str(db), timeout=2.0)
        try:
            row = conn.execute(
                "SELECT v FROM meta WHERE k='server_heartbeat'"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    try:
        beat = datetime.fromisoformat(row[0])
    except (ValueError, TypeError):
        return None
    return (datetime.now(timezone.utc) - beat).total_seconds()


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
) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return int(res.getcode() or 0), res.read(), {
                k.lower(): v for k, v in res.headers.items()
            }
    except urllib.error.HTTPError as exc:
        # API errors carry their explanation in the body; surface it, don't raise.
        return int(exc.code), exc.read(), {k.lower(): v for k, v in exc.headers.items()}


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
            # global/ holds the cross-project preferences — a restore without
            # it leaves ghost index rows pointing at files that no longer exist.
            for item in ("jarvis.yaml", SETTINGS_NAME, KeyStoreMarker, "projects", "global"):
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
        for item in ("index.db", "jarvis.yaml", SETTINGS_NAME, KeyStoreMarker):
            src = staging / item
            if src.exists():
                shutil.move(str(src), str(home / item))
        for tree in ("projects", "global"):
            if (staging / tree).exists():
                shutil.rmtree(home / tree, ignore_errors=True)
                shutil.move(str(staging / tree), str(home / tree))
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
        # Copy to a sidecar then rename into place: os.replace is atomic on the
        # same filesystem, so an interrupted copy (server killed mid-write) can
        # never leave a half-written archive that ``list`` would offer for
        # restore. The rotation that follows must not see a truncated ``.part``.
        dest = self.path / name
        tmp = self.path / f"{name}.part"
        try:
            shutil.copyfile(file, tmp)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
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
        status, body, _hdrs = self.http(
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
        status, body, _hdrs = self.http(
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
        status, body, _hdrs = self.http(
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
        status, body, hdrs = self.http(method, url, headers=headers, data=data)
        if status == 401:
            # One retry on a freshly forced token: access tokens expire midway
            # through long-running servers and that is routine, not an error.
            headers["Authorization"] = f"Bearer {self._token(force=True)}"
            status, body, hdrs = self.http(method, url, headers=headers, data=data)
        if status >= 400:
            raise RuntimeError(f"Drive API 오류 {status}: {body[:200].decode('utf-8', 'replace')}")
        out = _jsonb(body)
        # The resumable-upload handshake answers with an empty body and the
        # session URI in Location; smuggle it through on a reserved key.
        location = hdrs.get("location") or hdrs.get("Location", "")
        if location:
            out["_location"] = location
        return out

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
        """Resumable upload, one bounded chunk at a time.

        A multipart body would hold the whole archive in memory next to the
        server; resumable streams it in ``UPLOAD_CHUNK`` slices, and a chunk
        interrupted mid-flight only costs that chunk.
        """
        folder_id = self.creds.get("folder_id") or ""
        meta: dict[str, Any] = {"name": name}
        if folder_id:
            meta["parents"] = [folder_id]
        made = self._api("POST", GOOGLE_UPLOAD, data=json.dumps(meta).encode())
        session_uri = str(made.get("_location", ""))
        if not session_uri:
            raise RuntimeError("업로드 세션 URI 를 받지 못했습니다")

        total = file.stat().st_size
        sent = 0
        with file.open("rb") as fh:
            while sent < total:
                chunk = fh.read(UPLOAD_CHUNK)
                end = sent + len(chunk) - 1
                headers = {
                    "Authorization": f"Bearer {self._token()}",
                    "Content-Range": f"bytes {sent}-{end}/{total}",
                }
                status, body, hdrs = self.http("PUT", session_uri, headers=headers, data=chunk)
                if status == 401:
                    headers["Authorization"] = f"Bearer {self._token(force=True)}"
                    status, body, hdrs = self.http(
                        "PUT", session_uri, headers=headers, data=chunk
                    )
                if status in (200, 201):
                    return str(_jsonb(body).get("id", ""))
                if status == 308:  # resume incomplete: the server says how far it got
                    rng = hdrs.get("range", "")
                    sent = int(rng.rsplit("-", 1)[-1]) + 1 if "-" in rng else end + 1
                    continue
                raise RuntimeError(
                    f"업로드 실패 {status}: {body[:200].decode('utf-8', 'replace')}"
                )
        raise RuntimeError("업로드가 완료 응답 없이 끝났습니다")

    def list(self) -> list[dict[str, Any]]:
        folder_id = self.creds.get("folder_id") or ""
        terms = ["trashed=false", f"name contains '{SNAPSHOT_PREFIX}'"]
        if folder_id:
            terms.append(f"'{folder_id}' in parents")
        q = urllib.parse.quote(" and ".join(terms))
        # Page through every result: a page cap of 100 would silently hide the
        # oldest archives once the folder passed that many, and rotation would
        # then never delete them — the store's off-site footprint would grow
        # without bound exactly when ``keep`` was meant to stop it.
        out: list[dict[str, Any]] = []
        page = ""
        while True:
            url = f"{GOOGLE_API}/files?q={q}&fields=nextPageToken,files(id,name,size)&pageSize=100"
            if page:
                url += f"&pageToken={urllib.parse.quote(page)}"
            data = self._api("GET", url)
            out.extend(
                {"id": f["id"], "name": f["name"], "size": int(f.get("size") or 0)}
                for f in data.get("files", [])
            )
            page = data.get("nextPageToken") or ""
            if not page:
                break
        return sorted(out, key=lambda d: d["name"], reverse=True)

    def delete(self, file_id: str) -> None:
        self._api("DELETE", f"{GOOGLE_API}/files/{urllib.parse.quote(file_id)}")

    def download(self, file_id: str, dest: Path) -> Path:
        headers = {"Authorization": f"Bearer {self._token()}"}
        status, body, _hdrs = self.http(
            "GET", f"{GOOGLE_API}/files/{urllib.parse.quote(file_id)}?alt=media",
            headers=headers,
        )
        if status == 401:
            headers["Authorization"] = f"Bearer {self._token(force=True)}"
            status, body, _hdrs = self.http(
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
            # Inside the volume, not /tmp: in the container /tmp is a tmpfs, and
            # a full SQLite copy + tarball of a large store would live in RAM.
            with tempfile.TemporaryDirectory(dir=self.home, prefix="_backup-tmp-") as td:
                snap = make_snapshot(self.home, Path(td))
                size = snap.stat().st_size
                remote.upload(snap, snap.name)
                name = snap.name
        except Exception as exc:
            s = self.settings()  # 실패도 기록: 조용히 안 도는 백업이 최악이다
            s.last_run = _now_iso()
            s.last_status = f"error: {type(exc).__name__}: {exc}"
            s.save(self.home)
            raise
        # The archive is safely uploaded now. Rotation is separate housekeeping:
        # if pruning old copies fails, the backup itself still succeeded, and
        # recording it as an error would hide a good snapshot and re-run it
        # needlessly. Keep the extra copies and note the rotation problem.
        deleted: list[str] = []
        rotate_error = ""
        try:
            deleted = self._rotate(remote, s.keep)
        except Exception as exc:  # noqa: BLE001 — rotation must not fail a good backup
            rotate_error = f"{type(exc).__name__}: {exc}"
        s = self.settings()
        s.last_run = _now_iso()
        s.last_status = "ok" if not rotate_error else f"ok (회전 실패: {rotate_error})"
        s.last_name = name
        s.last_size = size
        s.save(self.home)
        return {"name": name, "size": size, "deleted": deleted, "rotate_error": rotate_error}

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

    def _refuse_if_server_live(self, force: bool) -> None:
        """A restore replaces the very index a running server holds open.

        Overwriting index.db under a live process leaves the server serving a
        file that no longer exists on disk and re-checkpointing its WAL over the
        fresh copy — silent corruption. So refuse while a heartbeat is fresh and
        say how to proceed. ``force`` is the escape hatch for someone who has
        genuinely stopped the server but whose heartbeat has not yet aged out.
        """
        if force:
            return
        age = server_heartbeat_age(self.home)
        if age is not None and age < LIVE_WINDOW_SECONDS:
            raise RuntimeError(
                "서버가 실행 중입니다 — 복원은 열려 있는 인덱스를 덮어써 손상시킵니다.\n"
                "  먼저 중지하세요:  docker compose down\n"
                "  중지 뒤 일회성 복원:  docker compose run --rm myviking jv backup restore ...\n"
                "  이미 중지했다면 --force-online 으로 무시할 수 있습니다."
            )

    def restore(self, name: str = "", force: bool = False) -> dict[str, Any]:
        self._refuse_if_server_live(force)
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
            pre = self._keep_pre_restore()
            restore_snapshot(archive, self.home)
        return {
            "restored": chosen["name"],
            "size": chosen.get("size", 0),
            "pre_restore": str(pre) if pre else "",
        }

    def restore_file(self, archive: Path | str, force: bool = False) -> dict[str, Any]:
        """Restore from a local archive — the undo path for a bad restore,
        and the road in for archives copied by hand."""
        self._refuse_if_server_live(force)
        path = Path(archive)
        if not path.exists():
            raise RuntimeError(f"파일이 없습니다: {path}")
        size = path.stat().st_size
        # The pre-restore snapshot lives beside undo archives and prunes the
        # directory — which would delete the very file we are restoring from
        # (undo restores *from* pre-restore/). Copy it aside first.
        with tempfile.TemporaryDirectory(dir=self.home) as td:
            staged = Path(td) / path.name
            shutil.copyfile(path, staged)
            pre = self._keep_pre_restore()
            restore_snapshot(staged, self.home)
        return {
            "restored": path.name,
            "size": size,
            "pre_restore": str(pre) if pre else "",
        }

    def _keep_pre_restore(self) -> Path | None:
        """Snapshot the current state before a restore overwrites it.

        A restore aimed at the wrong archive would otherwise destroy everything
        recorded since that archive, irreversibly. One local copy (only the
        latest — this is an undo step, not a second backup system) makes the
        mistake recoverable: ``jv backup restore --file <이 파일> --yes``.
        """
        pre_dir = self.home / "pre-restore"
        try:
            pre_dir.mkdir(parents=True, exist_ok=True)
            for old in pre_dir.glob(f"{SNAPSHOT_PREFIX}*.tar.gz"):
                old.unlink(missing_ok=True)
            return make_snapshot(self.home, pre_dir)
        except Exception:
            return None  # 안전망이 본 작업(복원)을 막아서는 안 된다

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
