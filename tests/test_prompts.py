import pytest


def test_save_extracts_variables(coding):
    node = coding.save_prompt("app", "review", "리뷰해줘: {{code}} / 관점 {{focus}}")
    assert node.extra["vars"] == ["code", "focus"]
    assert node.extra["version"] == 1


def test_render_substitutes_and_reports_missing(coding):
    coding.save_prompt("app", "review", "코드: {{code}} 관점: {{focus}}")
    res = coding.render_prompt("app", "review", {"code": "print(1)"})
    assert "print(1)" in res.text
    assert res.missing == ["focus"]
    # An unfilled variable stays visible rather than becoming an empty hole.
    assert "{{focus}}" in res.text


def test_strict_render_raises_on_missing(coding):
    coding.save_prompt("app", "review", "{{a}}")
    with pytest.raises(ValueError):
        coding.render_prompt("app", "review", {}, strict=True)


def test_render_unknown_prompt_raises(coding):
    with pytest.raises(KeyError):
        coding.render_prompt("app", "nope", {})


def test_versioning_snapshots_previous_text(coding):
    coding.save_prompt("app", "review", "v1 본문")
    coding.save_prompt("app", "review", "v2 본문")
    versions = coding.prompt_versions("app", "review")
    assert [v["version"] for v in versions] == ["v1"]
    assert coding.get_prompt("app", "review").body == "v2 본문"


def test_no_snapshot_when_text_unchanged(coding):
    coding.save_prompt("app", "review", "같은 본문")
    coding.save_prompt("app", "review", "같은 본문")
    assert coding.prompt_versions("app", "review") == []


def test_rollback_restores_old_text(coding):
    coding.save_prompt("app", "review", "원본")
    coding.save_prompt("app", "review", "실수로 망친 본문")
    coding.rollback_prompt("app", "review", "1")
    assert coding.get_prompt("app", "review").body == "원본"


def test_render_records_usage(coding):
    coding.save_prompt("app", "review", "{{x}}")
    coding.render_prompt("app", "review", {"x": "1"})
    coding.render_prompt("app", "review", {"x": "2"})
    listed = [p for p in coding.list_prompts("app") if p["name"] == "review"][0]
    assert listed["uses"] == 2


def test_versions_hidden_from_list(coding):
    coding.save_prompt("app", "review", "a")
    coding.save_prompt("app", "review", "b")
    names = [p["name"] for p in coding.list_prompts("app")]
    assert names.count("review") == 1
    assert not any(n.startswith("v") for n in names)


def test_global_prompt_is_visible_to_projects(jarvis):
    jarvis.init_project("app")
    jarvis.save_prompt("global", "tone", "항상 간결하게 답해줘")
    res = jarvis.render_prompt("app", "tone", {})
    assert "간결하게" in res.text
