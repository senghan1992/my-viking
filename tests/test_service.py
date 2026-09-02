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
