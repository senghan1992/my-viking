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


def test_distilled_memories_are_marked_as_agent_written(coding):
    coding.commit("app", "빌드는 어떻게?", "make build 를 실행하세요.")
    queue = coding.review_queue("app", include_unconfirmed=True)
    assert queue
    assert all("unconfirmed" in it["reasons"] for it in queue)
    assert all(it["origin"] == "distilled" for it in queue)


def test_confirm_removes_from_queue_and_raises_confidence(coding):
    coding.commit("app", "빌드는 어떻게?", "make build")
    item = coding.review_queue("app", include_unconfirmed=True)[0]
    res = coding.confirm_memory(item["uri"])
    assert res["confidence"] >= 0.85
    assert item["uri"] not in [
        i["uri"] for i in coding.review_queue("app", include_unconfirmed=True)
    ]


def test_edit_counts_as_review_and_can_move_category(coding):
    # A session with no rule shape lands under the fallback category, which is
    # exactly the case you later want to reclassify by hand.
    coding.commit("app", "주석은 무슨 언어로 쓰고 있지?", "지금은 한글로 쓰고 있습니다.")
    item = [
        i
        for i in coding.review_queue("app", include_unconfirmed=True)
        if i["category"] == "cases"
    ][0]
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
    assert res["uri"] not in [
        i["uri"] for i in coding.review_queue("app", include_unconfirmed=True)
    ]


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
    summary = jarvis.review_summary(include_unconfirmed=True)
    assert summary["total"] == summary["projects"]["a"]["items"] + summary["projects"]["b"]["items"]
    assert summary["by_reason"]["unconfirmed"] >= 2


def test_memory_detail_exposes_provenance_and_file_path(coding):
    coding.commit("app", "웹훅 검증은 어디서?", "webhooks/verify.py 에서 HMAC 검증")
    item = coding.review_queue("app", include_unconfirmed=True)[0]
    d = coding.memory_detail(item["uri"])
    assert d["origin"] == "distilled"
    assert d["sources"], "출처 세션이 기록되지 않았습니다"
    assert d["path"].endswith(".md")
    assert Path(d["path"]).exists()
    assert d["tokens"]["l0"] <= d["tokens"]["l2"]


def test_archived_memory_leaves_the_queue(coding):
    coding.commit("app", "질문", "답변")
    item = coding.review_queue("app", include_unconfirmed=True)[0]
    coding.forget(item["uri"], archive=True)
    assert item["uri"] not in [
        i["uri"] for i in coding.review_queue("app", include_unconfirmed=True)
    ]


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


def test_conflict_headline_follows_the_newer_claim(coding):
    coding.commit("app", "앞으로 커밋 메시지는 항상 한글로 써줘", "네")
    coding.commit("app", "앞으로 커밋 메시지는 항상 영어로 써줘", "네")
    mem = coding.memories("app", "conventions")[0]
    detail = coding.memory_detail(mem["uri"])
    assert "영어" in detail["abstract"]
    # The superseded wording stays in the record.
    assert "한글" in detail["body"] or "한글" in detail["conflict"]["existing"]


def test_unconfirmed_is_not_a_review_item_by_default(coding):
    """A knowledge base written by agents is not a backlog of chores."""
    coding.commit("app", "빌드는 어떻게?", "make build 를 실행")
    assert coding.review_queue("app") == []
    # Still available for anyone who does want to audit.
    assert coding.review_queue("app", include_unconfirmed=True)


def test_session_transcripts_stay_out_of_the_prompt(coding):
    """Whatever a session was worth has been distilled into memory; including
    the transcript too pays for the same content twice — and can quote a rule
    that has since been replaced."""
    coding.commit("app", "웹훅 서명은 어디서 검증하지?", "webhooks/verify.py 에서 HMAC 으로 검증")
    prepared = coding.prepare("app", "웹훅 서명 검증 위치", use_cache=False)
    assert prepared.packed.items, "메모리는 검색되어야 합니다"
    assert all(i.kind != "session" for i in prepared.packed.items)
    # They remain searchable and surface as pointers instead.
    found = coding.find("웹훅 서명", "app", kinds=["session"])
    assert found["results"]


