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
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8787"
DEFAULT_STATE_DIR = Path.home() / ".myviking" / "hook-state"
_STATE_TTL = 7 * 24 * 3600
# Injected context is capped hard: it rides on *every* prompt, so it competes
# with the user's own words for attention. L0 abstracts fit comfortably.
_MAX_QUESTION_CHARS = 4000
_MAX_ANSWER_CHARS = 12000


class HttpTransport:
    """Minimal stdlib HTTP client: hook machines need nothing installed
    beyond the core package."""

    def __init__(self, url: str = DEFAULT_URL, key: str = "", timeout: float = 6.0):
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
        with urllib.request.urlopen(req, timeout=self.timeout) as res:
            raw = res.read().decode("utf-8")
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


def _resolve(transport: Any, cwd: str, state: dict[str, Any]) -> str:
    """The project this checkout belongs to, remembered for the session."""
    if state.get("project"):
        return str(state["project"])
    repo = _git_remote(cwd)
    res = transport.request(
        "POST",
        "/resolve",
        {"project": "", "repo": repo, "path": cwd, "create": True},
    )
    project = str((res or {}).get("project") or "")
    if project:
        state["project"] = project
        state["repo"] = repo
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
            "question": prompt[:_MAX_QUESTION_CHARS],
            "agent": _agent_name(),
            "session_id": sid,
            "max_tier": 0,
        },
    ) or {}
    state["trace_id"] = str(prepared.get("trace_id") or "")
    state["question"] = prompt
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
    trace_id = state.get("trace_id", "") if state.get("question") == question else ""
    transport.request(
        "POST",
        "/commit",
        {
            "project": project,
            "question": question[:_MAX_QUESTION_CHARS],
            "answer": answer[:_MAX_ANSWER_CHARS],
            "model": model,
            "trace_id": trace_id,
            "agent": _agent_name(),
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
    try:
        result = handler(payload or {}, transport, sdir)
        _mark_ok(sdir, event)
        return result
    except Exception as exc:
        _log_failure(sdir, event, exc)
        return None


_ERRORS_FILE = "errors.log"
_LAST_OK_FILE = "last-ok"
_MAX_ERROR_LINES = 50


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
    out: dict[str, Any] = {"server_ok": False, "server": {}, "last_ok": "", "recent_failures": []}
    try:
        health = transport.request("GET", "/health")
        out["server_ok"] = bool((health or {}).get("ok"))
        out["server"] = health or {}
    except Exception as exc:
        out["server_error"] = f"{type(exc).__name__}: {exc}"
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


def _last_exchange(path: Path) -> tuple[str, str, str]:
    """The last user prompt and the assistant text that answered it.

    Tool results arrive as ``user`` entries and harness chatter (slash-command
    wrappers, meta records) as angle-bracketed text; neither is a question, so
    both are skipped when looking for what the person actually asked.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return "", "", ""
    entries: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if isinstance(e, dict):
            entries.append(e)

    question, q_index = "", -1
    for i, e in enumerate(entries):
        if e.get("type") != "user" or e.get("isMeta"):
            continue
        text = _text_of((e.get("message") or {}).get("content"))
        if not text or text.startswith("<"):
            continue
        question, q_index = text, i

    if q_index < 0:
        return "", "", ""

    answer_parts: list[str] = []
    model = ""
    for e in entries[q_index + 1 :]:
        if e.get("type") != "assistant":
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

    work = brief.get("recent_work") or []
    if work:
        lines.append("■ 최근 작업")
        for w in work[:3]:
            qs = " / ".join(_clip(x.get("question"), 60) for x in (w.get("work") or [])[:3])
            when = str(w.get("started") or "")[:10]
            lines.append(f"- {when} ({w.get('agent') or '?'}): {qs}")

    learned = brief.get("recently_learned") or []
    if learned:
        lines.append("■ 최근에 정해진 것")
        for m in learned[:4]:
            lines.append(f"- [{m.get('category')}] {_clip(m.get('title'), 40)}: {_clip(m.get('abstract'), 90)}")

    warnings = brief.get("warnings") or []
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
        lines.append("아직 기록이 없습니다. 지금부터의 작업이 축적됩니다.")
    lines.append(
        tail
        or "위 내용은 이미 확인된 사실이니 다시 조사하지 말고 여기서 시작하세요. "
        "작업 기록은 자동으로 수집됩니다. 새로 확정된 규칙·명령·함정은 jarvis_remember 로 남기세요."
    )
    return "\n".join(lines)


def _context_note(project: str, prepared: dict[str, Any]) -> str:
    """The per-prompt read: a prior answer if one exists, else the packed L0
    context, always carrying the trace_id so the agent can report back."""
    trace_id = str(prepared.get("trace_id") or "")
    tail = (
        f"(이 작업의 trace_id={trace_id} — 사용자가 만족/불만을 표현하면 jarvis_score 로 보고, "
        "새로 확정된 것은 jarvis_remember 로 기록)"
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
    return "\n".join(
        x
        for x in [
            f"[MyViking · {project}] 이 프로젝트에서 이미 확인된 지식입니다."
            " 같은 것을 다시 조사하지 말고 여기서 시작하세요."
            " ⚠ 주의 항목을 거스르는 제안은 하지 마세요.",
            context,
            "상세가 필요하면 jarvis_browse(op=read) 로 URI 를 읽으세요. " + tail,
        ]
        if x
    )
