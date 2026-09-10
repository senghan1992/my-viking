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


def _verify_server(url: str, key: str, project: str) -> dict:
    """URL·키·프로젝트 슬러그를 한꺼번에 확인한다.

    /api/v1/health 는 인증이 없어서 틀린 키도 '✓ 성공' 으로 통과한다.
    (실제 배포에서 '연결은 됐는데 세션이 하나도 안 보이는' 사고가 이 때문에 났다.)
    인증 + 프로젝트 바인딩까지 검사하는 brief 로 대신 확인한다 — 부수 효과 없음(session_id 없음).
    """
    if not project:
        # 프로젝트 미지정이면 주소 연결만 확인 (키 검증은 못 한다)
        return _api(url, key, "GET", "/health")
    return _api(url, key, "GET", f"/projects/{project}/brief")


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
    # 서버 확인 — 틀린 주소/키로 몇 주 방치되는 것을 막는다 (인증까지 검사)
    try:
        data = _verify_server(url, key, project)
        name = data.get("project_name") if isinstance(data, dict) else None
        print(f"✓ 서버 확인: {url} · 프로젝트 {project or '(미지정)'}"
              + (f" · {name}" if name else ""))
    except SystemExit as e:
        print(f"⚠ 서버 확인 실패: {e}")
        print("  연결 탭의 주소(URL)와 방금 발급받은 키(jv_...)를 다시 확인하고 명령을 다시 실행하세요.")
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
            data = _verify_server(env.get("MYVIKING_URL", ""), env.get("MYVIKING_KEY", ""),
                                  env.get("MYVIKING_PROJECT", ""))
            print(f"✓ 서버 연결·인증: {env.get('MYVIKING_URL')}"
                  + (f" · 프로젝트 {env.get('MYVIKING_PROJECT')}" if env.get("MYVIKING_PROJECT") else ""))
        except (SystemExit, Exception) as e:
            print(f"⚠ 서버 연결/키 확인 실패: {env.get('MYVIKING_URL')} — 주소/키를 확인하세요.")
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
            "additionalContext": data.get("orientation", "") + "\n\n" + (
                "위 브리핑의 '확립된 지식'은 이전 작업이 뒷받침한 내용입니다. "
                "'검증 필요'는 단정하지 말고 확인하세요. 작업 중 새 규칙·함정·결정을 "
                "알게 되면 jv remember (또는 도구)로 남기세요."),
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


# ══════════════════ pi 확장 (허브) — 프로젝트별 연결 관리 ══════════════════ #
# 개념 (git checkout 과 비슷):
#   · 허브 확장 1개만 전역(~/.pi/agent/extensions/myviking.ts)에 설치 — 프로젝트 고정 없음
#   · 연결(키 포함)은 ~/.myviking/connections.json (0600) 에 이름·주소·키·프로젝트로 저장
#   · 각 프로젝트 폴더의 .myviking-connection.json 이 '현재 그 폴더의 연결'을 정한다 (비밀 없음)
#   · pi 를 어떤 폴더에서 열든 그 폴더의 연결만 따라가고, 연결이 없으면 그냥 자유 사용
_PI_EXT_FILE = "myviking.ts"
_PI_LINK_FILE = ".myviking-connection.json"
# 템플릿에 마커로 박혀 있어야 한다 — 확장 내용이 바뀌면 번호를 올린다.
# 마커가 없는 설치본은 오래된 버전으로 보고 pi install 이 최신으로 갱신한다.
_PI_HUB_VERSION = "myviking-hub-v2"


def _pi_path() -> Path:
    """pi 전역 확장(허브) 경로 — 호출 시점에 HOME 을 읽어 테스트 격리 가능."""
    home = os.environ.get("HOME") or str(Path.home())
    return Path(home) / ".pi" / "agent" / "extensions" / _PI_EXT_FILE


def _pi_conns_path() -> Path:
    home = os.environ.get("HOME") or str(Path.home())
    return Path(home) / ".myviking" / "connections.json"


def _pi_link_path(cwd: Path) -> Path:
    return cwd / _PI_LINK_FILE


def _conn_id(url: str, project: str) -> str:
    return f"{url}|{project}"


