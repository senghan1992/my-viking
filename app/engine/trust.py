"""신뢰 상태 기계 — 도서관 지식이 결과에 따라 적응합니다.

상태 흐름 (가장 중요한 4개만으로 단순화):
    fresh(검증 전) ──좋은 결과──▶ established(확립)
    established ──반박──▶ contested(검증 필요)  ──좋은 결과──▶ established
    established ──교정본──▶ superseded(대체됨, 주입 안 됨)

원칙: '주입됐다'는 맞았다는 증거가 아닙니다. 좋은 결과·사람 확인만이 확립을
만듭니다. 반박은 최근 결과(evidence)로 기록되어 대시보드에서 추적됩니다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import db
from .distill import _append_evidence

OUTCOMES = {"good", "bad", "settled"}

# 브리핑만 받고 질문 없이 죽은 세션을 정리하는 유예 시간 (이후 삭제)
STALE_SESSION_GRACE_HOURS = 24


def apply(project_id: int, memory_id: int, outcome: str, note: str = "") -> dict:
    """추천/주입한 지식의 결과를 반영. → {status, trust, delta}"""
    if outcome not in OUTCOMES:
        return {"status": "unknown", "trust": 0, "delta": 0}

    mem = db.one("SELECT * FROM memories WHERE id=? AND project_id=?", (memory_id, project_id))
    if not mem or mem["status"] == "superseded":
        return {"status": "superseded", "trust": 0, "delta": 0}

    trust = float(mem["trust"])
    status = mem["status"]
    delta = 0.0
    kind = outcome

    if outcome == "good":
        delta = 0.15 if trust < 1 else 0.0
        trust = min(1.0, trust + 0.15)
        if status != "established":
            status = "established"
        db.execute(
            "UPDATE memories SET status=?, trust=?, correct_count=correct_count+1, last_used_at=? WHERE id=?",
            (status, round(trust, 2), db.now(), memory_id),
        )
    elif outcome == "bad":
        delta = -0.25
        trust = max(0.0, trust - 0.25)
        if status != "contested":
            status = "contested"  # 반박 1회면 즉시 검증 필요로 강등 (간결·안전)
        db.execute(
            "UPDATE memories SET status=?, trust=?, wrong_count=wrong_count+1 WHERE id=?",
            (status, round(trust, 2), memory_id),
        )
    else:  # settled — 불만 없이 지나감: 신뢰는 안 오르고 반박만 소멸
        db.execute("UPDATE memories SET last_used_at=? WHERE id=?", (db.now(), memory_id))

    _append_evidence(memory_id, kind, note or outcome)
    db.log_event(project_id, "memory", f"결과 {outcome} → {status}: [{mem['title'][:40]}]")
    return {"status": status, "trust": round(trust, 2), "delta": delta}


def brief_sections(project_id: int, limit: int = 5) -> dict:
    """세션 시작 브리핑에 들어갈 도서관 요약."""
    established = db.rows(
        """SELECT * FROM memories WHERE project_id=? AND status='established'
           ORDER BY last_used_at IS NULL, trust DESC, updated_at DESC LIMIT ?""",
        (project_id, limit),
    )
    contested = db.rows(
        """SELECT id, category, title, summary FROM memories
           WHERE project_id=? AND status='contested' ORDER BY wrong_count DESC LIMIT 5""",
        (project_id,),
    )
    recent = db.rows(
        """SELECT * FROM sessions WHERE project_id=? AND question_count>0
           ORDER BY started_at DESC LIMIT 5""",
        (project_id,),
    )
    return {"established": established, "contested": contested, "recent": recent}


def sweep_stale_sessions(project_id: int, grace_hours: int = STALE_SESSION_GRACE_HOURS) -> int:
    """질문 0회로 유예 시간을 넘긴 죽은 세션을 정리한다.

    세션 행은 brief/prepare 가 호출되는 순간 생성되는데, 에이전트가 그 뒤
    질문·답(commit) 없이 죽거나 연결이 끊기면 0회 행으로 남는다.
    브리핑만 받은 세션은 유예 시간이 지나면 의미가 없으므로 삭제한다.
    (질문이 있었던 세션은 기록이므로 절대 지우지 않는다.)
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=grace_hours)).isoformat(timespec="seconds")
    return db.execute(
        """DELETE FROM sessions
           WHERE project_id=? AND question_count=0 AND started_at < ?""",
        (project_id, cutoff),
    )