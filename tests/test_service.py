from pathlib import Path

import pytest

from jarvis.tokens import estimate_tokens


def test_full_loop_prepare_commit_reuse(coding):
    q = "이 프로젝트 테스트는 어떻게 돌려?"
    a = "루트에서 `pytest -q` 를 실행하세요."

    first = coding.prepare("app", q)
    assert first.cache_hit is None
    assert first.messages[0]["role"] == "system"

    coding.commit("app", q, a, model="claude-opus-5", tokens_in=1500, tokens_out=40)

    second = coding.prepare("app", q)
    assert second.cache_hit is not None
    assert second.cache_hit.answer == a
    assert second.cache_hit.tokens_saved == 1540
    # A cache hit sends nothing to a model.
    assert second.tokens < first.tokens


def test_prepare_includes_rendered_prompt_in_user_turn(coding):
    coding.save_prompt("app", "bugfix", "증상: {{symptom}} / 파일: {{files}}")
    prepared = coding.prepare(
        "app",
        "이 버그 고쳐줘",
        prompt="bugfix",
        values={"symptom": "500 에러", "files": "api.py"},
        use_cache=False,
    )
    assert "500 에러" in prepared.user
    assert "api.py" in prepared.user
    assert prepared.prompt_uri.endswith("prompts/bugfix")


def test_prompt_text_is_not_duplicated_into_context(coding):
    """A rendered prompt goes in the user turn; paying for it twice is waste."""
    coding.save_prompt("app", "bugfix", "고유표식ABC 증상: {{s}}")
    prepared = coding.prepare("app", "버그", prompt="bugfix", values={"s": "x"}, use_cache=False)
    assert prepared.context.count("고유표식ABC") == 0
    assert prepared.user.count("고유표식ABC") == 1


def test_prepare_reinforces_used_memories(coding):
    uri = coding.remember("app", "commands", "테스트 실행", "pytest -q 로 돌린다")
    before = coding.store.read_node(uri).confidence
    coding.prepare("app", "테스트 실행 명령", use_cache=False)
    after = coding.store.read_node(uri)
    assert after.hits >= 1
    assert after.confidence >= before


def test_commit_autodistills_by_default(coding):
    res = coding.commit("app", "빌드 방법?", "make build 를 실행")
    assert "distill" in res
    assert res["distill"]["sessions"] == 1


def test_report_accumulates_measured_savings(coding):
    coding.remember("app", "commands", "테스트", "pytest -q" * 40)
    coding.prepare("app", "테스트 명령", use_cache=False)
    coding.commit("app", "테스트 명령", "pytest -q", tokens_in=900, tokens_out=20)
    coding.prepare("app", "테스트 명령")
    rep = coding.report("app")
    assert rep.cache_hits == 1
    assert rep.saved_cache == 920
    assert rep.saved_total >= rep.saved_cache
    assert 0.0 <= rep.saved_ratio <= 1.0


def test_read_returns_requested_tier(coding):
    text = "\n".join(f"## 섹션 {i}\n" + ("상세한 설명 문장이다. " * 10) for i in range(8))
    node = coding.add_resource("app", "doc", text, title="문서")
    l0 = coding.read(str(node.uri), tier=0)
    l2 = coding.read(str(node.uri), tier=2)
    assert estimate_tokens(l0["text"]) < estimate_tokens(l2["text"])
    assert estimate_tokens(l0["text"]) <= 110


def test_find_exposes_scoring_signals(coding):
    coding.remember("app", "architecture", "인증", "JWT 를 쓴다")
    out = coding.find("JWT", "app")
    assert out["results"]
    sig = out["results"][0]["signals"]
    assert set(sig) == {"vector", "lexical", "dir", "confidence"}


def test_apply_template_switches_schema_but_keeps_description(jarvis):
    jarvis.init_project("x", template="coding", description="내 설명")
    new = jarvis.apply_template("x", "research")
    assert new.template == "research"
    assert new.description == "내 설명"
    assert "findings" in new.category_names()


def test_stats_covers_global_and_projects(coding):
    coding.remember("app", "commands", "빌드", "make build")
    stats = coding.stats()
    assert "app" in stats and "global" in stats
    assert stats["app"]["total_nodes"] >= 1
    assert stats["app"]["profile"] == "coding"


# ----- curation: the review-and-fix loop -----------------------------------
def test_manual_memories_need_no_review(coding):
    coding.remember("app", "commands", "테스트", "pytest -q 로 돌린다")
    queue = coding.review_queue("app")
    assert queue == [], "직접 쓴 메모리가 검토 큐에 올랐습니다"


def test_distilled_memories_wait_for_confirmation(coding):
    coding.commit("app", "빌드는 어떻게?", "make build 를 실행하세요.")
    queue = coding.review_queue("app")
    assert queue
    assert all("unconfirmed" in it["reasons"] for it in queue)
    assert all(it["origin"] == "distilled" for it in queue)


def test_confirm_removes_from_queue_and_raises_confidence(coding):
    coding.commit("app", "빌드는 어떻게?", "make build")
    item = coding.review_queue("app")[0]
    res = coding.confirm_memory(item["uri"])
    assert res["confidence"] >= 0.85
    assert item["uri"] not in [i["uri"] for i in coding.review_queue("app")]


