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


def test_merged_contradiction_is_recorded_with_both_statements(coding):
    """Even when resolved automatically, the pair stays auditable."""
    coding.commit("app", "앞으로 커밋 메시지는 항상 한글로 써줘", "네, 한글로 씁니다.")
    coding.commit("app", "앞으로 커밋 메시지는 항상 영어로 써줘", "네, 영어로 씁니다.")
    conv = coding.memories("app", "conventions")
    detail = coding.memory_detail(conv[0]["uri"])
    clash = detail["conflict"]
    assert "한글" in clash["existing"] and "영어" in clash["incoming"]
    assert clash["kind"] == "substitution"
    assert clash["resolution"] == "superseded"
    # The current headline follows the newer instruction.
    assert "영어" in detail["abstract"]


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


def test_rule_distilled_memory_carries_its_extractor_and_stays_below_established(coding):
    """규칙 기반 추출은 LLM 추출과 구별돼 저장되고(extractor='rule'), 마커 하나만
    맞은 선호는 '확립된 지식' 게이트(0.6) 아래에 머문다 — 추측이 사실로 둔갑하지
    않도록. LLM 없는 기본 설정에서의 신뢰성 핵심."""
    assert not coding.store.llm.available  # 기본 설정: 규칙 기반
    coding.commit(
        "app", "앞으로 주석은 항상 한글로 써줘", "네, 한글로 씁니다.", distill=False
    )
    rep = coding.distill("app")
    pref = next(
        coding.memory_detail(u) for u in rep.created
        if Uri.parse(u).parts[1] in ("preferences", "conventions")
    )
    assert pref["extractor"] == "rule"
    assert pref["origin"] == "distilled"
    assert pref["confidence"] < 0.6, "마커 매칭 추측이 확립된 지식으로 올라갔다"
    # 그러니 브리핑의 '확립된 지식(know)' 에는 뜨지 않는다.
    know_uris = {k["uri"] for k in coding.brief("app")["know"]}
    assert pref["uri"] not in know_uris


def test_provenance_stays_out_of_memory_text(coding):
    coding.commit("app", "앞으로 주석은 항상 한글로 써줘", "네, 한글로 씁니다.")
    for mem in coding.memories("app"):
        detail = coding.memory_detail(mem["uri"])
        assert "jarvis://" not in detail["abstract"], detail["abstract"]
        assert "출처 세션" not in detail["abstract"]
        # It is still recoverable, just not inside the prose.
        if detail["origin"] == "distilled":
            assert detail["sources"]


@_pytest.mark.parametrize(
    "a,b",
    [
        # Different polarity, nothing in common: not a contradiction.
        ("빌드는 make build 로 한다", "배포 전에는 절대 강제 푸시하지 않는다"),
        ("테스트는 pytest -q 로 돌린다", "로그는 JSON 으로 남기지 않는다"),
        ("포트는 8080 을 쓴다", "캐시는 없다"),
        ("커밋 메시지는 한글로 쓴다", "배포는 금요일에 하지 않는다"),
    ],
)
def test_unrelated_statements_are_not_conflicts(a, b):
    """Polarity alone is meaningless between unrelated sentences, and scanning a
    whole category with that rule would flood review with noise."""
    assert _clash_kind(a, b) == ""


def test_newer_instruction_supersedes_the_older_one(coding):
    """The default policy resolves contradictions without a person: a later
    instruction is normally the current one."""
    coding.commit("app", "앞으로 커밋은 항상 한글로", "네")
    coding.commit("app", "앞으로 커밋은 항상 영어로", "네")

    conv = coding.memories("app", "conventions")
    assert len(conv) == 1, [m["title"] for m in conv]
    survivor = coding.memory_detail(conv[0]["uri"])
    assert "영어" in survivor["abstract"]
    assert survivor["conflict"]["resolution"] == "superseded"

    # Reversible: the old one is archived with a pointer, not deleted.
    old = coding.store.read_node(
        "jarvis://projects/app/_archive/memories/conventions/앞으로-커밋은-항상-한글로"
    )
    assert old is not None
    assert old.extra["superseded_by"] == conv[0]["uri"]
    assert "영어" in old.tier(2)

    # Out of the *rules* the agent is given, so it never sees two live rules.
    from jarvis.models import KIND_MEMORY

    packed = coding.retriever.pack("커밋 메시지 언어 규칙", "app", kinds=[KIND_MEMORY])
    assert "한글로" not in packed.text
    # A transcript may still quote it, so that section says it is not a rule.
    full = coding.retriever.pack("커밋 메시지 언어 규칙", "app")
    if "한글로" in full.text:
        assert "현재 규칙이 아님" in full.text


def test_superseded_conflict_does_not_ask_for_review(coding):
    coding.commit("app", "앞으로 커밋은 항상 한글로", "네")
    coding.commit("app", "앞으로 커밋은 항상 영어로", "네")
    assert coding.review_queue("app") == []


