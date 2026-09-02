"""HTTP API.

Thin wrapper over ``Jarvis`` so any language or a web UI can drive it. Bound to
localhost by default: this database holds your accumulated project context, so
it should not be reachable from the network unless you decide otherwise.

Importing this module requires the ``server`` extra (fastapi, uvicorn,
pydantic); the rest of MyViking does not.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .auth import KeyStore
from .mcp_core import Handler, tools
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


class AliasBody(BaseModel):
    alias: str
    project: str
    kind: str = "repo"


class ResolveBody(BaseModel):
    project: str = ""
    repo: str = ""
    path: str = ""
    create: bool = False


class KeyBody(BaseModel):
    name: str
    projects: list[str] = Field(default_factory=lambda: ["*"])


class FeedbackBody(BaseModel):
    project: str
    uri: str
    helpful: bool = True
    note: str = ""


PROTOCOL_VERSION = "2024-11-05"


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
    mcp = Handler(jarvis)
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
            return await call_next(request)

        header = request.headers.get("authorization", "")
        raw = header[7:].strip() if header.lower().startswith("bearer ") else ""
        raw = raw or request.headers.get("x-api-key", "")
        info = keys.verify(raw)
        if info is None:
            return JSONResponse(
                {"detail": "유효한 API 키가 필요합니다 (Authorization: Bearer jv_...)"},
                status_code=401,
            )
        request.state.key = info
        return await call_next(request)

    def _guard(request: Request, project: str) -> None:
        """A key scoped to some projects must not read the others."""
        info = getattr(request.state, "key", None)
        if info is not None and project and not info.allows(project):
            raise HTTPException(403, f"이 키는 '{project}' 에 접근할 수 없습니다")

    # ----- meta -------------------------------------------------------
    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "home": str(jarvis.config.home),
            "llm": jarvis.store.llm.available,
            "embed": jarvis.config.embed.provider,
            "projects": len(jarvis.store.projects()),
            "auth_required": keys.any_active(),
            "mcp_endpoint": "/mcp",
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
        out = []
        for msg in batch:
            reply = _handle_rpc(mcp, msg)
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
    def list_projects() -> list[dict[str, Any]]:
        return jarvis.projects()

    @app.post("/projects")
    def init_project(body: InitBody) -> dict[str, Any]:
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
    def save_prompt(body: PromptBody) -> dict[str, Any]:
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
    def render_prompt(body: RenderBody) -> dict[str, Any]:
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
    def add_memory(body: MemoryBody) -> dict[str, Any]:
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
    def forget(uri: str, archive: bool = True) -> dict[str, Any]:
        return {"ok": jarvis.forget(uri, archive)}

    @app.post("/feedback")
    def feedback(body: FeedbackBody) -> dict[str, Any]:
        node = jarvis.feedback(body.project, body.uri, body.helpful, body.note)
        if node is None:
            raise HTTPException(404, "대상을 찾지 못했습니다")
        return {"uri": str(node.uri), "confidence": node.confidence}

    @app.post("/resources")
    def add_resource(body: ResourceBody) -> dict[str, Any]:
        node = jarvis.add_resource(body.project, body.name, body.text, body.title, body.tags)
        return {"uri": str(node.uri)}

    # ----- the loop ---------------------------------------------------
    @app.post("/prepare")
    def prepare(body: PrepareBody, request: Request) -> dict[str, Any]:
        resolved = jarvis.resolve_project(
            body.project, body.repo, body.path, create=True
        )
        if not resolved["project"]:
            raise HTTPException(
                400,
                "프로젝트를 특정할 수 없습니다. project 또는 repo 를 지정하세요. "
                f"등록됨: {', '.join(resolved.get('candidates') or [])}",
            )
        _guard(request, resolved["project"])
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
        )

    # ----- curation ----------------------------------------------------
    @app.get("/projects/{project}/review")
    def review(project: str, request: Request, limit: int = 50) -> list[dict[str, Any]]:
        _guard(request, project)
        return jarvis.review_queue(project, limit)

    @app.get("/review/summary")
    def review_summary(project: str = "") -> dict[str, Any]:
        return jarvis.review_summary(project)

    @app.get("/memories/detail")
    def memory_detail(uri: str) -> dict[str, Any]:
        data = jarvis.memory_detail(uri)
        if data is None:
            raise HTTPException(404, "없는 메모리")
        return data

    @app.patch("/memories")
    def edit_memory(body: EditMemoryBody) -> dict[str, Any]:
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
    def confirm_memory(body: ConfirmBody) -> dict[str, Any]:
        try:
            return jarvis.confirm_memory(body.uri, body.confidence)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    # ----- observability ----------------------------------------------
    @app.post("/scores")
    def add_score(body: ScoreBody) -> dict[str, Any]:
        try:
            return jarvis.score(
                body.trace_id,
                name=body.name,
                value=body.value,
                comment=body.comment,
                source=body.source,
                apply_to_memory=body.apply_to_memory,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/traces")
    def list_traces(
        project: str = "",
        limit: int = 50,
        cursor: str = "",
        name: str = "",
        min_latency: int = 0,
    ) -> list[dict[str, Any]]:
        return jarvis.traces(project, limit, cursor, name, min_latency)

    @app.get("/traces/{trace_id}")
    def get_trace(trace_id: str) -> dict[str, Any]:
        data = jarvis.trace(trace_id)
        if data is None:
            raise HTTPException(404, "없는 트레이스")
        return data

    @app.get("/metrics")
    def get_metrics(project: str = "", days: int = 7) -> dict[str, Any]:
        return jarvis.metrics(project, days)

    @app.get("/timeseries")
    def get_timeseries(project: str = "", days: int = 14) -> list[dict[str, Any]]:
        return jarvis.timeseries(project, days)

    @app.get("/projects/{project}/impact")
    def get_impact(project: str, limit: int = 20) -> list[dict[str, Any]]:
        return jarvis.memory_impact(project, limit)

    @app.get("/agents")
    def list_agents() -> list[dict[str, Any]]:
        return jarvis.agents()

    # ----- project resolution -----------------------------------------
    @app.post("/resolve")
    def resolve(body: ResolveBody) -> dict[str, Any]:
        return jarvis.resolve_project(body.project, body.repo, body.path, body.create)

    @app.post("/aliases")
    def add_alias(body: AliasBody) -> dict[str, Any]:
        jarvis.bind_alias(body.alias, body.project, body.kind)
        return {"alias": body.alias, "project": body.project}

    @app.get("/aliases")
    def list_aliases(project: str = "") -> list[dict[str, Any]]:
        return jarvis.aliases(project)

    # ----- keys --------------------------------------------------------
    @app.get("/keys")
    def list_keys() -> list[dict[str, Any]]:
        return keys.list()

    @app.post("/keys")
    def create_key(body: KeyBody) -> dict[str, Any]:
        kid, raw = keys.create(body.name, body.projects)
        return {
            "id": kid,
            "name": body.name,
            "key": raw,
            "note": "이 값은 다시 볼 수 없습니다. 지금 저장하세요.",
        }

    @app.delete("/keys/{key_id}")
    def revoke_key(key_id: str) -> dict[str, Any]:
        return {"revoked": keys.revoke(key_id)}

    @app.post("/projects/{project}/distill")
    def distill(project: str, limit: int = 20) -> dict[str, Any]:
        return jarvis.distill(project, limit).to_dict()

    # ----- browsing ---------------------------------------------------
    @app.get("/ls")
    def ls(uri: str) -> dict[str, Any]:
        return jarvis.ls(uri)

    @app.get("/tree")
    def tree(uri: str, depth: int = 3) -> dict[str, Any]:
        return jarvis.tree(uri, depth)

    @app.get("/find")
    def find(q: str, project: str, limit: int = 15) -> dict[str, Any]:
        return jarvis.find(q, project, limit=limit)

    @app.get("/grep")
    def grep(term: str, uri: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        return jarvis.grep(term, uri, limit)

    @app.get("/read")
    def read(uri: str, tier: int = 2) -> dict[str, Any]:
        data = jarvis.read(uri, tier)
        if data is None:
            raise HTTPException(404, "없습니다")
        return data

    # ----- accounting -------------------------------------------------
    @app.get("/report")
    def report(project: str | None = None, days: int = 0) -> dict[str, Any]:
        return jarvis.report(project, days).to_dict()

    @app.get("/stats")
    def stats(project: str | None = None) -> dict[str, Any]:
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
    def reindex(project: str | None = None) -> dict[str, int]:
        return jarvis.reindex(project)

    return app


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
            try:
                worker.maintain()
            except Exception as exc:  # pragma: no cover - background best effort
                print(f"[maintain] 실패: {type(exc).__name__}: {exc}", flush=True)

    threading.Thread(target=loop, name="jarvis-maintain", daemon=True).start()


def run(
    host: str = "127.0.0.1",
    port: int = 8787,
    home: str | None = None,
    maintain_every: float = 6.0,
) -> None:
    import uvicorn

    app = create_app(home)
    start_maintenance(home, maintain_every)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