def _load_conns() -> list[dict]:
    p = _pi_conns_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for c in data.get("connections", []):
        if isinstance(c, dict) and c.get("url") and c.get("key") and c.get("project"):
            out.append(c)
    return out


def _save_conns(conns: list[dict]) -> None:
    p = _pi_conns_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"connections": conns}, ensure_ascii=False, indent=2))
    try:
        p.chmod(0o600)
    except OSError:
        pass


def _print_conns(conns: list[dict], cwd: Path | None = None) -> None:
    cwd_link = None
    if cwd is not None and _pi_link_path(cwd).exists():
        try:
            cwd_link = json.loads(_pi_link_path(cwd).read_text()).get("connection")
        except (OSError, ValueError):
            pass
    if not conns:
        print("저장된 연결이 없습니다. → jv pi install --url <서버> --key jv_... --project <슬러그>")
        return
    print(f"저장된 연결 {len(conns)}개:")
    for i, c in enumerate(conns, 1):
        mark = " ← 현재 폴더" if cwd_link and c.get("id") == cwd_link else ""
        print(f"  [{i}] {c.get('name') or c['project']} — {c['project']} @ {c['url']}{mark}")


_PI_EXT_TEMPLATE = r"""// myviking — 프로젝트 지식 도서관 pi 확장 (허브) (@CREATED@)
// myviking-hub-v2 — 이 마커가 없으면 jv pi install 이 최신 템플릿으로 덮어씁니다
// 이 파일 자체에는 비밀이 없다 — 프로젝트 고정도 없다.
//   · 연결(주소+키+프로젝트): ~/.myviking/connections.json  (0600, jv pi install 이 저장)
//   · 폴더 연결: 각 프로젝트 폴더의 .myviking-connection.json  (git 의 HEAD 같은 것)
//   · pi 를 어느 폴더에서 열든 그 폴더의 연결만 따라간다. 연결이 없으면 그냥 자유 사용.
//   · CLI: jv pi install/switch/disconnect/list/check   · pi 안: /myviking
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { chmodSync, existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

const HOME = homedir();
const LINK_NAME = ".myviking-connection.json";
const CONNS_FILE = join(HOME, ".myviking", "connections.json");

interface Conn { id: string; name: string; url: string; key: string; project: string }
interface Active { name: string; url: string; key: string; project: string }

let active: Active | null = null;   // 현재 세션에서 해석된 연결 (도구는 이걸 쓴다)
let threadId = "";                  // pi 스레드(세션) id — 서가 세션 기록용

function loadConns(): Conn[] {
  try {
    if (!existsSync(CONNS_FILE)) return [];
    const j = JSON.parse(readFileSync(CONNS_FILE, "utf8"));
    const list: unknown[] = Array.isArray(j?.connections) ? j.connections : [];
    return list.filter((c: any) => c && c.url && c.key && c.project);
  } catch { return []; }
}

function findLink(start: string): string | null {
  // 현재 폴더에서 위로 홈까지 올라가며 연결 파일을 찾는다 (git 과 비슷하게)
  let dir = resolve(start);
  for (let i = 0; i < 12; i++) {
    const f = join(dir, LINK_NAME);
    if (existsSync(f)) return f;
    const parent = resolve(dir, "..");
    if (parent === dir || dir === HOME) return null;
    dir = parent;
  }
  return null;
}

function resolveActive(cwd: string): Active | null {
  // 0) 환경변수 (CI/컨테이너용) — 폴더 연결보다 우선
  if (process.env.MYVIKING_URL && process.env.MYVIKING_KEY && process.env.MYVIKING_PROJECT) {
    const p = process.env.MYVIKING_PROJECT;
    return { name: p, url: process.env.MYVIKING_URL, key: process.env.MYVIKING_KEY, project: p };
  }
  // 1) 폴더 연결 파일
  const link = findLink(cwd);
  if (!link) return null;
  try {
    const j = JSON.parse(readFileSync(link, "utf8"));
    const id = String(j?.connection || "");
    const conn = loadConns().find((c) => c.id === id);
    return conn ? { name: conn.name || conn.project, url: conn.url, key: conn.key, project: conn.project } : null;
  } catch { return null; }
}

async function call<T>(c: Active, path: string, method = "GET", body?: unknown): Promise<T> {
  const r = await fetch(c.url.replace(/\/+$/, "") + path, {
    method,
    headers: { Authorization: "Bearer " + c.key, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`myviking ${path}: HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

function noConn(): string {
  return "이 폴더는 myviking 프로젝트에 연결되어 있지 않습니다.\n"
    + "  · 새로 연결: jv pi install --url <서버> --key jv_... --project <슬러그>\n"
    + "    (서버 → 프로젝트 → 🔗 에이전트 연결 탭에서 키 발급)\n"
    + "  · 저장된 연결로 바꾸기: /myviking switch  ·  삭제: /myviking remove  ·  목록: /myviking";
}

function briefUrl(c: Active, sid: string): string {
  const q = new URLSearchParams({ agent: "pi" });
  if (sid) q.set("session_id", sid);
  return `/api/v1/projects/${c.project}/brief?${q.toString()}`;
}

async function injectBrief(c: Active, sid: string, pi: ExtensionAPI): Promise<void> {
  try {
    const b = await call<{ orientation: string }>(c, briefUrl(c, sid));
    if (b.orientation) {
      await pi.sendMessage(
        { customType: "myviking-brief", content: b.orientation, display: false },
        { deliverAs: "nextTurn" });
    }
  } catch { /* 서버에 닿지 않아도 코딩 세션은 계속된다 */ }
}

function fmtConn(c: Conn, i: number): string {
  return `${i}. ${c.name || c.project} — ${c.project}`;
}

function linkPath(cwd: string): string {
  return join(resolve(cwd), LINK_NAME);
}

function setFolderLink(cwd: string, id: string): void {
  const f = linkPath(cwd);
  writeFileSync(f, JSON.stringify({ connection: id }, null, 2));
}

export default function (pi: ExtensionAPI) {
  // ── 도구 4종 — 연결된 프로젝트가 이 폴더 일 때만 동작 ──
  const jobs: Array<{ name: string; label: string; description: string; params: any; run: (c: Active, p?: any) => Promise<string> }> = [
    {
      name: "viking_brief",
      label: "Viking 브리핑",
      description: "프로젝트 도서관의 작업 브리핑(확립 지식·검증 필요·최근 작업)을 가져온다. 세션 시작 시 자동 주입되며, 다시 보려면 호출한다.",
      params: Type.Object({}),
      run: (c) => call<{ orientation: string }>(c, briefUrl(c, threadId)).then((b) => b.orientation),
    },
    {
      name: "viking_search",
      label: "Viking 검색",
      description: "프로젝트 지식 도서관에서 관련 지식을 검색한다. 막혔거나 규칙·함정이 궁금할 때 호출한다.",
      params: Type.Object({ query: Type.String({ description: "검색어" }) }),
      async run(c, p) {
        const r = await call<{ items: Array<{ category: string; title: string; text: string; verified?: boolean }>; warnings: Array<{ title: string }> }>(
          c, `/api/v1/projects/${c.project}/search?q=${encodeURIComponent(p.query)}`);
        const lines = r.items.map((it) => `[${it.category}] ${it.title}${it.verified ? "" : " ⟨검증 전⟩"}\n${it.text.slice(0, 500)}`);
        for (const w of r.warnings) lines.push(`⚠ [검증 필요] ${w.title}`);
        return lines.length ? lines.join("\n\n") : "관련 지식 없음";
      },
    },
    {
      name: "viking_remember",
      label: "Viking 기록",
      description: "새로 정한 규칙·함정·결정을 지식 도서관에 남긴다. confirmed=true 면 확립으로 기록.",
      params: Type.Object({
        title: Type.String({ description: "제목" }),
        content: Type.String({ description: "내용" }),
        category: Type.Optional(Type.String({ description: "knowledge|commands|pitfalls|decisions" })),
        confirmed: Type.Optional(Type.Boolean()),
      }),
      async run(c, p) {
        const r = await call<{ uri: string }>(c, `/api/v1/projects/${c.project}/remember`, "POST", {
          title: p.title, content: p.content,
          category: p.category ?? "knowledge", confirmed: !!p.confirmed,
        });
        return `기록됨 → ${r.uri}`;
      },
    },
    {
      name: "viking_score",
      label: "Viking 채점",
      description: "주입된 지식이 틀렸으면 memory_id 와 outcome=bad 로 알려 교정하게 한다.",
      params: Type.Object({
        memory_id: Type.Number({ description: "지식 id" }),
        outcome: Type.Optional(Type.String({ description: "good|bad|settled" })),
      }),
      async run(c, p) {
        const r = await call<{ status: string }>(c, `/api/v1/projects/${c.project}/score`, "POST", {
          memory_id: p.memory_id, outcome: p.outcome ?? "settled",
        });
        return `상태 ${r.status}`;
      },
    },
  ];

  for (const job of jobs) {
    pi.registerTool({
      name: job.name,
      label: job.label,
      description: job.description,
      promptSnippet: `${job.name} — ${job.description.split(".")[0]}.`,
      parameters: job.params,
      async execute(_id: string, params: any) {
        try {
          if (!active) return { content: [{ type: "text", text: noConn() }] };
          return { content: [{ type: "text", text: await job.run(active, params) }] };
        } catch (e) {
          return { content: [{ type: "text", text: `myviking 오류: ${(e as Error).message}` }] };
        }
      },
    });
  }

  // ── 세션 시작 → 이 폴더의 연결 해석 + 브리핑 주입 (startup/new 에만) ──
  pi.on("session_start", async (event, ctx) => {
    active = resolveActive(ctx.cwd);
    try { threadId = ctx.sessionManager.getSessionId() || ""; } catch { threadId = ""; }
    if (!active) return;                           // 연결 없으면 자유 사용
    if (ctx.hasUI) ctx.ui.notify(`myviking: ${active.name} 연결됨`, "info");
    if (event.reason !== "startup" && event.reason !== "new") return;
    await injectBrief(active, threadId, pi);
  });

  // ── /myviking — 연결 목록/전환/새 연결/해제/삭제 (git checkout 느낌) ──
  pi.registerCommand("myviking", {
    description: "myviking: 연결 목록·전환·새 연결·해제·삭제 (list | switch | connect | disconnect | remove)",
    getArgumentCompletions: (prefix: string) =>
      ["list", "switch", "connect", "disconnect", "remove"]
        .filter((v) => v.startsWith(prefix))
        .map((v) => ({ value: v, label: v })),
    handler: async (args, ctx) => {
      const word = (args || "").trim().split(/\s+/)[0] || "list";

      if (word === "connect") {
        if (!ctx.hasUI) { ctx.ui.notify("터미널에서: jv pi install --url ... --key ... --project ...", "info"); return; }
        const url = (await ctx.ui.input("서버 주소", "http://ip:포트 — 프로젝트를 만든 서버")) || "";
        const key = (await ctx.ui.input("API 키 (jv_...) — 서버 → 프로젝트 → 🔗 에이전트 연결")) || "";
        const project = (await ctx.ui.input("프로젝트 슬러그", "서가 주소의 마지막 부분 (예: my-project)")) || "";
        if (!url || !key || !project) { ctx.ui.notify("연결하지 않았습니다 (입력 취소).", "info"); return; }
        try {
          const b = await call<{ project_name: string }>({ name: project, url, key, project }, `/api/v1/projects/${project}/brief?agent=pi`);
          const conns = loadConns();
          const id = `${url}|${project}`;
          const rest = conns.filter((c) => c.id !== id);
          rest.unshift({ id, name: b.project_name || project, url, key, project });
          try {
            mkdirSync(join(HOME, ".myviking"), { recursive: true });
            writeFileSync(CONNS_FILE, JSON.stringify({ connections: rest }, null, 2));
            chmodSync(CONNS_FILE, 0o600);
          } catch {}
          setFolderLink(ctx.cwd, id);
          active = { name: b.project_name || project, url, key, project };
          ctx.ui.notify(`✓ 이 폴더를 '${b.project_name || project}' 에 연결했습니다 (키 저장: ~/.myviking/connections.json)`, "info");
          await injectBrief(active, threadId, pi);
        } catch (e) {
          ctx.ui.notify(`연결 실패: ${(e as Error).message} — 주소/키/슬러그를 확인하세요.`, "error");
        }
        return;
      }

      const conns = loadConns();

      if (word === "remove") {
        if (!conns.length) { ctx.ui.notify("저장된 연결이 없습니다.", "info"); return; }
        if (!ctx.hasUI) { ctx.ui.notify("터미널에서: jv pi remove <이름>", "info"); return; }
        const items = conns.map((c, i) => fmtConn(c, i + 1));
        const pick = await ctx.ui.select("삭제할 연결 (키도 함께 제거됩니다)", items);
        if (!pick) { ctx.ui.notify("취소했습니다.", "info"); return; }
        const conn = conns[parseInt(pick.split(".")[0], 10) - 1];
        if (!conn) { ctx.ui.notify("찾을 수 없습니다.", "info"); return; }
        const rest = conns.filter((c) => c.id !== conn.id);
        try {
          writeFileSync(CONNS_FILE, JSON.stringify({ connections: rest }, null, 2));
          chmodSync(CONNS_FILE, 0o600);
        } catch {}
        let msg = `✓ 연결 삭제: ${conn.name || conn.project} (${conn.project}) — 키도 함께 제거했습니다.`;
        const f = linkPath(ctx.cwd);
        let linkedId: string | null = null;
        try { linkedId = (JSON.parse(readFileSync(f, "utf8")) as { connection?: string }).connection || null; } catch {}
        if (existsSync(f) && linkedId === conn.id) {
          try { unlinkSync(f); } catch {}
          active = null;
          msg += `\n이 폴더의 연결도 함께 해제했습니다 — 자유 사용.`;
        }
        ctx.ui.notify(msg, "info");
        return;
      }

      if (word === "disconnect") {
        const f = linkPath(ctx.cwd);
        if (!existsSync(f)) { ctx.ui.notify("이 폴더는 연결되어 있지 않습니다.", "info"); return; }
        try { unlinkSync(f); } catch {}
        active = null;
        ctx.ui.notify("이 폴더의 myviking 연결을 해제했습니다 — 자유 사용.", "info");
        return;
      }

      if (word === "switch") {
        if (!conns.length) {
          ctx.ui.notify("저장된 연결이 없습니다. /myviking connect 또는 jv pi install ...", "info");
          return;
        }
        const items = conns.map((c, i) => fmtConn(c, i + 1)).concat([`${conns.length + 1}. ＋ 새로 연결하기 (/myviking connect)`]);
        const pick = ctx.hasUI ? await ctx.ui.select("연결할 프로젝트 (이 폴더를 바꿉니다)", items) : null;
        if (!pick) { ctx.ui.notify("취소했습니다.", "info"); return; }
        const idx = parseInt(pick.split(".")[0], 10) - 1;
        if (idx === conns.length) {
          ctx.ui.notify("터미널에서: jv pi install --url ... --key ... --project ... (설명은 연결 탭)", "info");
          return;
        }
        const conn = conns[idx];
        if (!conn) { ctx.ui.notify("찾을 수 없습니다.", "info"); return; }
        try { setFolderLink(ctx.cwd, conn.id); } catch {}
        active = { name: conn.name || conn.project, url: conn.url, key: conn.key, project: conn.project };
        ctx.ui.notify(`✓ 이 폴더를 '${conn.name || conn.project}' 프로젝트로 바꿔 연결했습니다 (git checkout 느낌)`, "info");
        await injectBrief(active, threadId, pi);
        return;
      }

      // list (기본)
      const link = findLink(ctx.cwd);
      const lines: string[] = [];
      if (active) lines.push(`현재 폴더 연결: ${active.name} — ${active.project} (${active.url})`);
      else if (link) lines.push(`현재 폴더가 가리키는 연결을 찾지 못했습니다 — jv pi install 또는 /myviking switch`);
      else lines.push(`이 폴더는 연결되어 있지 않습니다 (자유 사용). /myviking switch 또는 connect`);
      if (conns.length) {
        lines.push("");
        lines.push(`저장된 연결 ${conns.length}개 — /myviking switch 로 전환, remove 로 삭제:`);
        conns.forEach((c, i) => lines.push(fmtConn(c, i + 1)));
      }
      if (ctx.hasUI) ctx.ui.notify(lines.join("\n"), "info");
    },
  });
}
"""


