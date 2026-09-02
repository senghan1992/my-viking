from jarvis.models import Node, Uri


def test_init_creates_category_dirs(coding):
    base = coding.store.scope_dir("app")
    assert (base / "profile.yaml").exists()
    for cat in coding.profile("app").category_names():
        assert (base / "memories" / cat).is_dir()


def test_write_read_roundtrip(coding):
    uri = coding.remember("app", "commands", "빌드", "make build 로 빌드한다")
    node = coding.store.read_node(uri)
    assert node is not None
    assert node.category == "commands"
    assert "make build" in node.tier(2)
    # File is the source of truth and is human-readable.
    path = coding.store.path_for(uri)
    assert path.exists()
    assert "make build" in path.read_text(encoding="utf-8")


def test_read_missing_returns_none(coding):
    assert coding.store.read_node("jarvis://projects/app/memories/commands/nope") is None


def test_ls_shows_dirs_and_nodes(coding):
    coding.remember("app", "commands", "빌드", "make build")
    listing = coding.store.ls("jarvis://projects/app/memories")
    names = [d["name"] for d in listing["dirs"]]
    assert "commands" in names
    inner = coding.store.ls("jarvis://projects/app/memories/commands")
    assert [n["name"] for n in inner["nodes"]] == ["빌드"]
    assert inner["nodes"][0]["tokens"]["l0"] > 0


def test_tree_depth_is_respected(coding):
    coding.remember("app", "commands", "빌드", "make build")
    shallow = coding.store.tree("jarvis://projects/app", depth=1)
    memories = [d for d in shallow["dirs"] if d["name"] == "memories"][0]
    assert memories["dirs"] and memories["dirs"][0].get("truncated")


def test_reindex_rebuilds_from_files(coding):
    coding.remember("app", "commands", "빌드", "make build")
    coding.store.db.clear_all()
    assert coding.store.ls("jarvis://projects/app/memories")["dirs"] == []
    counts = coding.store.reindex("app")
    assert counts["nodes"] >= 1
    assert coding.store.ls("jarvis://projects/app/memories")["dirs"]


def test_archive_is_reversible_move_not_delete(coding):
    uri = coding.remember("app", "commands", "빌드", "make build")
    target = coding.store.archive_node(uri, reason="테스트")
    assert target is not None
    assert coding.store.read_node(uri) is None
    archived = coding.store.read_node(target)
    assert archived is not None
    assert archived.extra["archive_reason"] == "테스트"
    assert "make build" in archived.tier(2)


def test_grep_finds_literal_text(coding):
    coding.remember("app", "commands", "빌드", "make build 로 빌드한다")
    hits = coding.store.grep("make build")
    assert hits and hits[0]["line"] > 0


def test_touch_increments_hits(coding):
    uri = coding.remember("app", "commands", "빌드", "make build")
    coding.store.touch_node(uri, reinforce=0.1)
    node = coding.store.read_node(uri)
    assert node.hits == 1
    assert node.confidence > 0.8


def test_delete_project_removes_files_and_index(jarvis):
    jarvis.init_project("temp")
    jarvis.remember("temp", "facts", "x", "y")
    assert jarvis.delete_project("temp")
    assert "temp" not in jarvis.store.projects()
    assert jarvis.store.db.query("SELECT uri FROM nodes WHERE scope=?", ("temp",)) == []


def test_projects_are_isolated(jarvis):
    jarvis.init_project("a", template="coding")
    jarvis.init_project("b", template="research")
    jarvis.remember("a", "commands", "빌드", "make build")
    jarvis.remember("b", "findings", "발견", "출처가 있는 사실")
    assert [m["title"] for m in jarvis.memories("a")] == ["빌드"]
    assert [m["title"] for m in jarvis.memories("b")] == ["발견"]


def test_tier_costs_are_monotonic(coding):
    """L0 <= L1 <= L2 must hold, or 'saving' can come out negative."""
    from jarvis.tokens import estimate_tokens

    uri = coding.remember(
        "app",
        "commands",
        "테스트 실행",
        "pytest -q 로 전체 테스트를 돌린다",
        detail="루트에서 실행. PYTHONPATH=src 가 필요하다.",
    )
    node = coding.store.read_node(uri)
    t0, t1, t2 = (estimate_tokens(node.tier(i)) for i in range(3))
    assert t0 <= t1 <= t2


def test_overview_longer_than_body_is_collapsed(coding):
    from jarvis.models import KIND_MEMORY

    node = Node(
        uri=Uri.parse("jarvis://projects/app/memories/commands/이상한노드"),
        kind=KIND_MEMORY,
        title="이상한 노드",
        category="commands",
        abstract="요약",
        overview="개요가 본문보다 훨씬 길다 " * 20,
        body="짧은 본문",
    )
    coding.store.write_node(node, regenerate_tiers=False)
    stored = coding.store.read_node(node.uri)
    assert stored.overview == "짧은 본문"


def test_manual_memory_body_contains_statement(coding):
    uri = coding.remember("app", "commands", "빌드", "make build 로 빌드한다", detail="루트에서")
    body = coding.store.read_node(uri).tier(2)
    assert "make build" in body and "루트에서" in body


def test_touch_persists_counters_to_the_file_not_just_the_index(coding):
    """Reinforcement patches the frontmatter directly for speed. It must still
    survive a rebuild from disk, or the index becomes the real source of truth."""
    uri = coding.remember("app", "commands", "빌드", "make build 로 빌드한다")
    coding.store.touch_nodes([uri], reinforce=0.05)
    coding.store.touch_nodes([uri], reinforce=0.05)

    coding.store.reindex("app")
    node = coding.store.read_node(uri)
    assert node.hits == 2
    assert node.confidence > 0.8
    assert node.last_used
    # The body must be untouched by the surgical edit.
    assert "make build 로 빌드한다" in node.tier(2)


def test_touch_does_not_corrupt_a_node_with_rich_frontmatter(coding):
    uri = coding.remember(
        "app", "commands", "배포", "make deploy", detail="scripts/deploy.sh"
    )
    node = coding.store.read_node(uri)
    node.tags = ["배포", "위험"]
    node.sources = ["jarvis://projects/app/sessions/2026-01-01/x"]
    node.extra = {"conflict": {"kind": "negation", "existing": "a", "incoming": "b"}}
    coding.store.write_node(node, regenerate_tiers=False)

    coding.store.touch_nodes([uri], reinforce=0.1)
    back = coding.store.read_node(uri)
    assert back.tags == ["배포", "위험"]
    assert back.sources == node.sources
    assert back.extra["conflict"]["kind"] == "negation"
    assert back.hits == 1


def test_touch_nodes_ignores_unknown_uris(coding):
    coding.remember("app", "commands", "빌드", "make build")
    n = coding.store.touch_nodes(
        [
            "jarvis://projects/app/memories/commands/빌드",
            "jarvis://projects/app/memories/commands/없음",
        ],
        reinforce=0.05,
    )
    assert n == 1


def test_patch_frontmatter_leaves_a_body_dash_line_alone():
    from jarvis.store import _patch_frontmatter

    text = "---\nhits: 0\ntitle: x\n---\n\n## Details\n- 항목\n--- 본문 속 구분선\n"
    out = _patch_frontmatter(text, {"hits": "5"})
    assert "hits: 5" in out
    assert "--- 본문 속 구분선" in out
    assert out.count("## Details") == 1
