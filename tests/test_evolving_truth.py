"""정답지가 스스로 갱신되는 구조의 회귀 테스트.

사용자 요구: "정답이라고 생각했던 부분이 나중 프롬프트를 보니 아니었을 경우
스스로 정답지를 업그레이드하고, 주입된 내용을 '확인된 사실'로만 받지 않도록."

여기서 고정하는 것:
  1) trust() 가 결과·나이·충돌을 신뢰 상태로 요약한다 (established↔contested 를 오간다).
  2) score() 가 결과를 근거(evidence) 로 남기고, 반박이 쌓이면 정답지에서 내린다.
  3) 최근 나쁜 결과로 지목된 지식을, 곧 학습된 교정 지식이 대체(supersede)한다.
  4) prepare() 가 확정이 아닌 주입 항목을 trust_notes 로 표시한다.
"""

from __future__ import annotations


# --------------------------------------------------------------------------
# 1) trust(): 결과가 신뢰 상태를 움직인다
# --------------------------------------------------------------------------
def test_trust_starts_fresh_then_establishes_then_contests(jarvis):
    jarvis.init_project("app", template="coding")
    uri = jarvis.remember("app", "conventions", "탭 규칙", "탭이 아니라 스페이스 4칸")
    node = jarvis.store.read_node(uri)
    # 직접 작성(manual)은 fresh 가 아니라 신뢰 0.8 로 시작 → 확립
    assert jarvis.trust(node)["status"] == "established"

    # 반박이 contested_after 회 연속 쌓이면 정답지에서 내려온다
    jarvis.config.learn.contested_after = 2
    node.extra["challenge_streak"] = 2
    t = jarvis.trust(node)
    assert t["status"] == "contested" and not t["established"]
    assert "확인 필요" in t["label"]

    # 다시 확인되면(streak 0) 확립으로 돌아온다 — 양방향 치유
    node.extra["challenge_streak"] = 0
    assert jarvis.trust(node)["established"]


def test_trust_marks_stale_and_low_confidence(jarvis):
    jarvis.init_project("app", template="coding")
    uri = jarvis.remember("app", "commands", "빌드", "make build")
    node = jarvis.store.read_node(uri)

    node.confidence = 0.4
    assert jarvis.trust(node)["status"] == "tentative"

    node.confidence = 0.8
    node.updated = "2000-01-01T00:00:00+00:00"
    node.last_used = "2000-01-01T00:00:00+00:00"
    jarvis.config.learn.stale_days = 90
    # 사람이 확인한(reviewed) 지식은 나이만으로 낡지 않는다 — 사람이 보증한 것이다.
    node.extra["reviewed"] = True
    assert jarvis.trust(node)["status"] == "established"
    # 에이전트가 남기고 아무도 재확인 안 한 지식은 오래되면 재확인 대상이 된다.
    node.extra["reviewed"] = False
    assert jarvis.trust(node)["status"] == "stale"


def test_superseded_node_is_never_established(jarvis):
    jarvis.init_project("app", template="coding")
    uri = jarvis.remember("app", "commands", "테스트", "pytest 로 돌린다")
    node = jarvis.store.read_node(uri)
    node.extra["superseded_by"] = "jarvis://projects/app/memories/commands/테스트-new"
    t = jarvis.trust(node)
    assert t["status"] == "superseded" and not t["established"]


# --------------------------------------------------------------------------
# 2) score(): 결과가 근거로 남고, 반박이 신뢰 상태를 바꾼다
# --------------------------------------------------------------------------
def _traced_use(jarvis, project, question):
    prepared = jarvis.prepare(project, question, agent="t")
    return prepared.trace_id


def test_score_records_evidence_and_flips_to_contested(jarvis):
    jarvis.init_project("app", template="coding")
    uri = jarvis.remember("app", "conventions", "린트", "eslint 로 검사한다")
    jarvis.config.learn.contested_after = 2

    # 이 지식을 실제로 쓴 트레이스를 두 번 만들고, 두 번 다 나쁜 결과로 평가
    for _ in range(2):
        tid = _traced_use(jarvis, "app", "린트 어떻게 검사해?")
        jarvis.score(tid, value=0.0, comment="틀린 방법이었다", uris=[str(uri)])

    node = jarvis.store.read_node(uri)
    ev = node.extra.get("evidence") or []
    assert ev and all(e["kind"] == "contradicted" for e in ev)
    assert node.extra["challenge_streak"] >= 2
    assert jarvis.trust(node)["status"] == "contested"

    # 좋은 결과 한 번이면 streak 이 풀리고 확인 횟수가 오른다
    tid = _traced_use(jarvis, "app", "린트 어떻게 검사해?")
    jarvis.score(tid, value=1.0, comment="이번엔 맞았다", uris=[str(uri)])
    node = jarvis.store.read_node(uri)
    assert node.extra["challenge_streak"] == 0
    assert node.extra["confirmations"] >= 1
    assert node.extra.get("last_confirmed")


