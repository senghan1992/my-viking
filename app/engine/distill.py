"""증류(distill) — 작업 기록(질문→답)을 도서관의 지식 한 권으로 승격합니다.

OpenViking 의 '세션이 장기 메모리가 된다' 아이디어의 구현:
- 세션의 질문·답이 끝나면 자동으로 memory 한 건이 됩니다 (사람 손 안 댐).
- 같은 제목(정규화)이 이미 있으면 본문을 갱신 → 쌓이지 않고 최신 유지.
- 기존 지식이 '확립' 상태인데 새로운 답이 반박적인 경우 → 옛것은 대체(superseded)
  처리되고 새것이 교정본이 됩니다.
"""
from __future__ import annotations

import re

from .. import db
from .redact import redact
from .tiers import make_keywords, make_overview, make_summary
from .tokens import keywords

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
    # 물음표·마침표 제거, '어떻게/하는지' 류 어미 정리
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
) -> int:
    summary = make_summary(title, content)
    overview = make_overview(content)
    kws = make_keywords(title, content)
    status = "established" if confirmed else "fresh"
    now = db.now()
    mid = db.execute(
        """INSERT INTO memories(project_id, category, title, summary, overview, content,
           status, trust, keywords, source, corrects, session_ref, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            project_id, category, title[:200], summary, overview, content[:50000],
            status, 0.7 if confirmed else 0.0, db.jdumps(kws), source,
            corrects, session_ref[:100], now, now,
        ),
    )
    return mid


def commit(
    project_id: int,
    question: str,
    answer: str,
    session_id: str = "",
    model: str = "",
    files: list[str] | None = None,
) -> dict:
    """세션 한 턴(질문→답)을 지식으로 승격. → {created, updated, superseded, memory_id}"""
    question = redact(question.strip())
    answer = redact(answer.strip())
    if not question or not answer:
        return {"created": 0, "updated": 0, "superseded": 0, "memory_id": None}

    title = condense_question(question)
    norm = normalize_title(title)
    category = guess_category(question, answer)

    # 같은 제목이 이미 있는가 (도서관 중복 방지)
    existing = db.rows(
        "SELECT * FROM memories WHERE project_id=? AND status!='superseded'",
        (project_id,),
    )
    match = next((m for m in existing if normalize_title(m["title"]) == norm), None)

    superseded = 0
    if match:
        # 반박적 새 답 + 옛것이 확립 상태 → 교정으로 취급
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

        # 같은 주제의 갱신 → 본문 교체, 상태 유지 (증거에 기록)
        now = db.now()
        summary = make_summary(title, answer)
        overview = make_overview(answer)
        kws = make_keywords(title, answer)
        db.execute(
            """UPDATE memories SET title=?, summary=?, overview=?, content=?, keywords=?,
               updated_at=?, session_ref=? WHERE id=?""",
            (title[:200], summary, overview, answer[:50000], db.jdumps(kws), now, session_id[:100], match["id"]),
        )
        _attach_files(match["id"], files)
        _append_evidence(match["id"], "updated", "같은 주제의 새 작업으로 본문 갱신")
        return {"created": 0, "updated": match["id"], "superseded": 0, "memory_id": match["id"]}

    mid = _store_memory(project_id, category, title, answer, "session", session_ref=session_id)
    _attach_files(mid, files)
    db.log_event(project_id, "memory", f"지식 추가 [{category}] {title}")
    return {"created": 1, "updated": 0, "superseded": 0, "memory_id": mid}


def remember(
    project_id: int,
    title: str,
    content: str,
    category: str = "knowledge",
    confirmed: bool = False,
) -> dict:
    """에이전트·사람이 명시적으로 남기는 지식. confirmed=True 면 확립으로.

    같은 제목(정규화)의 확립 지식이 이미 있으면, 이 기록을 교정으로 보고
    옛것을 대체합니다 (사람이 '같은 제목으로 다시 쓴다' = 정정).
    """
    if category not in CATEGORIES:
        category = "knowledge"
    norm = normalize_title(title)
    superseded = 0
    if confirmed:
        existing = db.rows(
            "SELECT * FROM memories WHERE project_id=? AND status='established'",
            (project_id,),
        )
        match = next((m for m in existing if normalize_title(m["title"]) == norm), None)
        if match:
            superseded = match["id"]
    mid = _store_memory(project_id, category, title.strip(), redact(content.strip()),
                        "manual", confirmed=confirmed,
                        corrects=superseded or None)
    if superseded:
        db.execute(
            "UPDATE memories SET status='superseded', superseded_by=? WHERE id=?",
            (mid, superseded),
        )
        _append_evidence(superseded, "superseded", f"사람이 같은 제목으로 교정 → 새 지식 #{mid}")
    db.log_event(project_id, "memory", f"기록 [{category}] {title.strip()[:60]}")
    return {"memory_id": mid, "uri": f"viking://{project_id}/memories/{category}/{mid}", "superseded": superseded}


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