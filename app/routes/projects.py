"""내 프로젝트 — 도서관 화면·연결 정보·키 관리·지식 편집."""
from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from .. import db
from ..deps import get_project_or_404, login_required, require_owner
from ..engine import redact as redact_engine
from ..engine import tiers, trust as trust_engine
from ..engine import llm
from ..security import generate_api_key

router = APIRouter(tags=["projects"])

CATEGORY_LABELS = {
    "knowledge": "📖 지식",
    "commands": "🛠️ 명령",
    "pitfalls": "⚠️ 함정",
    "decisions": "🧭 결정",
}
STATUS_LABELS = {
    "fresh": "검증 전",
    "established": "확립",
    "contested": "검증 필요",
    "superseded": "대체됨",
}


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if len(s) >= 3:
        return s[:40]
    return f"p{secrets.token_hex(3)}"  # 한글 이름 등 → 랜덤 슬러그


def _unique_slug(base: str) -> str:
    slug = base
    n = 1
    while db.one("SELECT id FROM projects WHERE slug=?", (slug,)):
        slug = f"{base}-{n}"
        n += 1
    return slug


# ── 프로젝트 생성 ─────────────────────────────────────── #
@router.post("/projects")
def create_project(request: Request, user: dict = Depends(login_required),
                   name: str = Form(""), description: str = Form("")):
    name = name.strip()[:60]
    if not name:
        return RedirectResponse("/?msg=프로젝트 이름이 필요합니다.", status_code=303)
    slug = _unique_slug(_slugify(name))
    now = db.now()
    pid = db.execute(
        "INSERT INTO projects(user_id, slug, name, description, created_at, updated_at) VALUES(?,?,?,?,?,?)",
        (user["id"], slug, name, description.strip()[:500], now, now),
    )
    db.log_event(pid, "project", "프로젝트 생성")
    return RedirectResponse(f"/projects/{slug}?msg=프로젝트 도서관이 생겼습니다. 이제 연결 탭에서 에이전트를 붙이세요.", status_code=303)


# ── 도서관 본체 ───────────────────────────────────────── #
@router.get("/projects/{slug}")
def library(request: Request, slug: str, user: dict = Depends(login_required),
            q: str = "", cat: str = "", msg: str = ""):
    project = get_project_or_404(slug)
    require_owner(project, user)
    return _library_response(request, project, user, q, cat, msg)


def _library_response(request, project, user, q="", cat="", msg=""):
    memories = _query_memories(project["id"], q, cat)
    events = db.rows(
        "SELECT * FROM events WHERE project_id=? ORDER BY id DESC LIMIT 20",
        (project["id"],),
    )
    sessions = db.rows(
        """SELECT * FROM sessions WHERE project_id=? ORDER BY started_at DESC LIMIT 8""",
        (project["id"],),
    )
    counts = db.one(
        """SELECT COUNT(*) AS total,
                  SUM(status='established') AS established,
                  SUM(status='contested') AS contested,
                  SUM(status='fresh') AS fresh
           FROM memories WHERE project_id=? AND status!='superseded'""",
        (project["id"],),
    ) or {"total": 0, "established": 0, "contested": 0, "fresh": 0}
    return request.app.state.templates.TemplateResponse(request, "project.html", {
            "request": request, "user": user, "project": project,
            "memories": memories, "events": events, "sessions": sessions,
            "counts": counts, "q": q, "cat": cat, "msg": msg,
            "category_labels": CATEGORY_LABELS, "status_labels": STATUS_LABELS,
            "categories": list(CATEGORY_LABELS),
        },
    )


def _query_memories(project_id: int, q: str = "", cat: str = ""):
    sql = "SELECT * FROM memories WHERE project_id=? AND status!='superseded'"
    args: list = [project_id]
    if cat in CATEGORY_LABELS:
        sql += " AND category=?"
        args.append(cat)
    if q.strip():
        sql += " AND (title LIKE ? OR content LIKE ? OR keywords LIKE ?)"
        like = f"%{q.strip()[:60]}%"
        args += [like, like, like]
    sql += " ORDER BY (status='contested') DESC, updated_at DESC LIMIT 300"
    return db.rows(sql, tuple(args))