def test_brief_drops_a_contested_memory_from_the_answer_key(jarvis):
    jarvis.init_project("app", template="coding")
    uri = jarvis.remember("app", "conventions", "포맷", "prettier 를 쓴다")
    assert any(m["uri"] == str(uri) for m in jarvis.brief("app")["know"])

    jarvis.config.learn.contested_after = 2
    node = jarvis.store.read_node(uri)
    node.extra["challenge_streak"] = 2
    jarvis.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)

    know_uris = {m["uri"] for m in jarvis.brief("app")["know"]}
    assert str(uri) not in know_uris  # 확립된 지식에서 내려왔다


# --------------------------------------------------------------------------
# 3) 교정이 오답을 대체한다 (정답지 업그레이드)
# --------------------------------------------------------------------------
def test_a_recent_correction_supersedes_the_blamed_belief(jarvis):
    jarvis.init_project("app", template="coding")
    # 병합이 아니라 교정-대체 경로를 보기 위해, 제목이 다르면 합쳐지지 않게 한다.
    jarvis.config.learn.merge_threshold = 0.99
    old = jarvis.remember(
        "app", "commands", "테스트 실행", "npm test 로 테스트를 돌린다"
    )

    # 이 지식을 쓴 답이 나쁜 결과로 지목된다 (최근 blame)
    tid = _traced_use(jarvis, "app", "테스트 실행 어떻게 해?")
    jarvis.score(tid, value=0.0, comment="npm test 는 없는 스크립트였다", uris=[str(old)])

    # 곧 같은 주제의 교정 지식을 손으로 기록 → 이전 것을 대체해야 한다
    new = jarvis.remember(
        "app", "commands", "테스트 도구", "이 저장소 테스트는 pytest -q 로 돌린다"
    )
    new_node = jarvis.store.read_node(new)
    assert str(old) in (new_node.extra.get("corrects") or [])
    assert new_node.confidence >= 0.7

    # 옛 지식은 보관되며, 무엇으로 대체됐는지 가리킨다 (되돌릴 수 있다).
    assert jarvis.store.read_node(old) is None  # 라이브에서 내려감
    archived = jarvis.store.read_node(
        str(old).replace("/memories/", "/_archive/memories/")
    )
    assert archived is not None and archived.extra.get("superseded_by") == str(new)

    # 대체된 옛 지식은 정답지(brief know)에 더는 없고, 새 것이 그 자리에 있다.
    know = {m["uri"] for m in jarvis.brief("app")["know"]}
    assert str(old) not in know and str(new) in know


def test_commit_reconciles_a_distilled_correction(jarvis):
    jarvis.init_project("app", template="coding")
    old = jarvis.remember(
        "app", "conventions", "패키지 매니저", "이 저장소는 yarn 을 쓴다"
    )
    tid = _traced_use(jarvis, "app", "패키지 매니저 뭐 써?")
    jarvis.score(tid, value=0.0, comment="yarn 아니라 pnpm 이었다", uris=[str(old)])

    # 사용자가 바로잡는 지시를 하고, 그 교환이 distill 되며 교정 지식이 생긴다
    res = jarvis.commit(
        "app",
        "패키지 매니저 뭐 써?",
        "이 저장소는 pnpm 을 쓴다. package.json 의 packageManager 필드가 pnpm.",
        outcome="성공",
        trace_id=tid,
    )
    # distill 이 교정 후보를 만들었다면: 에이전트의 다음 시도는 아직 검증 전이므로
    # 옛 것을 바로 보관하지 않고 '교정 후보 있음(contested)' 으로만 내린다.
    corrected = res.get("corrected") or []
    old_node = jarvis.store.read_node(old)
    if corrected:
        assert all(c["mode"] == "pending" for c in corrected)
        assert old_node is not None and old_node.extra.get("challenged_by")
        assert old_node.extra.get("superseded_by") is None
        assert jarvis.trust(old_node)["status"] == "contested"


