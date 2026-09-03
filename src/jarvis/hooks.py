"""Claude Code hook receivers — capture that does not depend on cooperation.

The MCP tools record a session only when the agent *chooses* to call them, and
an agent that skips the instruction block records nothing. Hooks close that
gap: the client runs them unconditionally, so the loop the tools describe
happens even when the agent never thinks about MyViking.

* **SessionStart** — resolves the project from the checkout and injects a
  short orientation: what was worked on last, what got decided, what is open.
* **UserPromptSubmit** — records a trace for every prompt (which also drives
  the implicit-feedback loop) and injects the budgeted L0 context.
* **Stop** — reads the transcript, extracts the finished exchange, and commits
  it. This is the Langfuse-like part: every turn lands in the database.
* **SessionEnd** — clears the per-session state file.

The agent's judgement is still needed for what only it knows —
``jarvis_remember`` (what got *decided*) and ``jarvis_score`` (how it went) —
but the record no longer depends on it.

Everything here must fail open: the context server being down is never a
reason a coding session breaks. ``run`` returns None on any error, and the CLI
wrapper always exits 0.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .redact import redact

DEFAULT_URL = "http://127.0.0.1:8787"
DEFAULT_STATE_DIR = Path.home() / ".myviking" / "hook-state"
_STATE_TTL = 7 * 24 * 3600
# Where `jv agent hooks --install` writes. Claude Code reads settings.local.json
# alongside settings.json and gitignores it by default — the right home for a
# hook line that carries a personal API key. settings.json is the shared,
# committed file: a key there ships to the git remote on the first `git add -A`.
HOOKS_FILE = "settings.local.json"
# Injected context is capped hard: it rides on *every* prompt, so it competes
# with the user's own words for attention. L0 abstracts fit comfortably.
_MAX_QUESTION_CHARS = 4000
_MAX_ANSWER_CHARS = 12000


class HttpTransport:
    """Minimal stdlib HTTP client: hook machines need nothing installed
    beyond the core package."""

    def __init__(self, url: str = DEFAULT_URL, key: str = "", timeout: float = 4.0):
        self.base = (url or DEFAULT_URL).rstrip("/")
        self.key = key
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        from urllib.parse import urlencode

        url = self.base + path
        if params:
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.key:
            req.add_header("Authorization", f"Bearer {self.key}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                raw = res.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # The server explains refusals in the JSON body (bad key, wrong
            # scope, unknown project). urlopen throws that away and leaves only
            # "HTTP Error 403", so read it back and raise the real reason —
            # otherwise the shell bridge and the hook error log say nothing useful.
            detail = ""
            try:
                payload = json.loads(exc.read().decode("utf-8") or "{}")
                detail = payload.get("detail") or payload.get("error") or ""
            except Exception:
                detail = ""
            raise RuntimeError(
                f"MyViking {exc.code}: {detail or exc.reason}"
            ) from exc
        return json.loads(raw) if raw else None


# --------------------------------------------------------------------------
# per-session state, shared between the three hooks of one sitting
# --------------------------------------------------------------------------
def _state_path(state_dir: Path, session_id: str) -> Path:
    name = hashlib.sha256((session_id or "default").encode("utf-8")).hexdigest()[:24]
    return state_dir / f"{name}.json"


def _load_state(state_dir: Path, session_id: str) -> dict[str, Any]:
    try:
        return json.loads(_state_path(state_dir, session_id).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state_dir: Path, session_id: str, state: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    _state_path(state_dir, session_id).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    _prune(state_dir)


def _drop_state(state_dir: Path, session_id: str) -> None:
    try:
        _state_path(state_dir, session_id).unlink(missing_ok=True)
    except Exception:
        pass


def _prune(state_dir: Path) -> None:
    """State files outlive crashed sessions; sweep the stale ones on write."""
    cutoff = time.time() - _STATE_TTL
    try:
        for f in state_dir.glob("*.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:
        pass


# --------------------------------------------------------------------------
# project resolution on the agent machine
# --------------------------------------------------------------------------
def _git_remote(cwd: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _agent_name() -> str:
    host = ""
    try:
        host = socket.gethostname().split(".")[0]
    except Exception:
        pass
    return f"claude-code@{host or 'unknown'}"


class NoProjectError(RuntimeError):
    """The checkout has no git remote and no explicit project: nothing to
    file the session under. Guessing a name from the directory ("tmp",
    "Desktop") silently spawned junk projects in a live audit."""


def _resolve(transport: Any, cwd: str, state: dict[str, Any]) -> str:
    """The project this checkout belongs to, remembered for the session."""
    if state.get("project"):
        return str(state["project"])
    repo = _git_remote(cwd)
    # `jv agent hooks --install --project <name>` bakes MYVIKING_PROJECT into
    # the hook command for checkouts without a remote (or to pin a name).
    explicit = os.environ.get("MYVIKING_PROJECT", "").strip()
    # A git checkout without a remote may still be named after its folder —
    # that is what the person called it. A plain directory (/tmp, ~) may not.
    is_git = (Path(cwd) / ".git").exists()
    if not repo and not explicit and not is_git:
        # Still ask: a path alias registered on the server counts.
        res = transport.request(
            "POST", "/resolve", {"project": "", "repo": "", "path": cwd, "create": False}
        )
        project = str((res or {}).get("project") or "")
        if not project:
            raise NoProjectError(
                f"git 저장소가 아닌 폴더입니다 ({cwd}). 프로젝트를 정하지 못해 이 세션은 "
                "기록되지 않습니다 — 저장소 폴더에서 열거나, "
                "`jv agent hooks --install --project <이름> ...` 으로 이름을 지정하세요."
            )
        state["project"] = project
        state["repo"] = ""
        return project
    res = transport.request(
        "POST",
        "/resolve",
        # These hooks only ever fire inside a coding agent, so a project born
        # here must carry the coding categories (commands/conventions/pitfalls/
        # decisions) — the default template lacks them and the agent's
        # remember('pitfalls', ...) calls would silently vanish.
        {"project": explicit, "repo": repo, "path": cwd, "create": True, "template": "coding"},
    )
    project = str((res or {}).get("project") or "")
    if not project:
        # A scoped key outside its fence, or a server that may not create
        # projects, lands here. Returning "" would stamp last-ok and record
        # nothing — exactly the silent loss `--check` exists to catch.
        raise RuntimeError(
            f"프로젝트를 정하지 못했습니다 (repo={repo or '-'}, cwd={cwd}). "
            "키의 범위 밖이거나 서버가 프로젝트를 만들 수 없습니다 — "
            "`jv remote link <프로젝트>` 로 묶거나 관리자에게 범위를 요청하세요."
        )
    state["project"] = project
    state["repo"] = repo
    # A brand-new project born from a guessed name is worth announcing once:
    # a typo'd directory or an unbound checkout otherwise silently spawns a
    # parallel project and the user never learns why their history is empty.
    state["created"] = bool((res or {}).get("created"))
    return project


# --------------------------------------------------------------------------
# handlers
# --------------------------------------------------------------------------
def session_start(
    payload: dict[str, Any], transport: Any, state_dir: Path
) -> dict[str, Any] | None:
    cwd = str(payload.get("cwd") or os.getcwd())
    sid = str(payload.get("session_id") or "")
    state = _load_state(state_dir, sid)
    project = _resolve(transport, cwd, state)
    if not project:
        return None
    _save_state(state_dir, sid, state)
    brief = transport.request("GET", f"/projects/{project}/brief", params={"limit": 6})
    text = _orientation(project, brief or {})
    if not text:
        return None
    if state.get("created"):
        text = (
            f"[MyViking] 이 위치를 새 프로젝트 '{project}' 로 등록했습니다"
            " (기존에 매칭되는 프로젝트가 없었음). 의도한 프로젝트가 따로 있다면"
            " `jv remote link <프로젝트명>` 으로 이 체크아웃을 서버의 그 프로젝트에"
            " 연결하세요.\n" + text
        )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }


def user_prompt_submit(
    payload: dict[str, Any], transport: Any, state_dir: Path
) -> dict[str, Any] | None:
    prompt = str(payload.get("prompt") or "").strip()
    # Slash commands are instructions to the harness, not project questions.
    if not prompt or prompt.startswith("/"):
        return None
    cwd = str(payload.get("cwd") or os.getcwd())
    sid = str(payload.get("session_id") or "")
    state = _load_state(state_dir, sid)
    project = _resolve(transport, cwd, state)
    if not project:
        return None
    # L0 only: what gets injected must ride on every prompt, and the trace
    # must record exactly what was shown — abstracts are cheap and honest.
    prepared = transport.request(
        "POST",
        "/prepare",
        {
            "project": project,
            "repo": state.get("repo", ""),
            "path": cwd,
            # Masked before it leaves the machine: a key pasted into a prompt
            # must not be stored on the server or shown to a later session.
            "question": redact(prompt[:_MAX_QUESTION_CHARS]),
            "agent": _agent_name(),
            "session_id": sid,
            "max_tier": 0,
        },
    ) or {}
    state["trace_id"] = str(prepared.get("trace_id") or "")
    # The state file sits on disk in plain text; keep the masked form there.
    state["question"] = redact(prompt)
    _save_state(state_dir, sid, state)
    text = _context_note(project, prepared)
    if not text:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        }
    }


def stop(
    payload: dict[str, Any], transport: Any, state_dir: Path
) -> dict[str, Any] | None:
    sid = str(payload.get("session_id") or "")
    cwd = str(payload.get("cwd") or os.getcwd())
    transcript = str(payload.get("transcript_path") or "")
    if not transcript:
        return None
    question, answer, model = _last_exchange(Path(transcript))
    if not question or not answer:
        return None
    state = _load_state(state_dir, sid)
    digest = hashlib.sha256(f"{question}\n{answer}".encode("utf-8")).hexdigest()[:16]
    if state.get("committed") == digest:
        return None  # Stop can fire more than once for the same turn
    project = _resolve(transport, cwd, state)
    if not project:
        return None
    # Attach to the trace the prompt hook opened — but only if it was opened
    # for *this* question, or the generation lands on someone else's retrieval.
    trace_id = state.get("trace_id", "") if state.get("question") == redact(question) else ""
    transport.request(
        "POST",
        "/commit",
        {
            "project": project,
            "question": redact(question[:_MAX_QUESTION_CHARS]),
            "answer": redact(answer[:_MAX_ANSWER_CHARS]),
            "model": model,
            "trace_id": trace_id,
            "agent": _agent_name(),
            "files": _touched_files(Path(transcript)),
        },
    )
    state["committed"] = digest
    state["trace_id"] = ""
    _save_state(state_dir, sid, state)
    return None


def session_end(
    payload: dict[str, Any], transport: Any, state_dir: Path
) -> dict[str, Any] | None:
    _drop_state(state_dir, str(payload.get("session_id") or ""))
    return None


_HANDLERS = {
    "session-start": session_start,
    "user-prompt-submit": user_prompt_submit,
    "stop": stop,
    "session-end": session_end,
}

EVENTS = tuple(_HANDLERS)


def run(
    event: str,
    payload: dict[str, Any] | None,
    transport: Any,
    state_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    """Dispatch one hook event. Never raises: a hook that fails must not
    break the coding session it is observing.

    Fail-open has a failure mode of its own: a wrong URL or a revoked key
    means weeks of silently missing records. So every failure leaves a local
    breadcrumb and every success a timestamp — ``jv agent hooks --check``
    reads both, turning "조용한 유실" into something a person can see.
    """
    handler = _HANDLERS.get(event)
    if handler is None:
        return None
    sdir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    if _server_down_recently(sdir) and hasattr(transport, "timeout"):
        # The server was unreachable a moment ago. Still try (a blip must not
        # cost minutes of capture), but fail fast: otherwise every prompt blocks
        # for the full timeout twice while the laptop is off the home network.
        transport.timeout = min(float(transport.timeout), 1.0)
    try:
        result = handler(payload or {}, transport, sdir)
        _mark_ok(sdir, event)
        _clear_server_down(sdir)
        return result
    except NoProjectError as exc:
        # Not a server fault and not transient: breadcrumb for --check, and
        # say it once on screen so the person learns why nothing is recorded.
        _log_failure(sdir, event, exc)
        return _once_per_session_message(f"[MyViking] {exc}", payload or {}, sdir, "noproject_warned")
    except Exception as exc:
        _log_failure(sdir, event, exc)
        if _is_unreachable(exc):
            _note_server_down(sdir)
        return _auth_refused_message(exc, payload or {}, sdir)


_ERRORS_FILE = "errors.log"
_LAST_OK_FILE = "last-ok"
_DOWN_FILE = "server-down"
_DOWN_SKIP_SECONDS = 120
_MAX_ERROR_LINES = 50


def _is_unreachable(exc: Exception) -> bool:
    """Connection-level failures (server off, DNS, firewall) — not HTTP refusals."""
    import socket

    return isinstance(exc, (urllib.error.URLError, socket.timeout, OSError)) and not isinstance(
        exc, urllib.error.HTTPError
    )


def _note_server_down(state_dir: Path) -> None:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / _DOWN_FILE).write_text(str(int(time.time())), encoding="utf-8")
    except Exception:
        pass


def _clear_server_down(state_dir: Path) -> None:
    try:
        (state_dir / _DOWN_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def _server_down_recently(state_dir: Path) -> bool:
    try:
        stamp = int((state_dir / _DOWN_FILE).read_text(encoding="utf-8").strip() or 0)
    except Exception:
        return False
    return (time.time() - stamp) < _DOWN_SKIP_SECONDS


def _auth_refused_message(
    exc: Exception, payload: dict[str, Any], state_dir: Path
) -> dict[str, Any] | None:
    """A 401/403/429 is a *configuration* error, not a transient one: the key
    is wrong, revoked, or out of scope, and nothing will be recorded until a
    human fixes it. Fail-open still applies (the session goes on), but say so
    once per session via Claude Code's ``systemMessage`` — otherwise the only
    trace is a log file nobody knows exists."""
    msg = str(exc)
    if not any(f"MyViking {code}" in msg for code in ("401", "403", "429")):
        return None
    return _once_per_session_message(
        f"[MyViking] 서버가 요청을 거부했습니다 — {msg}. 이 세션은 기록되지 않습니다. "
        "`jv agent hooks --check --url <서버> --key <키>` 로 확인하세요.",
        payload, state_dir, "auth_warned",
    )


def _once_per_session_message(
    text: str, payload: dict[str, Any], state_dir: Path, flag: str
) -> dict[str, Any] | None:
    sid = str(payload.get("session_id") or "")
    try:
        state = _load_state(state_dir, sid)
        if state.get(flag):
            return None
        state[flag] = True
        _save_state(state_dir, sid, state)
    except Exception:
        pass
    return {"systemMessage": text}


def _mark_ok(state_dir: Path, event: str) -> None:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / _LAST_OK_FILE).write_text(
            f"{_now_stamp()} {event}\n", encoding="utf-8"
        )
    except Exception:
        pass


def _log_failure(state_dir: Path, event: str, exc: Exception) -> None:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        path = state_dir / _ERRORS_FILE
        lines: list[str] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()[-(_MAX_ERROR_LINES - 1):]
        lines.append(f"{_now_stamp()} {event} {type(exc).__name__}: {exc}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _now_stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def health_check(transport: Any, state_dir: Path | str | None = None) -> dict[str, Any]:
    """Can the hooks reach the server, and have they been failing?

    Returns what ``jv agent hooks --check`` prints: server reachability, the
    last successful hook, and the recent failure breadcrumbs.
    """
    sdir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    out: dict[str, Any] = {
        "server_ok": False, "server": {}, "last_ok": "", "recent_failures": [],
        "key_ok": None, "key_error": "", "me": {},
    }
    try:
        health = transport.request("GET", "/health")
        out["server_ok"] = bool((health or {}).get("ok"))
        out["server"] = health or {}
    except Exception as exc:
        out["server_error"] = f"{type(exc).__name__}: {exc}"
    # /health is public, so it says nothing about *this* key. A wrong or revoked
    # key used to pass --check green and then fail every hook silently.
    if out["server_ok"] and out["server"].get("auth_required"):
        if not getattr(transport, "key", ""):
            out["key_ok"] = False
            out["key_error"] = "키 없음 — 이 서버는 API 키를 요구합니다 (--key 또는 MYVIKING_KEY)"
        else:
            try:
                me = transport.request("GET", "/me") or {}
                out["me"] = me
                out["key_ok"] = True
            except Exception as exc:
                out["key_ok"] = False
                out["key_error"] = f"키 거부됨 — {exc}"
    elif out["server_ok"]:
        out["key_ok"] = True  # open server: nothing to verify
    try:
        out["last_ok"] = (sdir / _LAST_OK_FILE).read_text(encoding="utf-8").strip()
    except Exception:
        pass
    try:
        out["recent_failures"] = (
            (sdir / _ERRORS_FILE).read_text(encoding="utf-8").splitlines()[-10:]
        )
    except Exception:
        pass
    return out


def installed_events(project_path: Path | str = ".") -> dict[str, Any]:
    """Which hook events this checkout's ``.claude/settings.json`` actually wires
    to ``jv hook``. A server that answers /health says nothing about whether the
    repo in front of you is even calling the hooks — the most common reason
    capture silently never starts. This closes that gap for ``--check``."""
    from .connect import HOOK_EVENTS

    claude_dir = Path(project_path) / ".claude"
    local_path = claude_dir / HOOKS_FILE
    shared_path = claude_dir / "settings.json"
    out: dict[str, Any] = {
        "path": str(local_path),
        "exists": False,
        "installed": [],
        "missing": [event for event, _ in HOOK_EVENTS],
        "key_in_shared_file": False,
    }

    def _jv_hooks(path: Path) -> list[str] | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            out["error"] = f"{path.name} 을 읽지 못했습니다 (JSON 오류)"
            return []
        hooks = (data or {}).get("hooks", {}) or {}
        return [e for e, _ in HOOK_EVENTS if "jv hook" in json.dumps(hooks.get(e, []))]

    installed: list[str] = []
    for path in (local_path, shared_path):
        found = _jv_hooks(path)
        if found is None:
            continue
        if found:
            out["exists"] = True
            out["path"] = str(path)
            installed = found
            # The shared settings.json is the committed one — a key in there
            # is on its way to the git remote.
            if path == shared_path and "MYVIKING_KEY=" in path.read_text(encoding="utf-8"):
                out["key_in_shared_file"] = True
            break
    out["installed"] = installed
    out["missing"] = [event for event, _ in HOOK_EVENTS if event not in installed]
    return out


# --------------------------------------------------------------------------
# transcript parsing (Claude Code JSONL)
# --------------------------------------------------------------------------
def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p).strip()
    return ""


_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Update"}
_MAX_FILES = 20

# Text the harness puts in the user's seat that no person typed. Verified
# against real Claude Code transcripts: the compaction summary ("This session
# is being continued…", 17k chars) would otherwise be committed as the
# question, and an interrupted turn as a question with no answer.
_HARNESS_USER_PREFIXES = (
    "<",  # <command-name>, <local-command-stdout>, <task-notification>, <system-reminder>
    "[Request interrupted",
    "This session is being continued from a previous conversation",
)


def _read_entries(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    entries: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        # Subagent turns share the file in some versions; they are not the
        # person's conversation.
        if isinstance(e, dict) and not e.get("isSidechain"):
            entries.append(e)
    return entries


def _is_person_prompt(e: dict[str, Any]) -> str:
    """The text of a user entry a person actually typed, else ''."""
    if e.get("type") != "user" or e.get("isMeta"):
        return ""
    # Task notifications and other harness-originated turns carry this mark.
    if e.get("promptSource") == "system":
        return ""
    text = _text_of((e.get("message") or {}).get("content"))
    if not text or text.startswith(_HARNESS_USER_PREFIXES):
        return ""
    return text


def _is_real_answer(e: dict[str, Any]) -> bool:
    """Assistant entries that are model output, not an API error placeholder
    ("API Error: Server error mid-response…", model "<synthetic>")."""
    if e.get("type") != "assistant" or e.get("isApiErrorMessage"):
        return False
    model = str((e.get("message") or {}).get("model") or "")
    return model != "<synthetic>"


def _touched_files(path: Path) -> list[str]:
    """Files the assistant wrote to while answering the last question.

    Reads the same tool calls the editor already ran, so "what was worked on"
    is recorded as concretely as "what was asked" — a returning session then
    knows which files last time's work touched, not just the topic.
    """
    entries = _read_entries(path)
    q_index = -1
    for i, e in enumerate(entries):
        if _is_person_prompt(e):
            q_index = i
    if q_index < 0:
        return []

    files: list[str] = []
    for e in entries[q_index + 1 :]:
        if not _is_real_answer(e):
            continue
        content = (e.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in _EDIT_TOOLS:
                continue
            inp = block.get("input") or {}
            fp = inp.get("file_path") or inp.get("notebook_path") or ""
            if fp and fp not in files:
                files.append(fp)
                if len(files) >= _MAX_FILES:
                    return files
    return files


def _last_exchange(path: Path) -> tuple[str, str, str]:
    """The last user prompt and the assistant text that answered it.

    Tool results arrive as ``user`` entries and harness chatter (slash-command
    wrappers, meta records) as angle-bracketed text; neither is a question, so
    both are skipped when looking for what the person actually asked.
    """
    entries = _read_entries(path)
    question, q_index = "", -1
    for i, e in enumerate(entries):
        text = _is_person_prompt(e)
        if text:
            question, q_index = text, i

    if q_index < 0:
        return "", "", ""

    answer_parts: list[str] = []
    model = ""
    for e in entries[q_index + 1 :]:
        if not _is_real_answer(e):
            continue
        msg = e.get("message") or {}
        model = str(msg.get("model") or model)
        text = _text_of(msg.get("content"))
        if text:
            answer_parts.append(text)
    return question, "\n\n".join(answer_parts).strip(), model


# --------------------------------------------------------------------------
# formatting — what gets injected into the conversation
# --------------------------------------------------------------------------
def _clip(text: Any, n: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _basename(path: str) -> str:
    """Show ``auth.py`` not the whole absolute path — the orientation line is
    a reminder, not a link."""
    return str(path).replace("\\", "/").rsplit("/", 1)[-1] or str(path)


# How a memory's trust status is tagged inline, so the agent weights it instead
# of taking every injected line as confirmed fact. Established items get no tag.
_TRUST_TAGS = {
    "contested": " ⟨확인 필요⟩",
    "stale": " ⟨오래됨⟩",
    "tentative": " ⟨미확정⟩",
    "fresh": " ⟨검증 전⟩",
}


def _trust_tag(m: dict[str, Any]) -> str:
    return _TRUST_TAGS.get((m.get("trust") or {}).get("status", ""), "")


def _orientation(project: str, brief: dict[str, Any], tail: str = "") -> str:
    """The session-start read: recent work first, because "what was I doing"
    is the question a returning session actually has. ``tail`` swaps the
    closing guidance — the hook and the shell bridge end differently."""
    lines = [f"[MyViking] '{project}' — 이 프로젝트에 대해 이미 축적된 내용입니다."]

    threads = brief.get("open_threads") or []
    if threads:
        lines.append("■ 지난번에 끝나지 않았을 수 있는 것 (같은 요청이 반복됐음)")
        for t in threads[:3]:
            lines.append(f"- {_clip(t.get('question'), 110)}")

    # A sitting with no actor and no questions (a probe, an aborted start)
    # rendered as "- 2026-09-03 (?): " — nothing to orient anyone with.
    work = [
        w
        for w in (brief.get("recent_work") or [])
        if (w.get("work") and any((x.get("question") or "").strip() for x in w["work"]))
    ]
    if work:
        lines.append("■ 최근 작업")
        for w in work[:3]:
            qs = " / ".join(_clip(x.get("question"), 60) for x in (w.get("work") or [])[:3])
            when = str(w.get("started") or "")[:10]
            lines.append(f"- {when} ({w.get('agent') or '?'}): {qs}")
            wfiles = w.get("files") or []
            if wfiles:
                shown = ", ".join(_basename(f) for f in wfiles[:5])
                more = f" 외 {len(wfiles) - 5}개" if len(wfiles) > 5 else ""
                lines.append(f"  · 파일: {shown}{more}")

    learned = brief.get("recently_learned") or []
    learned_uris = {m.get("uri") for m in learned}
    if learned:
        lines.append("■ 최근에 정해진 것")
        for m in learned[:4]:
            lines.append(
                f"- [{m.get('category')}] {_clip(m.get('title'), 40)}:"
                f" {_clip(m.get('abstract'), 90)}{_trust_tag(m)}"
            )

    # The durable, high-confidence knowledge — the point of coming back oriented.
    # Skip anything already shown under "최근에 정해진 것" so it is not repeated.
    know = [m for m in (brief.get("know") or []) if m.get("uri") not in learned_uris]
    if know:
        lines.append("■ 확립된 지식")
        for m in know[:4]:
            lines.append(
                f"- [{m.get('category')}] {_clip(m.get('title'), 40)}:"
                f" {_clip(m.get('abstract'), 90)}{_trust_tag(m)}"
            )

    # A pitfall learned this week already appeared under "최근에 정해진 것";
    # the warning section is for the ones that did not.
    warnings = [m for m in (brief.get("warnings") or []) if m.get("uri") not in learned_uris]
    if warnings:
        lines.append("■ ⚠ 주의 — 이 프로젝트에서 이미 밟은 함정")
        for m in warnings[:4]:
            lines.append(f"- {_clip(m.get('title'), 40)}: {_clip(m.get('abstract'), 90)}")

    unresolved = brief.get("unresolved") or []
    if unresolved:
        lines.append("■ 미해결 (필요하면 사용자에게 확인)")
        for m in unresolved[:3]:
            lines.append(f"- {_clip(m.get('title'), 60)} ({', '.join(m.get('reasons') or [])})")

    if len(lines) == 1:
        # Nothing accumulated yet: say so, and do not follow it with "do not
        # re-investigate" — there is nothing here to start from.
        lines.append(
            "아직 기록이 없습니다. 지금부터의 작업이 자동으로 축적됩니다. 확정된 규칙·명령은 "
            "jarvis_remember(MCP) 또는 `jv remote remember <카테고리> <제목> <내용>` 으로 남길 수 있습니다."
        )
        return "\n".join(lines)
    lines.append(
        tail
        or "위 내용은 지금까지 이 프로젝트에서 축적·검증된 것이니 다시 조사하지 말고 여기서 시작하세요. "
        "다만 이건 고정된 정답이 아니라 계속 갱신되는 기록입니다 — ⟨확인 필요⟩·⟨오래됨⟩·⟨미확정⟩·⟨검증 전⟩ "
        "표시가 붙은 항목은 사실로 단정하지 말고 쓰기 전에 확인하세요. 표시가 없으면 확립된 것으로 봐도 됩니다. "
        "작업 기록은 자동으로 수집됩니다. 틀렸던 내용을 바로잡을 땐 같은 제목으로 jarvis_remember(MCP) 또는 "
        "`jv remote remember` 하면 이전 것을 대체하고(이력은 보관), 확실히 틀렸으면 jarvis_score 또는 "
        "`jv remote score <trace_id> 0` 으로 알려 주세요."
    )
    return "\n".join(lines)


def _context_note(project: str, prepared: dict[str, Any]) -> str:
    """The per-prompt read: a prior answer if one exists, else the packed L0
    context, always carrying the trace_id so the agent can report back."""
    trace_id = str(prepared.get("trace_id") or "")
    tail = (
        f"(이 작업의 trace_id={trace_id} — 사용자가 만족/불만을 표현하면 jarvis_score 또는 "
        f"`jv remote score {trace_id} <0..1>` 로 보고, 새로 확정된 것은 jarvis_remember 또는 "
        "`jv remote remember` 로 기록)"
        if trace_id
        else ""
    )

    hit = prepared.get("cache_hit")
    if hit:
        return "\n".join(
            x
            for x in [
                f"[MyViking · {project}] 전에 같은 질문에 답했습니다"
                f" (유사도 {hit.get('similarity')}, {str(hit.get('created') or '')[:10]}):",
                str(hit.get("answer") or ""),
                "여전히 유효한지 확인한 뒤 재사용하세요. " + tail,
            ]
            if x
        )

    context = str(prepared.get("context") or "").strip()
    if not context:
        return ""
    notes = prepared.get("trust_notes") or []
    verify = ""
    if notes:
        # The items themselves carry ⟨label⟩ on their heading; this line only
        # says what the marks mean. No cap — a partial list reads as "the rest
        # are fine".
        verify = (
            f"※ 제목에 ⟨…⟩ 표시가 붙은 {len(notes)}개 항목은 아직 확정이 아니니 사실로 단정하지 말고 "
            "쓰기 전에 확인하세요."
        )
    return "\n".join(
        x
        for x in [
            f"[MyViking · {project}] 이 질문과 관련 있을 만한, 이 프로젝트에 축적된 기록입니다."
            " 표시가 없는 항목은 확립된 것이니 다시 조사하지 말고 활용하고, ⟨…⟩ 표시가 붙은 항목은"
            " 확인 후 쓰세요. 질문과 무관하면 무시하세요. ⚠ 주의 항목을 거스르는 제안은 하지 마세요.",
            context,
            verify,
            "상세가 필요하면 jarvis_browse(op=read) 또는 `jv remote ctx \"<질문>\" --max-tier 2` 로 읽으세요."
            " 틀렸던 내용을 바로잡을 땐 같은 제목으로 jarvis_remember 또는 `jv remote remember` 하면"
            " 이전 것을 대체합니다. " + tail,
        ]
        if x
    )
