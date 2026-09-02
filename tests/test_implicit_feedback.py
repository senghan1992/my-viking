"""다음 프롬프트로 직전 답변을 채점하는 암묵적 피드백.

사람은 명시적 평가를 거의 남기지 않지만, 같은 일을 다시 시키는 행동은
거짓말을 하지 않습니다. 이 신호가 정확해야 back data 로 쓸 수 있습니다.
"""

import pytest

from jarvis.service import _classify_followup, topic_overlap


# ----- 분류 규칙 -------------------------------------------------------------
@pytest.mark.parametrize(
    "question,overlap,minutes,expected",
    [
        # 불만 표현이 가장 강한 신호 — 유사도가 0 이어도 직전 작업에 대한 말이다.
        ("배포가 안 되는데 제대로 동작하도록 해줘", 0.0, 1, "reworked"),
        ("A기능이 구현이 안되었는데 제대로 동작하도록 해줘", 0.0, 2, "reworked"),
        ("테스트가 여전히 실패해", 0.5, 3, "reworked"),
        ("still doesn't work", 0.1, 1, "reworked"),
        ("아직 에러가 나", 0.0, 5, "reworked"),
        # 불만 없이 같은 요청 반복 → 답이 닿지 않았다는 뜻
        ("배포 어떻게 해?", 0.8, 4, "repeated"),
        # 이어지는 질문은 직전 답에 대한 판정이 아니다
        ("그럼 테스트는 어떻게 돌려?", 0.9, 2, None),
        ("추가로 롤백도 알려줘", 0.7, 3, None),
        # 주제 전환 → 약한 긍정
        ("커밋 메시지 규칙이 뭐야?", 0.0, 5, "moved_on"),
        # 한참 뒤의 주제 전환은 근거가 약해 판정하지 않는다
        ("커밋 메시지 규칙이 뭐야?", 0.0, 900, None),
        # 같은 주제인데 한참 뒤면 재작업인지 알 수 없다
        ("배포 어떻게 해?", 0.8, 900, None),
    ],
)
def test_classify_followup(question, overlap, minutes, expected):
    kind, _value, _why = _classify_followup(question, overlap, minutes, 0.25)
    assert kind == expected


def test_rework_is_scored_worse_than_a_bare_repeat():
    bad, _, _ = _classify_followup("배포가 안 되는데 고쳐줘", 0.8, 1, 0.25)
    mid, _, _ = _classify_followup("배포 어떻게 해?", 0.8, 1, 0.25)
    v_bad = _classify_followup("배포가 안 되는데 고쳐줘", 0.8, 1, 0.25)[1]
    v_mid = _classify_followup("배포 어떻게 해?", 0.8, 1, 0.25)[1]
    v_ok = _classify_followup("다른 주제 질문", 0.0, 1, 0.25)[1]
    assert (bad, mid) == ("reworked", "repeated")
    assert v_bad < v_mid < 0.5 < v_ok


def test_moving_on_is_only_mild_evidence():
    """넘어간 것이 성공인지 포기인지 구분할 수 없으므로 약하게만 반영한다."""
    _, positive, _ = _classify_followup("완전히 다른 질문", 0.0, 5, 0.25)
    _, negative, _ = _classify_followup("안 되는데 고쳐줘", 0.8, 5, 0.25)
    assert abs(positive - 0.5) < abs(negative - 0.5)


# ----- 주제 일치 측정 --------------------------------------------------------
@pytest.mark.parametrize(
    "a,b",
    [
        ("A기능 배포 어떻게 해?", "배포 어떻게 해?"),
        ("배포가 안 되는데 제대로 동작하도록 해줘", "배포 어떻게 해?"),
        ("마이그레이션 롤백은?", "마이그레이션 롤백 방법 알려줘"),
        ("웹훅 서명 검증 어디서 해?", "웹훅 서명 검증 다시 봐줘"),
    ],
)
def test_same_topic_is_recognised_through_korean_particles(a, b):
    assert topic_overlap(a, b) >= 0.25, topic_overlap(a, b)


@pytest.mark.parametrize(
    "a,b",
    [
        ("배포 어떻게 해?", "커밋 메시지 규칙이 뭐야?"),
        ("결제 승인 실패 처리", "정산 배치 스케줄 변경"),
        # 의문 상투어만 공유하는 쌍이 같은 주제로 잡히면 안 된다.
        ("배포 어떻게 해?", "테스트는 어떻게 돌려?"),
        ("웹훅 서명 검증 어디서 해?", "DB 마이그레이션 순서가 어떻게 되지?"),
    ],
)
def test_different_topics_do_not_overlap(a, b):
    assert topic_overlap(a, b) < 0.25, topic_overlap(a, b)


