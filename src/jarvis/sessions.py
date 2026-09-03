"""Session recording and the answer cache.

Every exchange you commit becomes a session node — the raw material the
distiller later turns into memory, and the corpus the cache searches.

The cache has two levels:

* **exact** — normalised-question hash. Free, instant, zero risk.
* **near** — cosine similarity over question vectors above a threshold. This is
  where real spend disappears: a repeated question costs 0 input tokens instead
  of a full prompt.

A near hit is always reported with its similarity and the original question so
you can see what was reused rather than trusting it blindly.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .embed import batch_cosine, pack_vector
from .models import KIND_SESSION, Node, Uri, now_iso, slugify
from .store import Store
from .tokens import estimate_tokens, truncate_to_tokens

_WS = re.compile(r"\s+")


def normalize_question(q: str) -> str:
    """Collapse whitespace/case/punctuation so trivial edits still hit the cache."""
    s = _WS.sub(" ", (q or "").strip().lower())
    return re.sub(r"[?!.,·]+$", "", s)


def question_hash(q: str) -> str:
    return hashlib.sha256(normalize_question(q).encode("utf-8")).hexdigest()[:32]


# The answer cache exists for *knowledge* questions ("결제 실패 시 재시도해야
# 하나?") whose answer is the same tomorrow. Three kinds of prompt are not that,
# and replaying their previous answer is wrong, not cheap:
#   * work orders — "리팩터링해줘", "로그인 API 만들어줘": the work must be redone;
#   * prompts pointing at something outside the text — "이 함수", "이거", "여기":
#     the same words name a different target every time;
#   * chatter — "hello", "고마워".
# In a live run "이 함수 리팩터링해줘" served "함수를 3개로 분리했습니다. 재사용하세요".
# Interrogatives only. Nouns like "방법"/"규칙" and a trailing "?" are not
# enough: "로그인 방법 바꿔줘" and "테스트 좀 돌려줄래?" are orders.
_INTERROGATIVE_KO = (
    "어떻게", "어떡", "무엇", "뭐", "뭘", "왜", "어디", "언제", "어느", "어떤", "몇", "얼마",
    "될까", "할까", "일까", "되나", "하나요", "인가", "인지", "맞아", "맞나", "인데", "야?",
)
# English interrogatives count at the start of the sentence: "when" inside
# "Add a retry when payment fails" is a conjunction, not a question.
_INTERROGATIVE_EN = re.compile(
    r"^(what|why|how|where|when|which|who|is|are|does|do|did|should|can i|can we|"
    r"could i|could we|would it|will it)\b"
)
# Imperative forms only: a bare topic ("테스트 실행", "배포 파이프라인 설정") is a
# lookup and stays cacheable; "실행해줘" is an order.
_WORK_MARKERS = (
    "해줘", "해 줘", "해주세요", "해 주세요", "주세요", "해줄래", "줄래", "부탁", "하자", "해봐",
    "해 봐", "바꿔", "고쳐", "수정해", "만들어", "추가해", "삭제해", "지워", "구현해", "작성해",
    "리팩터링해", "리팩토링해", "정리해", "적용해", "옮겨", "생성해", "실행해", "돌려줘", "돌려봐",
    "돌려줄", "배포해", "커밋해", "푸시해", "머지해", "올려", "내려", "붙여", "빼줘", "봐줘",
    "확인해", "검토해", "테스트해", "출력해", "그려",
)
_WORK_EN = re.compile(
    r"^(please\s+|(could|can|would|will)\s+you\s+(please\s+)?|i\s+need\s+you\s+to\s+|let'?s\s+)?"
    r"(fix|add|create|implement|refactor|change|update|remove|delete|write|run|"
    r"make|build|rename|move|deploy|commit|push|merge|review|check|convert|rewrite|clean|"
    r"set\s+up|install|generate|migrate|optimi[sz]e|debug|test)\b"
)
# Pointers at something outside the text. Only forms that *cannot* name their
# target: a demonstrative with a code noun, or the bare "이거/그거". English
# "it"/"that"/"here" are too common in ordinary questions ("Is it safe to
# retry?", "Where do logs live here?") to count.
_DEICTIC = re.compile(
    # "이 파일" after a Hangul word or at the start is a demonstrative; after a
    # Latin identifier ("Store.all 이 파일") it is the subject particle.
    r"(^|[가-힣,.!?]\s+)(이|그|저)\s?(함수|파일|코드|부분|버그|클래스|메서드|메소드|줄|라인|블록|모듈|컴포넌트|에러|오류|"
    r"테스트|쿼리|화면|페이지|변수|로직|거|걸|건|게)(\s|$|[을를이가은는도,.?!])"
    r"|(^|\s)(이거|그거|저거|방금\s?(그|이)|아까\s?(그|이)|this\s+(function|file|code|part|bug|class|method|line|block|module|component|error|test|query|page|variable|one)|that\s+(function|file|code|part|bug|class|method|line|block|module|component|error|test|query|page|variable|one))(\s|$|[,.?!])",
    re.IGNORECASE,
)
_GREETING = frozenset(
    "hello hi hey 안녕 하이 헬로 고마워 고맙 감사 ok okay 오케이 응 네 넵 알겠 수고 thanks thank bye".split()
)
_WORD = re.compile(r"[0-9A-Za-z]+|[가-힣]+")


def _is_greeting_word(w: str) -> bool:
    # Prefix only for Hangul words of 2+ syllables ("고마워요", "안녕하세요");
    # "응"/"네" as a prefix made "응답"/"네트워크" chatter.
    for g in _GREETING:
        if w == g:
            return True
        if len(g) >= 2 and g[0] >= "가" and w.startswith(g):
            return True
    return False


def is_work_order(q: str) -> bool:
    """An imperative request to change something (as opposed to a question)."""
    low = " ".join((q or "").split()).lower()
    is_work = any(m in low for m in _WORK_MARKERS) or bool(_WORK_EN.match(low))
    if not is_work:
        return False
    return not (any(m in low for m in _INTERROGATIVE_KO) or bool(_INTERROGATIVE_EN.match(low)))


def reusable_question(q: str) -> bool:
    """Is a prior answer to ``q`` worth serving again for the same words?

    Yes for knowledge questions; no for work orders (must be redone), for
    prompts pointing at an unnamed target, and for chatter. A work order with
    an interrogative in it ("Store.all 이 파일 없을 때 뭐 돌려줘") is a question.
    """
    low = " ".join((q or "").split()).lower()
    if not low:
        return False
    words = _WORD.findall(low)
    if words and len(words) <= 3 and all(_is_greeting_word(w) for w in words):
        return False
    if _DEICTIC.search(low):
        return False
    return not is_work_order(low)


@dataclass
class CacheHit:
    kind: str  # "exact" | "near"
    similarity: float
    question: str
    answer: str
    created: str
    tokens_saved: int
    session_uri: str = ""
    cache_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "similarity": round(self.similarity, 4),
            "question": self.question,
            "answer": self.answer,
            "created": self.created,
            "tokens_saved": self.tokens_saved,
            "session_uri": self.session_uri,
        }


class SessionLog:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db
        self.embedder = store.embedder

    # ------------------------------------------------------------------
    def record(
        self,
        project: str,
        question: str,
        answer: str,
        model: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        prompt_uri: str = "",
        tags: list[str] | None = None,
        outcome: str = "",
        cache: bool = True,
        files: list[str] | None = None,
    ) -> Node:
        """Store one exchange and (optionally) make it cacheable."""
        files = [f for f in (files or []) if f]
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stub = slugify(question[:48], "session")
        short = question_hash(question)[:8]
        uri = Uri(project, ("sessions", day, f"{stub}-{short}"))

        transcript = f"## Q\n{question.strip()}\n\n## A\n{answer.strip()}"
        if files:
            transcript += "\n\n## 변경한 파일\n" + "\n".join(f"- {f}" for f in files)
        node = Node(
            uri=uri,
            kind=KIND_SESSION,
            title=truncate_to_tokens(question.strip().splitlines()[0], 24) if question.strip() else "session",
            category="session",
            abstract="",
            overview="",
            body=transcript,
            tags=tags or [],
            confidence=0.4,
            sources=[prompt_uri] if prompt_uri else [],
            extra={
                "question": question,
                "answer_tokens": estimate_tokens(answer),
                "model": model,
                "tokens_in": int(tokens_in),
                "tokens_out": int(tokens_out),
                "prompt_uri": prompt_uri,
                "outcome": outcome,
                "files": files,
                "distilled": False,
            },
        )
        # A session's abstract is the question: that is exactly what a future
        # lookup will be matching against.
        node.abstract = truncate_to_tokens(question.strip(), 100)
        node.overview = truncate_to_tokens(
            f"Q: {question.strip()}\nA: {answer.strip()}", 2000
        )
        node = self.store.write_node(node, regenerate_tiers=False)

        self.db.log_usage(
            now_iso(),
            project,
            "session_record",
            uri=str(uri),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            detail={"model": model, "outcome": outcome},
        )
        if cache and answer.strip() and reusable_question(question):
            self.cache_put(
                project,
                question,
                answer,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                session_uri=str(uri),
            )
        return node

    # ------------------------------------------------------------------
    def cache_put(
        self,
        project: str,
        question: str,
        answer: str,
        model: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        session_uri: str = "",
    ) -> int:
        vec = self.embedder.embed(question)
        qh = question_hash(question)
        existing = self.db.one(
            "SELECT id FROM cache WHERE scope=? AND qhash=?", (project, qh)
        )
        if existing:
            self.db.execute(
                "UPDATE cache SET answer=?, model=?, tokens_in=?, tokens_out=?,"
                " session_uri=?, vector=? WHERE id=?",
                (
                    answer,
                    model,
                    int(tokens_in),
                    int(tokens_out),
                    session_uri,
                    pack_vector(vec),
                    existing["id"],
                ),
            )
            self.db.commit()
            return int(existing["id"])
        cur = self.db.execute(
            "INSERT INTO cache (scope, qhash, question, answer, model, tokens_in,"
            " tokens_out, created, vector) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                project,
                qh,
                question,
                answer,
                model,
                int(tokens_in),
                int(tokens_out),
                now_iso(),
                pack_vector(vec),
            ),
        )
        self.db.commit()
        return int(cur.lastrowid or 0)

    def cache_forget(self, project: str, question: str) -> int:
        """Drop the cached answer for one question — it was judged wrong."""
        cur = self.db.execute(
            "DELETE FROM cache WHERE scope=? AND qhash=?",
            (project, question_hash(question)),
        )
        self.db.commit()
        return cur.rowcount or 0

    def cache_lookup(
        self,
        project: str,
        question: str,
        threshold: float | None = None,
        include_global: bool = False,
    ) -> CacheHit | None:
        """Return a reusable prior answer, or None."""
        # Rows written before this gate existed are still in the table.
        if not reusable_question(question):
            return None
        thr = (
            threshold
            if threshold is not None
            else self.store.config.budget.cache_hit_threshold
        )
        scopes = [project] + (["global"] if include_global and project != "global" else [])
        marks = ", ".join("?" for _ in scopes)

        qh = question_hash(question)
        row = self.db.one(
            f"SELECT * FROM cache WHERE scope IN ({marks}) AND qhash=?",
            [*scopes, qh],
        )
        if row is not None:
            return self._hit(row, "exact", 1.0)

        qvec = self.embedder.embed(question)
        rows = self.db.query(f"SELECT * FROM cache WHERE scope IN ({marks})", scopes)
        if not rows:
            return None
        sims = self.embedder.calibrate_many(batch_cosine(qvec, [r["vector"] for r in rows]))
        best_i = max(range(len(rows)), key=lambda i: sims[i])
        if sims[best_i] >= thr:
            return self._hit(rows[best_i], "near", sims[best_i])
        return None

    def _hit(self, row: Any, kind: str, sim: float) -> CacheHit:
        saved = int(row["tokens_in"] or 0) + int(row["tokens_out"] or 0)
        if saved == 0:
            saved = estimate_tokens(row["question"]) + estimate_tokens(row["answer"])
        self.db.execute(
            "UPDATE cache SET hits = hits + 1, last_used = ? WHERE id = ?",
            (now_iso(), row["id"]),
        )
        self.db.log_usage(
            now_iso(),
            row["scope"],
            "cache_hit",
            uri=row["session_uri"] or "",
            tokens_saved=saved,
            baseline=saved,
            detail={"kind": kind, "similarity": round(sim, 4)},
        )
        self.db.commit()
        return CacheHit(
            kind=kind,
            similarity=sim,
            question=row["question"],
            answer=row["answer"],
            created=row["created"] or "",
            tokens_saved=saved,
            session_uri=row["session_uri"] or "",
            cache_id=int(row["id"]),
        )

    def cache_prune(self, keep_per_project: int) -> int:
        """Cap the answer cache per project, most recently useful first.

        The near-miss lookup scans every cached row of its scope, so an
        unbounded cache is a tax on *every* prompt, not just a storage cost.
        """
        if keep_per_project <= 0:
            return 0
        deleted = 0
        scopes = [r["scope"] for r in self.db.query("SELECT DISTINCT scope FROM cache")]
        for scope in scopes:
            cur = self.db.execute(
                "DELETE FROM cache WHERE scope=? AND id NOT IN ("
                " SELECT id FROM cache WHERE scope=?"
                " ORDER BY COALESCE(last_used, created) DESC LIMIT ?)",
                (scope, scope, keep_per_project),
            )
            deleted += cur.rowcount or 0
        if deleted:
            self.db.commit()
        return deleted

    def cache_clear(self, project: str) -> int:
        cur = self.db.execute("DELETE FROM cache WHERE scope=?", (project,))
        self.db.commit()
        return cur.rowcount or 0

    def cache_list(self, project: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id, question, hits, tokens_in, tokens_out, created, last_used"
            " FROM cache WHERE scope=? ORDER BY hits DESC, created DESC LIMIT ?",
            (project, limit),
        )
        return [
            {
                "id": r["id"],
                "question": r["question"][:160],
                "hits": r["hits"],
                "tokens": (r["tokens_in"] or 0) + (r["tokens_out"] or 0),
                "created": r["created"],
                "last_used": r["last_used"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    def undistilled(self, project: str, limit: int = 50) -> list[Node]:
        """Sessions the learner has not folded into memory yet."""
        rows = self.db.query(
            "SELECT uri FROM nodes WHERE scope=? AND kind=? ORDER BY created ASC",
            (project, KIND_SESSION),
        )
        out: list[Node] = []
        for r in rows:
            node = self.store.read_node(Uri.parse(r["uri"]))
            if node is None or node.extra.get("distilled"):
                continue
            out.append(node)
            if len(out) >= limit:
                break
        return out

    def mark_distilled(self, node: Node, memories: list[str]) -> None:
        node.extra["distilled"] = True
        node.extra["distilled_at"] = now_iso()
        node.extra["memories"] = memories
        self.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)

    def recent(self, project: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT uri, title, abstract, created, tokens_l2 FROM nodes"
            " WHERE scope=? AND kind=? ORDER BY created DESC LIMIT ?",
            (project, KIND_SESSION, limit),
        )
        return [
            {
                "uri": r["uri"],
                "title": r["title"],
                "question": r["abstract"],
                "created": r["created"],
                "tokens": r["tokens_l2"],
            }
            for r in rows
        ]
