"""개인 어시스턴트로서의 동작: 전역 선호, 선제적 경고, 프로젝트 간 회상, 브리핑."""

import pytest


# ----- global preferences ---------------------------------------------------
def test_global_preference_reaches_every_project(jarvis):
    jarvis.remember_about_me("답변과 주석은 항상 한글로 작성한다")
    jarvis.init_project("a", template="coding")
    jarvis.init_project("b", template="research")
    for project in ("a", "b"):
        prepared = jarvis.prepare(project, "답변 스타일은 어떻게 할까?", use_cache=False)
        assert "한글" in prepared.system, project


def test_about_me_lists_and_forgets(jarvis):
    uri = jarvis.remember_about_me("설명은 짧게 한다")
    assert [m["title"] for m in jarvis.about_me()] == ["설명은 짧게 한다"]
    assert jarvis.forget_about_me(str(uri))
    assert jarvis.about_me() == []


def test_about_me_rejects_unknown_category(jarvis):
    with pytest.raises(ValueError, match="카테고리"):
        jarvis.remember_about_me("무엇이든", category="없는것")


def test_forget_about_me_refuses_a_project_memory(coding):
    uri = coding.remember("app", "commands", "빌드", "make build")
    with pytest.raises(ValueError, match="전역"):
        coding.forget_about_me(str(uri))


def test_manual_preference_starts_trusted(jarvis):
    """You asserted it, so it is not a guess waiting for review."""
    jarvis.remember_about_me("한글로 답한다")
    assert jarvis.review_queue("global") == []


# ----- proactive warnings ---------------------------------------------------
def test_pitfall_is_promoted_ahead_of_ordinary_context(coding):
    coding.remember("app", "commands", "테스트", "pytest -q 로 돌린다")
    coding.remember(
        "app",
        "pitfalls",
        "PG 재시도 금지",
        "승인 응답이 0000 이 아니면 재시도하지 않는다",
        detail="재시도하면 중복 승인이 발생한다",
    )
    prepared = coding.prepare("app", "결제 재시도 로직 넣을까? 테스트도 필요해", use_cache=False)

    assert [w["title"] for w in prepared.warnings] == ["PG 재시도 금지"]
    # The warning group must come before ordinary memory in the prompt.
    assert prepared.context.index("주의") < prepared.context.index("commands")
    # The instruction to obey it is in the system prompt.
    assert "PG 재시도 금지" in prepared.system


def test_warning_text_is_not_duplicated_in_the_prompt(coding):
    """Emphasis by position, not by paying for the same text twice."""
    coding.remember(
        "app", "pitfalls", "재시도 금지", "승인 응답이 0000 이 아니면 재시도하지 않는다"
    )
    prepared = coding.prepare("app", "재시도해도 될까?", use_cache=False)
    body = "승인 응답이 0000 이 아니면 재시도하지 않는다"
    assert prepared.system.count(body) == 1


def test_non_warning_category_is_not_promoted(coding):
    coding.remember("app", "commands", "테스트", "pytest -q 로 돌린다")
    prepared = coding.prepare("app", "테스트 실행", use_cache=False)
    assert prepared.warnings == []


def test_profile_decides_what_counts_as_a_warning(jarvis):
    """A research project warns about contradictions, not about pitfalls."""
    from jarvis.profiles import builtin

    assert builtin("coding").warn_categories() == {"pitfalls"}
    assert builtin("research").warn_categories() == {"contradictions"}
    assert builtin("ops").warn_categories() == {"incidents"}
    assert builtin("writing").warn_categories() == set()


# ----- cross-project recall -------------------------------------------------
def test_other_projects_are_offered_but_labelled(jarvis):
    jarvis.init_project("backend", template="coding")
    jarvis.init_project("infra", template="ops")
    jarvis.remember(
        "infra",
        "runbooks",
        "중복 승인 대응",
        "중복 승인이 발생하면 정산 배치를 멈추고 수동 취소한다",
    )
    prepared = jarvis.prepare("backend", "중복 승인이 발생하면 어떻게 하지?", use_cache=False)

    hits = prepared.from_other_projects
    assert hits and hits[0]["project"] == "infra"
    # It must never look like a verified fact of *this* project.
    assert "검증된 것이 아닙니다" in prepared.system
    assert "[infra]" in prepared.system
    # And it must not enter this project's own context block.
    assert "runbooks" not in prepared.context


def test_cross_project_can_be_switched_off(jarvis):
    jarvis.init_project("backend", template="coding")
    jarvis.init_project("infra", template="ops")
    jarvis.remember("infra", "runbooks", "중복 승인 대응", "정산 배치를 멈추고 수동 취소한다")
    prepared = jarvis.prepare(
        "backend", "중복 승인 대응", use_cache=False, cross_project=False
    )
    assert prepared.from_other_projects == []


