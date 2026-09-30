"""관찰(observations) — 작업 세션에서 벌어진 일을 '그대로' 쌓아두고, 서기 agent 가 골라 정리한다.

컨셉의 핵심: **이 모듈은 지식을 만들지 않는다.** 판단은 서기 agent(pi 별도 세션)의 몫이고,
서버는 두 가지만 한다.

1. 적재 — 관찰을 state='open' 으로 받고, 같은 자리에서 같은 요청이 반복되면 hits 를 올린다.
2. 대조 — 어휘 겹침으로 '여러 번 요청한 항목'과 '다시 나타난 오류'를 결정론적으로 묶어 준다.
   (모델을 쓰지 않는다 — 빠르고 재현 가능하다.)

서기는 `/inbox` 로 읽은 뒤 `/remember` 로 등재하고 `/inbox/ack` 로 처리 사실을 통보한다.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .. import db
from .redact import redact
from .tokens import root_words

KINDS = ("prompt", "reply", "error", "edit", "note", "decision", "request")
STATES = ("open", "claimed", "filed", "skipped")

# 관찰 원문은 잘라 저장한다 (트랜스크립트 자체는 로컬 파일에 있고, 서기가 직접 읽는다)
MAX_TEXT = 3000
MAX_FILES = 20

# 반복 감지에서 버리는 말 — 조사·어미·지시어까지 남기면 서로 다른 요청이 같은 것으로 갉아먹힌다
_FILLER = re.compile(
    r"(?:(?:해|알려|줘|주세요|해줘|바꿔|고쳐|수정| 요청| 다시| 또| 여전| 왜| 안 되| 안되)"
    r"|(?:please|again|still|fix|do it|make it work))",
    re.IGNORECASE,
)


def fingerprint(text: str) -> str:
    """요청 텍스트의 '지문' — 같은 세션에서 같은 요청이 반복된 것을 합치는 데 쓴다."""
    return " ".join(sorted(set(root_words(_FILLER.sub(" ", text or ""))))[:12])


def log(project_id: int, *, session_id: str, agent: str, kind: str, text: str,
        files: list[str] | None = None, transcript: str = "", turn: int = 0,
        redact_text: bool = True) -> dict:
    """관찰 한 줄 적재. 같은 세션·같은 지문이 이미 열려 있으면 hits 를 올린다(중복 저감)."""
    kind = kind if kind in KINDS else "note"
    body = (text or "").strip()
    if not body:
        return {"id": None, "created": False, "hits": 0, "kind": kind,
                "pending": pending_count(project_id)}
    if redact_text:
        body = redact(body)
    body = body[:MAX_TEXT]
    norm = fingerprint(body)
    files_json = db.jdumps([str(f)[:200] for f in (files or [])][:MAX_FILES])

    open_row = db.one(
        """SELECT id, hits FROM observations
           WHERE project_id=? AND session_id=? AND state IN ('open','claimed') AND kind=? AND norm=? AND norm!=''
           ORDER BY id DESC LIMIT 1""",
        (project_id, session_id[:64], kind, norm),
    ) if norm else None
    now = db.now()
    if open_row:
        db.execute(
            "UPDATE observations SET hits=hits+1, text=?, files=?, transcript=?, created_at=? WHERE id=?",
            (body, files_json, transcript[:300] or "", now, open_row["id"]),
        )
        obs_id, hits, created = open_row["id"], int(open_row["hits"]) + 1, False
    else:
        obs_id = db.execute(
            """INSERT INTO observations(project_id, session_id, agent, turn, kind, text, norm,
               files, transcript, state, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,'open',?)""",
            (project_id, session_id[:64], agent[:40] or "unknown", turn, kind, body, norm,
             files_json, transcript[:300], now),
        )
        hits = db.one("SELECT hits FROM observations WHERE id=?", (obs_id,))["hits"]
        created = True
    return {"id": obs_id, "created": created, "hits": int(hits), "kind": kind,
            "pending": pending_count(project_id)}


def batch(project_id: int, *, session_id: str, agent: str, transcript: str,
          items: list[dict]) -> list[dict]:
    """작업 세션이 한 턴에 여러 관찰을 한 번에 보낼 때 (fail-open — 개별 실패는 건너뜀)."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        out.append(log(
            project_id,
            session_id=session_id or str(it.get("session_id") or ""),
            agent=agent or str(it.get("agent") or ""),
            kind=str(it.get("kind") or "note"),
            text=str(it.get("text") or ""),
            files=it.get("files") or None,
            transcript=str(it.get("transcript") or transcript or ""),
            turn=int(it.get("turn") or 0),
        ))
    return out


