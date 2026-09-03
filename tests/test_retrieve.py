from jarvis.config import BudgetConfig
from jarvis.models import KIND_MEMORY


def _big_resource(jarvis, project, name, topic, sections=10):
    text = "\n".join(
        f"## 섹션 {i}\n" + (f"{topic} 에 관한 상세 설명이 반복된다. " * 8)
        for i in range(sections)
    )
    return jarvis.add_resource(project, name, text, title=f"{topic} 문서")


def test_pack_stays_within_budget(coding):
    for i in range(4):
        _big_resource(coding, "app", f"doc{i}", f"주제{i}")
    budget = BudgetConfig(total=600, memories=300, prompts=100, sessions=200, resources=400)
    packed = coding.retriever.pack("주제1 에 대해 알려줘", "app", budget=budget)
    assert packed.tokens <= 600


def test_tiering_beats_loading_everything_in_full(coding):
    for i in range(5):
        _big_resource(coding, "app", f"doc{i}", f"주제{i}")
    packed = coding.retriever.pack("주제2 상세 설명", "app")
    # With the relevance gate only 주제2 is selected, and a single relevant
    # document fits at full detail — so the saving is against dumping every
    # candidate, not against the (identical) full-detail baseline.
    assert [i.title for i in packed.items] == ["주제2 문서"]
    assert packed.baseline_tokens >= packed.tokens
    assert packed.tokens <= coding.config.budget.total
    # Five documents on disk, one considered and sent: the saving is the four not sent.
    assert packed.considered == 1


def test_high_scoring_item_gets_a_deeper_tier(coding):
    _big_resource(coding, "app", "auth", "인증 토큰 저장")
    _big_resource(coding, "app", "billing", "결제 정산")
    packed = coding.retriever.pack("인증 토큰 저장 방식", "app")
    tiers = {i.title: i.tier for i in packed.items}
    assert tiers, "아무것도 선택되지 않았습니다"
    top = max(packed.items, key=lambda i: i.score)
    assert "인증" in top.title
    assert top.tier >= 1


def test_tier_zero_budget_only_loads_abstracts(coding):
    _big_resource(coding, "app", "auth", "인증 토큰")
    packed = coding.retriever.pack("인증", "app", max_tier=0)
    assert packed.items
    assert all(i.tier == 0 for i in packed.items)


def test_trace_records_the_path_taken(coding):
    coding.remember("app", "commands", "빌드", "make build 로 빌드한다")
    packed = coding.retriever.pack("빌드 명령", "app")
    steps = [t["step"] for t in packed.trace]
    assert "dir" in steps and "rank" in steps and "pack" in steps
    entered = [t["uri"] for t in packed.trace if t["step"] == "dir"]
    assert any("memories" in u for u in entered)


def test_kind_filter_excludes_other_kinds(coding):
    coding.remember("app", "commands", "빌드", "make build")
    coding.commit("app", "빌드 어떻게?", "make build", distill=False)
    packed = coding.retriever.pack("빌드", "app", kinds=[KIND_MEMORY])
    assert all(i.kind == KIND_MEMORY for i in packed.items)


def test_global_preferences_reach_every_project(jarvis):
    jarvis.init_project("app", template="coding")
    jarvis.remember("global", "preferences", "간결함", "답변은 항상 간결하게 쓴다")
    packed = jarvis.retriever.pack("답변 스타일 어떻게 해야 해?", "app")
    uris = [i.uri for i in packed.items]
    assert any("global" in u for u in uris)


def test_include_global_false_isolates_project(jarvis):
    jarvis.init_project("app", template="coding")
    jarvis.remember("global", "preferences", "간결함", "답변은 항상 간결하게 쓴다")
    packed = jarvis.retriever.pack(
        "답변 스타일", "app", include_global=False
    )
    assert not any("global" in i.uri for i in packed.items)


def test_search_ranks_relevant_above_irrelevant(coding):
    coding.remember("app", "architecture", "인증 모듈", "인증은 JWT 와 리프레시 토큰을 쓴다")
    coding.remember("app", "architecture", "결제 모듈", "결제는 외부 PG 를 호출한다")
    hits, _ = coding.retriever.search("JWT 리프레시 토큰", "app")
    assert hits
    assert "인증" in hits[0].title