def _pi_hub_installed() -> bool:
    """허브 확장이 설치되어 있는가? (없거나 레거시=프로젝트 고정 버전이면 False)"""
    path = _pi_path()
    if not path.exists():
        return False
    try:
        text = path.read_text()
    except OSError:
        return False
    if "const URL =" in text and "MYVIKING_URL" not in text:
        return False  # 옛 방식: URL/KEY/PROJECT 가 박힌 버전 → 허브로 교체 필요
    if _PI_HUB_VERSION not in text:
        return False  # 오래된 허브 버전 → 최신 템플릿으로 갱신
    return "resolveActive" in text


def _install_hub_extension() -> Path:
    """허브 확장(전역 1개) 설치 — 이미 최신이면 그대로 둔다."""
    path = _pi_path()
    if not _pi_hub_installed():
        text = (_PI_EXT_TEMPLATE
                .replace("@CREATED@", __import__("datetime").date.today().isoformat()))
        leftovers = [t for t in ("@CREATED@", "@URL@", "@KEY@", "@PROJECT@", "{{", "}}") if t in text]
        if leftovers:
            raise SystemExit(f"jv: pi 확장 템플릿 오류 — 치환이 완전하지 않습니다 ({', '.join(leftovers)}).")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path


def pi_install(args: argparse.Namespace) -> None:
    """새 연결 저장 + 현재 폴더 연결 + 허브 확장 설치. (프로젝트마다 실행)"""
    url, key, project = _env_args(args)
    if not project:
        print("⚠ --project (프로젝트 슬러그) 가 필요합니다 — 서가 주소의 마지막 부분.", file=sys.stderr)
        raise SystemExit(2)
    if not key:
        print("⚠ --key 가 필요합니다 (연결 탭에서 발급).", file=sys.stderr)
        raise SystemExit(2)
    # 서버·키·프로젝트 확인 (인증까지 — 틀린 키로 설치되는 사고 차단)
    data = _verify_server(url, key, project)
    name = data.get("project_name") or project
    cwd = Path(args.cwd or os.getcwd())

    # 1) 연결 저장소 (~/.myviking/connections.json, 키 포함 0600)
    conns = [c for c in _load_conns() if c.get("id") != _conn_id(url, project)]
    conns.insert(0, {"id": _conn_id(url, project), "name": name, "url": url,
                     "key": key, "project": project, "folder": str(cwd),
                     "updated_at": __import__("datetime").date.today().isoformat()})
    _save_conns(conns)

    # 2) 이 폴더 연결 (비밀 없음 — git 의 HEAD 같은 파일)
    link = _pi_link_path(cwd)
    try:
        link.write_text(json.dumps({"connection": _conn_id(url, project)}, ensure_ascii=False, indent=2))
    except OSError as e:
        print(f"⚠ 폴더 연결 파일을 쓸 수 없습니다: {e}", file=sys.stderr)
        raise SystemExit(1)

    # 3) 허브 확장 (전역 1개) — 구버전이면 최신 템플릿으로 덮어씀
    hub = _install_hub_extension()

    print(f"✓ 서버 확인: {url} · 프로젝트 {project} · {name}")
    print(f"✓ 연결 저장: {_pi_conns_path()} (키 0600, 현재 {len(conns)}개 연결)")
    print(f"✓ 이 폴더 연결: {cwd} → {name} ({_PI_LINK_FILE})")
    print(f"✓ pi 허브 확장: {hub}")
    print("이제 이 폴더에서 pi 를 열면 자동으로 이 프로젝트에 연결됩니다.")
    print("  · 저장된 연결 목록/전환: jv pi list / jv pi switch <이름>")
    print("  · pi 안에서 목록·전환·새 연결·해제: /myviking")