def test_edit_counts_as_review_and_can_move_category(coding):
    coding.commit("app", "앞으로 주석은 항상 한글로", "네")
    item = [i for i in coding.review_queue("app") if i["category"] == "cases"][0]
    res = coding.edit_memory(
        item["uri"],
        title="주석 언어",
        statement="주석은 한글로 쓴다",
        category="conventions",
    )
    assert res["uri"].endswith("memories/conventions/주석-언어")
    assert res["moved_from"] == item["uri"]
    # The old location is gone, not duplicated.
    assert coding.store.read_node(item["uri"]) is None
    detail = coding.memory_detail(res["uri"])
    assert detail["reviewed"] is True
    assert detail["abstract"] == "주석은 한글로 쓴다"
    assert res["uri"] not in [i["uri"] for i in coding.review_queue("app")]


def test_edit_rejects_a_category_the_profile_lacks(coding):
    coding.remember("app", "commands", "테스트", "pytest -q")
    uri = coding.memories("app")[0]["uri"]
    with pytest.raises(ValueError, match="카테고리"):
        coding.edit_memory(uri, category="없는카테고리")


def test_edited_memory_is_actually_retrieved_with_new_text(coding):
    uri = coding.remember("app", "commands", "배포", "make deploy 로 배포한다")
    coding.edit_memory(str(uri), statement="배포는 스크립트 scripts/deploy.sh 로 한다",
                       body="scripts/deploy.sh --env prod")
    packed = coding.retriever.pack("배포 방법", "app")
    assert "scripts/deploy.sh" in packed.text
    assert "make deploy" not in packed.text


def test_frequently_used_but_never_scored_is_flagged_unproven(coding):
    coding.remember("app", "commands", "테스트", "pytest -q 로 돌린다")
    for _ in range(3):
        coding.prepare("app", "테스트 실행 방법", use_cache=False)
    reasons = {r for it in coding.review_queue("app") for r in it["reasons"]}
    assert "unproven" in reasons


def test_low_scoring_memory_is_flagged_harmful(coding):
    coding.remember("app", "commands", "틀린 배포", "make deploy 로 배포한다", confidence=0.9)
    prepared = coding.prepare("app", "배포 방법", use_cache=False)
    coding.score(prepared.trace_id, "helpfulness", 0.0, comment="그런 명령 없음")
    reasons = {r for it in coding.review_queue("app") for r in it["reasons"]}
    assert "harmful" in reasons


def test_review_summary_counts_across_projects(jarvis):
    jarvis.init_project("a", template="coding")
    jarvis.init_project("b", template="coding")
    jarvis.commit("a", "질문1", "답1")
    jarvis.commit("b", "질문2", "답2")
    summary = jarvis.review_summary()
    assert summary["total"] == summary["projects"]["a"]["items"] + summary["projects"]["b"]["items"]
    assert summary["by_reason"]["unconfirmed"] >= 2


def test_memory_detail_exposes_provenance_and_file_path(coding):
    coding.commit("app", "웹훅 검증은 어디서?", "webhooks/verify.py 에서 HMAC 검증")
    item = coding.review_queue("app")[0]
    d = coding.memory_detail(item["uri"])
    assert d["origin"] == "distilled"
    assert d["sources"], "출처 세션이 기록되지 않았습니다"
    assert d["path"].endswith(".md")
    assert Path(d["path"]).exists()
    assert d["tokens"]["l0"] <= d["tokens"]["l2"]


def test_archived_memory_leaves_the_queue(coding):
    coding.commit("app", "질문", "답변")
    item = coding.review_queue("app")[0]
    coding.forget(item["uri"], archive=True)
    assert item["uri"] not in [i["uri"] for i in coding.review_queue("app")]


def test_accumulated_memory_keeps_a_clean_one_line_summary(coding):
    """L0 is a headline. A carrier that has accumulated dated observations must
    not surface its own change log as its summary."""
    coding.commit("app", "앞으로 주석은 항상 한글로 써줘", "네")
    coding.commit("app", "앞으로 주석은 항상 한글로 쓰고 존댓말은 쓰지 마", "네")
    for mem in coding.memories("app"):
        d = coding.memory_detail(mem["uri"])
        assert "## " not in d["abstract"], d["abstract"]
        assert "Observations" not in d["abstract"]
        assert "\n" not in d["abstract"].strip()
        # The full record is still there at L2.
        assert d["tokens"]["l2"] >= d["tokens"]["l0"]


def test_conflict_keeps_the_original_headline_for_the_human_to_decide(coding):
    coding.commit("app", "앞으로 커밋 메시지는 항상 한글로 써줘", "네")
    coding.commit("app", "앞으로 커밋 메시지는 항상 영어로 써줘", "네")
    item = [i for i in coding.review_queue("app") if "conflict" in i["reasons"]][0]
    # The incoming claim must not silently become the headline.
    assert "한글" in item["abstract"]
    assert "영어" in coding.memory_detail(item["uri"])["body"]