def test_empty_store_returns_empty_pack(coding):
    packed = coding.retriever.pack("아무것도 없는 프로젝트", "app")
    assert packed.items == []
    assert packed.text == ""
    assert packed.saved_tokens == 0


def test_profile_budget_override_is_applied(coding):
    for i in range(4):
        _big_resource(coding, "app", f"doc{i}", f"주제{i}")
    profile = coding.profile("app")
    profile.budget = {"total": 300}
    coding.set_profile("app", profile)
    packed = coding.retriever.pack("주제0", "app")
    assert packed.tokens <= 300


def test_archived_nodes_are_not_retrieved(coding):
    uri = coding.remember("app", "commands", "폐기된 명령", "이제 안 쓰는 명령이다")
    coding.store.archive_node(uri, reason="테스트")
    coding.store.reindex("app")
    packed = coding.retriever.pack("폐기된 명령", "app")
    assert not any("_archive" in i.uri for i in packed.items)


import pytest


@pytest.mark.parametrize("total", [50, 120, 300, 800, 2000])
def test_budget_is_never_exceeded_at_any_size(coding, total):
    """The budget is a hard promise; header overhead must not leak past it."""
    for i in range(6):
        _big_resource(coding, "app", f"doc{i}", f"주제{i}")
    for i in range(4):
        coding.remember("app", "commands", f"명령 {i}", f"명령 {i} 를 실행한다" * 3)
    budget = BudgetConfig(
        total=total, memories=total, prompts=total, sessions=total, resources=total
    )
    packed = coding.retriever.pack("주제3 명령 실행 방법", "app", budget=budget)
    assert packed.tokens <= total
    from jarvis.tokens import estimate_tokens

    assert estimate_tokens(packed.text) <= total


def test_reported_items_match_rendered_text(coding):
    for i in range(4):
        _big_resource(coding, "app", f"doc{i}", f"주제{i}")
    packed = coding.retriever.pack(
        "주제1", "app", budget=BudgetConfig(total=250, resources=250)
    )
    # Every reported item must actually appear; a trimmed item must not linger.
    for item in packed.items:
        assert item.title in packed.text
    assert len(packed.items) == packed.text.count("### ")


def test_default_budget_can_reach_l1_on_a_large_document(coding):
    """Per-kind caps must be able to hold at least one L1, or long documents
    are permanently stuck at their abstract."""
    _big_resource(coding, "app", "spec", "웹훅 서명 검증", sections=14)
    packed = coding.retriever.pack("웹훅 서명 검증 규격", "app")
    spec = [i for i in packed.items if "spec" in i.uri]
    assert spec and spec[0].tier >= 1


# ----- Korean particles must not defeat retrieval --------------------------
@pytest.mark.parametrize(
    "query,expect_title",
    [
        # Each query attaches a particle to the subject noun ("배포는", not "배포").
        ("배포는 어떻게 해?", "배포 명령"),
        ("테스트는 어떻게 돌려?", "테스트 실행"),
        ("웹훅 서명 검증은 어디서 하지?", "웹훅 서명 검증"),
        ("마이그레이션 롤백이 필요해", "마이그레이션 롤백"),
    ],
)
def test_query_with_a_particle_still_finds_its_subject(coding, query, expect_title):
    """Korean attaches particles to nouns, so word- and trigram-level features
    never matched the bare form and retrieval scored on filler words instead."""
    coding.remember("app", "commands", "배포 명령", "make deploy 로 배포한다")
    coding.remember("app", "commands", "테스트 실행", "pytest -q 로 돌린다")
    coding.remember("app", "architecture", "웹훅 서명 검증", "webhooks/verify.py 에서 HMAC 으로 검증한다")
    coding.remember("app", "commands", "마이그레이션 롤백", "alembic downgrade -1 로 되돌린다")

    hits, _trace = coding.retriever.search(query, "app")
    assert hits, query
    assert hits[0].title == expect_title, [h.title for h in hits[:3]]