def test_repeated_question_still_short_circuits_without_session_context(coding):
    coding.commit("app", "테스트 어떻게 돌려?", "pytest -q", tokens_in=800)
    prepared = coding.prepare("app", "테스트 어떻게 돌려?")
    assert prepared.cache_hit is not None
    assert prepared.cache_hit.answer == "pytest -q"


# ----- blame attribution ---------------------------------------------------
def test_a_bad_answer_does_not_punish_unrelated_knowledge(coding):
    """One wrong answer used to drag down every memory that happened to be
    retrieved alongside it, including ones with nothing to do with the question.
    """
    culprit = coding.remember("app", "commands", "배포 명령", "make deploy 로 배포한다")
    bystander = coding.remember(
        "app", "pitfalls", "PG 재시도 금지", "승인 응답이 0000 이 아니면 재시도하지 않는다"
    )
    prepared = coding.prepare("app", "배포 어떻게 해?", use_cache=False)
    used = {i.uri for i in prepared.packed.items}
    assert {str(culprit), str(bystander)} <= used, "두 지식이 모두 검색되어야 전제가 성립합니다"

    # Baseline *after* retrieval: being used reinforces both, which is a
    # separate signal from being judged. We are measuring the judgement.
    before = {
        str(u): coding.store.read_node(u).confidence for u in (culprit, bystander)
    }
    res = coding.score(prepared.trace_id, "helpfulness", 0.0, comment="그런 타겟 없음")

    assert str(culprit) in res["memories_adjusted"]
    assert str(bystander) not in res["memories_adjusted"]
    assert coding.store.read_node(culprit).confidence < before[str(culprit)]
    assert coding.store.read_node(bystander).confidence == before[str(bystander)]


def test_a_good_answer_credits_everything_it_used_but_by_contribution(coding):
    coding.remember("app", "commands", "테스트 실행", "pytest -q 로 돌린다")
    coding.remember("app", "conventions", "한글 문서", "문서는 한글로 쓴다")
    prepared = coding.prepare("app", "테스트 실행 방법", use_cache=False)
    res = coding.score(prepared.trace_id, "helpfulness", 1.0)

    adj = {a["uri"]: a for a in res["adjustments"]}
    assert len(adj) >= 2, "좋은 결과는 쓰인 지식 전반에 반영되어야 합니다"
    weights = [a["weight"] for a in adj.values()]
    # Scaled by how strongly each was matched, so the top match gains most.
    assert max(weights) == 1.0
    assert min(weights) < 1.0
    top = max(adj.values(), key=lambda a: a["weight"])
    assert top["delta"] >= max(a["delta"] for a in adj.values())


def test_explicit_attribution_moves_only_the_named_memory(coding):
    culprit = coding.remember("app", "commands", "배포 명령", "make deploy 로 배포한다")
    other = coding.remember("app", "commands", "테스트 실행", "pytest -q 로 돌린다")
    prepared = coding.prepare("app", "배포와 테스트 명령", use_cache=False)
    res = coding.score(
        prepared.trace_id, "correctness", 0.0, uris=[str(culprit)]
    )
    assert res["memories_adjusted"] == [str(culprit)]
    assert coding.store.read_node(other).confidence >= 0.8


def test_global_preferences_are_not_scored_down_by_a_project_answer(jarvis):
    """Your standing instructions are not hypotheses a bad answer can erode."""
    jarvis.remember_about_me("답변은 항상 한글로 작성한다")
    jarvis.init_project("app", template="coding")
    jarvis.remember("app", "commands", "배포 명령", "make deploy 로 배포한다")

    prepared = jarvis.prepare("app", "배포 어떻게 해? 한글로 설명해줘", use_cache=False)
    assert any("global" in i.uri for i in prepared.packed.items)
    before = jarvis.about_me()[0]["confidence"]
    jarvis.score(prepared.trace_id, "helpfulness", 0.0)

    assert jarvis.about_me()[0]["confidence"] == before