def pi_list(args: argparse.Namespace) -> None:
    _print_conns(_load_conns(), Path(args.cwd or os.getcwd()))


def pi_switch(args: argparse.Namespace) -> None:
    """저장된 연결로 현재 폴더를 바꿔 연결 (git checkout 느낌)."""
    conns = _load_conns()
    cwd = Path(args.cwd or os.getcwd())
    q = (getattr(args, "name", "") or "").strip().lower()
    if q:
        hit = [c for c in conns if q in str(c.get("name", "")).lower()
               or q in str(c.get("project", "")).lower() or q in str(c.get("id", "")).lower()]
        if len(hit) == 1:
            conn = hit[0]
        elif len(hit) > 1:
            _print_conns(conns, cwd)
            print(f"\n'{q}' 에 해당하는 연결이 여러 개입니다 — 이름/슬러그로 더 정확히 지정하세요.")
            raise SystemExit(2)
        else:
            _print_conns(conns, cwd)
            print(f"\n'{q}' 를 찾지 못했습니다.")
            raise SystemExit(2)
    elif len(conns) == 1:
        conn = conns[0]
    else:
        _print_conns(conns, cwd)
        print("\n연결 이름을 지정하세요 — 예: jv pi switch 데이터자판기")
        raise SystemExit(2)

    _pi_link_path(cwd).write_text(json.dumps({"connection": conn["id"]}, ensure_ascii=False, indent=2))
    print(f"✓ 이 폴더를 '{conn.get('name') or conn['project']}' 프로젝트로 바꿔 연결했습니다: {cwd}")
    print("pi 안에서 쓰고 있다면 /myviking list 로 확인하거나 /reload 하세요.")


