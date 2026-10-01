"""등재(remember) — 서기 agent 가 판단한 것을 지식 한 권으로 올리는 입구.

컨셉: 서버는 지식을 '추출'하지 않는다. 추출·판단은 서기 agent(별도 pi 세션)가 하고,
여기서는 그 결과만 받아 보관한다.

- 같은 제목(정규화)이 이미 있으면 새 권을 만들지 않고 본문을 갱신한다 → 서가는 자라지
  않고 `occurrences`(사람이 이걸 몇 번 요청했나)만 누적된다.
- 확립된 지식을 같은 제목으로 다시 쓰면 교정으로 본다 → 옛것은 superseded.
- commit() 은 레거시 경로다 (서버가 직접 증류하던 옛 방식 — auto_distill 켠 프로젝트만).
"""
from __future__ import annotations

import re

from .. import db
from .redact import redact
from .tiers import make_keywords, make_overview, make_summary
from . import llm

CATEGORIES = ("knowledge", "commands", "pitfalls", "decisions")

_COMPLAINT = re.compile(
    r"(안\s*되|안\s*돼|틀렸|에러|오류|실패|실패해|안 먹|안 먹혀|무시|작동 안|"
    r"not working|doesn'?t work|failed|error|broken|문제|이상한데|뭔가 잘못|"
    r"여전히|그래도|도저히|왜 이래|버그)",
    re.IGNORECASE,
)
_COMMAND_HINT = re.compile(r"```|^\s*\$ |^\s*> |npm |pip |docker |brew |git |apt |curl |yarn |pnpm ")
_DECISION_HINT = re.compile(r"(선택|결정|채택|정하기로|하기로|이유는|왜냐하면|근거)", re.IGNORECASE)


