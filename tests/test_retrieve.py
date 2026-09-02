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
    assert packed.baseline_tokens > packed.tokens
    assert packed.saved_ratio > 0.2
    # The naive "paste everything relevant" number must dominate the baseline.
    assert packed.dump_tokens >= packed.baseline_tokens


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