def pi_disconnect(args: argparse.Namespace) -> None:
    cwd = Path(args.cwd or os.getcwd())
    link = _pi_link_path(cwd)
    if not link.exists():
        print("이 폴더는 연결되어 있지 않습니다.")
        return
    link.unlink()
    print(f"✓ 이 폴더의 myviking 연결을 해제했습니다: {cwd} — 자유 사용")


def pi_remove(args: argparse.Namespace) -> None:
    """저장된 연결 삭제 (키 포함). 현재 폴더가 그 연결을 가리키면 링크도 함께 해제."""
    conns = _load_conns()
    cwd = Path(args.cwd or os.getcwd())
    q = (getattr(args, "name", "") or "").strip().lower()
    if q:
        hit = [c for c in conns if q in str(c.get("name", "")).lower()
               or q in str(c.get("project", "")).lower() or q in str(c.get("id", "")).lower()]
        if len(hit) == 1:
            conn = hit[0]
        elif len(hit) > 1:
            _print_conns(conns, cwd)
            print(f"\n'{q}' 에 해당하는 연결이 여러 개입니다 — 이름/슬러그로 더 정확히 지정하세요.")
            raise SystemExit(2)
        else:
            _print_conns(conns, cwd)
            print(f"\n'{q}' 를 찾지 못했습니다.")
            raise SystemExit(2)
    elif len(conns) == 1:
        conn = conns[0]
    else:
        _print_conns(conns, cwd)
        print("\n삭제할 연결 이름을 지정하세요 — 예: jv pi remove 데이터자판기")
        raise SystemExit(2)

    rest = [c for c in conns if c.get("id") != conn["id"]]
    _save_conns(rest)
    print(f"✓ 연결 삭제: {conn.get('name') or conn['project']} ({conn['project']} @ {conn['url']}) — 키도 함께 제거했습니다")

    link = _pi_link_path(cwd)
    cid = None
    if link.exists():
        try:
            cid = json.loads(link.read_text()).get("connection")
        except (OSError, ValueError):
            cid = None
    if cid == conn["id"]:
        link.unlink(missing_ok=True)
        print(f"✓ 이 폴더({cwd})가 그 연결을 가리키고 있어 링크도 함께 해제했습니다 — 자유 사용")
    elif rest:
        print("남은 연결:", ", ".join(c.get("name") or c["project"] for c in rest))


