from jarvis.profiles import MemoryProfile, builtin, templates


def test_all_templates_have_categories():
    for name in templates():
        p = builtin(name)
        assert p.categories, name
        assert p.template == name
        assert p.fallback_category() in p.category_names()


def test_builtin_returns_independent_copies():
    a, b = builtin("coding"), builtin("coding")
    a.categories[0].keep = 999
    assert b.categories[0].keep != 999


def test_fallback_defaults_to_lowest_priority():
    p = builtin("default")
    p.fallback = ""
    lowest = min(p.categories, key=lambda c: c.priority).name
    assert p.fallback_category() == lowest


def test_explicit_fallback_wins():
    p = builtin("coding")
    assert p.fallback == "cases"
    assert p.fallback_category() == "cases"


def test_roundtrip_through_dict(tmp_path):
    p = builtin("research")
    p.budget = {"total": 1234}
    back = MemoryProfile.from_dict(p.to_dict())
    assert back.category_names() == p.category_names()
    assert back.budget == {"total": 1234}


def test_save_and_load(tmp_path):
    p = builtin("writing")
    p.save(tmp_path)
    loaded = MemoryProfile.load(tmp_path)
    assert loaded.template == "writing"
    assert "voice" in loaded.category_names()


def test_load_missing_falls_back_to_template(tmp_path):
    loaded = MemoryProfile.load(tmp_path / "nope", template="ops")
    assert loaded.template == "ops"


def test_templates_differ_by_domain():
    """The whole premise: different projects need different memory kinds."""
    coding = set(builtin("coding").category_names())
    research = set(builtin("research").category_names())
    writing = set(builtin("writing").category_names())
    assert coding != research != writing
    assert "commands" in coding and "commands" not in research
    assert "findings" in research and "findings" not in coding
    assert "voice" in writing and "voice" not in coding
