from datetime import datetime, timedelta, timezone

from jarvis.learn import MemoryCandidate, _polarity_differs
from jarvis.models import Uri


def test_distill_creates_memory_from_session(coding):
    coding.commit(
        "app",
        "테스트는 어떻게 돌려?",
        "루트에서 `pytest -q` 를 실행하세요.",
        distill=False,
    )
    rep = coding.distill("app")
    assert rep.sessions == 1
    assert rep.created, "세션이 아무 메모리도 남기지 못했습니다"
    uri = Uri.parse(rep.created[0])
    assert uri.parts[0] == "memories"


def test_distill_files_durable_instruction_as_convention(coding):
    coding.commit(
        "app",
        "앞으로 모든 주석은 항상 한글로 작성해줘",
        "알겠습니다. 한글로 작성합니다.",
        distill=False,
    )
    rep = coding.distill("app")
    cats = {Uri.parse(u).parts[1] for u in rep.created}
    assert "conventions" in cats


def test_distill_uses_fallback_when_no_rule_matches(coding):
    coding.commit("app", "그냥 물어봄", "그냥 답함", distill=False)
    rep = coding.distill("app")
    cats = {Uri.parse(u).parts[1] for u in rep.created}
    assert cats == {"cases"}


def test_same_title_merges_into_one_carrier(coding):
    """Stable filenames are the point: repeated lessons accumulate in place."""
    profile = coding.profile("app")
    for i in range(3):
        cand = MemoryCandidate(
            category="commands",
            title="테스트 실행",
            statement=f"관찰 {i}",
            source=f"session-{i}",
        )
        coding.learner.absorb("app", cand, profile)
    mems = coding.memories("app", "commands")
    assert len(mems) == 1
    node = coding.store.read_node(mems[0]["uri"])
    assert "관찰 0" in node.body and "관찰 2" in node.body
    assert len(node.sources) == 3


def test_merge_raises_confidence(coding):
    profile = coding.profile("app")
    cand = MemoryCandidate(category="commands", title="빌드", statement="첫 관찰", confidence=0.4)
    coding.learner.absorb("app", cand, profile)
    before = coding.memories("app", "commands")[0]["confidence"]
    coding.learner.absorb(
        "app", MemoryCandidate(category="commands", title="빌드", statement="두 번째"), profile
    )
    assert coding.memories("app", "commands")[0]["confidence"] > before


def test_non_cumulative_category_keeps_separate_files(coding):
    profile = coding.profile("app")
    assert profile.category("cases").cumulative is False
    for i in range(2):
        coding.learner.absorb(
            "app",
            MemoryCandidate(category="cases", title="같은 제목", statement=f"사례 {i}"),
            profile,
        )
    assert len(coding.memories("app", "cases")) == 2


def test_contradiction_is_flagged_not_silently_overwritten(coding):
    profile = coding.profile("app")
    coding.learner.absorb(
        "app",
        MemoryCandidate(category="conventions", title="주석 언어", statement="주석은 한글로 쓴다"),
        profile,
    )
    uri, action, conflict = coding.learner.absorb(
        "app",
        MemoryCandidate(
            category="conventions", title="주석 언어", statement="주석은 한글로 쓰지 않는다"
        ),
        profile,
    )
    assert action == "merged"
    assert conflict
    node = coding.store.read_node(uri)
    assert "conflict" in node.extra


def test_polarity_helper():
    assert _polarity_differs("한글로 쓴다", "한글로 쓰지 않는다")
    assert not _polarity_differs("한글로 쓴다", "한글로 작성한다")


def test_unused_stale_memory_is_archived(coding):
    uri = coding.remember("app", "cases", "오래된 사례", "쓸모없어진 관찰", confidence=0.26)
    node = coding.store.read_node(uri)
    old = (datetime.now(timezone.utc) - timedelta(days=400)).replace(microsecond=0).isoformat()
    node.updated = old
    node.last_used = ""
    coding.store.path_for(uri).write_text(node.to_markdown(), encoding="utf-8")
    coding.store.reindex("app")
    coding.store.db.execute(
        "UPDATE nodes SET updated=?, last_used='' WHERE uri=?", (old, str(uri))
    )
    coding.store.db.commit()

    rep = coding.distill("app")
    assert str(uri) in rep.archived
    assert coding.store.read_node(uri) is None


def test_recently_used_memory_survives_decay(coding):
    uri = coding.remember("app", "commands", "살아있는 메모리", "자주 쓰는 명령")
    coding.distill("app")
    assert coding.store.read_node(uri) is not None


def test_keep_cap_archives_weakest(coding):
    profile = coding.profile("app")
    profile.category("commands").keep = 3
    coding.set_profile("app", profile)
    for i in range(6):
        coding.remember("app", "commands", f"명령 {i}", f"내용 {i}", confidence=0.3 + i * 0.1)
    coding.learner.enforce_caps("app", coding.profile("app"))
    remaining = coding.memories("app", "commands")
    assert len(remaining) == 3
    # The strongest survive.
    assert {m["title"] for m in remaining} == {"명령 5", "명령 4", "명령 3"}


def test_negative_feedback_lowers_confidence_and_can_archive(coding):
    uri = coding.remember("app", "commands", "틀린 명령", "make deploy", confidence=0.5)
    coding.feedback("app", str(uri), helpful=False, note="이 명령은 없음")
    assert coding.store.read_node(uri) is None  # 0.5 - 0.35 = 0.15 < archive_below