def pi_uninstall(args: argparse.Namespace) -> None:
    path = _pi_path()
    if path.exists():
        path.unlink()
        print(f"✓ pi 허브 확장 제거: {path}")
    else:
        print("설치된 pi 확장이 없습니다.")
    conns = _load_conns()
    if conns:
        print(f"참고: 저장된 연결 {len(conns)}개는 ~/.myviking/connections.json 에 남아 있습니다. "
              f"(지우려면: jv pi remove <이름>)")


def pi_check(args: argparse.Namespace) -> None:
    cwd = Path(args.cwd or os.getcwd())
    hub = _pi_path()
    if not hub.exists():
        print("⚠ pi 허브 확장이 설치되어 있지 않습니다 → jv pi install --url ... --key ... --project ...")
        return
    if not _pi_hub_installed():
        print("⚠ 설치된 pi 확장이 구버전/옛 방식입니다 → jv pi install 한 번 실행하면 최신 허브로 갱신됩니다.")
        return
    mode = hub.stat().st_mode & 0o777
    print(f"✓ pi 허브 확장: {hub}" + ("" if mode == 0o600 else f"  ⚠ 권한 {oct(mode)} (0600 권장)"))

    link = _pi_link_path(cwd)
    if not link.exists():
        print(f"이 폴더({cwd})는 연결되어 있지 않습니다 (자유 사용).")
        print("  연결: jv pi install --url ... --key ... --project ... · 저장된 연결에서: jv pi switch")
        return
    try:
        cid = json.loads(link.read_text()).get("connection")
    except (OSError, ValueError):
        print("⚠ .myviking-connection.json 을 읽을 수 없습니다. jv pi install 을 다시 실행하세요.")
        return
    conns = _load_conns()
    conn = next((c for c in conns if c.get("id") == cid), None)
    if not conn:
        print(f"⚠ 이 폴더가 가리키는 연결({cid})이 저장소에 없습니다 → jv pi install 또는 jv pi switch")
        return
    print(f"✓ 폴더 연결: {cwd} → {conn.get('name') or conn['project']} ({conn['project']})")
    try:
        _verify_server(conn["url"], conn["key"], conn["project"])
        print(f"✓ 서버 연결·인증: {conn['url']} · 프로젝트 {conn['project']}")
    except SystemExit as e:
        print(f"⚠ 서버 연결/키 확인 실패: {e}")# ══════════════════ main ══════════════════ #
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

    sp = sub.add_parser("pi", help="pi 코딩 에이전트 확장·프로젝트 연결 관리")
    pisub = sp.add_subparsers(dest="action", required=True)
    pp = pisub.add_parser("install", help="새 연결 저장 + 이 폴더에 연결 + 허브 확장 설치"); hooks_common(pp)
    pp.add_argument("--cwd", default="")
    pp.set_defaults(func=pi_install)
    pp = pisub.add_parser("list", help="저장된 연결 목록")
    pp.add_argument("--cwd", default="")
    pp.set_defaults(func=pi_list)
    pp = pisub.add_parser("switch", help="저장된 연결로 이 폴더를 바꿔 연결 (git checkout 느낌)")
    pp.add_argument("name", nargs="?", default="", help="연결 이름/슬러그 (생략 시 하나뿐이면 자동)")
    pp.add_argument("--cwd", default="")
    pp.set_defaults(func=pi_switch)
    pp = pisub.add_parser("disconnect", help="이 폴더의 연결 해제")
    pp.add_argument("--cwd", default="")
    pp.set_defaults(func=pi_disconnect)
    pp = pisub.add_parser("remove", aliases=["rm"], help="저장된 연결 삭제 (키 포함)")
    pp.add_argument("name", nargs="?", default="", help="연결 이름/슬러그 (생략 시 하나뿐이면 자동)")
    pp.add_argument("--cwd", default="")
    pp.set_defaults(func=pi_remove)
    pp = pisub.add_parser("uninstall")
    pp.set_defaults(func=pi_uninstall)
    pp = pisub.add_parser("check"); hooks_common(pp)
    pp.add_argument("--cwd", default="")
    pp.set_defaults(func=pi_check)

    args = p.parse_args(argv)
    fn = getattr(args, "func", None)
    if fn:
        fn(args)


if __name__ == "__main__":
    main()