def pending_count(project_id: int) -> int:
    row = db.one(
        "SELECT COUNT(*) AS n FROM observations WHERE project_id=? AND state IN ('open','claimed')",
        (project_id,),
    )
    return int(row["n"]) if row else 0


def _overlap(a: set, b: set) -> float:
    """두 요청의 어휘 겹침 (Jaccard) — 단, 공통 단어가 2개 미만이면 같은 요청으로 보지 않는다."""
    if not a or not b:
        return 0.0
    shared = len(a & b)
    if shared < 2:                      # 한 단어 우연 일치는 같은 요청으로 보지 않는다
        return 0.0
    return shared / len(a | b)


def repeats(project_id: int, limit: int = 12, min_hits: int = 2) -> list[dict]:
    """'여러 번 요청한 항목' — 지문이 완전히 같지 않아도 어휘가 겹치면 같은 요청으로 묶는다.

    서버가 하는 결정론적 계산이다 (모델 안 씀). 서기에게는 '규칙으로 승격 1순위' 신호이고,
    person 에게는 "내가 이걸 또 시켰다" 는 가시성이 된다.
    """
    rows = db.rows(
        """SELECT id, text, hits, created_at FROM observations
           WHERE project_id=? AND state IN ('open','claimed') AND kind IN ('prompt','request','note')
           ORDER BY id DESC LIMIT 300""",
        (project_id,),
    )
    clusters: list[dict] = []           # {words:set, ids:[], hits:int, text:str, last_at:str}
    for r in rows:
        words = set(root_words(_FILLER.sub(" ", r["text"] or "")))
        if not words:
            continue
        best, best_score = None, 0.0
        for c in clusters:
            score = _overlap(words, c["words"])
            if score > best_score:
                best, best_score = c, score
        if best is not None and best_score >= 0.30:
            best["ids"].append(r["id"])
            best["hits"] += int(r["hits"])
            best["words"] |= words
        else:
            clusters.append({"words": words, "ids": [r["id"]], "hits": int(r["hits"]),
                             "text": r["text"], "last_at": r["created_at"]})

    picked = [c for c in clusters if c["hits"] >= min_hits]
    picked.sort(key=lambda c: (-c["hits"], c["last_at"]), reverse=False)
    out = []
    for c in picked[:limit]:
        similar = similar_memory_ids(project_id, c["text"])
        out.append({"text": c["text"], "hits": c["hits"], "entries": len(c["ids"]),
                    "ids": c["ids"], "last_at": db.utc(c["last_at"]),
                    "already_filed": bool(similar), "similar_memory_ids": similar})
    return out


def errors(project_id: int, limit: int = 10) -> list[dict]:
    """같은 자리에 다시 나타난 오류 — pitfalls 후보로 특히 가치 높다."""
    rows = db.rows(
        """SELECT norm, MAX(text) AS text, SUM(hits) AS hits, MAX(created_at) AS last_at,
                  GROUP_CONCAT(DISTINCT session_id) AS sessions
           FROM observations
           WHERE project_id=? AND state IN ('open','claimed') AND kind='error' AND norm != ''
           GROUP BY norm
           ORDER BY SUM(hits) DESC, MAX(created_at) DESC
           LIMIT ?""",
        (project_id, limit),
    )
    return [{"text": r["text"], "hits": int(r["hits"]), "last_at": db.utc(r["last_at"]),
             "sessions": [s for s in str(r["sessions"] or "").split(",") if s]} for r in rows]