# --------------------------------------------------------------------------
# 4) 주입에 확정/미확정 표시가 실린다
# --------------------------------------------------------------------------
def test_prepare_flags_unsettled_packed_memories(jarvis):
    jarvis.init_project("app", template="coding")
    uri = jarvis.remember("app", "conventions", "배포 규칙", "배포는 main 에서만 한다")
    # 반박을 쌓아 contested 로 만든 뒤, 이 지식이 잡히는 질문으로 prepare
    jarvis.config.learn.contested_after = 2
    node = jarvis.store.read_node(uri)
    node.extra["challenge_streak"] = 2
    jarvis.store.write_node(node, regenerate_tiers=False, reinforce_dirs=False)

    prepared = jarvis.prepare("app", "배포 규칙 알려줘", agent="t")
    notes = {n["uri"]: n for n in prepared.trust_notes}
    if str(uri) in {i.uri for i in prepared.packed.items}:
        assert str(uri) in notes
        assert notes[str(uri)]["status"] == "contested"
        assert "trust_notes" in prepared.to_dict()


# --------------------------------------------------------------------------
# 5) 내려간 지식은 다시 올라올 수 있어야 한다 — 래칫이 아니다
# --------------------------------------------------------------------------
def _turn(j, question, answer="그렇게 하시면 됩니다.", sid="s1"):
    p = j.prepare("app", question, session_id=sid, agent="a", use_cache=False, max_tier=0)
    j.commit("app", question, answer, trace_id=p.trace_id, agent="a")
    return p.trace_id


def _learn(j, category, title, statement, confidence=0.5):
    """증류가 만든 것처럼 지식을 넣는다 — remember() 는 사람 손이라 즉시 대체한다."""
    from jarvis.learn import MemoryCandidate

    cand = MemoryCandidate(
        category=category, title=title, statement=statement,
        confidence=confidence, source="distilled", extractor="rule",
    )
    uri, _action, _conflict = j.learner.absorb("app", cand, j.store.profile("app"))
    return uri


def test_moving_on_without_complaint_heals_an_open_challenge(jarvis):
    """긍정 암묵 신호는 신뢰를 올리지 않지만, 열린 반박 streak 은 닫는다.
    안 그러면 무인 운영에서 contested 를 되돌릴 길이 명시적 점수밖에 없다."""
    jarvis.init_project("app", template="coding")
    uri = jarvis.remember("app", "commands", "린트 검사", "eslint . 로 검사한다")
    before = jarvis.store.read_node(uri).confidence

    _turn(jarvis, "린트 검사 어떻게 해?", answer="eslint . 를 실행하세요.")
    _turn(jarvis, "린트 검사 여전히 안 되는데", answer="캐시를 지우고 다시 실행하세요.")
    node = jarvis.store.read_node(uri)
    assert node.extra["challenge_streak"] == 1

    # 이 지식이 다시 주입된 답 뒤에, 사용자가 불만 없이 다른 주제로 넘어간다
    _turn(jarvis, "린트 검사 규칙 파일 어디야?", answer=".eslintrc 입니다.")
    _turn(jarvis, "커밋 메시지 규칙이 뭐야?", answer="conventional commits 입니다.")
    node = jarvis.store.read_node(uri)
    assert node.extra["challenge_streak"] == 0
    assert node.extra.get("settled", 0) >= 1
    assert any(e["kind"] == "settled" for e in node.extra["evidence"])
    # 신뢰 자체는 오르지 않았다 — 불만이 없었다는 것은 칭찬이 아니다
    assert node.confidence <= before


def test_lifetime_harm_alone_does_not_keep_a_memory_contested(jarvis):
    """누적 harm 은 평생 합계라서 단독으로 쓰면 한 번의 오채점이 영구 contested 가
    된다. streak 이 열려 있고 한 번의 암묵 blame 보다 클 때만 작동한다."""
    jarvis.init_project("app", template="coding")
    uri = jarvis.remember("app", "commands", "빌드", "make build")
    node = jarvis.store.read_node(uri)
    node.extra["challenge_streak"] = 0
    assert jarvis.trust(node, {"harm": -0.3})["status"] == "established"
    node.extra["challenge_streak"] = 1
    assert jarvis.trust(node, {"harm": -0.07})["status"] == "established"  # 암묵 1회
    assert jarvis.trust(node, {"harm": -0.3})["status"] == "contested"  # 명시적 오답