# ----- 실제 루프 -------------------------------------------------------------
def _turn(j, project, question, answer="그렇게 하시면 됩니다.", sid="s1"):
    p = j.prepare(project, question, session_id=sid, agent="a@x", use_cache=False)
    j.commit(project, question, answer, trace_id=p.trace_id, latency_ms=1200)
    return p.trace_id


def _outcome(j, trace_id):
    return (j.trace(trace_id)["metadata"] or {}).get("implicit_outcome")


def test_asking_again_marks_the_previous_answer_as_reworked(coding):
    coding.remember("app", "commands", "배포 명령", "make deploy 로 배포한다")
    first = _turn(coding, "app", "A기능 배포 어떻게 해?")
    assert _outcome(coding, first) is None, "판정은 다음 요청이 와야 생긴다"

    _turn(coding, "app", "배포가 안 되는데 제대로 동작하도록 해줘")
    got = _outcome(coding, first)
    assert got["kind"] == "reworked"
    assert got["signal"] == "wording"
    assert got["value"] < 0.5


def test_moving_on_marks_the_previous_answer_as_settled(coding):
    coding.remember("app", "commands", "테스트 실행", "pytest -q 로 돌린다")
    first = _turn(coding, "app", "테스트는 어떻게 돌려?")
    _turn(coding, "app", "커밋 메시지 규칙이 뭐야?")
    got = _outcome(coding, first)
    assert got["kind"] == "moved_on"
    assert got["value"] > 0.5


def test_a_stated_score_is_never_overwritten_by_inference(coding):
    coding.remember("app", "commands", "배포 명령", "make deploy")
    first = _turn(coding, "app", "배포 어떻게 해?")
    coding.score(first, "helpfulness", 1.0, comment="사람이 직접 평가")

    _turn(coding, "app", "배포가 안 되는데 고쳐줘")
    assert _outcome(coding, first) is None
    names = [s["name"] for s in coding.trace(first)["scores"]]
    assert names == ["helpfulness"]


def test_a_trace_is_judged_only_once(coding):
    coding.remember("app", "commands", "배포 명령", "make deploy")
    first = _turn(coding, "app", "배포 어떻게 해?")
    _turn(coding, "app", "배포가 안 되는데 고쳐줘")
    _turn(coding, "app", "배포가 아직도 안 돼")
    implicit = [
        s for s in coding.trace(first)["scores"] if s["name"] == "implicit_outcome"
    ]
    assert len(implicit) == 1


def test_uncommitted_traces_are_not_judged(coding):
    """답변이 기록되지 않았으면 판정할 대상이 없다."""
    coding.remember("app", "commands", "배포 명령", "make deploy")
    p = coding.prepare("app", "배포 어떻게 해?", session_id="s1", use_cache=False)
    coding.prepare("app", "배포가 안 되는데 고쳐줘", session_id="s1", use_cache=False)
    assert _outcome(coding, p.trace_id) is None


def test_inference_is_scoped_to_one_sitting(coding):
    coding.remember("app", "commands", "배포 명령", "make deploy")
    first = _turn(coding, "app", "배포 어떻게 해?", sid="s1")
    _turn(coding, "app", "배포가 안 되는데 고쳐줘", sid="s2")
    assert _outcome(coding, first) is None


def test_it_can_be_switched_off(coding):
    coding.config.learn.implicit_feedback = False
    coding.remember("app", "commands", "배포 명령", "make deploy")
    first = _turn(coding, "app", "배포 어떻게 해?")
    _turn(coding, "app", "배포가 안 되는데 고쳐줘")
    assert _outcome(coding, first) is None


def test_rework_demotes_what_actually_drove_the_answer(coding):
    culprit = coding.remember("app", "commands", "배포 명령", "make deploy 로 배포한다")
    other = coding.remember("app", "commands", "테스트 실행", "pytest -q 로 돌린다")
    before = {str(u): coding.store.read_node(u).confidence for u in (culprit, other)}

    _turn(coding, "app", "배포는 어떻게 해?")
    _turn(coding, "app", "배포가 안 되는데 제대로 동작하도록 해줘")

    after = {str(u): coding.store.read_node(u).confidence for u in (culprit, other)}
    fell_culprit = before[str(culprit)] - after[str(culprit)]
    fell_other = before[str(other)] - after[str(other)]
    assert fell_culprit > 0, "재작업을 유발한 지식이 깎이지 않았습니다"
    assert fell_culprit > fell_other * 2, (fell_culprit, fell_other)