def test_flag_policy_keeps_both_sides_for_a_person(coding):
    """Opt back into human arbitration when that is what you want."""
    coding.config.learn.conflict_policy = "flag"
    coding.commit("app", "앞으로 커밋은 항상 한글로", "네")
    coding.commit("app", "앞으로 커밋은 항상 영어로", "네")

    conv = coding.memories("app", "conventions")
    assert len(conv) == 2
    flagged = [coding.memory_detail(m["uri"]) for m in conv]
    assert all(d["conflict"] for d in flagged)
    # Each side points at the other so either can be corrected.
    assert {d["conflict"]["other"] for d in flagged} == {m["uri"] for m in conv}
    assert [it for it in coding.review_queue("app") if "conflict" in it["reasons"]]


def test_no_false_conflicts_across_a_populated_category(coding):
    facts = [
        ("빌드", "빌드는 make build 로 한다"),
        ("강제 푸시", "배포 전에는 절대 강제 푸시하지 않는다"),
        ("로그", "로그는 JSON 으로 남기지 않는다"),
        ("포트", "포트는 8080 을 쓴다"),
    ]
    for title, statement in facts:
        coding.remember("app", "conventions", title, statement)
    conflicts = [
        m for m in coding.memories("app", "conventions")
        if coding.memory_detail(m["uri"])["conflict"]
    ]
    assert conflicts == []


def test_session_is_not_filed_twice(coding):
    """A session that produced a categorised memory must not also leave a
    duplicate under the fallback category."""
    coding.commit("app", "앞으로 주석은 항상 한글로 써줘", "네, 한글로 씁니다.")
    cats = {m["category"] for m in coding.memories("app")}
    assert cats == {"conventions"}, cats


def test_session_with_nothing_specific_still_lands_somewhere(coding):
    """The fallback must still catch a session no rule claimed, or the loop
    looks like it runs while nothing accumulates."""
    coding.commit("app", "이 프로젝트 이름이 뭐였지?", "backend 입니다.")
    assert {m["category"] for m in coding.memories("app")} == {"cases"}


def test_supersession_chain_keeps_names_matching_content(coding):
    """A carrier whose content was replaced must not keep the old statement as
    its name, or the knowledge list shows a title that contradicts its body."""
    for q in (
        "앞으로 커밋 메시지는 항상 한글로 써줘",
        "앞으로 커밋 메시지는 항상 영어로 써줘",
        "앞으로 커밋 메시지는 항상 일본어로 써줘",
    ):
        coding.commit("app", q, "네, 그렇게 하겠습니다.")

    live = coding.memories("app", "conventions")
    assert len(live) == 1
    detail = coding.memory_detail(live[0]["uri"])
    assert "일본어" in detail["title"]
    assert "일본어" in detail["abstract"]
    assert "한글" not in detail["title"] and "영어" not in detail["title"]

    # Only the current rule reaches the agent.
    prepared = coding.prepare("app", "커밋 메시지 언어 규칙", use_cache=False)
    assert "일본어" in prepared.context
    assert "한글로" not in prepared.context and "영어로" not in prepared.context


def test_superseded_wording_is_still_recoverable(coding):
    """Nothing is destroyed by an automatic supersession. Depending on whether
    the replacement merged into the carrier or replaced it, the old wording is
    either in the file's history section or in the archive — but it is there."""
    coding.commit("app", "앞으로 커밋 메시지는 항상 한글로 써줘", "네")
    coding.commit("app", "앞으로 커밋 메시지는 항상 영어로 써줘", "네")

    live = "".join(
        p.read_text(encoding="utf-8")
        for p in (coding.store.scope_dir("app") / "memories").rglob("*.md")
    )
    archive_dir = coding.store.scope_dir("app") / "_archive"
    archived = "".join(
        p.read_text(encoding="utf-8") for p in archive_dir.rglob("*.md")
    ) if archive_dir.exists() else ""

    assert "한글" in live + archived, "대체된 문구가 어디에도 남지 않았습니다"
    assert "대체" in live + archived or "superseded" in live + archived


def test_retitle_refuses_to_overwrite_an_occupied_name(coding):
    """The rename guard, exercised directly: two different memories must never
    collapse into one file because a supersession wanted that name."""
    occupied = coding.remember("app", "conventions", "이미 있는 이름", "다른 지식의 내용")
    other = coding.remember("app", "conventions", "바뀔 이름", "바뀌기 전 내용")

    node = coding.store.read_node(other)
    result = coding.learner._retitle(node, "이미 있는 이름")

    # Stayed put rather than clobbering the occupant.
    assert result == other
    assert coding.store.read_node(occupied) is not None
    assert coding.memory_detail(str(occupied))["abstract"] == "다른 지식의 내용"
    assert len(coding.memories("app", "conventions")) == 2


def test_retitle_moves_the_file_when_the_name_is_free(coding):
    uri = coding.remember("app", "conventions", "옛 이름", "내용")
    node = coding.store.read_node(uri)
    moved = coding.learner._retitle(node, "새 이름")
    assert str(moved).endswith("conventions/새-이름")
    assert coding.store.read_node(uri) is None
    detail = coding.memory_detail(str(moved))
    assert detail["title"] == "새 이름"
    assert detail["abstract"] == "내용"