# ── 지식 상세·편집 ────────────────────────────────────── #
@router.get("/projects/{slug}/memories/{mid}")
def memory_detail(slug: str, mid: int, user: dict = Depends(login_required)):
    project = get_project_or_404(slug)
    require_owner(project, user)
    mem = db.one("SELECT * FROM memories WHERE id=? AND project_id=?", (mid, project["id"]))
    if not mem:
        raise HTTPException(404, "지식을 찾을 수 없습니다.")
    mem["evidence"] = db.jloads(mem["evidence"])
    return mem


@router.post("/projects/{slug}/memories/{mid}")
def memory_update(slug: str, mid: int, user: dict = Depends(login_required),
                  title: str = Form(""), category: str = Form(""), content: str = Form("")):
    project = get_project_or_404(slug)
    require_owner(project, user)
    mem = db.one("SELECT * FROM memories WHERE id=? AND project_id=?", (mid, project["id"]))
    if not mem:
        raise HTTPException(404, "지식을 찾을 수 없습니다.")

    title = redact_engine.redact((title or mem["title"]).strip()[:200])
    content = redact_engine.redact((content or mem["content"]).strip())[:50000]
    category = category if category in CATEGORY_LABELS else mem["category"]
    summary = tiers.make_summary(title, content)
    overview = tiers.make_overview(content)
    db.execute(
        "UPDATE memories SET title=?, category=?, content=?, summary=?, overview=?, updated_at=? WHERE id=?",
        (title, category, content, summary, overview, db.now(), mid),
    )
    from ..engine.tokens import keywords
    llm.save_embeddings(mid, f"{title} {content}")
    db.execute("UPDATE memories SET keywords=? WHERE id=?", (db.jdumps(keywords(f"{title} {content}")), mid))
    return RedirectResponse(f"/projects/{slug}?msg=지식을 수정했습니다.", status_code=303)


@router.post("/projects/{slug}/memories/{mid}/confirm")
def memory_confirm(slug: str, mid: int, user: dict = Depends(login_required)):
    project = get_project_or_404(slug)
    require_owner(project, user)
    mem = db.one("SELECT * FROM memories WHERE id=? AND project_id=?", (mid, project["id"]))
    if not mem:
        raise HTTPException(404)
    trust_engine.apply(project["id"], mid, "good", note="사람이 대시보드에서 확인")
    return RedirectResponse(f"/projects/{slug}" + "?msg=지식을 확립 지식으로 확인했습니다.", status_code=303)


@router.post("/projects/{slug}/memories/{mid}/delete")
def memory_delete(slug: str, mid: int, user: dict = Depends(login_required)):
    project = get_project_or_404(slug)
    require_owner(project, user)
    db.execute("DELETE FROM memories WHERE id=? AND project_id=?", (mid, project["id"]))
    return RedirectResponse(f"/projects/{slug}?msg=지식을 삭제했습니다.", status_code=303)


# ── 검색 (AJAX) ───────────────────────────────────────── #
@router.get("/projects/{slug}/search")
def search_json(slug: str, q: str = "", user: dict = Depends(login_required)):
    project = get_project_or_404(slug)
    require_owner(project, user)
    from ..engine.retrieve import search
    result = search(project["id"], q or "", max_items=8, max_tier=2)
    return {"items": result["items"], "warnings": result["warnings"]}


# ── 연결 탭 ───────────────────────────────────────────── #
@router.get("/projects/{slug}/connect")
def connect_page(request: Request, slug: str, user: dict = Depends(login_required), msg: str = ""):
    project = get_project_or_404(slug)
    require_owner(project, user)
    keys = db.rows(
        "SELECT * FROM api_keys WHERE project_id=? ORDER BY revoked_at IS NULL DESC, id",
        (project["id"],),
    )
    base = _base_url(request)
    return request.app.state.templates.TemplateResponse(request, "connect.html", {"request": request, "user": user, "project": project, "keys": keys,
         "base": base, "msg": msg, "status_labels": STATUS_LABELS},
    )


