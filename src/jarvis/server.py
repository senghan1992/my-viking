"""HTTP API.

Thin wrapper over ``Jarvis`` so any language or a web UI can drive it. Bound to
localhost by default: this database holds your accumulated project context, so
it should not be reachable from the network unless you decide otherwise.

Importing this module requires the ``server`` extra (fastapi, uvicorn,
pydantic); the rest of MyViking does not.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .auth import KeyStore
from .backup import BackupManager, start_backup_loop
from .connect import CLIENTS, build as build_connection, instruction_file
from .mcp_core import Handler, tools
from .models import Uri
from .service import Jarvis
from .ui import DASHBOARD_HTML

# Everything a route signature mentions must live at module level. With
# postponed annotation evaluation (``from __future__ import annotations``),
# FastAPI resolves a handler's type hints against the *module* namespace, so a
# name imported or defined inside create_app() resolves to nothing — and the
# parameter is silently misread as a query parameter instead of a body. That
# failure mode is a 422 with no hint about the cause, so keep these here.
class InitBody(BaseModel):
    project: str
    template: str = "default"
    description: str = ""
    stack: list[str] = Field(default_factory=list)


class PromptBody(BaseModel):
    project: str
    name: str
    template: str
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class RenderBody(BaseModel):
    project: str
    name: str
    values: dict[str, Any] = Field(default_factory=dict)
    strict: bool = False


class MemoryBody(BaseModel):
    project: str
    category: str
    title: str
    statement: str
    detail: str = ""
    tags: list[str] = Field(default_factory=list)
    confidence: float = 0.8


class ResourceBody(BaseModel):
    project: str
    name: str
    text: str
    title: str = ""
    tags: list[str] = Field(default_factory=list)


class PrepareBody(BaseModel):
    project: str = ""
    repo: str = ""
    path: str = ""
    question: str
    agent: str = ""
    session_id: str = ""
    prompt: str = ""
    values: dict[str, Any] = Field(default_factory=dict)
    use_cache: bool = True
    include_global: bool = True
    max_tier: int = 2
    kinds: list[str] | None = None
    # Template for the project if this call has to create it (see ResolveBody).
    template: str = "default"


class CommitBody(BaseModel):
    project: str
    question: str
    answer: str
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    prompt_uri: str = ""
    tags: list[str] = Field(default_factory=list)
    outcome: str = ""
    distill: bool | None = None
    trace_id: str = ""
    latency_ms: int = 0
    agent: str = ""
    files: list[str] = Field(default_factory=list)


class EditMemoryBody(BaseModel):
    uri: str
    title: str | None = None
    statement: str | None = None
    body: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    confidence: float | None = None


class ConfirmBody(BaseModel):
    uri: str
    confidence: float | None = None


class ScoreBody(BaseModel):
    trace_id: str
    name: str = "helpfulness"
    value: float
    comment: str = ""
    source: str = "human"
    apply_to_memory: bool = True
    uris: list[str] | None = None


class AliasBody(BaseModel):
    alias: str
    project: str
    kind: str = "repo"


class ResolveBody(BaseModel):
    project: str = ""
    repo: str = ""
    path: str = ""
    create: bool = False
    # Only used when this call creates the project. Coding-agent hooks send
    # "coding" so the auto-made project has the right categories from the start.
    template: str = "default"


class KeyBody(BaseModel):
    name: str
    projects: list[str] = Field(default_factory=lambda: ["*"])


class ConnectionBody(BaseModel):
    # The key to *embed* in the generated snippet travels in the POST body, not
    # a URL query string, so it never lands in proxy/access logs or browser
    # history. It is distinct from the Authorization header used to *auth. the
    # request — an admin can mint a scoped key for a teammate and embed that.
    client: str = "claude-code"
    base_url: str = ""
    key: str = ""


class FeedbackBody(BaseModel):
    project: str
    uri: str
    helpful: bool = True
    note: str = ""


class BackupConfigBody(BaseModel):
    provider: str | None = None  # none | gdrive | local
    every_hours: float | None = None
    keep: int | None = None
    folder: str | None = None
    local_path: str | None = None


class BackupConnectBody(BaseModel):
    client_id: str
    client_secret: str


PROTOCOL_VERSION = "2024-11-05"
_log = logging.getLogger("myviking.auth")


def _new_session_id() -> str:
    import secrets

    return secrets.token_hex(16)


def _handle_rpc(mcp: Handler, msg: dict[str, Any]) -> dict[str, Any] | None:
    """Answer one JSON-RPC message. Returns None for notifications."""
    method = msg.get("method")
    rid = msg.get("id")
    if rid is None:
        return None  # notification

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "myviking", "version": __version__},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools()}}
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            payload = mcp.dispatch(params.get("name", ""), params.get("arguments") or {})
            text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {"content": [{"type": "text", "text": text}], "isError": False},
            }
        except Exception as exc:
            # Tool failures are results, not transport errors: the agent should
            # see the message and adjust rather than lose the connection.
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "content": [{"type": "text", "text": f"오류: {exc}"}],
                    "isError": True,
                },
            }
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": f"지원하지 않는 메서드: {method}"},
    }


# Endpoints that must work before you hold a key.
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}


def create_app(home: str | None = None, allow_origins: list[str] | None = None):
    jarvis = Jarvis(home=home)
    keys = KeyStore(jarvis.store.db)
    app = FastAPI(
        title="MyViking",
        description="프로젝트별 자가학습 컨텍스트 데이터베이스 · 에이전트 컨텍스트 서버",
        version=__version__,
        docs_url="/docs",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins or ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Failed-auth backoff. This server is designed to sit on a port-forwarded
    # home connection, where "someone will try keys all day" is the baseline,
    # not the edge case. Per-IP, in-memory: simple, and it only ever touches
    # requests that already failed to authenticate.
    auth_failures: dict[str, list[float]] = {}
    AUTH_WINDOW, AUTH_MAX_FAILS = 60.0, 10

    # Behind the bundled TLS reverse proxy every request arrives from the proxy's
    # own IP, so a single attacker would trip the per-IP backoff for *everyone*
    # and hide behind one address. Honour X-Forwarded-For to get the real client
    # — but only when explicitly told we sit behind a trusted proxy, since the
    # header is trivially spoofed when we are directly exposed.
    import os as _os

    TRUST_PROXY = _os.environ.get("MYVIKING_TRUST_PROXY", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

    def _client_ip(request: Request) -> str:
        if TRUST_PROXY:
            fwd = request.headers.get("x-forwarded-for", "")
            if fwd:
                # The *last* hop is the one our own proxy appended; anything
                # before it was supplied by the client and is free to forge.
                # (The bundled Caddy sends a single value, so both agree there.)
                return fwd.split(",")[-1].strip()
        return request.client.host if request.client else "?"

    def _auth_blocked(ip: str) -> bool:
        import time as _time

        now = _time.time()
        log = auth_failures.get(ip)
        if not log:
            return False
        log[:] = [t for t in log if now - t < AUTH_WINDOW]
        if not log:
            auth_failures.pop(ip, None)
            return False
        return len(log) >= AUTH_MAX_FAILS

    def _auth_failed(ip: str) -> None:
        import time as _time

        if len(auth_failures) > 10_000:  # an IP-rotating attacker defeats any map
            auth_failures.clear()
        auth_failures.setdefault(ip, []).append(_time.time())

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        """Require a key once one exists.

        Before any key is created the server is open, which is right for a
        localhost trial. Creating the first key flips the whole surface to
        authenticated — there is no partially-protected state to misread.
        """
        path = request.url.path
        if (
            request.method == "OPTIONS"
            or path in PUBLIC_PATHS
            or path.startswith("/ui")
            or path == "/"
        ):
            return await call_next(request)
        if not keys.any_active():
            if keys.auth_lost():
                # Auth was on and the key table is gone (index.db lost/replaced).
                # Refusing is the only safe answer on an exposed host; the fix is
                # a restore, or a fresh `jv key create` on the server itself.
                return JSONResponse(
                    {"detail": "인증이 켜져 있던 서버인데 API 키 DB 가 없습니다. 백업에서 복원하거나 "
                               "서버에서 `jv key create <이름>` 으로 새 키를 만드세요."},
                    status_code=503,
                )
            return await call_next(request)

        ip = _client_ip(request)
        header = request.headers.get("authorization", "")
        raw = header[7:].strip() if header.lower().startswith("bearer ") else ""
        raw = raw or request.headers.get("x-api-key", "")
        info = keys.verify(raw)
        if info is None:
            # The backoff only ever answers *failed* attempts. A valid key from
            # the same address must keep working — otherwise one teammate's
            # revoked key (or a hook with a typo) locks the whole office NAT out,
            # and the admin cannot even get in to fix it.
            if _auth_blocked(ip):
                return JSONResponse(
                    {"detail": f"인증 실패가 너무 잦습니다. 약 {int(AUTH_WINDOW)}초 후 다시 시도하세요."},
                    status_code=429,
                    headers={"Retry-After": str(int(AUTH_WINDOW))},
                )
            _auth_failed(ip)
            # One structured line per failure, so a fail2ban/loki rule has
            # something to read — the in-memory counter alone is invisible.
            _log.warning(
                "auth_failed ip=%s path=%s key=%s", ip, path,
                (raw[:7] + "…") if raw else "(none)",
            )
            return JSONResponse(
                {"detail": "유효한 API 키가 필요합니다 (Authorization: Bearer jv_...)"},
                status_code=401,
            )
        auth_failures.pop(ip, None)
        request.state.key = info

        # Scope enforcement, centralised for the common vectors: a project named
        # in the path (/projects/{project}/...) or in a ?project= query param.
        # Body- and uri-addressed routes still guard themselves (the request body
        # is not safely readable here), but this one check closes the bulk of the
        # surface so a new scoped route cannot silently leak by forgetting a call.
        if info is not None and not info.allows("*"):
            scoped = ""
            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "projects":
                scoped = unquote(parts[1])
            if not scoped:
                scoped = request.query_params.get("project", "")
            if scoped and not info.allows(scoped):
                return JSONResponse(
                    {"detail": f"이 키는 '{scoped}' 에 접근할 수 없습니다"},
                    status_code=403,
                )
        return await call_next(request)

    def _guard(request: Request, project: str) -> None:
        """A key scoped to some projects must not touch the others. Used by
        routes whose project lives in the body or a uri, where the middleware
        cannot see it."""
        info = getattr(request.state, "key", None)
        if info is not None and project and not info.allows(project):
            raise HTTPException(403, f"이 키는 '{project}' 에 접근할 수 없습니다")

    def _guard_uri(request: Request, uri: str) -> None:
        """Guard a route addressed by a memory uri, whose scope is the project."""
        try:
            parsed = Uri.parse(uri)
        except Exception as exc:  # "jarvis://" and friends: a 400, not a traceback
            raise HTTPException(400, f"잘못된 URI: {uri} ({exc})") from exc
        _guard(request, parsed.scope)

    def _guard_trace(request: Request, trace_id: str) -> None:
        info = getattr(request.state, "key", None)
        if info is None or info.allows("*"):
            return
        row = jarvis.store.db.one("SELECT scope FROM traces WHERE id=?", (trace_id,))
        if row is not None:
            _guard(request, row["scope"])

    def _scoped_key(request: Request):
        """The caller's key if it is confined to some projects, else None
        (all-access key, or an open server before any key exists)."""
        info = getattr(request.state, "key", None)
        return info if (info is not None and not info.allows("*")) else None

    def _confine(request: Request, project: str) -> None:
        """Cross-project reads (metrics/traces/agents/... with no project) span
        every scope. A scoped key must name a project it's allowed to see; an
        all-access key (or open server) may still read the whole picture."""
        if _scoped_key(request) is not None and not project:
            raise HTTPException(
                403, "스코프가 제한된 키는 project 를 지정해야 합니다 (전체 집계 불가)"
            )
        _guard(request, project)

    def _require_admin(request: Request) -> None:
        """Key management is an all-access operation. A scoped key must not list,
        mint, or revoke keys — that would let it escalate straight past its scope.
        The server stays open only until the first key exists (bootstrap)."""
        if _scoped_key(request) is not None:
            raise HTTPException(403, "키 관리는 전체 접근 키만 가능합니다")

    def _resolve_guarded(request: Request, body) -> dict[str, Any]:
        """Resolve a project for a route that may create one, honouring scope.

        A scoped key must never invent a project *outside* its scope. But hooks
        resolve by repo/path with no explicit name, so refusing every unnamed
        creation would quietly kill capture under a project-scoped key — the
        common deployment. So: an explicit name must be in scope; an unnamed
        resolve is allowed to create only when the slug the repo/path would map
        to is itself in scope. Either way the resolved project is guarded, so a
        scoped key can only ever create the project it is already allowed to see.
        """
        from .service import _project_from_alias

        info = _scoped_key(request)
        name = body.project or ""
        if info is not None and name:
            _guard(request, name)
        want_create = getattr(body, "create", True)
        if info is None:
            may_create = want_create
        elif name:
            may_create = want_create and info.allows(name)
        else:
            guess = _project_from_alias(body.repo or body.path or "")
            may_create = want_create and bool(guess) and info.allows(guess)
        resolved = jarvis.resolve_project(
            name, body.repo, body.path, create=may_create, template=body.template
        )
        if resolved["project"]:
            _guard(request, resolved["project"])
        return resolved

    # ----- meta -------------------------------------------------------
    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        llm = jarvis.store.llm
        embed = jarvis.config.embed
        # A store on the offline fallbacks still works, but its distillation is
        # crude and its recall is keyword-shaped, not semantic. Say so plainly
        # so the dashboard can nudge toward the settings that make it good.
        embed_real = embed.provider not in ("", "hashing")
        notes = []
        if not llm.available:
            notes.append("LLM 미설정 — 증류/요약이 규칙 기반입니다 (JARVIS_LLM_PROVIDER+키 권장)")
        if not embed_real:
            notes.append("임베딩이 해싱 폴백 — 의미 기반 회상이 제한됩니다 (JARVIS_EMBED_PROVIDER 권장)")
        # Self-observation: a background sweep or backup that dies must show here,
        # not only in a stdout line nobody is watching.
        raw = jarvis.store.db.get_meta("maintenance")
        maintenance = json.loads(raw) if raw else {"last_run": None, "status": "미실행"}
        if maintenance.get("status") == "error":
            notes.append(f"유지보수 스윕 실패 — {maintenance.get('error', '원인 미상')}")
        # Liveness: the beat a restore checks before it dares overwrite the index.
        heartbeat = jarvis.store.db.get_meta("server_heartbeat")
        try:
            backup = BackupManager(jarvis.config.home).status()
        except Exception:
            backup = {"provider": "none"}
        if backup.get("last_status", "").startswith("error"):
            notes.append(f"백업 실패 — {backup['last_status']}")
        # Problems that need a human, not just a note: an external monitor
        # can alert on `degraded` instead of parsing Korean strings.
        problems = []
        if keys.auth_lost():
            problems.append("API 키 DB 유실 — 요청을 거부하는 중 (백업 복원 또는 jv key create)")
        if getattr(jarvis.store.db, "recovered_from", ""):
            problems.append(f"색인 DB 손상 → 새로 만듦 ({jarvis.store.db.recovered_from})")
        if maintenance.get("status") == "error":
            problems.append("유지보수 스윕 실패")
        if backup.get("last_status", "").startswith("error"):
            problems.append("백업 실패")
        notes.extend(p for p in problems if p not in notes)
        # The absolute home path is operational detail; only a key holder sees it.
        authed = getattr(request.state, "key", None) is not None or not keys.any_active()
        return {
            "ok": True,
            "degraded": bool(problems),
            "problems": problems,
            "version": __version__,
            "home": str(jarvis.config.home) if authed else "",
            "llm": llm.available,
            "embed": embed.provider,
            "projects": len(jarvis.store.projects()),
            "auth_required": keys.any_active(),
            "mcp_endpoint": "/mcp",
            "heartbeat": heartbeat,
            "maintenance": maintenance,
            "backup": {
                "provider": backup.get("provider", "none"),
                "last_status": backup.get("last_status", ""),
                "last_run": backup.get("last_run"),
                "due": backup.get("due"),
            },
            "quality": {
                # distillation: real model vs. rule-based fallback
                "llm_provider": llm.cfg.provider,
                "llm_model": llm.cfg.model,
                "llm_ready": llm.available,
                # recall: semantic embeddings vs. hashing fallback
                "embed_provider": embed.provider,
                "embed_model": embed.model,
                "embed_dim": embed.dim,
                "embed_semantic": embed_real,
                # the index re-embeds itself when the provider/model changes, so
                # switching providers needs no manual reindex.
                "reindex_automatic": True,
                "full_quality": llm.available and embed_real,
                "notes": notes,
            },
        }

    # ----- remote MCP (Streamable HTTP) --------------------------------
    @app.post("/mcp")
    async def mcp_endpoint(request: Request) -> Response:
        """MCP over HTTP, so an agent on any machine speaks the same protocol.

        Responds with plain JSON rather than an SSE stream: every tool here
        returns a single result, and the Streamable HTTP transport explicitly
        permits a JSON response in that case.
        """
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "잘못된 JSON"}},
                status_code=400,
            )
        batch = payload if isinstance(payload, list) else [payload]
        # Bind the caller's key so tool dispatch enforces their scope; the
        # shared `mcp` is unscoped and would let a scoped key read everything.
        scoped_mcp = Handler(jarvis, key=getattr(request.state, "key", None))
        out = []
        for msg in batch:
            reply = _handle_rpc(scoped_mcp, msg)
            if reply is not None:
                out.append(reply)
        if not out:
            return Response(status_code=202)
        body = out if isinstance(payload, list) else out[0]
        headers = {}
        if any(m.get("method") == "initialize" for m in batch):
            headers["Mcp-Session-Id"] = _new_session_id()
        return JSONResponse(body, headers=headers)

    @app.get("/mcp")
    def mcp_get() -> Response:
        # No server-initiated messages: nothing to stream.
        return Response(status_code=405)

    @app.delete("/mcp")
    def mcp_delete() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/mcp/tools")
    def mcp_tools() -> list[dict[str, Any]]:
        return tools()

    # ----- dashboard ---------------------------------------------------
    @app.get("/", include_in_schema=False)
    def dashboard() -> Response:
        return HTMLResponse(DASHBOARD_HTML)

    @app.get("/templates")
    def list_templates() -> list[dict[str, Any]]:
        return jarvis.templates()

    # ----- projects ---------------------------------------------------
    @app.get("/projects")
    def list_projects(request: Request) -> list[dict[str, Any]]:
        rows = jarvis.projects()
        info = _scoped_key(request)
        if info is not None:
            rows = [p for p in rows if info.allows(p["project"])]
        return rows

    @app.post("/projects")
    def init_project(body: InitBody, request: Request) -> dict[str, Any]:
        # The project lives in the body, where the middleware cannot see it — a
        # scoped key must not conjure projects outside its fence.
        _guard(request, body.project)
        profile = jarvis.init_project(
            body.project, body.template, body.description, body.stack
        )
        return {"project": body.project, "profile": profile.to_dict()}

    @app.get("/projects/{project}/profile")
    def get_profile(project: str) -> dict[str, Any]:
        return jarvis.profile(project).to_dict()

    @app.put("/projects/{project}/profile")
    def put_profile(project: str, body: dict[str, Any]) -> dict[str, Any]:
        from .profiles import MemoryProfile

        profile = MemoryProfile.from_dict(body)
        jarvis.set_profile(project, profile)
        return profile.to_dict()

    @app.delete("/projects/{project}")
    def delete_project(project: str) -> dict[str, Any]:
        return {"deleted": jarvis.delete_project(project)}

    # ----- prompts ----------------------------------------------------
    @app.get("/projects/{project}/prompts")
    def list_prompts(project: str) -> list[dict[str, Any]]:
        return jarvis.list_prompts(project)

    @app.post("/prompts")
    def save_prompt(body: PromptBody, request: Request) -> dict[str, Any]:
        _guard(request, body.project)
        node = jarvis.save_prompt(
            body.project,
            body.name,
            body.template,
            body.title,
            body.description,
            body.tags,
        )
        return {"uri": str(node.uri), "extra": node.extra}

    @app.post("/prompts/render")
    def render_prompt(body: RenderBody, request: Request) -> dict[str, Any]:
        _guard(request, body.project)
        try:
            res = jarvis.render_prompt(body.project, body.name, body.values, body.strict)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"text": res.text, "tokens": res.tokens, "missing": res.missing, "uri": res.uri}

    @app.get("/projects/{project}/prompts/{name}/versions")
    def prompt_versions(project: str, name: str) -> list[dict[str, Any]]:
        return jarvis.prompt_versions(project, name)

    @app.post("/projects/{project}/prompts/{name}/rollback/{version}")
    def prompt_rollback(project: str, name: str, version: str) -> dict[str, Any]:
        node = jarvis.rollback_prompt(project, name, version)
        if node is None:
            raise HTTPException(404, "해당 버전이 없습니다")
        return {"uri": str(node.uri), "version": node.extra.get("version")}

    # ----- memory -----------------------------------------------------
    @app.get("/projects/{project}/memories")
    def list_memories(project: str, category: str = "", limit: int = 100) -> list[dict[str, Any]]:
        return jarvis.memories(project, category, limit)

    @app.post("/memories")
    def add_memory(body: MemoryBody, request: Request) -> dict[str, Any]:
        _guard(request, body.project)
        try:
            uri = jarvis.remember(
                body.project,
                body.category,
                body.title,
                body.statement,
                body.detail,
                body.tags,
                body.confidence,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"uri": str(uri)}

    @app.delete("/memories")
    def forget(uri: str, request: Request, archive: bool = True) -> dict[str, Any]:
        _guard_uri(request, uri)
        return {"ok": jarvis.forget(uri, archive)}

    @app.post("/feedback")
    def feedback(body: FeedbackBody, request: Request) -> dict[str, Any]:
        _guard(request, body.project)
        node = jarvis.feedback(body.project, body.uri, body.helpful, body.note)
        if node is None:
            raise HTTPException(404, "대상을 찾지 못했습니다")
        return {"uri": str(node.uri), "confidence": node.confidence}

    @app.post("/resources")
    def add_resource(body: ResourceBody, request: Request) -> dict[str, Any]:
        _guard(request, body.project)
        node = jarvis.add_resource(body.project, body.name, body.text, body.title, body.tags)
        return {"uri": str(node.uri)}

    # ----- the loop ---------------------------------------------------
    @app.post("/prepare")
    def prepare(body: PrepareBody, request: Request) -> dict[str, Any]:
        resolved = _resolve_guarded(request, body)
        if not resolved["project"]:
            raise HTTPException(
                400,
                "프로젝트를 특정할 수 없습니다. project 또는 repo 를 지정하세요. "
                f"등록됨: {', '.join(resolved.get('candidates') or [])}",
            )
        try:
            prepared = jarvis.prepare(
                resolved["project"],
                body.question,
                prompt=body.prompt,
                values=body.values,
                use_cache=body.use_cache,
                include_global=body.include_global,
                max_tier=body.max_tier,
                kinds=body.kinds,
                agent=body.agent,
                session_id=body.session_id,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return prepared.to_dict()

    @app.post("/commit")
    def commit(body: CommitBody, request: Request) -> dict[str, Any]:
        _guard(request, body.project)
        return jarvis.commit(
            body.project,
            body.question,
            body.answer,
            model=body.model,
            tokens_in=body.tokens_in,
            tokens_out=body.tokens_out,
            prompt_uri=body.prompt_uri,
            tags=body.tags,
            outcome=body.outcome,
            distill=body.distill,
            trace_id=body.trace_id,
            latency_ms=body.latency_ms,
            agent=body.agent,
            files=body.files,
        )

    # ----- connection info (what a person actually comes here for) ------
    @app.post("/projects/{project}/connection")
    def connection(
        project: str,
        request: Request,
        body: ConnectionBody | None = None,
    ) -> dict[str, Any]:
        """Everything needed to point a coding agent at this project.

        POST, not GET, because the key to embed rides in the body — a secret in
        a query string leaks into proxy/access logs and browser history.

        ``base_url`` matters: the server sees the address it was bound to, not
        the one an agent on another machine has to dial. The dashboard passes
        the address you are browsing, which is right far more often than
        anything the process could infer about itself.
        """
        body = body or ConnectionBody()
        _guard(request, project)
        if not jarvis.store.project_exists(project):
            raise HTTPException(404, f"없는 프로젝트: {project}")
        url = (body.base_url or str(request.base_url)).rstrip("/")
        try:
            conn = build_connection(body.client, url, project, body.key)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        data = conn.to_dict()
        data["instruction_file"] = instruction_file(body.client)
        data["auth_required"] = keys.any_active()
        data["clients"] = list(CLIENTS)
        data["aliases"] = jarvis.aliases(project)
        return data

    # ----- curation ----------------------------------------------------
    @app.get("/projects/{project}/review")
    def review(
        project: str,
        request: Request,
        limit: int = 50,
        include_unconfirmed: bool = False,
    ) -> list[dict[str, Any]]:
        _guard(request, project)
        return jarvis.review_queue(project, limit, include_unconfirmed)

    @app.get("/review/summary")
    def review_summary(
        request: Request, project: str = "", include_unconfirmed: bool = False
    ) -> dict[str, Any]:
        _confine(request, project)
        return jarvis.review_summary(project, include_unconfirmed)

    @app.get("/memories/detail")
    def memory_detail(uri: str, request: Request) -> dict[str, Any]:
        _guard_uri(request, uri)
        data = jarvis.memory_detail(uri)
        if data is None:
            raise HTTPException(404, "없는 메모리")
        return data

    @app.patch("/memories")
    def edit_memory(body: EditMemoryBody, request: Request) -> dict[str, Any]:
        _guard_uri(request, body.uri)
        try:
            return jarvis.edit_memory(
                body.uri,
                title=body.title,
                statement=body.statement,
                body=body.body,
                category=body.category,
                tags=body.tags,
                confidence=body.confidence,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/memories/confirm")
    def confirm_memory(body: ConfirmBody, request: Request) -> dict[str, Any]:
        _guard_uri(request, body.uri)
        try:
            return jarvis.confirm_memory(body.uri, body.confidence)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    # ----- observability ----------------------------------------------
    @app.post("/scores")
    def add_score(body: ScoreBody, request: Request) -> dict[str, Any]:
        _guard_trace(request, body.trace_id)
        try:
            return jarvis.score(
                body.trace_id,
                name=body.name,
                value=body.value,
                comment=body.comment,
                source=body.source,
                apply_to_memory=body.apply_to_memory,
                uris=body.uris,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/traces")
    def list_traces(
        request: Request,
        project: str = "",
        limit: int = 50,
        cursor: str = "",
        name: str = "",
        min_latency: int = 0,
    ) -> list[dict[str, Any]]:
        _confine(request, project)
        return jarvis.traces(project, limit, cursor, name, min_latency)

    @app.get("/traces/{trace_id}")
    def get_trace(trace_id: str, request: Request) -> dict[str, Any]:
        _guard_trace(request, trace_id)
        data = jarvis.trace(trace_id)
        if data is None:
            raise HTTPException(404, "없는 트레이스")
        return data

    @app.get("/metrics")
    def get_metrics(request: Request, project: str = "", days: int = 7) -> dict[str, Any]:
        _confine(request, project)
        return jarvis.metrics(project, days)

    @app.get("/timeseries")
    def get_timeseries(
        request: Request, project: str = "", days: int = 14
    ) -> list[dict[str, Any]]:
        _confine(request, project)
        return jarvis.timeseries(project, days)

    @app.get("/projects/{project}/impact")
    def get_impact(project: str, limit: int = 20) -> list[dict[str, Any]]:
        return jarvis.memory_impact(project, limit)

    @app.get("/projects/{project}/brief")
    def brief(project: str, request: Request, limit: int = 8) -> dict[str, Any]:
        _guard(request, project)
        if not jarvis.store.project_exists(project):
            raise HTTPException(404, f"없는 프로젝트: {project}")
        return jarvis.brief(project, limit)

    @app.get("/worksessions")
    def work_sessions(
        request: Request, project: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        """Traces grouped into the sittings they belonged to."""
        _confine(request, project)
        return jarvis.work_sessions(project, limit)

    @app.get("/agents")
    def list_agents(request: Request) -> list[dict[str, Any]]:
        # Cross-project by nature: only an all-access key sees every agent.
        _confine(request, "")
        return jarvis.agents()

    # ----- project resolution -----------------------------------------
    @app.post("/resolve")
    def resolve(body: ResolveBody, request: Request) -> dict[str, Any]:
        # Honour scope: a scoped key can neither probe for other projects nor
        # conjure one outside its scope by pointing at a fresh repo/path.
        return _resolve_guarded(request, body)

    @app.post("/aliases")
    def add_alias(body: AliasBody, request: Request) -> dict[str, Any]:
        _guard(request, body.project)
        # Re-pointing a repo that already belongs to another project would
        # redirect that project's future captures into this one — the alias's
        # *current* owner has to be inside the caller's scope as well.
        owner = jarvis.alias_owner(body.alias)
        if owner and owner != body.project:
            _guard(request, owner)
        jarvis.bind_alias(body.alias, body.project, body.kind)
        return {"alias": body.alias, "project": body.project}

    @app.get("/aliases")
    def list_aliases(request: Request, project: str = "") -> list[dict[str, Any]]:
        _confine(request, project)
        return jarvis.aliases(project)

    @app.delete("/aliases")
    def remove_alias(alias: str, request: Request) -> dict[str, Any]:
        # The alias's *current owner* decides who may unbind it — a scoped key
        # must not be able to detach another project's repo (that would silently
        # redirect its captures). A remote URL is not a secret, so the query
        # string is fine here (unlike the embed key on /connection).
        owner = jarvis.alias_owner(alias)
        if not owner:
            raise HTTPException(404, f"등록되지 않은 별칭: {alias}")
        _guard(request, owner)
        return {"alias": alias, "project": owner, "removed": jarvis.unbind_alias(alias)}

    # ----- identity -----------------------------------------------------
    @app.get("/me")
    def whoami(request: Request) -> dict[str, Any]:
        """What the caller's key can do. The dashboard renders the answer as a
        header chip so a scoped user sees their fence — and an admin notices
        when they are about to hand out their own all-access key."""
        info = getattr(request.state, "key", None)
        if info is None:
            return {"auth_required": keys.any_active(), "admin": True,
                    "name": None, "projects": ["*"]}
        return {"auth_required": True, "admin": info.allows("*"),
                "id": info.id, "name": info.name, "projects": info.projects}

    # ----- keys --------------------------------------------------------
    @app.get("/keys")
    def list_keys(request: Request) -> list[dict[str, Any]]:
        _require_admin(request)
        return keys.list()

    @app.post("/keys")
    def create_key(body: KeyBody, request: Request) -> dict[str, Any]:
        _require_admin(request)
        kid, raw = keys.create(body.name, body.projects)
        return {
            "id": kid,
            "name": body.name,
            "key": raw,
            "note": "이 값은 다시 볼 수 없습니다. 지금 저장하세요.",
        }

    @app.delete("/keys/{key_id}")
    def revoke_key(key_id: str, request: Request) -> dict[str, Any]:
        _require_admin(request)
        return {"revoked": keys.revoke(key_id)}

    @app.post("/projects/{project}/distill")
    def distill(project: str, limit: int = 20) -> dict[str, Any]:
        return jarvis.distill(project, limit).to_dict()

    # ----- browsing ---------------------------------------------------
    @app.get("/ls")
    def ls(uri: str, request: Request) -> dict[str, Any]:
        _guard_uri(request, uri)
        return jarvis.ls(uri)

    @app.get("/tree")
    def tree(uri: str, request: Request, depth: int = 3) -> dict[str, Any]:
        _guard_uri(request, uri)
        return jarvis.tree(uri, depth)

    @app.get("/find")
    def find(q: str, project: str, limit: int = 15) -> dict[str, Any]:
        return jarvis.find(q, project, limit=limit)

    @app.get("/grep")
    def grep(
        term: str, request: Request, uri: str | None = None, limit: int = 30
    ) -> list[dict[str, Any]]:
        if uri:
            _guard_uri(request, uri)
        elif _scoped_key(request) is not None:
            # No uri means grep sweeps every project — confine a scoped key.
            raise HTTPException(403, "스코프가 제한된 키는 grep 에 uri 를 지정해야 합니다")
        return jarvis.grep(term, uri, limit)

    @app.get("/read")
    def read(uri: str, request: Request, tier: int = 2) -> dict[str, Any]:
        _guard_uri(request, uri)
        data = jarvis.read(uri, tier)
        if data is None:
            raise HTTPException(404, "없습니다")
        return data

    # ----- accounting -------------------------------------------------
    @app.get("/report")
    def report(
        request: Request, project: str | None = None, days: int = 0
    ) -> dict[str, Any]:
        _confine(request, project or "")
        return jarvis.report(project, days).to_dict()

    @app.get("/stats")
    def stats(request: Request, project: str | None = None) -> dict[str, Any]:
        _confine(request, project or "")
        return jarvis.stats(project)

    @app.get("/projects/{project}/cache")
    def cache_list(project: str, limit: int = 30) -> list[dict[str, Any]]:
        return jarvis.cache_list(project, limit)

    @app.delete("/projects/{project}/cache")
    def cache_clear(project: str) -> dict[str, Any]:
        return {"deleted": jarvis.cache_clear(project)}

    @app.get("/projects/{project}/sessions")
    def sessions(project: str, limit: int = 10) -> list[dict[str, Any]]:
        return jarvis.recent_sessions(project, limit)

    @app.post("/reindex")
    def reindex(request: Request, project: str | None = None) -> dict[str, int]:
        _confine(request, project or "")
        return jarvis.reindex(project)

    # ----- backup ------------------------------------------------------
    # Admin only, every route. The archive holds *every* project and the config
    # holds the OAuth secrets — a project-scoped key that could point the backup
    # at its own Drive (or a local path) would walk off with the whole store.
    backups = BackupManager(home)

    @app.get("/backup/status")
    def backup_status(request: Request) -> dict[str, Any]:
        _require_admin(request)
        return backups.status()

    @app.post("/backup/config")
    def backup_config(body: BackupConfigBody, request: Request) -> dict[str, Any]:
        _require_admin(request)
        backups.configure(
            provider=body.provider,
            every_hours=body.every_hours,
            keep=body.keep,
            folder=body.folder,
            local_path=body.local_path,
        )
        return backups.status()

    @app.post("/backup/connect/start")
    def backup_connect_start(body: BackupConnectBody, request: Request) -> dict[str, Any]:
        _require_admin(request)
        try:
            return backups.connect_start(body.client_id, body.client_secret)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/backup/connect/poll")
    def backup_connect_poll(request: Request) -> dict[str, Any]:
        _require_admin(request)
        return backups.connect_poll()

    @app.post("/backup/disconnect")
    def backup_disconnect(request: Request) -> dict[str, Any]:
        _require_admin(request)
        return backups.disconnect()

    @app.post("/backup/run")
    def backup_run(request: Request) -> dict[str, Any]:
        _require_admin(request)
        try:
            return backups.run()
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/backup/list")
    def backup_list(request: Request) -> list[dict[str, Any]]:
        _require_admin(request)
        try:
            return backups.list_remote()
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc

    return app


def _record_maintain(worker, started: float, ok: bool, error: str = "") -> None:
    """Persist the last maintenance sweep so /health can vouch it is alive."""
    import time as _time
    from datetime import datetime, timezone

    payload = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "status": "ok" if ok else "error",
        "took_ms": round((_time.time() - started) * 1000),
    }
    if error:
        payload["error"] = error
    try:
        worker.store.db.set_meta("maintenance", json.dumps(payload, ensure_ascii=False))
    except Exception:  # pragma: no cover - never let bookkeeping crash the loop
        pass


def start_maintenance(home: str | None, every_hours: float) -> None:
    """Run distill/decay periodically inside the server process.

    The learning loop needs a sweep that is not tied to a request: sessions
    committed with ``distill=false`` have to be folded in, and confidence decay
    is what stops the store growing forever. Doing it here means one container
    and one systemd unit instead of a second scheduler to forget about.

    Uses its own ``Jarvis`` (and so its own SQLite connection) rather than
    sharing the request one across threads.
    """
    if every_hours <= 0:
        return
    import threading
    import time

    def loop() -> None:
        worker = Jarvis(home=home)
        while True:
            time.sleep(every_hours * 3600)
            started = time.time()
            try:
                worker.maintain()
                _record_maintain(worker, started, ok=True)
            except Exception as exc:  # pragma: no cover - background best effort
                # A background sweep that dies quietly is a store that silently
                # stops decaying and pruning. Leave a trace /health can show.
                _record_maintain(worker, started, ok=False,
                                 error=f"{type(exc).__name__}: {exc}")
                print(f"[maintain] 실패: {type(exc).__name__}: {exc}", flush=True)

    threading.Thread(target=loop, name="jarvis-maintain", daemon=True).start()


def start_heartbeat(home: str | None, every: float = 30.0) -> None:
    """Stamp a liveness beat so a restore knows the server is holding the index.

    Overwriting index.db under this process would corrupt it, so
    ``jv backup restore`` refuses while this beat is fresh (see
    backup.server_heartbeat_age). Its own connection; failures stay quiet — a
    server that cannot write its heartbeat must still serve."""
    if every <= 0:
        return
    import threading
    import time
    from datetime import datetime, timezone

    def loop() -> None:
        worker = Jarvis(home=home)
        while True:
            try:
                worker.store.db.set_meta(
                    "server_heartbeat", datetime.now(timezone.utc).isoformat()
                )
            except Exception:  # pragma: no cover - best effort
                pass
            time.sleep(every)

    threading.Thread(target=loop, name="jarvis-heartbeat", daemon=True).start()


def run(
    host: str = "127.0.0.1",
    port: int = 8787,
    home: str | None = None,
    maintain_every: float = 6.0,
) -> None:
    import uvicorn

    app = create_app(home)
    start_heartbeat(home)
    start_maintenance(home, maintain_every)
    # The backup cadence lives in backup.yaml (set via UI/CLI); this thread
    # only checks whether one is due, so a short interval costs nothing.
    start_backup_loop(home)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