def test_unrelated_projects_do_not_bleed(jarvis):
    jarvis.init_project("backend", template="coding")
    jarvis.init_project("blog", template="writing")
    jarvis.remember("blog", "voice", "문체", "글은 존댓말로 쓴다")
    prepared = jarvis.prepare("backend", "결제 승인 코드 확인", use_cache=False)
    assert prepared.from_other_projects == []


def test_only_a_few_cross_project_hits_are_offered(jarvis):
    for i in range(6):
        jarvis.init_project(f"p{i}", template="coding")
        jarvis.remember(f"p{i}", "pitfalls", f"중복 승인 함정 {i}", "중복 승인이 발생한다")
    jarvis.init_project("main", template="coding")
    prepared = jarvis.prepare("main", "중복 승인 문제", use_cache=False)
    assert len(prepared.from_other_projects) <= 3


# ----- briefing and digest --------------------------------------------------
def test_brief_separates_knowledge_warnings_and_open_questions(coding):
    coding.remember("app", "commands", "테스트", "pytest -q 로 돌린다")
    coding.remember("app", "pitfalls", "재시도 금지", "0000 이 아니면 재시도하지 않는다")
    coding.config.learn.conflict_policy = "flag"
    coding.commit("app", "앞으로 커밋은 항상 한글로", "네")
    coding.commit("app", "앞으로 커밋은 항상 영어로", "네")

    b = coding.brief("app")
    assert b["template"] == "coding"
    assert [w["title"] for w in b["warnings"]] == ["재시도 금지"]
    assert "테스트" in [k["title"] for k in b["know"]]
    assert not any(k["category"] == "pitfalls" for k in b["know"])
    assert b["unresolved"], "상충이 미해결로 올라오지 않았습니다"
    assert b["totals"]["needs_review"] >= 1


def test_brief_on_an_empty_project_is_still_valid(coding):
    b = coding.brief("app")
    assert b["totals"]["memories"] == 0
    assert b["know"] == [] and b["warnings"] == []


def test_digest_summarises_every_project(jarvis):
    jarvis.init_project("a", template="coding")
    jarvis.init_project("b", template="coding")
    jarvis.remember_about_me("한글로 답한다")
    prepared = jarvis.prepare("a", "질문", use_cache=False)
    jarvis.commit("a", "질문", "답변", trace_id=prepared.trace_id, latency_ms=1200)

    d = jarvis.digest()
    names = [e["project"] for e in d["projects"]]
    assert set(names) == {"a", "b"}
    assert d["about_me"] == 1
    entry = [e for e in d["projects"] if e["project"] == "a"][0]
    assert entry["traces"] >= 1
    assert entry["new_memories"] >= 1
    # Ordered by what needs attention, so the digest leads with the work.
    assert d["projects"] == sorted(d["projects"], key=lambda e: -e["needs_review"])


def test_maintain_distills_sessions_left_undistilled(jarvis):
    jarvis.init_project("a", template="coding")
    jarvis.commit("a", "빌드 방법?", "make build 를 실행", distill=False)
    assert jarvis.sessions.undistilled("a")
    res = jarvis.maintain()
    assert res["projects"][0]["sessions"] == 1
    assert jarvis.sessions.undistilled("a") == []


def test_brief_never_lists_a_disputed_rule_as_established(coding):
    """Both sides of a contradiction under "확립된 지식" is worse than silence."""
    coding.config.learn.conflict_policy = "flag"
    coding.remember("app", "commands", "테스트", "pytest -q 로 돌린다")
    coding.commit("app", "앞으로 커밋은 항상 한글로", "네")
    coding.commit("app", "앞으로 커밋은 항상 영어로", "네")

    b = coding.brief("app")
    known = {k["uri"] for k in b["know"]}
    disputed = {u["uri"] for u in b["unresolved"]}
    assert disputed, "상충이 미해결로 올라와야 합니다"
    assert not (known & disputed), "상충 항목이 확립된 지식에 섞였습니다"
    assert "테스트" in [k["title"] for k in b["know"]]
    assert b["totals"]["disputed"] == len(disputed)


def test_brief_excludes_a_harmful_memory_from_knowledge(coding):
    uri = coding.remember("app", "commands", "틀린 배포", "make deploy 로 배포한다")
    prepared = coding.prepare("app", "배포 방법", use_cache=False)
    coding.score(prepared.trace_id, "helpfulness", 0.0, comment="없는 명령")
    b = coding.brief("app")
    assert str(uri) not in {k["uri"] for k in b["know"]}
