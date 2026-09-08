"""jv 명령줄 — 에이전트 머신에서 도서관 서버에 붙는 얇은 클라이언트.

모든 호출은 fail-open: 서버가 죽거나 연결이 틀어져도 코딩 세션을 막지 않고
흔적만 남깁니다. 훅은 Claude Code hook format v2 JSON 을 stdout 으로 출력합니다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

import httpx

# ── 경량 마스킹 (서버 쪽과 별개로, 클라이언트에서 먼저 한 번) ──
_MASK = [
    (re.compile(r"(?i)(api[_-]?key|apikey|password|passwd|secret|token)\s*[:=]\s*['\"]?[0-9A-Za-z_\-./+=]{8,}"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)(?:sk|pk)-[A-Za-z0-9_-]{16,}"), "sk-…"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA…"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "AIza…"),
    (re.compile(r"\bgh[pousr]_[0-9A-Za-z]{20,}\b"), "gh_…"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "JWT…"),
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1[REDACTED]@"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), "[REDACTED KEY]"),
]
_HEX = re.compile(r"\b[0-9a-f]{40,}\b")


def mask(text: str) -> str:
    out = text or ""
    for pat, repl in _MASK:
        out = pat.sub(repl, out)
    out = _HEX.sub("[REDACTED]", out)
    return out


# ── 공용 ──
STATE_DIR = Path(os.environ.get("MYVIKING_STATE_DIR", Path.home() / ".myviking" / "hook-state"))


def _log_error(msg: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "errors.log", "a") as f:
            f.write(f"{msg}\n")
    except OSError:
        pass


def _env_args(args: argparse.Namespace) -> tuple[str, str, str]:
    url = (args.url or os.environ.get("MYVIKING_URL") or "").rstrip("/")
    key = args.key or os.environ.get("MYVIKING_KEY") or ""
    project = args.project or os.environ.get("MYVIKING_PROJECT") or ""
    if not url or not key:
        raise SystemExit("jv: MYVIKING_URL / MYVIKING_KEY 가 필요합니다 (--url, --key 또는 환경 변수).")
    return url, key, project


def _api(url: str, key: str, method: str, path: str, **kw) -> dict:
    try:
        r = httpx.request(method, f"{url}/api/v1{path}",
                          headers={"Authorization": f"Bearer {key}"},
                          timeout=15, **kw)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        _log_error(f"HTTP {e.response.status_code} {path}: {e.response.text[:200]}")
        raise SystemExit(f"jv: 서버 응답 오류 ({e.response.status_code}) — 키/주소를 확인하세요.")
    except httpx.HTTPError as e:
        _log_error(f"HTTP {path}: {e}")
        raise SystemExit(f"jv: 서버에 연결할 수 없습니다 ({url}).")


# ══════════════════ 훅 설치 / 점검 ══════════════════ #
HOOK_EVENTS = (
    ("SessionStart", "session-start"),
    ("UserPromptSubmit", "user-prompt-submit"),
    ("Stop", "stop"),
    ("SessionEnd", "session-end"),
)


def _settings_path(cwd: Path) -> Path:
    return cwd / ".claude" / "settings.local.json"


def hook_install(args: argparse.Namespace) -> None:
    url, key, project = _env_args(args)
    # 서버 확인 — 틀린 주소/키로 몇 주 방치되는 것을 막는다
    try:
        health = _api(url, key, "GET", "/health")
        print(f"✓ 서버 확인: {url} · myviking {health.get('version', '?')}")
    except SystemExit as e:
        print(f"⚠ 서버 확인 실패: {e}")
        raise

    cwd = Path(args.cwd or os.getcwd())
    timeout = int(args.timeout or 15)
    jv = _self_command()

    def command(event: str) -> str:
        env = f"MYVIKING_URL={url} MYVIKING_KEY={key}"
        if project:
            env += f" MYVIKING_PROJECT={project}"
        return f"{env} {jv} hook {event} --timeout {timeout}"

    hooks_block = {
        "hooks": {
            event: [{"hooks": [{"type": "command", "command": command(cli), "timeout": timeout}]}]
            for event, cli in HOOK_EVENTS
        }
    }

    path = _settings_path(cwd)
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            existing = {}
    merged = {**existing, **hooks_block}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    print(f"✓ 훅 설치: {path}")
    print(f"  프로젝트: {project or '자동 감지'} · 이벤트: {', '.join(e for e, _ in HOOK_EVENTS)}")
    print("이제 이 폴더에서 Claude Code 를 열면 기록이 쌓이기 시작합니다.")
    if args.key:  # 명령줄에 키를 줬다면 상태에 남지 않게
        pass


def _self_command() -> str:
    """훅에 박을 jv 호출 — pipx/--user 설치 경로 문제 회피."""
    if _in_container():
        return "jv"
    exe = os.environ.get("_JV_SELF", "")
    if exe and Path(exe).exists():
        return exe
    return "jv"


def _in_container() -> bool:
    return os.environ.get("MYVIKING_IN_CONTAINER") == "1" or Path("/.dockerenv").exists()


def hook_uninstall(args: argparse.Namespace) -> None:
    path = _settings_path(Path(args.cwd or os.getcwd()))
    if not path.exists():
        print("훅이 설치되어 있지 않습니다.")
        return
    data = json.loads(path.read_text())
    data.pop("hooks", None)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"✓ 훅 제거: {path}")


def hook_check(args: argparse.Namespace) -> None:
    path = _settings_path(Path(args.cwd or os.getcwd()))
    if not path.exists():
        print("훅이 설치되어 있지 않습니다. jv hook install --url ... --key ...")
        return
    data = json.loads(path.read_text())
    hooks = data.get("hooks", {})
    print(f"✓ 훅 파일: {path}")
    missing = [e for e, _ in HOOK_EVENTS if e not in hooks]
    if missing:
        print(f"⚠ 빠진 이벤트: {', '.join(missing)} — jv hook install 로 다시 설치하세요.")
    else:
        print(f"✓ 이벤트: {', '.join(e for e, _ in HOOK_EVENTS)}")
    # 환경 변수로 서버 확인 시도
    env = os.environ
    for cfg in hooks.values():
        cmd = cfg[0]["hooks"][0]["command"] if cfg else ""
        m = re.search(r"MYVIKING_URL=(\S+)", cmd)
        if m:
            env = {**env, "MYVIKING_URL": m.group(1)}
        m = re.search(r"MYVIKING_KEY=(\S+)", cmd)
        if m:
            env = {**env, "MYVIKING_KEY": m.group(1)}
    if env.get("MYVIKING_URL") and env.get("MYVIKING_KEY"):
        try:
            r = httpx.get(f"{env['MYVIKING_URL']}/api/v1/health",
                          headers={"Authorization": f"Bearer {env['MYVIKING_KEY']}"}, timeout=10)
            r.raise_for_status()
            print(f"✓ 서버 연결: {env['MYVIKING_URL']}")
        except Exception:
            print(f"⚠ 서버 연결 실패: {env.get('MYVIKING_URL')} — 서버 주소/키를 확인하세요.")
    log = STATE_DIR / "errors.log"
    if log.exists() and log.stat().st_size:
        print(f"⚠ 기록된 오류: {log} (최근 몇 줄)")
        print("\n".join(log.read_text().splitlines()[-5:]))


# ══════════════════ 훅 이벤트 처리 ══════════════════ #
def hook_handler(args: argparse.Namespace) -> None:
    event = args.event
    payload = json.loads(sys.stdin.read() or "{}")
    session_id = str(payload.get("session_id") or "")
    url, key, project = _env_args(args)
    try:
        if event == "session-start":
            out = _hook_session_start(url, key, project, session_id)
        elif event == "user-prompt-submit":
            out = _hook_user_prompt(url, key, project, session_id, payload)
        elif event == "stop":
            out = _hook_stop(url, key, project, session_id, payload)
        else:
            out = {}
    except SystemExit as e:  # fail-open
        _log_error(f"{event}: {e}")
        out = {}
    print(json.dumps(out, ensure_ascii=False))


def _hook_session_start(url: str, key: str, project: str, session_id: str) -> dict:
    path = f"/projects/{project}/brief"
    q = urllib.parse.urlencode({"session_id": session_id, "agent": _agent_name()})
    data = _api(url, key, "GET", f"{path}?{q}")
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "sessionStart": {
                "context": data.get("orientation", ""),
                "instructions": "위 브리핑의 '확립된 지식'은 이전 작업이 뒷받침한 내용입니다. "
                                "'검증 필요'는 단정하지 말고 확인하세요. 작업 중 새 규칙·함정·결정을 "
                                "알게 되면 jv remember (또는 도구)로 남기세요.",
            },
        }
    }


def _hook_user_prompt(url: str, key: str, project: str, session_id: str, payload: dict) -> dict:
    prompt = mask(str(payload.get("prompt") or ""))[:2000]
    if not prompt:
        return {}
    data = _api(url, key, "POST", f"/projects/{project}/prepare",
                json={"prompt": prompt, "session_id": session_id, "agent": _agent_name(), "max_tier": 1})
    text = data.get("injection") or ""
    if not text:
        return {}
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}


def _hook_stop(url: str, key: str, project: str, session_id: str, payload: dict) -> dict:
    transcript = str(payload.get("transcript_path") or "")
    if not transcript:
        return {}
    qa = _last_exchange(Path(transcript))
    if not qa:
        return {}
    question, answer = qa
    _api(url, key, "POST", f"/projects/{project}/commit",
         json={"question": mask(question)[:2000], "answer": mask(answer)[:20000],
               "session_id": session_id, "agent": _agent_name(),
               "files": _touched_files(Path(transcript))})
    return {}


def _last_exchange(transcript: Path):
    """Claude Code JSONL 트랜스크립트에서 마지막 질문/답 추출."""
    question = answer = ""
    try:
        for line in transcript.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            msg = row.get("message") or {}
            role = msg.get("role")
            content = msg.get("content")
            text = _content_text(content)
            if role == "user" and text:
                question = text
            elif role == "assistant" and text:
                answer = text
    except OSError:
        return None
    if question and answer:
        return question[-4000:], answer[-12000:]
    return None


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(str(c.get("text", "")))
            elif isinstance(c, dict) and c.get("type") == "tool_use":
                parts.append(f"[도구:{c.get('name','')} 사용]")
        return "\n".join(parts)
    return ""


def _touched_files(transcript: Path) -> list[str]:
    files: list[str] = []
    try:
        for line in transcript.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = (row.get("message") or {})
            content = msg.get("content")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Write":
                        files.append(str(c.get("input", {}).get("file_path", "")))
    except OSError:
        pass
    return list(dict.fromkeys(files))[:20]


def _agent_name() -> str:
    return os.environ.get("MYVIKING_AGENT", "cli")


# ══════════════════ 원격 명령 ══════════════════ #
def remote(args: argparse.Namespace) -> None:
    url, key, project = _env_args(args)
    if args.cmd == "brief":
        data = _api(url, key, "GET", f"/projects/{project}/brief")
        print(data.get("orientation", ""))
    elif args.cmd == "search":
        data = _api(url, key, "GET",
                    f"/projects/{project}/search?q=" + urllib.parse.quote(args.q or ""))
        for it in data.get("items", []):
            mark = "" if it.get("verified") else " ⟨검증 전⟩"
            print(f"[{it['category']}] {it['title']}{mark}")
            print(f"  {it['text'][:200]}")
        for w in data.get("warnings", []):
            print(f"⚠ [검증 필요] {w['title']}")
    elif args.cmd == "remember":
        content = args.content or sys.stdin.read()
        data = _api(url, key, "POST", f"/projects/{project}/remember",
                    json={"title": args.q, "content": mask(content), "category": args.category})
        print(f"✓ 기록됨 → {data.get('uri', '')}")
    elif args.cmd == "commit":
        data = _api(url, key, "POST", f"/projects/{project}/commit",
                    json={"question": mask(args.q), "answer": mask(args.content or "")})
        print("✓ 기록됨")
    elif args.cmd == "score":
        data = _api(url, key, "POST", f"/projects/{project}/score",
                    json={"memory_id": int(args.q or 0), "outcome": args.outcome})
        print(f"✓ {data.get('status')} (신뢰 {data.get('trust')})")


# ══════════════════ MCP stdio 서버 ══════════════════ #
MCP_TOOLS = [
    {
        "name": "viking_brief",
        "description": "프로젝트 도서관의 작업 브리핑(확립 지식·검증 필요·최근 작업)을 가져온다. 세션 시작 시 호출.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "viking_search",
        "description": "프로젝트 지식 도서관에서 관련 지식을 검색한다. 작업 중 막힐 때나 규칙·함정이 궁금할 때 호출.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "viking_remember",
        "description": "새로 정한 규칙·함정·결정을 지식 도서관에 남긴다. confirmed=true 면 확립으로 기록.",
        "inputSchema": {"type": "object", "properties": {
            "title": {"type": "string"}, "content": {"type": "string"},
            "category": {"type": "string", "enum": ["knowledge", "commands", "pitfalls", "decisions"]},
            "confirmed": {"type": "boolean"}}, "required": ["title", "content"]},
    },
    {
        "name": "viking_score",
        "description": "이전에 주입된 지식이 틀렸으면 memory_id 와 outcome=bad 로 알려 교정하게 한다.",
        "inputSchema": {"type": "object", "properties": {
            "memory_id": {"type": "integer"}, "outcome": {"type": "string", "enum": ["good", "bad", "settled"]}},
            "required": ["memory_id", "outcome"]},
    },
]


def mcp(args: argparse.Namespace) -> None:
    url, key, project = _env_args(args)

    def reply(req_id, result):
        print(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}, ensure_ascii=False), flush=True)

    def error(req_id, code, message):
        print(json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method", "")
        req_id = req.get("id")
        if method == "initialize":
            reply(req_id, {"protocolVersion": "2024-11-05",
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "myviking", "version": "1.0.0"}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            reply(req_id, {"tools": MCP_TOOLS})
        elif method == "tools/call":
            params = req.get("params", {})
            name, inp = params.get("name"), params.get("arguments", {}) or {}
            try:
                if name == "viking_brief":
                    text = _api(url, key, "GET", f"/projects/{project}/brief").get("orientation", "")
                elif name == "viking_search":
                    r = _api(url, key, "GET", f"/projects/{project}/search?q=" + urllib.parse.quote(str(inp.get("query", ""))))
                    text = "\n\n".join(f"[{it['category']}] {it['title']}\n{it['text'][:500]}" for it in r.get("items", [])) or "관련 지식 없음"
                elif name == "viking_remember":
                    r = _api(url, key, "POST", f"/projects/{project}/remember",
                             json={"title": inp["title"], "content": mask(str(inp["content"])),
                                   "category": inp.get("category", "knowledge"), "confirmed": bool(inp.get("confirmed"))})
                    text = f"기록됨 → {r.get('uri')}"
                elif name == "viking_score":
                    r = _api(url, key, "POST", f"/projects/{project}/score",
                             json={"memory_id": int(inp["memory_id"]), "outcome": inp.get("outcome", "settled")})
                    text = f"상태 {r.get('status')}"
                else:
                    raise ValueError(f"알 수 없는 도구: {name}")
                reply(req_id, {"content": [{"type": "text", "text": text}]})
            except SystemExit as e:
                error(req_id, -32000, str(e))
            except Exception as e:
                error(req_id, -32000, str(e))
        elif req_id is not None:
            error(req_id, -32601, f"알 수 없는 메서드: {method}")


# ══════════════════ main ══════════════════ #
def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="jv", description="myviking 클라이언트")
    sub = p.add_subparsers(dest="cmd", required=True)

    def hooks_common(sp):
        sp.add_argument("--url", "-u", default="")
        sp.add_argument("--key", "-k", default="")
        sp.add_argument("--project", "-p", default="")
        sp.add_argument("--timeout", default="15")

    sp = sub.add_parser("hook", help="Claude Code 훅 관리/이벤트 처리")
    hsub = sp.add_subparsers(dest="action", required=True)
    hp = hsub.add_parser("install"); hooks_common(hp); hp.add_argument("--cwd", default="")
    hp.set_defaults(func=hook_install)
    hp = hsub.add_parser("uninstall"); hp.add_argument("--cwd", default="")
    hp.set_defaults(func=hook_uninstall)
    hp = hsub.add_parser("check"); hp.add_argument("--cwd", default=""); hooks_common(hp)
    hp.set_defaults(func=hook_check)
    hp = hsub.add_parser("session-start"); hooks_common(hp); hp.add_argument("event", nargs="?", default="session-start")
    hp.set_defaults(func=hook_handler)
    for ev in ("user-prompt-submit", "stop", "session-end"):
        hp = hsub.add_parser(ev); hooks_common(hp); hp.add_argument("event", nargs="?", default=ev)
        hp.set_defaults(func=hook_handler)

    sp = sub.add_parser("brief", help="세션 시작 브리핑"); hooks_common(sp)
    sp.set_defaults(func=remote, cmd="brief")
    sp = sub.add_parser("search", help="지식 검색"); hooks_common(sp); sp.add_argument("q")
    sp.set_defaults(func=remote, cmd="search")
    sp = sub.add_parser("remember", help="지식 기록"); hooks_common(sp)
    sp.add_argument("q", help="제목"); sp.add_argument("--content", default="")
    sp.add_argument("--category", default="knowledge")
    sp.set_defaults(func=remote, cmd="remember")
    sp = sub.add_parser("commit", help="질문/답 기록"); hooks_common(sp)
    sp.add_argument("q", help="질문"); sp.add_argument("--content", default="", help="답")
    sp.set_defaults(func=remote, cmd="commit")
    sp = sub.add_parser("score", help="결과 피드백"); hooks_common(sp)
    sp.add_argument("q", help="memory_id"); sp.add_argument("--outcome", default="settled")
    sp.set_defaults(func=remote, cmd="score")
    sp = sub.add_parser("mcp", help="MCP stdio 서버"); hooks_common(sp)
    sp.set_defaults(func=mcp)

    args = p.parse_args(argv)
    fn = getattr(args, "func", None)
    if fn:
        fn(args)


if __name__ == "__main__":
    main()