def test_metrics_report_how_often_it_worked_first_try(coding):
    coding.remember("app", "commands", "배포 명령", "make deploy")
    coding.remember("app", "commands", "테스트 실행", "pytest -q")
    _turn(coding, "app", "배포는 어떻게 해?")
    _turn(coding, "app", "배포가 안 되는데 고쳐줘")
    _turn(coding, "app", "커밋 규칙이 뭐야?")

    m = coding.metrics("app")
    assert m["outcomes"], m
    assert 0.0 <= m["first_try_rate"] <= 1.0
    assert 0.0 <= m["rework_rate"] <= 1.0
    assert abs(m["first_try_rate"] + m["rework_rate"] - 1.0) < 1e-6


def test_metrics_are_silent_before_any_outcome_exists(coding):
    m = coding.metrics("app")
    assert m["outcomes"] == {}
    assert m["first_try_rate"] is None
    assert m["rework_rate"] is None


def test_reworked_threads_reach_the_next_session(coding):
    """다시 시킨 일은 다음 세션이 가장 먼저 알아야 하는 것이다."""
    coding.remember("app", "commands", "배포 명령", "make deploy")
    _turn(coding, "app", "A기능 배포 어떻게 해?", sid="s1")
    _turn(coding, "app", "배포가 안 되는데 제대로 동작하도록 해줘", sid="s1")

    threads = coding.open_threads("app")
    assert threads and threads[0]["kind"] in ("reworked", "repeated")

    fresh = coding.prepare("app", "무엇이든", session_id="s-new", use_cache=False)
    assert fresh.catch_up["open_threads"], "새 세션이 미해결 스레드를 못 받았습니다"
    assert coding.brief("app")["open_threads"]


def test_retrieval_is_not_inflated_by_being_used(coding):
    """검색되었다는 것은 정답이었다는 증거가 아니다."""
    uri = coding.remember("app", "commands", "테스트 실행", "pytest -q 로 돌린다")
    before = coding.store.read_node(uri).confidence
    for _ in range(3):
        coding.prepare("app", "테스트 실행 방법", use_cache=False)
    assert coding.store.read_node(uri).confidence == before
    assert coding.store.read_node(uri).hits == 3


# ----- 거짓 양성 방어 --------------------------------------------------------
@pytest.mark.parametrize(
    "question",
    [
        # 결제 도메인에서 "실패" 는 주제어다. 직전 작업에 대한 불만이 아니다.
        "결제 승인 실패하면 재시도해야 하나?",
        "오류 응답 코드는 어디에 정의되어 있어?",
        "에러 로그는 어디로 나가?",
        "장애 대응 런북이 있어?",
        "exception 처리 규칙이 뭐야?",
    ],
)
def test_a_question_about_failures_is_not_a_complaint(question):
    """장애 어휘를 문의한 것과 직전 작업이 실패했다고 말한 것은 다르다."""
    kind, _v, _w = _classify_followup(question, 0.0, 3, 0.25)
    assert kind != "reworked", question


@pytest.mark.parametrize(
    "question",
    [
        "배포가 안 되는데 제대로 동작하도록 해줘",
        "A기능이 구현이 안되었는데 제대로 동작하도록 해줘",
        "이거 여전히 안 돼",
        "아직 그대로야",
        "still doesn't work",
        "그 부분 고쳐줘",
    ],
)
def test_a_correction_counts_even_on_a_new_subject(question):
    """교정 요청은 주제어가 겹치지 않아도 직전 작업에 대한 말이다."""
    kind, _v, _w = _classify_followup(question, 0.0, 2, 0.25)
    assert kind == "reworked", question


def test_trouble_words_do_count_when_the_subject_is_unchanged():
    kind, _v, _w = _classify_followup("배포가 실패해", 0.9, 2, 0.25)
    assert kind == "reworked"


def test_a_good_answer_is_not_marked_reworked_by_the_next_topic(coding):
    """실제 루프에서의 거짓 양성 회귀: 테스트 질문 뒤에 결제 실패 질문이 와도
    앞의 답이 실패로 기록되면 안 된다."""
    coding.remember("app", "commands", "테스트 실행", "pytest -q 로 돌린다")
    coding.remember("app", "pitfalls", "재시도 금지", "0000 이 아니면 재시도하지 않는다")
    first = _turn(coding, "app", "테스트는 어떻게 돌려?")
    _turn(coding, "app", "결제 승인 실패하면 재시도해야 하나?")
    got = _outcome(coding, first)
    assert got is not None and got["kind"] == "moved_on", got
