from jarvis.sessions import normalize_question, question_hash


def test_normalize_ignores_case_space_and_trailing_punct():
    assert normalize_question("  테스트는 어떻게   돌려?  ") == "테스트는 어떻게 돌려"
    assert question_hash("Hello world?") == question_hash("hello   world")


def test_exact_cache_hit(coding):
    coding.commit("app", "테스트 어떻게 돌려?", "pytest -q 실행", distill=False)
    hit = coding.sessions.cache_lookup("app", "테스트 어떻게 돌려?")
    assert hit is not None
    assert hit.kind == "exact"
    assert hit.answer == "pytest -q 실행"


def test_near_cache_hit_on_paraphrase(coding):
    coding.commit(
        "app",
        "이 프로젝트에서 테스트를 실행하는 방법을 알려줘",
        "pytest -q 를 루트에서 실행",
        distill=False,
    )
    # Slight rewording must still hit; that is where the savings live.
    hit = coding.sessions.cache_lookup(
        "app", "이 프로젝트에서 테스트를 실행하는 방법 알려줘", threshold=0.8
    )
    assert hit is not None
    assert hit.kind == "near"
    assert hit.similarity >= 0.8


def test_unrelated_question_misses(coding):
    coding.commit("app", "테스트 실행 방법", "pytest", distill=False)
    assert coding.sessions.cache_lookup("app", "배포 파이프라인의 리전 설정") is None


def test_cache_is_scoped_per_project(jarvis):
    jarvis.init_project("a")
    jarvis.init_project("b")
    jarvis.commit("a", "비밀 질문", "a 프로젝트 답", distill=False)
    assert jarvis.sessions.cache_lookup("a", "비밀 질문") is not None
    assert jarvis.sessions.cache_lookup("b", "비밀 질문") is None


def test_cache_hit_counts_and_saves_reported_tokens(coding):
    coding.commit("app", "질문", "답변", tokens_in=1000, tokens_out=200, distill=False)
    hit = coding.sessions.cache_lookup("app", "질문")
    assert hit.tokens_saved == 1200
    rows = coding.cache_list("app")
    assert rows[0]["hits"] == 1


def test_cache_clear(coding):
    coding.commit("app", "질문", "답변", distill=False)
    assert coding.cache_clear("app") == 1
    assert coding.sessions.cache_lookup("app", "질문") is None


def test_undistilled_tracks_state(coding):
    coding.commit("app", "질문1", "답변1", distill=False)
    assert len(coding.sessions.undistilled("app")) == 1
    coding.distill("app")
    assert coding.sessions.undistilled("app") == []


def test_recent_sessions_newest_first(coding):
    coding.commit("app", "먼저 물어본 것", "답1", distill=False)
    coding.commit("app", "나중에 물어본 것", "답2", distill=False)
    recent = coding.recent_sessions("app")
    assert len(recent) == 2


def test_work_orders_and_deictic_prompts_are_not_cached_or_served(coding):
    """A live run served "함수를 3개로 분리했습니다 — 재사용하세요" for the next
    "이 함수 리팩터링해줘". Work must be redone; pointers name a new target."""
    from jarvis.sessions import reusable_question

    for q in ("이 함수 리팩터링해줘", "로그인 API 만들어줘", "CSS 버튼 색상을 파란색으로 바꿔줘",
              "결제 재시도 로직 다시 봐줘", "hello", "hi", "고마워", "이거 왜 안 돼?"):
        assert not reusable_question(q), q
        coding.commit("app", q, "했습니다.", agent="a")
        assert coding.sessions.cache_lookup("app", q) is None, q
    for q in ("테스트 어떻게 돌려?", "결제 승인 실패하면 재시도해야 해?", "커밋 메시지 규칙이 뭐야",
              "Store.all 이 파일 없을 때 뭐 돌려줘", "테스트 실행 방법", "질문", "배포 파이프라인 리전 설정은?"):
        assert reusable_question(q), q
    coding.commit("app", "결제 승인 실패하면 재시도해야 해?", "재시도하지 않습니다.", agent="a")
    assert coding.sessions.cache_lookup("app", "결제 승인 실패하면 재시도해야 해?") is not None


def test_reusable_question_matrix():
    """From the adversarial review: work orders with a question noun were cached,
    knowledge questions with "it"/"here" were not."""
    from jarvis.sessions import reusable_question

    not_reusable = [
        "로그인 방법 바꿔줘", "린트 규칙 추가해줘", "테스트 좀 돌려줄래?", "Could you refactor the payment module",
        "Add a retry when payment fails", "이 함수 리팩터링해줘", "이거 정리해줘", "결제 로직 수정해 주세요",
        "please fix the flaky test", "hello", "응", "네", "고마워요",
    ]
    reusable = [
        "Is it safe to retry payments after a timeout?", "Where do logs live here?", "Note that the DB is Postgres 15",
        "테스트 어떻게 돌려?", "Store.all 이 파일 없을 때 뭐 돌려줘", "테스트 실행 방법", "배포 파이프라인 리전 설정은?",
        "응답 코드 0000 의 의미가 뭐야", "네트워크 응답이 느린 이유는?", "결제 실패 시 재시도해도 될까?",
        "How do I deploy to staging?", "what does make deploy do", "커밋 메시지 규칙",
    ]
    for q in not_reusable:
        assert not reusable_question(q), q
    for q in reusable:
        assert reusable_question(q), q