def normalize_title(title: str) -> str:
    """제목 동일성 판정용 정규화 (조사·구두점·공백 제거)."""
    t = title.lower()
    t = re.sub(r"[^\w가-힣]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def condense_question(question: str, max_chars: int = 80) -> str:
    """질문을 지식 제목으로 압축 — 문장 부호·어미를 정리."""
    q = question.strip()
    q = re.sub(r"[?？.!。]+$", "", q)
    q = re.sub(r"(하는지|하는 건지|할지|할까요|해줘|해주세요|알려줘|알려주세요|있나요|있을까요)\s*$", "", q)
    q = " ".join(q.split())
    return q[:max_chars] or "기록"


def guess_category(question: str, answer: str) -> str:
    """질문·답의 어휘로 도서관 섹션 추정."""
    if _COMPLAINT.search(question):
        return "pitfalls"
    hint = answer[:800]
    if _COMMAND_HINT.search(hint) or re.search(r"\b(설치|실행|명령|커맨드|스크립트|세팅|설정|플러그인|패키지)\b", question):
        return "commands"
    if _DECISION_HINT.search(question):
        return "decisions"
    return "knowledge"


def is_complaint(question: str) -> bool:
    return bool(_COMPLAINT.search(question))


def _store_memory(
    project_id: int,
    category: str,
    title: str,
    content: str,
    source: str,
    confirmed: bool = False,
    corrects: int | None = None,
    session_ref: str = "",
    occurrences: int = 0,
    evidence_notes: list[str] | None = None,
) -> int:
    summary = make_summary(title, content)
    overview = make_overview(content)
    kws = make_keywords(title, content)
    status = "established" if confirmed else "fresh"
    now = db.now()
    seed = [{"at": now, "kind": "filed", "note": (n or "서기 등재")[:120]}
            for n in (evidence_notes or [])][:10]
    mid = db.execute(
        """INSERT INTO memories(project_id, category, title, summary, overview, content,
           status, trust, keywords, source, corrects, session_ref, created_at, updated_at,
           occurrences, last_seen_at, evidence)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",

        (
            project_id, category, title[:200], summary, overview, content[:50000],
            status, 0.7 if confirmed else 0.0, db.jdumps(kws), source,
            corrects, session_ref[:100], now, now,
            max(0, int(occurrences or 0)), now, db.jdumps(seed),
        ),
    )
    llm.save_embeddings(mid, f"{title} {content}")
    return mid


# ══════════════════ 서기·사람이 남기는 길이 (기본) ══════════════════ #
def remember(
    project_id: int,
    title: str,
    content: str,
    category: str = "knowledge",
    confirmed: bool = False,
    *,
    source: str = "manual",
    session_ref: str = "",
    occurrences: int = 0,
    evidence: list[str] | None = None,
) -> dict:
    """사람·서기가 명시적으로 남기는 지식. confirmed=True 면 확립으로.

    같은 제목(정규화)이 이미 있으면 중복 등재 대신 본문 갱신 + occurrences 누적.
    확립된 지식을 같은 제목으로 '다시 쓰는' 것만 교정(superseded)으로 본다.
    """
    if category not in CATEGORIES:
        category = "knowledge"
    title = (title or "").strip()[:200]
    body = redact((content or "").strip())[:50000]
    norm = normalize_title(title)
    match = next((m for m in db.rows(
        "SELECT * FROM memories WHERE project_id=? AND status!='superseded'", (project_id,),
    ) if normalize_title(m["title"]) == norm), None)

    correcting = bool(match and confirmed and match["status"] == "established"
                      and normalize_title(match["content"])[:120] != normalize_title(body)[:120])

    if match and not correcting:
        now = db.now()
        db.execute(
            """UPDATE memories SET category=?, summary=?, overview=?, content=?, keywords=?,
               source=?, updated_at=?, session_ref=?, occurrences=?, last_seen_at=? WHERE id=?""",
            (category, make_summary(title, body), make_overview(body), body,
             db.jdumps(make_keywords(title, body)), source, now, session_ref[:100],
             int(match["occurrences"] or 0) + max(1, int(occurrences or 0)), now, match["id"]),
        )
        llm.save_embeddings(match["id"], f"{title} {body}")
        for note in (evidence or [])[:3]:
            _append_evidence(match["id"], "updated", note)
        db.log_event(project_id, "memory", f"갱신 [{category}] {title[:50]}")
        return {"memory_id": match["id"], "merged": match["id"], "superseded": 0,
                "uri": f"scribe://{project_id}/memories/{category}/{match['id']}"}

    superseded = match["id"] if (correcting and match) else 0
    mid = _store_memory(project_id, category, title, body, source or "manual",
                        confirmed=confirmed, corrects=superseded or None,
                        session_ref=session_ref, occurrences=occurrences,
                        evidence_notes=evidence)
    if superseded:
        db.execute("UPDATE memories SET status='superseded', superseded_by=? WHERE id=?",
                   (mid, superseded))
        _append_evidence(superseded, "superseded", f"같은 제목으로 교정 → 새 지식 #{mid}")
    db.log_event(project_id, "memory", f"기록 [{category}] {title[:60]}")
    return {"memory_id": mid, "uri": f"scribe://{project_id}/memories/{category}/{mid}",
            "superseded": superseded}


def score_existing(project_id: int, memory_id: int, occurrences: int = 1) -> None:
    """서기가 '이미 있던 지식이 다시 쓰였다'고 보고한 횟수 — 반복 요청의 증거."""
    db.execute(
        """UPDATE memories SET occurrences=occurrences+?, last_seen_at=?
           WHERE id=? AND project_id=?""",
        (max(1, int(occurrences or 1)), db.now(), memory_id, project_id),
    )


# ══════════════════ 레거시: 서버가 매 턴을 증류하던 길 ══════════════════ #
def commit(
    project_id: int,
    question: str,
    answer: str,
    session_id: str = "",
    model: str = "",
    files: list[str] | None = None,
) -> dict:
    """[레거시] 세션 한 턴(질문→답)을 서버가 직접 지식으로 승격.

    새 컨셉에서는 쓰지 않는다 — auto_distill 이 켜진 프로젝트(서기에 의존하지 않는
    에이전트)에서만 이 경로가 동작한다. → {created, updated, superseded, memory_id}
    """
    question = redact(question.strip())
    answer = redact(answer.strip())
    if not question or not answer:
        return {"created": 0, "updated": 0, "superseded": 0, "memory_id": None}

    title = condense_question(question)
    norm = normalize_title(title)
    category = guess_category(question, answer)

    existing = db.rows(
        "SELECT * FROM memories WHERE project_id=? AND status!='superseded'",
        (project_id,),
    )
    match = next((m for m in existing if normalize_title(m["title"]) == norm), None)

    superseded = 0
    if match:
        if is_complaint(question) and match["status"] == "established":
            mid = _store_memory(
                project_id, category, title, answer, "session",
                corrects=match["id"], session_ref=session_id,
            )
            db.execute(
                "UPDATE memories SET status='superseded', superseded_by=? WHERE id=?",
                (mid, match["id"]),
            )
            superseded = match["id"]
            return {"created": 1, "updated": 0, "superseded": superseded, "memory_id": mid}

        now = db.now()
        summary = make_summary(title, answer)
        overview = make_overview(answer)
        kws = make_keywords(title, answer)
        db.execute(
            """UPDATE memories SET title=?, summary=?, overview=?, content=?, keywords=?,
               updated_at=?, session_ref=? WHERE id=?""",
            (title[:200], summary, overview, answer[:50000], db.jdumps(kws), now, session_id[:100], match["id"]),
        )
        llm.save_embeddings(match["id"], f"{title} {answer}")
        _attach_files(match["id"], files)
        _append_evidence(match["id"], "updated", "같은 주제의 새 작업으로 본문 갱신")
        return {"created": 0, "updated": match["id"], "superseded": 0, "memory_id": match["id"]}

    mid = _store_memory(project_id, category, title, answer, "session", session_ref=session_id)
    _attach_files(mid, files)
    db.log_event(project_id, "memory", f"지식 추가 [{category}] {title}")
    return {"created": 1, "updated": 0, "superseded": 0, "memory_id": mid}


def _attach_files(memory_id: int, files: list[str] | None) -> None:
    """지식에 '관련 파일' 꼬리표를 붙입니다 (L2 전문 끝)."""
    if files:
        footer = f"\n\n> 관련 파일: {', '.join(files[:8])}"
        db.execute("UPDATE memories SET content = content || ? WHERE id=?", (footer, memory_id))


def _append_evidence(memory_id: int, kind: str, note: str) -> None:
    row = db.one("SELECT evidence FROM memories WHERE id=?", (memory_id,))
    if not row:
        return
    ev = db.jloads(row["evidence"])
    ev.append({"at": db.now(), "kind": kind, "note": note[:120]})
    db.execute("UPDATE memories SET evidence=? WHERE id=?", (db.jdumps(ev[-20:]), memory_id))
