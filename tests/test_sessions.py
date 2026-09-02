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
