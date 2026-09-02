"""HTTP API.

Thin wrapper over ``Jarvis`` so any language or a web UI can drive it. Bound to
localhost by default: this database holds your accumulated project context, so
it should not be reachable from the network unless you decide otherwise.

Importing this module requires the ``server`` extra (fastapi, uvicorn,
pydantic); the rest of MyViking does not.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .service import Jarvis

# Request schemas live at module level on purpose: with postponed annotation
# evaluation (``from __future__ import annotations``), FastAPI resolves a
# handler's type hints against the *module* namespace. Models nested inside
# create_app() would resolve to nothing and every body would be misread as a
# query parameter.
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
    project: str
    question: str
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


class FeedbackBody(BaseModel):
    project: str
    uri: str
    helpful: bool = True
    note: str = ""


def create_app(home: str | None = None):
    from fastapi import FastAPI, HTTPException

    jarvis = Jarvis(home=home)
    app = FastAPI(
        title="MyViking",
        description="프로젝트별 자가학습 컨텍스트 데이터베이스",
        version="0.1.0",
    )

    # ----- meta -------------------------------------------------------
    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "home": str(jarvis.config.home),
            "llm": jarvis.store.llm.available,
            "embed": jarvis.config.embed.provider,
            "projects": len(jarvis.store.projects()),
        }

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
    def prepare(body: PrepareBody) -> dict[str, Any]:
        try:
            prepared = jarvis.prepare(
                body.project,
                body.question,
                prompt=body.prompt,
                values=body.values,
                use_cache=body.use_cache,
                include_global=body.include_global,
                max_tier=body.max_tier,
                kinds=body.kinds,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return prepared.to_dict()

    @app.post("/commit")
    def commit(body: CommitBody) -> dict[str, Any]:
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
        )

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


def run(host: str = "127.0.0.1", port: int = 8787, home: str | None = None) -> None:
    import uvicorn

    uvicorn.run(create_app(home), host=host, port=port)


if __name__ == "__main__":
    run()