def test_trace_output_is_the_answer_not_the_assembled_context(coding):
    """The answer column has to hold answers. Storing the context there made an
    uncommitted trace look answered."""
    coding.remember("app", "commands", "테스트", "pytest -q 로 돌린다")
    prepared = coding.prepare("app", "테스트 어떻게?", use_cache=False)
    trace = coding.trace(prepared.trace_id)
    assert trace["output"] == ""
    # The context is still recorded, on the retrieval step where it belongs.
    retrieval = [o for o in trace["observations"] if o["type"] == "retrieval"][0]
    assert "pytest" in str(retrieval["metadata"]) or retrieval["output"]

    coding.commit("app", "테스트 어떻게?", "pytest -q 입니다", trace_id=prepared.trace_id)
    assert coding.trace(prepared.trace_id)["output"] == "pytest -q 입니다"


def test_alias_bound_by_hand_resolves_across_url_forms(jarvis):
    """`jv link`/UI 가 저장한 별칭도 ssh/https 어느 형태로 물어도 해석돼야 한다.

    저장은 원문, 조회는 정규화 — 프로젝트 이름이 repo basename 과 다르면
    같은 remote 를 그대로 물어도 영영 해석되지 않았다."""
    jarvis.init_project("my-backend-svc")
    jarvis.bind_alias("git@github.com:me/backend.git", "my-backend-svc")

    for form in (
        "git@github.com:me/backend.git",
        "https://github.com/me/backend",
        "https://github.com/me/backend.git",
    ):
        r = jarvis.resolve_project(repo=form)
        assert r["project"] == "my-backend-svc"
        assert r["resolved_by"] == "repo"


def test_record_layer_retention(coding):
    """메모리처럼 기록 층(트레이스·회계·캐시)도 스스로 정리돼야 한다."""
    j = coding
    p = j.prepare("app", "질문", use_cache=False)
    j.commit("app", "질문", "답변", trace_id=p.trace_id)
    for i in range(6):
        j.sessions.cache_put("app", f"다른 질문 {i}", "답")

    # 전부 옛날 것으로 되돌려 놓고 스윕
    j.store.db.execute("UPDATE traces SET started = '2020-01-01T00:00:00'")
    j.store.db.execute("UPDATE usage SET ts = '2020-01-01T00:00:00'")
    j.store.db.commit()
    j.config.retention.cache_per_project = 3

    res = j.prune_records()
    assert res["traces"] >= 1 and res["usage"] >= 1 and res["cache"] >= 1
    assert j.traces("app") == []
    # 트레이스에 매달린 것들도 함께 사라진다
    assert j.store.db.query("SELECT 1 FROM observations") == []
    assert j.store.db.query("SELECT 1 FROM context_used") == []
    remaining = j.store.db.one("SELECT COUNT(*) c FROM cache WHERE scope='app'")["c"]
    assert remaining == 3

    # 0 은 '영구 보관' — 아무것도 지우지 않는다
    j.config.retention.traces_days = 0
    j.config.retention.usage_days = 0
    j.config.retention.cache_per_project = 0
    assert j.prune_records() == {"traces": 0, "usage": 0, "cache": 0}


def test_concurrent_mutations_stay_consistent(coding):
    """요청 스레드들이 한 커넥션을 공유해도 트랜잭션이 섞이지 않아야 한다."""
    import concurrent.futures

    j = coding

    def work(i):
        p = j.prepare("app", f"질문 {i}", use_cache=False)
        j.commit("app", f"질문 {i}", f"답 {i}", trace_id=p.trace_id, distill=False)
        return p.trace_id

    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        ids = list(ex.map(work, range(24)))

    assert len(set(ids)) == 24
    rows = j.traces("app", limit=100)
    assert len(rows) == 24
    assert all(r["output"] for r in rows)  # 모든 커밋이 제 트레이스에 붙었다
