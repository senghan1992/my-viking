"""myviking — 사람별 프로젝트 지식 도서관 서버 (FastAPI)."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from . import db
from . import model_settings
from .config import config
from .routes import admin_models, agent, projects, web

app = FastAPI(title="myviking", version="1.0.0", docs_url="/api/docs")

# 데이터베이스 준비 (스키마 + 마이그레이션)
db.db.init()

# 첫 실행 시드 — 관리자가 아직 모델 설정/등록을 안 했으면 models_seed.json 과
# 사전 카탈로그를 기본으로 채운다 (VIKING_SEED_MODELS=false 로 끔).
model_settings.seed_if_empty()
model_settings.apply_to(config)  # 시드 후 config 에 반영

app.state.templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

app.include_router(web.router)
app.include_router(admin_models.router)
app.include_router(projects.router)
app.include_router(agent.router)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """웹 요청은 메시지와 함께 대시보드로, API 요청은 JSON 으로."""
    from fastapi import HTTPException

    if isinstance(exc, HTTPException):
        if exc.status_code == 303:
            return RedirectResponse(exc.headers.get("Location", "/"), status_code=303)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)
        if exc.status_code == 404 and not request.url.path.startswith(("/login", "/signup")):
            return RedirectResponse("/?msg=" + str(exc.detail), status_code=303)
        return RedirectResponse("/login" if exc.status_code == 401 else f"/?msg={exc.detail}", status_code=303)
    # 500 — 로그만 남기고 사용자에게는 간단히
    import logging
    import traceback

    logging.getLogger("myviking").error("unhandled: %s", traceback.format_exc())
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "서버 오류"}, status_code=500)
    return RedirectResponse("/?msg=서버 오류가 발생했습니다.", status_code=303)


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=config.port)


if __name__ == "__main__":
    run()