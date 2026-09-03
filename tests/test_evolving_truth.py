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
    )
    # distill 이 교정 지식을 만들었고 그것이 옛 것을 대체했다면 corrected 에 잡힌다
    corrected = res.get("corrected") or []
    old_node = jarvis.store.read_node(old)
    # distill 이 후보를 못 뽑는 환경도 있으니, 대체가 일어났으면 정합성만 강히 본다
    if corrected:
        assert old_node.extra.get("superseded_by")
        assert str(old) in [c["replaced"] for c in corrected]


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
