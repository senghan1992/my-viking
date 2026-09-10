"""Agent API — 코딩 에이전트(훅·MCP·CLI)가 붙는 입구.

모든 엔드포인트는 Bearer API 키 (프로젝트 스코프) 로 인증됩니다.
한 바퀴: brief(세션 시작 오리엔테이션) → prepare(질문마다 관련 지식) →
        commit(작업 끝나면 자동 기록·증류) → score(결과가 어땠는지, 선택).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db
from ..deps import bearer_auth
from ..engine import distill, redact, retrieve
from ..engine import trust as trust_engine

router = APIRouter(prefix="/api/v1", tags=["agent"])


def _ensure_session(project_id: int, session_id: str, agent: str = "unknown") -> None:
    if not session_id:
        return
    row = db.one("SELECT id FROM sessions WHERE id=?", (session_id,))
    if row:
        return
    db.execute(
        "INSERT INTO sessions(id, project_id, agent, started_at) VALUES(?,?,?,?)",
        (session_id[:64], project_id, agent[:40] or "unknown", db.now()),
    )


@router.get("/health")
def health():
    return {
        "ok": True,
        "name": "myviking",
        "version": "1.0.0",
        "server_time": db.now(),
        "docs": "GET /api/v1/projects/{slug}/brief",
    }


@router.get("/me")
def me(key: dict = Depends(bearer_auth)):
    """키만으로 프로젝트를 식별 — 연결할 때 슬러그를 몰라도 된다."""
    return {"project": key["slug"], "project_name": key["project_name"]}


@router.get("/projects/{slug}/brief")
def brief(slug: str, session_id: str = Query(default=""), agent: str = Query(default="unknown"),
          key: dict = Depends(bearer_auth)):
    project = db.one("SELECT * FROM projects WHERE slug=?", (slug,))
    if not project or project["id"] != key["project_id"]:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")

    section = trust_engine.brief_sections(project["id"])
    _ensure_session(project["id"], session_id, agent)

    lines = [
        f"# 📚 {project['name']} — 작업 브리핑",
        "",
        "이 프로젝트의 지식 도서관입니다. 아래는 이전 작업에서 자동으로 쌓인 내용입니다.",
        "",
    ]
    if section["established"]:
        lines.append("## ✅ 확립된 지식")
        for m in section["established"]:
            lines.append(f"- **[{m['category']}] {m['title']}** — {m['summary']}")
        lines.append("")
    else:
        lines.append("## ✅ 확립된 지식\n(아직 없음 — 첫 작업부터 자동으로 쌓입니다)\n")
    if section["contested"]:
        lines.append("## ⚠️ 검증 필요 (어긋난 적이 있는 지식 — 단정하지 말 것)")
        for m in section["contested"]:
            lines.append(f"- [{m['category']}] {m['title']} — {m['summary']}")
        lines.append("")
    if section["recent"]:
        lines.append("## 🕘 최근 작업")
        for s in section["recent"]:
            lines.append(f"- {db.utc(s['started_at'])} · {s['agent']} · 질문 {s['question_count']}회")
        lines.append("")

    return {
        "project": project["slug"],
        "project_name": project["name"],
        "orientation": "\n".join(lines).strip(),
        "session_id": session_id,
        "established_count": len(section["established"]),
        "contested_count": len(section["contested"]),
    }


@router.post("/projects/{slug}/prepare")
def prepare(slug: str, body: dict, key: dict = Depends(bearer_auth)):
    """질문 전에 호출 — 관련 지식을 팩킹해서 주입."""
    project = db.one("SELECT * FROM projects WHERE slug=?", (slug,))
    if not project or project["id"] != key["project_id"]:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")

    prompt = redact.redact((body.get("prompt") or "").strip())[:2000]
    max_tier = int(body.get("max_tier", 1))
    result = retrieve.search(project["id"], prompt, max_items=8, max_tier=max_tier)
    trace_id = uuid.uuid4().hex[:12]
    injection = retrieve.inject_block(project["name"], result, trace_id)

    db.log_event(project["id"], "trace", db.jdumps(
        {"trace_id": trace_id, "q": prompt[:300],
         "memory_ids": [it["id"] for it in result["items"]]}
    ))
    sess = (body.get("session_id") or "")[:64]
    _ensure_session(project["id"], sess, body.get("agent") or "unknown")

    return {
        "trace_id": trace_id,
        "items": result["items"],
        "warnings": result["warnings"],
        "injection": injection,
    }


@router.post("/projects/{slug}/commit")
def commit(slug: str, body: dict, key: dict = Depends(bearer_auth)):
    """작업 한 턴이 끝나면 호출 — 기록 + 증류 + 직전 답 채점(암묵 피드백)."""
    project = db.one("SELECT * FROM projects WHERE slug=?", (slug,))
    if not project or project["id"] != key["project_id"]:
        from fastapi import HTTPException

        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")

    question = (body.get("question") or "").strip()[:2000]
    answer = (body.get("answer") or "").strip()[:20000]
    session_id = (body.get("session_id") or "")[:64]
    agent = (body.get("agent") or "unknown")[:40]
    files = [str(f)[:200] for f in (body.get("files") or [])][:20]

    _ensure_session(project["id"], session_id, agent)
    if session_id:
        db.execute(
            "UPDATE sessions SET question_count=question_count+1, ended_at=? WHERE id=?",
            (db.now(), session_id),
        )

    # ── 암묵 피드백: 직전에 주입한 지식이 '틀렸다'는 신호인가? ──
    if question:
        _implicit_feedback(project["id"], question)

    # ── 증류: 질문→답을 도서관 지식으로 ──
    result = distill.commit(project["id"], question, answer, session_id, files=files)
    if result["created"] or result["updated"]:
        db.execute(
            "INSERT INTO events(project_id, kind, detail, created_at) VALUES(?,?,?,?)",
            (project["id"], "session", f"{agent}: {question[:120]}", db.now()),
        )

    return {
        "ok": True,
        "session_id": session_id,
        "distilled": result,
    }


def _implicit_feedback(project_id: int, question: str) -> None:
    """직전 prepare 가 남긴 trace 를 다음 질문으로 채점합니다.

    '안 되는데/틀렸어/에러' 류 = 지난 답이 틀렸다는 판정 (bad)
    그 외 = 불만 없이 넘어감 (settled: 신뢰는 안 오름)
    """
    rows = db.rows(
        "SELECT id, detail FROM events WHERE project_id=? AND kind='trace' ORDER BY id DESC LIMIT 1",
        (project_id,),
    )
    if not rows:
        return
    detail = db.jloads(rows[0]["detail"])
    memory_ids = detail.get("memory_ids") or []
    if not memory_ids:
        return

    outcome = "bad" if distill.is_complaint(question) else "settled"
    for mid in memory_ids:
        trust_engine.apply(project_id, mid, outcome, note=f"다음 질문: {question[:80]}")
    db.execute("DELETE FROM events WHERE id=?", (rows[0]["id"],))


@router.post("/projects/{slug}/remember")
def remember(slug: str, body: dict, key: dict = Depends(bearer_auth)):
    """에이전트가 도구로 직접 지식을 남깁니다 (선택)."""
    project = db.one("SELECT * FROM projects WHERE slug=?", (slug,))
    if not project or project["id"] != key["project_id"]:
        from fastapi import HTTPException

        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    title = (body.get("title") or "").strip()[:200]
    content = (body.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(400, "title 과 content 가 필요합니다.")
    confirmed = bool(body.get("confirmed"))
    return distill.remember(project["id"], title, content,
                            body.get("category") or "knowledge", confirmed=confirmed)


@router.post("/projects/{slug}/score")
def score(slug: str, body: dict, key: dict = Depends(bearer_auth)):
    """명시적 피드백: outcome = good | bad | settled."""
    project = db.one("SELECT * FROM projects WHERE slug=?", (slug,))
    if not project or project["id"] != key["project_id"]:
        from fastapi import HTTPException

        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    memory_id = int(body.get("memory_id") or 0)
    outcome = body.get("outcome") or "settled"
    return trust_engine.apply(project["id"], memory_id, outcome,
                              note=(body.get("note") or "")[:120])


@router.get("/projects/{slug}/search")
def search(slug: str, q: str = "", key: dict = Depends(bearer_auth)):
    project = db.one("SELECT * FROM projects WHERE slug=?", (slug,))
    if not project or project["id"] != key["project_id"]:
        from fastapi import HTTPException

        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    result = retrieve.search(project["id"], q or "", max_items=8, max_tier=2)
    return {"items": result["items"], "warnings": result["warnings"]}