def test_positive_feedback_raises_confidence(coding):
    uri = coding.remember("app", "commands", "좋은 명령", "make build", confidence=0.5)
    node = coding.feedback("app", str(uri), helpful=True, note="정확함")
    assert node.confidence > 0.5
    assert "정확함" in coding.store.read_node(uri).tier(2)


def test_reinforce_marks_usage(coding):
    uri = coding.remember("app", "commands", "빌드", "make build")
    coding.learner.reinforce("app", [str(uri)])
    assert coding.store.read_node(uri).hits == 1


def test_remember_rejects_unknown_category(coding):
    import pytest

    with pytest.raises(ValueError, match="카테고리"):
        coding.remember("app", "없는카테고리", "제목", "내용")


def test_profile_change_redirects_learning(jarvis):
    """Swap the schema and the same session lands in different categories."""
    jarvis.init_project("r", template="research")
    jarvis.commit("r", "이 논문의 결론은?", "출처에 따르면 A 이다", distill=False)
    rep = jarvis.distill("r")
    cats = {Uri.parse(u).parts[1] for u in rep.created}
    assert cats <= set(jarvis.profile("r").category_names())
    assert "commands" not in cats  # research 프로파일에는 없는 카테고리


# ----- contradiction detection --------------------------------------------
import pytest as _pytest  # noqa: E402

from jarvis.learn import _clash_kind, _term_swapped  # noqa: E402


@_pytest.mark.parametrize(
    "a,b,expected",
    [
        # A swapped term is the common shape of a real contradiction, and
        # negation checks cannot see it.
        ("주석은 한글로 쓴다", "주석은 영어로 쓴다", "substitution"),
        ("앞으로 커밋 메시지는 항상 한글로 써줘", "앞으로 커밋 메시지는 항상 영어로 써줘", "substitution"),
        ("포트는 8080 을 쓴다", "포트는 9090 을 쓴다", "substitution"),
        ("comments are written in Korean", "comments are written in English", "substitution"),
        # Negation is the other shape.
        ("주석은 한글로 쓴다", "주석은 한글로 쓰지 않는다", "negation"),
        # Adding detail is not disagreement.
        (
            "테스트는 pytest -q 로 돌린다",
            "테스트는 루트에서 실행하며 PYTHONPATH=src 가 필요하다",
            "",
        ),
        ("주석은 한글로 쓴다", "주석은 한글로 쓴다", ""),
        ("배포는 make deploy 로 한다", "완전히 다른 주제의 문장이다", ""),
        ("", "무엇이든", ""),
    ],
)
def test_clash_kind(a, b, expected):
    assert _clash_kind(a, b) == expected


def test_term_swapped_ignores_pure_elaboration():
    assert not _term_swapped("빌드", "빌드는 make 로 하고 산출물은 dist 에 들어가며 서명이 필요하다")


def test_merged_contradiction_surfaces_in_review(coding):
    """The end-to-end path: two opposite instructions land in one carrier and
    the review queue asks which is right."""
    coding.commit("app", "앞으로 커밋 메시지는 항상 한글로 써줘", "네, 한글로 씁니다.")
    coding.commit("app", "앞으로 커밋 메시지는 항상 영어로 써줘", "네, 영어로 씁니다.")
    queue = coding.review_queue("app")
    conflicts = [it for it in queue if "conflict" in it["reasons"]]
    assert conflicts, "상충이 검토 큐에 오르지 않았습니다"
    clash = conflicts[0]["conflict"]
    assert "한글" in clash["existing"] and "영어" in clash["incoming"]
    assert clash["kind"] == "substitution"
    # A conflict must outrank a merely unconfirmed entry in the queue.
    assert queue[0]["priority"] >= conflicts[0]["priority"]


from jarvis.learn import _title_from  # noqa: E402


def test_title_never_cuts_mid_word():
    long = "앞으로 커밋 메시지는 항상 한글로 작성하고 본문에 이유를 적어줘"
    title = _title_from(long)
    assert title
    # Every word in the title must be a whole word from the source.
    assert all(w in long.split() for w in title.split())
    assert not title.endswith(("는", "은")) or title in long


def test_title_strips_trailing_punctuation():
    assert _title_from("웹훅 서명 검증은 어디서?") == "웹훅 서명 검증은 어디서"


def test_title_of_short_text_is_unchanged():
    assert _title_from("빌드 실패") == "빌드 실패"


def test_distinct_questions_get_distinct_titles(coding):
    """Truncation used to collapse different memories into the same row."""
    coding.commit("app", "앞으로 커밋 메시지는 항상 한글로 써줘", "네")
    coding.commit("app", "앞으로 커밋 메시지는 절대 대문자로 시작하지 마", "네")
    titles = [m["title"] for m in coding.memories("app", "cases")]
    assert len(titles) == len(set(titles)), titles


def test_provenance_stays_out_of_memory_text(coding):
    coding.commit("app", "앞으로 주석은 항상 한글로 써줘", "네, 한글로 씁니다.")
    for mem in coding.memories("app"):
        detail = coding.memory_detail(mem["uri"])
        assert "jarvis://" not in detail["abstract"], detail["abstract"]
        assert "출처 세션" not in detail["abstract"]
        # It is still recoverable, just not inside the prose.
        if detail["origin"] == "distilled":
            assert detail["sources"]