def release_stale(project_id: int, minutes: int = 45) -> int:
    """서기가 선점만 해두고 죽은 관찰을 다시 대기으로 되돌린다 (누락 방지)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    return db.execute(
        """UPDATE observations SET state='open', worker='' WHERE project_id=? AND state='claimed'
           AND COALESCE(processed_at, created_at) < ?""",
        (project_id, cutoff),
    )


def inbox(project_id: int, *, limit: int = 60, session_id: str = "", claim: bool = True,
          worker: str = "") -> dict:
    """서기가 읽는 작업 목록 — 세션별 관찰 + 반복 신호 + 트랜스크립트 위치.

    보이는 규칙: 자기가 선점한 것은 계속 보이고, 남이 선점한 것은 보이지 않는다.
    (단, 선점만 하고 45분 넘게 처리하지 않은 관찰은 자동으로 대기 복귀 — 서기가 죽어도 안 사라진다.)
    """
    release_stale(project_id)
    if claim and worker:
        where = ["project_id=?", "(state='open' OR (state='claimed' AND worker=?))"]
        args: list = [project_id, worker[:64]]
    else:
        # 읽기 전용(사람·셸 확인) — 남이 선점한 것까지 다 본다
        where = ["project_id=?", "state IN ('open','claimed')"]
        args: list = [project_id]
    if session_id:
        where.append("session_id=?")
        args.append(session_id[:64])
    rows = db.rows(
        f"""SELECT * FROM observations WHERE {' AND '.join(where)}
            ORDER BY id DESC LIMIT ?""",
        tuple(args) + (limit,),
    )
    rows.reverse()  # 오래된 것부터 읽게 (세션 흐름이 유지되어야 판단할 수 있다)
    by_session: dict[str, dict] = {}
    for r in rows:
        sid = r["session_id"] or "-"
        slot = by_session.setdefault(sid, {
            "session_id": sid, "agent": r["agent"], "transcript": "", "observations": [],
        })
        if r["transcript"] and not slot["transcript"]:
            slot["transcript"] = r["transcript"]
        slot["observations"].append({
            "id": r["id"], "kind": r["kind"], "turn": r["turn"], "text": r["text"],
            "hits": int(r["hits"]), "files": db.jloads(r["files"]), "at": db.utc(r["created_at"]),
            "state": r["state"],
        })
    sessions = list(by_session.values())
    fresh = [r["id"] for r in rows if r["state"] == "open"]
    if claim and worker and fresh:
        mark(project_id, fresh, state="claimed", worker=worker)

    return {
        "project_id": project_id,
        "pending": pending_count(project_id),
        "worker": worker,
        "sessions": sessions,
        "repeats": repeats(project_id),
        "recurrences": errors(project_id),
        "contested": contested(project_id),
    }


def contested(project_id: int, limit: int = 5) -> list[dict]:
    """검증 필요로 강등된 지식 — 서기가 이번 세션의 증거로 확립/삭제를 결정해야 한다."""
    rows = db.rows(
        """SELECT id, category, title, summary, wrong_count, updated_at FROM memories
           WHERE project_id=? AND status='contested' ORDER BY wrong_count DESC LIMIT ?""",
        (project_id, limit),
    )
    return [{"id": r["id"], "category": r["category"], "title": r["title"],
             "summary": r["summary"], "wrong_count": r["wrong_count"]} for r in rows]


def mark(project_id: int, ids: list[int], *, state: str, worker: str = "",
         memory_id: int | None = None, note: str = "") -> int:
    """관찰 상태를 바꾼다 (claimed/filed/skipped). 선점 시각은 processed_at 에 남긴다."""
    if state not in STATES or not ids:
        return 0
    body = ",".join("?" * len(ids))
    return db.execute(
        f"""UPDATE observations SET state=?, worker=?, memory_id=COALESCE(?, memory_id),
               note=CASE WHEN ?='' THEN note ELSE ? END,
               processed_at=CASE WHEN ? IN ('claimed','filed','skipped') THEN ? ELSE processed_at END
            WHERE project_id=? AND id IN ({body})""",
        (state, worker[:64] or "", memory_id, note[:300], note[:300], state, db.now(), project_id, *ids),
    )


def similar_memory_ids(project_id: int, text: str, limit: int = 3) -> list[int]:
    """비슷한 제목/본문의 지식이 이미 있는가 — 서기의 중복 등재를 막는 대조표."""
    from .retrieve import search

    found = search(project_id, text, max_items=limit, max_tier=0)
    return [it["id"] for it in found["items"]]


def sweep_closed(project_id: int, keep_days: int = 14) -> int:
    """처리 통보까지 끝난 관찰은 오래 두고 보지 않는다 (저장소 팽창 방지)."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat(timespec="seconds")
    return db.execute(
        "DELETE FROM observations WHERE project_id=? AND state IN ('filed','skipped') AND created_at < ?",
        (project_id, cutoff),
    )


