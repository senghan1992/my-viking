"""실사용 시뮬레이션(서브에이전트 5명: 훅·셸 브리지·MCP·회의론자·팀 리드)에서
잡힌 결함의 회귀 테스트. 각 테스트의 docstring 이 누가 무엇을 봤는지 말한다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.connect import hook_settings
from jarvis.server import create_app
from jarvis.hooks import _orientation
from jarvis.learn import _is_chatter, _lead, clip_sentences
from jarvis.mcp_core import dispatch
from jarvis.models import slugify


@pytest.fixture()
def client(home):
    return TestClient(create_app(home=str(home)))


# --------------------------------------------------------------------------
# Dave — 데이터 파괴: 제목의 '.' 가 슬러그 충돌을 만들었다
# --------------------------------------------------------------------------
def test_dots_never_collapse_two_titles_into_one_file(jarvis):
    assert "." not in slugify("Store.all() 이 파일 없을 때")
    assert slugify("store.py 열어봐") != slugify("Store.all() 이 파일 없을 때")

    jarvis.init_project("app", template="coding")
    a = jarvis.commit("app", "Store.all() 이 파일 없을 때 뭐 돌려줘", "빈 리스트를 돌려줍니다. 파일이 없으면 [] 입니다.", agent="d")
    b = jarvis.commit("app", "store.py 열어봐", "notes/store.py 를 열었습니다. Store 클래스가 있습니다.", agent="d")
    assert a["session"] != b["session"]
    assert jarvis.store.read_node(a["session"]) is not None
    assert jarvis.store.read_node(b["session"]) is not None


# --------------------------------------------------------------------------
# Dave/Carol — 같은 제목 remember 는 L0 만 바꾸고 본문·이력은 오답을 상속했다
# --------------------------------------------------------------------------
def test_manual_re_remember_replaces_headline_body_and_verdicts(jarvis):
    jarvis.init_project("app", template="coding")
    uri = jarvis.remember("app", "commands", "테스트 실행", "python -m unittest 으로 테스트를 실행한다")
    tid = jarvis.prepare("app", "테스트 어떻게 돌려?", agent="d", session_id="s1", max_tier=0).trace_id
    jarvis.score(tid, value=0.0, uris=[str(uri)])
    jarvis.score(tid, value=0.0, uris=[str(uri)])
    node = jarvis.store.read_node(uri)
    assert node.extra["contradictions"] >= 2

    same = jarvis.remember("app", "commands", "테스트 실행", "pytest -q 로 테스트를 실행한다 (unittest 아님)")
    assert same == uri
    node = jarvis.store.read_node(uri)
    assert node.abstract.startswith("pytest -q")
    assert node.body.startswith("pytest -q")
    assert "unittest 으로" in node.body  # 이력으로만 남는다
    assert not node.extra.get("contradictions") and not node.extra.get("challenge_streak")
    assert jarvis.trust(node)["status"] == "established"
    assert node.confidence == pytest.approx(0.8)
    assert node.extra["evidence"][-1]["kind"] == "replaced"


# --------------------------------------------------------------------------
# Dave — remember 경로의 비밀값
# --------------------------------------------------------------------------
def test_remember_masks_credentials_too(jarvis):
    jarvis.init_project("app", template="coding")
    uri = jarvis.remember(
        "app", "pitfalls", "DB 접속",
        "운영 DB 는 postgres://dave:pw12345secret@db.internal/app 로 붙는다",
        detail='DB_PASSWORD="Hunter2-Hunter2" ; key sk-proj-abcdefghijklmnopqrstuvwxyz0123456789',
    )
    node = jarvis.store.read_node(uri)
    text = f"{node.abstract}\n{node.overview}\n{node.body}"
    for secret in ("pw12345secret", "Hunter2", "sk-proj-abc"):
        assert secret not in text
    brief = jarvis.brief("app")
    assert "pw12345secret" not in str(brief)


# --------------------------------------------------------------------------
# Dave — 소음: 인사·진행 문구가 메모리가 되고, 일회성 금지가 규약이 됐다
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "q,a",
    [
        ("고마워", "천만에요."),
        ("ok 다음", "네, 다음으로 넘어갑니다."),
        ("좋아 그렇게 해", "완료했습니다."),
        ("오늘은 여기까지", "수고하셨습니다."),
    ],
)
def test_chatter_is_not_distilled(jarvis, q, a):
    assert _is_chatter(q, a)
    jarvis.init_project("app", template="coding")
    res = jarvis.commit("app", q, a, agent="d")
    assert not res["distill"]["created"] and not res["distill"]["merged"]


def test_real_exchanges_are_still_distilled(jarvis):
    assert not _is_chatter("add 에 빈 문자열 막아줘", "notes/store.py 의 add() 에 빈 문자열 검사를 넣었습니다.\n```\nif not text.strip(): raise ValueError\n```")
    jarvis.init_project("app", template="coding")
    res = jarvis.commit("app", "add 에 빈 문자열 막아줘", "notes/store.py 의 add() 에 빈 문자열 검사를 넣었습니다.", agent="d")
    assert res["distill"]["created"]


def test_a_bare_prohibition_is_not_promoted_to_a_convention(jarvis):
    jarvis.init_project("app", template="coding")
    res = jarvis.commit("app", "아직 커밋하지 마, 리뷰 먼저 볼게", "알겠습니다. 커밋은 보류하고 변경 내용을 정리해 두었습니다.", agent="d")
    assert not any("/conventions/" in u for u in res["distill"]["created"])
    res2 = jarvis.commit("app", "앞으로 커밋 메시지는 항상 conventional commits 로 써", "알겠습니다. 규칙으로 기록합니다.", agent="d")
    assert any("/conventions/" in u for u in res2["distill"]["created"])


# --------------------------------------------------------------------------
# Alice — L0 요약이 문장 중간에서 잘려 경고의 결론이 사라졌다
# --------------------------------------------------------------------------
def test_lead_cuts_at_sentence_or_clause_boundaries():
    answer = (
        "make reset-db 로 초기화합니다. 다만 위험한 점이 세 가지 있어서 그대로 쓰기 전에 확인해야 합니다. "
        "SHOP_ENV 가 비어도 지우고, SHOP_DATA_DIR 을 무시하며, prod 문자열만 가드합니다."
    )
    lead = _lead(answer, limit=90)
    assert lead.endswith(("니다.", "…"))
    assert not lead.endswith("전에")
    assert clip_sentences("짧은 문장.", 50) == "짧은 문장."
    long = "이 문장은 아주 길어서 한계를 넘습니다, 그래서 절 경계에서 잘려야 하고 말줄임표가 붙어야 합니다 정말로요"
    cut = clip_sentences(long, 40)
    assert cut.endswith("…") and len(cut) <= 41


# --------------------------------------------------------------------------
# Alice/Bob/Carol — 프롬프트마다 메모리 전량 덤프, 같은 제목 6개 중복
# --------------------------------------------------------------------------
def test_pack_caps_items_and_collapses_same_title(jarvis):
    jarvis.init_project("app", template="coding")
    jarvis.config.learn.merge_threshold = 0.99
    for i in range(12):
        jarvis.remember("app", "conventions", f"규칙 {i}", f"규칙 {i} 은 {i}번째 규칙이다. 내용 {i}")
    for i in range(3):
        jarvis.commit("app", "테스트 어떻게 돌려?", f"make test 로 돌립니다 ({i}번째 기록).", agent=f"a{i}")
    p = jarvis.prepare("app", "README 의 오타를 수정해줘", agent="x", session_id="s1", max_tier=0, use_cache=False)
    mem_items = [i for i in p.packed.items if i.kind == "memory"]
    assert len(mem_items) <= jarvis.config.budget.max_items
    titles = [i.title for i in mem_items]
    assert len(titles) == len(set(titles)), titles


def test_same_question_recommitted_supersedes_the_older_case(jarvis):
    """Alice·Carol: 같은 질문을 두 사람이 commit 하면 cases 가 무한 누적됐다."""
    jarvis.init_project("app", template="coding")
    jarvis.commit("app", "테스트 어떻게 돌려?", "make test 로 돌립니다. 결과 2 passed 1 failed.", agent="alice")
    jarvis.commit("app", "테스트 어떻게 돌려?", "make test 로 돌립니다. 결과 3 passed.", agent="carol")
    live = [m for m in jarvis.memories("app", category="cases") if m["title"] == "테스트"]
    assert len(live) == 1
    assert "3 passed" in live[0]["abstract"]


# --------------------------------------------------------------------------
# Alice/Eve — 액터 식별이 호스트명 기반이라 남의 작업이 내 것으로 표기됐다
# --------------------------------------------------------------------------
def test_actor_is_named_by_the_key_not_the_host(client):
    def hdr(k):
        return {"authorization": f"Bearer {k}"}

    client.post("/projects", json={"project": "alpha"})
    admin = client.post("/keys", json={"name": "admin"}).json()["key"]
    alice = client.post("/keys", json={"name": "alice-laptop", "projects": ["alpha"]}, headers=hdr(admin)).json()["key"]
    p = client.post(
        "/prepare",
        json={"project": "alpha", "question": "테스트 어떻게 돌려?", "agent": "claude-code@samehost", "session_id": "s1", "max_tier": 0},
        headers=hdr(alice),
    ).json()
    client.post(
        "/commit",
        json={"project": "alpha", "question": "테스트 어떻게 돌려?", "answer": "pytest -q", "agent": "claude-code@samehost", "trace_id": p["trace_id"]},
        headers=hdr(alice),
    )
    brief = client.get("/projects/alpha/brief", headers=hdr(admin)).json()
    agents = {w["agent"] for w in brief["recent_work"]}
    assert "claude-code@alice-laptop" in agents
    assert "claude-code@samehost" not in agents


# --------------------------------------------------------------------------
# Alice — 훅 설치·주입 문구
# --------------------------------------------------------------------------
def test_hook_install_keeps_the_state_dir():
    cmd = hook_settings("http://h:1", "k", state_dir="/tmp/st")["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert cmd.endswith("hook stop --state-dir /tmp/st")


def test_orientation_skips_empty_sittings_and_does_not_contradict_itself():
    empty = _orientation("app", {})
    assert "아직 기록이 없습니다" in empty and "다시 조사하지" not in empty
    text = _orientation(
        "app",
        {
            "recent_work": [
                {"agent": "", "started": "2026-09-03", "work": [{"question": ""}], "files": []},
                {"agent": "bob", "started": "2026-09-03", "work": [{"question": "테스트 어떻게 돌려?"}], "files": []},
            ]
        },
    )
    assert "(?)" not in text and "(bob)" in text
    assert "jv remote remember" in text  # 훅만 설치한 사용자도 쓸 수 있는 수단


def test_context_note_tags_unsettled_items_on_their_heading(jarvis):
    """Dave: ⟨확인 필요⟩ 가 항목 자리에 없고 꼬리에 제목 5개만 나열됐다."""
    from jarvis.hooks import _context_note

    jarvis.init_project("app", template="coding")
    uri = jarvis.remember("app", "commands", "배포 규칙", "배포는 main 에서만 한다")
    jarvis.config.learn.contested_after = 2
    node = jarvis.store.read_node(uri)
    node.extra["challenge_streak"] = 2
    jarvis.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)
    prepared = jarvis.prepare("app", "배포 규칙 알려줘", agent="t", max_tier=0, use_cache=False)
    if str(uri) in {i.uri for i in prepared.packed.items}:
        assert "### 배포 규칙 ⟨확인 필요" in prepared.context
        note = _context_note("app", prepared.to_dict())
        assert "⟨…⟩ 표시가 붙은 1개 항목" in note


def test_brief_recently_learned_hides_disputed_and_carries_trust(jarvis):
    jarvis.init_project("app", template="coding")
    jarvis.config.learn.merge_threshold = 0.99  # 해싱 임베딩이 둘을 합치지 않게
    ok = jarvis.remember("app", "conventions", "포맷", "prettier 를 쓴다")
    bad = jarvis.remember("app", "conventions", "린트", "eslint 를 쓴다")
    assert ok != bad
    node = jarvis.store.read_node(bad)
    node.extra["challenge_streak"] = 5
    jarvis.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)
    learned = jarvis.brief("app")["recently_learned"]
    uris = {m["uri"] for m in learned}
    assert str(ok) in uris and str(bad) not in uris
    assert all("trust" in m for m in learned)


# --------------------------------------------------------------------------
# Carol — MCP 표면
# --------------------------------------------------------------------------
def test_mcp_rejects_blank_question_and_typo_projects(jarvis):
    jarvis.init_project("app", template="coding")
    with pytest.raises(ValueError):
        dispatch(jarvis, "jarvis_context", {"project": "app", "question": "   "})
    with pytest.raises(ValueError, match="등록되지 않은 프로젝트"):
        dispatch(jarvis, "jarvis_remember", {"project": "does-not-exist", "category": "nope", "title": "t", "statement": "s"})
    assert "does-not-exist" not in jarvis.store.projects()
    with pytest.raises(ValueError, match="project"):
        dispatch(jarvis, "jarvis_browse", {"op": "find", "query": "x"})


def test_mcp_commit_records_files(jarvis):
    jarvis.init_project("app", template="coding")
    p = dispatch(jarvis, "jarvis_context", {"project": "app", "question": "쿠폰 테스트 추가해줘", "session_id": "c1", "agent": "carol"})
    dispatch(
        jarvis,
        "jarvis_commit",
        {"project": "app", "question": "쿠폰 테스트 추가해줘", "answer": "tests/test_coupons.py 를 추가했습니다.",
         "trace_id": p["trace_id"], "agent": "carol", "files": ["tests/test_coupons.py"]},
    )
    work = jarvis.brief("app")["recent_work"]
    assert any("tests/test_coupons.py" in (w.get("files") or []) for w in work)
