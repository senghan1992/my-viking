"""Agent API — 코딩 에이전트가 붙는 입구.

모든 엔드포인트는 Bearer API 키 (프로젝트 스코프) 로 인증됩니다.

새 컨셉 (서기 agent):
  작업 세션  brief → prepare → observe   — 관측만 남긴다 (지식을 만들지 않는다)
  서기 세션  inbox → remember → ack      — 판단·요약·등재는 여기서 (별도 pi 세션)
  서버       저장·검색·반복 카운트·적응   — 결정론적 일만 한다 (모델 연결은 선택)

레거시: auto_distill 이 켜진 프로젝트는 /commit 이 옛 방식으로 지식을 승격한다
(Claude Code·jcode 처럼 서기를 붙이지 않는 에이전트용).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db
from ..deps import bearer_auth
from ..engine import distill, observe, redact, retrieve
from ..engine import trust as trust_engine

router = APIRouter(prefix="/api/v1", tags=["agent"])


def _project_or_404(slug: str, key: dict) -> dict:
    project = db.one("SELECT * FROM projects WHERE slug=?", (slug,))
    if not project or project["id"] != key["project_id"]:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    return project


def _ensure_session(project_id: int, session_id: str, agent: str = "unknown",
                    transcript: str = "", counted: bool = False) -> None:
    """세션 행을 확보한다. question_count 는 '실제 질문이 관측된 턴'에서만 올린다."""
    if not session_id:
        return
    row = db.one("SELECT id FROM sessions WHERE id=?", (session_id,))
    now = db.now()
    if row:
        db.execute(
            """UPDATE sessions SET ended_at=?,
                   question_count=question_count+?,
                   transcript=CASE WHEN transcript='' THEN ? ELSE transcript END
               WHERE id=?""",
            (now, 1 if counted else 0, transcript[:300] or "", session_id),
        )
        return
    db.execute(
        """INSERT INTO sessions(id, project_id, agent, started_at, ended_at,
             question_count, transcript) VALUES(?,?,?,?,?,?,?)""",
        (session_id[:64], project_id, agent[:40] or "unknown", now, now,
         1 if counted else 0, transcript[:300]),
    )


@router.get("/health")
def health():
    return {
        "ok": True,
        "name": "myviking",
        "version": "2.0.0",
        "model": "secretary",           # 지식 추출은 서기 agent 가 한다 (서버는 저장고)
        "server_time": db.now(),
        "docs": "GET /api/v1/projects/{slug}/brief · GET .../inbox (서기용)",
    }


@router.get("/me")
def me(key: dict = Depends(bearer_auth)):
    """키만으로 프로젝트를 식별 — 연결할 때 슬러그를 몰라도 된다."""
    return {"project": key["slug"], "project_name": key["project_name"]}


# ══════════════════ 작업 세션 ══════════════════ #
@router.get("/projects/{slug}/brief")
def brief(slug: str, session_id: str = Query(default=""), agent: str = Query(default="unknown"),
          role: str = Query(default="worker"), key: dict = Depends(bearer_auth)):
    project = _project_or_404(slug, key)
    trust_engine.sweep_stale_sessions(project["id"])
    observe.sweep_closed(project["id"])
    section = trust_engine.brief_sections(project["id"])
    # 브리핑은 세션 행만 확보한다 — 질문 횟수에 세지 않는다 (브리핑만 받고 죽은 세션 구분)
    _ensure_session(project["id"], session_id, agent)

    lines = [
        f"# 📚 {project['name']} — 작업 브리핑",
        "",
        "이 프로젝트의 지식 도서관입니다. 아래는 서기 agent 가 이전 세션에서 골라낸 내용입니다.",
        "",
    ]
    if role == "secretary":
        lines.append("> 당신은 지금 **서기 세션**으로 열렸습니다. 코드를 고치지 말고,"
                     " `/inbox` 를 읽어 지식화할 것만 골라 등재하세요. (jv secretary status)")
        lines.append("")
    if section["established"]:
        lines.append("## ✅ 확립된 지식")
        for m in section["established"]:
            occ = f" · {m['occurrences']}회 요청" if m.get("occurrences") else ""
            lines.append(f"- **[{m['category']}] {m['title']}** — {m['summary']}{occ}")
        lines.append("")
    else:
        lines.append("## ✅ 확립된 지식\n(아직 없음 — 세션을 관찰한 서기가 정리하면 여기에 꽂힙니다)\n")
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

    pending = observe.pending_count(project["id"])
    rep = observe.run_status(project["id"])["last_run"]
    lines.append("## 🧾 서기 (지식 정리 agent)")
    lines.append(f"- 정리 대기 관찰 **{pending}건**"
                 + (f" · 마지막 정리 {rep['started_at']}" if rep else " · 아직 정리 실행 없음"))
    if pending:
        lines.append("- 정리 실행: `jv secretary once` (pi 세션 안에서: `/myviking secretary once`)")
    else:
        lines.append("- 대기 중 없음 — 작업 세션의 관찰은 계속 쌓입니다.")

    return {
        "project": project["slug"],
        "project_name": project["name"],
        "orientation": "\n".join(lines).strip(),
        "session_id": session_id,
        "established_count": len(section["established"]),
        "contested_count": len(section["contested"]),
        "pending_observations": pending,
        "auto_distill": bool(project.get("auto_distill")),
    }


@router.post("/projects/{slug}/prepare")
def prepare(slug: str, body: dict, key: dict = Depends(bearer_auth)):
    """질문 전에 호출 — 관련 지식을 팩킹해서 주입."""
    project = _project_or_404(slug, key)

    prompt = redact.redact((body.get("prompt") or "").strip())[:2000]
    max_tier = int(body.get("max_tier", 1))
    result = retrieve.search(project["id"], prompt, max_items=8, max_tier=max_tier)
    trace_id = uuid.uuid4().hex[:12]
    injection = retrieve.inject_block(project["name"], result, trace_id)

    trust_engine.sweep_stale_sessions(project["id"])
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


@router.post("/projects/{slug}/observe")
def observe_turn(slug: str, body: dict, key: dict = Depends(bearer_auth)):
    """작업 세션 → 서버: '지금 이런 일이 있었다' 를 관찰로 남긴다. (지식을 만들지 않는다)

    body = {session_id, agent, transcript?, items:[{kind, text, turn?, files?}]}
           또는 단일 {kind, text}
    """
    project = _project_or_404(slug, key)
    session_id = (body.get("session_id") or "")[:64]
    agent = (body.get("agent") or "unknown")[:40]
    transcript = (body.get("transcript") or "")[:300]
    items = body.get("items")
    if not isinstance(items, list) or not items:
        items = [{"kind": body.get("kind") or "note", "text": body.get("text") or "",
                  "files": body.get("files") or [], "turn": body.get("turn") or 0}]

    logged = observe.batch(project["id"], session_id=session_id, agent=agent,
                           transcript=transcript, items=items)
    asked = any(x["created"] and (x.get("kind") in ("prompt", "request", "note"))
                for x in logged)
    if session_id and (asked or transcript):
        _ensure_session(project["id"], session_id, agent, transcript, counted=asked)

    # 암묵 피드백: 이번 질문이 "지난 지식이 어긋났다"는 신호면 강등한다
    prompt_text = next((i["text"] for i in items
                        if (i.get("kind") or "") in ("prompt", "note") and i.get("text")), "")
    if prompt_text and distill.is_complaint(prompt_text):
        _implicit_feedback(project["id"], prompt_text)

    repeat = max((x["hits"] for x in logged if x["created"]), default=0)
    return {
        "ok": True,
        "logged": [x["id"] for x in logged if x["id"]],
        "pending": observe.pending_count(project["id"]),
        "repeat_hits": repeat,
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


# ══════════════════ 서기 세션 ══════════════════ #
@router.get("/projects/{slug}/inbox")
def inbox(slug: str, limit: int = Query(default=60, ge=1, le=400),
          session_id: str = Query(default=""), claim: bool = Query(default=True),
          worker: str = Query(default=""), key: dict = Depends(bearer_auth)):
    """서기의 작업 목록 — 미처리 관찰 + 반복 요청 + 재발 오류 + 세션 트랜스크립트 위치."""
    project = _project_or_404(slug, key)
    return observe.inbox(project["id"], limit=limit, session_id=session_id,
                         claim=bool(claim) and bool(worker), worker=worker[:64])


@router.post("/projects/{slug}/inbox/ack")
def inbox_ack(slug: str, body: dict, key: dict = Depends(bearer_auth)):
    """서기의 처리 통보 — filed(지식 등재/갱신)·skipped(버림)·merged.

    body = {worker, results:[{ids:[..], action:'filed'|'skipped'|'merged', memory_id?, reason?}],
            report?: str, found?: int}
    """
    project = _project_or_404(slug, key)
    worker = (body.get("worker") or body.get("session_id") or "")[:64]
    results = body.get("results") or []
    tally = {"filed": 0, "merged": 0, "skipped": 0}
    for r in results:
        if not isinstance(r, dict):
            continue
        action = str(r.get("action") or "skipped")
        action = action if action in tally else "skipped"
        ids = [int(i) for i in (r.get("ids") or ([r["id"]] if r.get("id") else []))]
        observe.mark(project["id"], ids, state=action, worker=worker,
                     memory_id=r.get("memory_id"), note=str(r.get("reason") or ""))
        tally[action] += 1
    observe.run_finish(
        worker or "anon", project["id"],
        filed=tally["filed"], merged=tally["merged"], skipped=tally["skipped"],
        found=body.get("found"), report=str(body.get("report") or ""),
    )
    return {"ok": True, **tally, "pending": observe.pending_count(project["id"])}


@router.get("/projects/{slug}/secretary/status")
def secretary_status(slug: str, key: dict = Depends(bearer_auth)):
    project = _project_or_404(slug, key)
    st = observe.run_status(project["id"])
    st["repeats"] = observe.repeats(project["id"], limit=5)
    st["auto_distill"] = bool(project.get("auto_distill"))
    return st


# ══════════════════ 등재·채점·검색 ══════════════════ #
@router.post("/projects/{slug}/remember")
def remember(slug: str, body: dict, key: dict = Depends(bearer_auth)):
    """지식 등재 — 서기의 주된 길 (사람·에이전트의 명시 기록도 여기로).

    body = {title, content, category?, confirmed?, source?, occurrences?,
            observation_ids?: [..], reason?}
    """
    project = _project_or_404(slug, key)
    title = (body.get("title") or "").strip()[:200]
    content = (body.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(400, "title 과 content 가 필요합니다.")
    source = (body.get("source") or "manual")[:20]
    obs_ids = [int(i) for i in (body.get("observation_ids") or [])][:50]
    reason = (body.get("reason") or "")[:120]

    out = distill.remember(
        project["id"], title, content,
        body.get("category") or "knowledge",
        confirmed=bool(body.get("confirmed")),
        source=source,
        session_ref=(body.get("session_id") or "")[:100],
        occurrences=int(body.get("occurrences") or 0),
        evidence=[reason] if reason else None,
    )
    merged = bool(out.get("merged"))
    if obs_ids:
        observe.mark(project["id"], obs_ids, state="merged" if merged else "filed",
                     worker=source, memory_id=out["memory_id"], note=reason)
        if source == "secretary":
            observe.run_tally(project["id"], (body.get("session_id") or "")[:64] or source,
                              filed=0 if merged else 1, merged=1 if merged else 0)
    return out


@router.post("/projects/{slug}/seen")
def seen(slug: str, body: dict, key: dict = Depends(bearer_auth)):
    """'이 지식이 이번 세션에서 또 쓰였다' — 반복 요청 횟수만 올린다 (본문은 그대로)."""
    project = _project_or_404(slug, key)
    mid = int(body.get("memory_id") or 0)
    if not mid:
        raise HTTPException(400, "memory_id 가 필요합니다.")
    distill.score_existing(project["id"], mid, int(body.get("occurrences") or 1))
    return {"ok": True, "memory_id": mid}


@router.post("/projects/{slug}/score")
def score(slug: str, body: dict, key: dict = Depends(bearer_auth)):
    """명시적 피드백: outcome = good | bad | settled."""
    project = _project_or_404(slug, key)
    memory_id = int(body.get("memory_id") or 0)
    outcome = body.get("outcome") or "settled"
    return trust_engine.apply(project["id"], memory_id, outcome,
                              note=(body.get("note") or "")[:120])


@router.get("/projects/{slug}/search")
def search(slug: str, q: str = "", key: dict = Depends(bearer_auth)):
    project = _project_or_404(slug, key)
    result = retrieve.search(project["id"], q or "", max_items=8, max_tier=2)
    return {"items": result["items"], "warnings": result["warnings"]}


# ══════════════════ 레거시 (Claude Code·jcode 훅) ══════════════════ #
@router.post("/projects/{slug}/commit")
def commit(slug: str, body: dict, key: dict = Depends(bearer_auth)):
    """[레거시] 한 턴의 질문→답을 넘긴다.

    기본(auto_distill=0): 관찰로만 저장 — 지식을 만들지 않는다. 서기가 정리한다.
    auto_distill=1 인 프로젝트: 옛 방식으로 즉시 증류 (서기를 두지 않는 에이전트용).
    """
    project = _project_or_404(slug, key)
    question = (body.get("question") or "").strip()[:2000]
    answer = (body.get("answer") or "").strip()[:20000]
    session_id = (body.get("session_id") or "")[:64]
    agent = (body.get("agent") or "unknown")[:40]
    files = [str(f)[:200] for f in (body.get("files") or [])][:20]

    trust_engine.sweep_stale_sessions(project["id"])
    if question and distill.is_complaint(question):
        _implicit_feedback(project["id"], question)

    if not int(body.get("auto_distill", project.get("auto_distill") or 0)):
        logged = observe.batch(
            project["id"], session_id=session_id, agent=agent,
            transcript=(body.get("transcript") or "")[:300],
            items=[{"kind": "prompt", "text": question, "files": files},
                   {"kind": "reply", "text": answer, "files": files}],
        )
        if session_id:
            _ensure_session(project["id"], session_id, agent,
                            (body.get("transcript") or ""), counted=bool(question))
        return {"ok": True, "mode": "observe",
                "logged": [x["id"] for x in logged if x["id"]],
                "pending": observe.pending_count(project["id"]),
                "distilled": {"created": 0, "updated": 0, "superseded": 0, "memory_id": None}}

    _ensure_session(project["id"], session_id, agent, counted=bool(question))
    result = distill.commit(project["id"], question, answer, session_id, files=files)
    if result["created"] or result["updated"]:
        db.execute(
            "INSERT INTO events(project_id, kind, detail, created_at) VALUES(?,?,?,?)",
            (project["id"], "session", f"{agent}: {question[:120]}", db.now()),
        )
    return {"ok": True, "mode": "distill", "session_id": session_id, "distilled": result}