def test_retrieval_and_age_alone_never_promote_a_distilled_memory(jarvis):
    """주입됐다는 사실(hits)과 나이는 맞았다는 증거가 아니다. 확인·검토·settled
    중 하나가 있어야 '검증 전' 표시가 떨어진다."""
    jarvis.init_project("app", template="coding")
    uri = jarvis.remember("app", "commands", "포맷", "prettier --write .", confidence=0.7)
    node = jarvis.store.read_node(uri)
    node.extra["origin"] = "distilled"
    node.extra["reviewed"] = False
    node.hits = 50
    node.created = "2000-01-01T00:00:00+00:00"
    node.updated = node.last_used = "2099-01-01T00:00:00+00:00"  # stale 아님
    assert jarvis.trust(node)["status"] == "fresh"
    node.extra["settled"] = jarvis.config.learn.settled_after
    assert jarvis.trust(node)["status"] == "established"
    node.extra["settled"] = 0
    node.extra["confirmations"] = 1
    assert jarvis.trust(node)["status"] == "established"


# --------------------------------------------------------------------------
# 6) 교정은 두 속도로 — 사람 손은 즉시, 에이전트의 재시도는 확인 뒤
# --------------------------------------------------------------------------
def test_a_distilled_retry_challenges_then_supersedes_only_when_confirmed(jarvis):
    jarvis.init_project("app", template="coding")
    jarvis.config.learn.merge_threshold = 0.99
    old = jarvis.remember("app", "commands", "테스트 실행", "npm test 로 테스트를 돌린다")
    tid = _turn(jarvis, "테스트 실행 어떻게 해?", answer="npm test 를 실행하세요.")
    jarvis.score(tid, value=0.0, comment="없는 스크립트", uris=[str(old)])

    # 에이전트가 손이 아니라 학습(commit)으로 다른 답을 냈다 → 임시 도전
    new = _learn(jarvis, "commands", "테스트 도구", "이 저장소 테스트는 pytest -q 로 돌린다")
    res = jarvis._reconcile_correction("app", [str(new)], immediate=False, trace_id=tid)
    assert res and res[0]["mode"] == "pending"
    old_node = jarvis.store.read_node(old)
    assert old_node is not None  # 아직 라이브
    assert old_node.extra["challenged_by"] == str(new)
    assert jarvis.trust(old_node)["status"] == "contested"
    assert str(old) in jarvis.store.read_node(new).extra["corrects"]

    # 새 지식이 좋은 결과를 내면 그때 대체가 완결된다
    tid2 = _turn(jarvis, "테스트 도구 뭐 써?", answer="pytest -q 입니다.")
    jarvis.score(tid2, value=1.0, comment="맞았다", uris=[str(new)])
    assert jarvis.store.read_node(old) is None
    archived = jarvis.store.read_node(str(old).replace("/memories/", "/_archive/memories/"))
    assert archived is not None and archived.extra["superseded_by"] == str(new)
    assert jarvis.store.read_node(new).confidence >= jarvis.config.learn.solid_confidence


def test_the_old_belief_is_vindicated_if_it_works_again(jarvis):
    jarvis.init_project("app", template="coding")
    jarvis.config.learn.merge_threshold = 0.99
    old = jarvis.remember("app", "commands", "테스트 실행", "npm test 로 테스트를 돌린다")
    tid = _turn(jarvis, "테스트 실행 어떻게 해?", answer="npm test 를 실행하세요.")
    jarvis.score(tid, value=0.0, comment="이번엔 안 됐다", uris=[str(old)])
    new = _learn(jarvis, "commands", "테스트 도구", "테스트는 pytest -q 로 돌린다")
    jarvis._reconcile_correction("app", [str(new)], immediate=False, trace_id=tid)
    assert jarvis.store.read_node(old).extra.get("challenged_by")

    # 옛 지식이 다시 좋은 결과를 내면 도전이 철회된다
    tid2 = _turn(jarvis, "테스트 실행 다시 알려줘", answer="npm test 입니다.")
    jarvis.score(tid2, value=1.0, comment="됐다", uris=[str(old)])
    old_node = jarvis.store.read_node(old)
    assert old_node is not None and not old_node.extra.get("challenged_by")
    assert any(e["kind"] == "vindicated" for e in old_node.extra["evidence"])
    assert jarvis.store.read_node(new) is not None  # 새 것도 남는다 (경쟁 기록)