# ── 서기 실행장 (감사 추적) ─────────────────────────────── #
def run_start(run_id: str, project_id: int, agent: str = "pi", found: int = 0) -> None:
    now = db.now()
    db.execute(
        """INSERT INTO secretary_runs(id, project_id, agent, started_at, found)
           VALUES(?,?,?,?,?)""",
        (run_id[:64], project_id, agent[:40] or "pi", now, found),
    )


def run_finish(run_id: str, project_id: int, *, filed: int = 0, merged: int = 0,
               skipped: int = 0, found: int | None = None, report: str = "") -> None:
    row = db.one("SELECT id FROM secretary_runs WHERE id=?", (run_id[:64],))
    if not row:
        run_start(run_id, project_id, found=found or 0)
    db.execute(
        """UPDATE secretary_runs SET finished_at=?, filed=filed+?, merged=merged+?,
               skipped=skipped+?, found=COALESCE(?, found),
               report=CASE WHEN report='' THEN ? ELSE report END WHERE id=?""",
        (db.now(), filed, merged, skipped, found, report[:500], run_id[:64]),
    )
    db.log_event(project_id, "secretary",
                 f"서기 업무: {run_id[:16]} — 등재 +{filed} · 갱신 +{merged} · 버림 +{skipped}"
                 + (f" — {report[:80]}" if report else ""))


def run_tally(project_id: int, worker: str, *, filed: int = 0, merged: int = 0) -> None:
    """remember 로 바로 등재한 경우를 실행장에 더해준다 (ack 없이 등재만 해도 장부가 맞도록)."""
    if not worker:
        return
    if not db.one("SELECT id FROM secretary_runs WHERE id=?", (worker[:64],)):
        run_start(worker, project_id)
    db.execute("UPDATE secretary_runs SET filed=filed+?, merged=merged+? WHERE id=?",
               (filed, merged, worker[:64]))


def run_status(project_id: int) -> dict:
    last = db.one(
        "SELECT * FROM secretary_runs WHERE project_id=? ORDER BY started_at DESC LIMIT 1",
        (project_id,),
    )
    counts = db.one(
        """SELECT SUM(state='open') AS open, SUM(state='claimed') AS claimed,
                  SUM(state='filed') AS filed, SUM(state='skipped') AS skipped
           FROM observations WHERE project_id=?""",
        (project_id,),
    ) or {}
    return {
        "pending": int(counts.get("open") or 0) + int(counts.get("claimed") or 0),
        "filed_total": int(counts.get("filed") or 0),
        "skipped_total": int(counts.get("skipped") or 0),
        "last_run": ({
            "id": last["id"], "agent": last["agent"], "started_at": db.utc(last["started_at"]),
            "finished_at": db.utc(last["finished_at"]), "found": last["found"],
            "filed": last["filed"], "merged": last["merged"], "skipped": last["skipped"],
            "report": last["report"],
        } if last else None),
    }