def _base_url(request: Request) -> str:
    from ..config import config
    if config.base_url:
        return config.base_url
    return str(request.base_url).rstrip("/")


@router.post("/projects/{slug}/keys")
def create_key(request: Request, slug: str, user: dict = Depends(login_required),
               name: str = Form("")):
    project = get_project_or_404(slug)
    require_owner(project, user)
    raw, digest, prefix = generate_api_key()
    kid = db.execute(
        "INSERT INTO api_keys(project_id, user_id, name, key_hash, key_prefix, created_at) VALUES(?,?,?,?,?,?)",
        (project["id"], user["id"], (name or user["name"]).strip()[:40], digest, prefix, db.now()),
    )
    db.log_event(project["id"], "key", f"키 발급: {name or user['name']}")
    return request.app.state.templates.TemplateResponse(request, "key_reveal.html", {"request": request, "user": user, "project": project, "key": raw, "key_id": kid},
    )


@router.post("/projects/{slug}/keys/{kid}/revoke")
def revoke_key(slug: str, kid: int, user: dict = Depends(login_required)):
    project = get_project_or_404(slug)
    require_owner(project, user)
    db.execute("UPDATE api_keys SET revoked_at=? WHERE id=? AND project_id=?",
               (db.now(), kid, project["id"]))
    return RedirectResponse(f"/projects/{slug}/connect?msg=키를 폐기했습니다.", status_code=303)


# ── 내보내기 / 설정 / 삭제 ─────────────────────────────── #
@router.get("/projects/{slug}/export.md")
def export_md(slug: str, user: dict = Depends(login_required)):
    project = get_project_or_404(slug)
    require_owner(project, user)
    memories = _query_memories(project["id"])
    lines = [
        f"# {project['name']} — 지식 도서관 (myviking 내보내기)",
        f"> 생성 {db.utc(project['created_at'])} · 지식 {len(memories)}권",
        "",
    ]
    for cat in CATEGORY_LABELS:
        group = [m for m in memories if m["category"] == cat]
        if not group:
            continue
        lines.append(f"## {CATEGORY_LABELS[cat]} ({len(group)})")
        for m in group:
            lines.append(f"### [{STATUS_LABELS.get(m['status'], m['status'])}] {m['title']}")
            if m["summary"]:
                lines.append(f"> {m['summary']}")
            lines.append("")
            lines.append(m["content"])
            lines.append("")
    return PlainTextResponse("\n".join(lines), media_type="text/markdown; charset=utf-8")


@router.post("/projects/{slug}/settings")
def project_settings(slug: str, user: dict = Depends(login_required),
                     name: str = Form(""), description: str = Form("")):
    project = get_project_or_404(slug)
    require_owner(project, user)
    db.execute(
        "UPDATE projects SET name=?, description=?, updated_at=? WHERE id=?",
        ((name or project["name"]).strip()[:60], (description or "").strip()[:500], db.now(), project["id"]),
    )
    return RedirectResponse(f"/projects/{slug}?msg=프로젝트 설정을 저장했습니다.", status_code=303)


@router.post("/projects/{slug}/delete")
def project_delete(slug: str, user: dict = Depends(login_required)):
    project = get_project_or_404(slug)
    require_owner(project, user)
    db.execute("DELETE FROM sessions WHERE project_id=?", (project["id"],))
    db.execute("DELETE FROM events WHERE project_id=?", (project["id"],))
    db.execute("DELETE FROM api_keys WHERE project_id=?", (project["id"],))
    db.execute("DELETE FROM memories WHERE project_id=?", (project["id"],))
    db.execute("DELETE FROM projects WHERE id=?", (project["id"],))
    return RedirectResponse("/?msg=프로젝트를 삭제했습니다.", status_code=303)