def test_blame_lookup_stays_inside_the_sitting(jarvis):
    """팀 프로젝트: 동료 세션의 새 지식이 내 세션에서 blame 된 지식의 교정으로
    읽히면 안 된다."""
    jarvis.init_project("app", template="coding")
    jarvis.config.learn.merge_threshold = 0.99
    old = jarvis.remember("app", "commands", "테스트 실행", "npm test 로 돌린다")
    tid_a = _turn(jarvis, "테스트 실행 어떻게 해?", answer="npm test", sid="alice")
    jarvis.score(tid_a, value=0.0, uris=[str(old)])
    tid_b = _turn(jarvis, "테스트 도구 정리해줘", answer="pytest 씁니다", sid="bob")
    new = _learn(jarvis, "commands", "테스트 도구", "테스트는 pytest -q 로 돌린다")
    assert jarvis._reconcile_correction("app", [str(new)], immediate=False, trace_id=tid_b) == []
    assert jarvis._reconcile_correction("app", [str(new)], immediate=False, trace_id=tid_a)


# --------------------------------------------------------------------------
# 7) 답 캐시도 진화 구조 안에 있다
# --------------------------------------------------------------------------
def test_a_bad_verdict_evicts_the_cached_answer(jarvis):
    jarvis.init_project("app", template="coding")
    q = "린트 검사 어떻게 해?"
    p = jarvis.prepare("app", q, session_id="s1", agent="a", max_tier=0)
    jarvis.commit("app", q, "eslint . 를 실행하세요.", trace_id=p.trace_id, agent="a")
    assert any(c["question"] == q for c in jarvis.cache_list("app"))
    jarvis.score(p.trace_id, value=0.0, comment="틀렸다")
    assert not any(c["question"] == q for c in jarvis.cache_list("app"))


def test_a_complaint_evicts_the_cached_answer_implicitly(jarvis):
    jarvis.init_project("app", template="coding")
    q = "린트 검사 어떻게 해?"
    _turn(jarvis, q, answer="eslint . 를 실행하세요.")
    # _turn 은 use_cache=False 로 prepare 하지만 commit 은 캐시에 넣는다
    assert any(c["question"] == q for c in jarvis.cache_list("app"))
    jarvis.prepare("app", "린트 검사 여전히 안 되는데", session_id="s1", agent="a", max_tier=0)
    assert not any(c["question"] == q for c in jarvis.cache_list("app"))


# --------------------------------------------------------------------------
# 8) 규칙 기반 증류의 재시도는 제목이 깨끗하고, 검증 전으로 남는다
# --------------------------------------------------------------------------
def test_a_retry_after_a_complaint_is_filed_as_unverified(jarvis):
    from jarvis.learn import _title_from

    assert _title_from("린트 검사 여전히 안 되는데") == "린트 검사"
    assert _title_from("테스트 실행 어떻게 해?") == "테스트 실행"
    assert _title_from("README 의 오타를 수정해줘") == "README 의 오타를 수정"

    jarvis.init_project("app", template="coding")
    jarvis.config.learn.merge_threshold = 0.99
    old = jarvis.remember("app", "commands", "린트 검사", "eslint . 로 검사한다")
    _turn(jarvis, "린트 검사 어떻게 해?", answer="eslint . 를 실행하세요.")
    _turn(jarvis, "린트 검사 여전히 안 되는데", answer="이걸 시도해 보세요:\n```\nnpx biome check .\n```")
    mems = {m["title"]: m for m in jarvis.memories("app", with_trust=True)}
    retry = mems.get("린트 검사 재시도")
    assert retry is not None and retry["confidence"] < 0.5
    assert retry["trust"]["status"] != "established"
    # 재시도가 blame 된 원본에 합쳐져 그 신뢰를 올리지 않았다
    assert jarvis.store.read_node(old).confidence < 0.8