def test_unrelated_memories_do_not_tie_with_the_right_one(coding):
    """They used to score identically, because only the shared filler matched."""
    coding.remember("app", "commands", "배포 명령", "make deploy 로 배포한다")
    coding.remember("app", "commands", "테스트 실행", "pytest -q 로 돌린다")
    hits, _ = coding.retriever.search("배포는 어떻게 해?", "app")
    by_title = {h.title: h.score for h in hits}
    assert by_title["배포 명령"] > by_title["테스트 실행"] * 1.2, by_title


# --------------------------------------------------------------------------
# "먼저 찾아보고, 없으면 그냥 일반 에이전트처럼" — the absolute relevance gate
# --------------------------------------------------------------------------
def _small_store(coding):
    coding.remember("app", "commands", "배포 명령", "make deploy 로 배포한다")
    coding.remember("app", "commands", "테스트 실행", "pytest -q 로 돌린다")
    coding.remember("app", "pitfalls", "PG 재시도 금지", "승인 응답이 0000 이 아니면 재시도하지 않는다")
    coding.commit("app", "README 오타 좀 고쳐줘", "README.md 의 오타 3곳을 고쳤습니다.", agent="a")


def test_unrelated_prompt_gets_an_empty_pack_even_in_a_small_store(coding):
    """Below max_items every memory used to ride on every prompt: "hello" got
    the payment rule, the test command and the README fix. With nothing that
    matches, the agent must see nothing and simply work as usual."""
    _small_store(coding)
    for q in ("CSS 버튼 색상을 파란색으로 바꿔줘", "이 함수 리팩터링해줘", "hi", "도커 이미지 빌드 스크립트 만들어줘"):
        p = coding.prepare("app", q, agent="t", session_id="s", max_tier=0, use_cache=False)
        assert [i.title for i in p.packed.items if i.kind == KIND_MEMORY] == [], q
        assert p.context == "", q
        assert p.trace_id  # the turn is still traced and committed later


def test_related_prompt_still_finds_the_one_right_memory(coding):
    _small_store(coding)
    cases = {
        "배포는 어떻게 해?": "배포 명령",  # particle on the subject
        "서버에서 배포할 때 주의점": "배포 명령",  # verb ending
        "테스트는 어떻게 돌려?": "테스트 실행",
        "결제 재시도해야 해?": "PG 재시도 금지",  # conjugated verb vs "재시도하지"
        "deploy 어떻게?": "배포 명령",
    }
    for q, want in cases.items():
        p = coding.prepare("app", q, agent="t", session_id="s", max_tier=0, use_cache=False)
        titles = [i.title for i in p.packed.items if i.kind == KIND_MEMORY]
        assert titles == [want], (q, titles)


def test_global_preferences_ride_along_even_when_unrelated(coding):
    coding.remember_about_me("답변은 한글로 한다")
    _small_store(coding)
    p = coding.prepare("app", "CSS 버튼 색상 바꿔줘", agent="t", session_id="s", max_tier=0, use_cache=False)
    assert "한글로" in p.context
    assert [i.title for i in p.packed.items if not i.uri.startswith("jarvis://global/")] == []


def test_relevance_gate_can_be_disabled(coding):
    _small_store(coding)
    coding.config.budget.min_relevance = 0
    p = coding.prepare("app", "CSS 버튼 색상 바꿔줘", agent="t", session_id="s", max_tier=0, use_cache=False)
    assert len([i for i in p.packed.items if i.kind == KIND_MEMORY]) >= 3


def test_fts_query_expands_korean_inflections_and_drops_filler():
    from jarvis.db import _fts_query

    q = _fts_query("배포는 어떻게 해?")
    assert '"배포"*' in q and "어떻게" not in q
    q = _fts_query("결제 재시도해야 해?")
    assert '"재시도"*' in q and '"재시"*' not in q
    assert _fts_query("어떻게 해줘") == '""'
    # No blind shortening: "로그인" must not become "로그*" (matched a log-location note).
    q = _fts_query("로그인 페이지 만들어줘")
    assert '"로그인"*' in q and '"로그"*' not in q
    # Short Latin terms match exactly, never as a prefix ("PR" is not "production").
    assert '"PR"' in _fts_query("PR 올려줘") and '"PR"*' not in _fts_query("PR 올려줘")
    # One clause per term: the exact clause would double-count the prefix hit.
    assert _fts_query("배포는 어떻게 해?").count("배포") == 1
