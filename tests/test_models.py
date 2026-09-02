import pytest

from jarvis.models import Node, Uri, slugify


@pytest.mark.parametrize(
    "raw",
    [
        "jarvis://projects/app/memories/commands/test-run",
        "jarvis://global/memories/preferences/tone",
        "jarvis://projects/app",
        "jarvis://global",
    ],
)
def test_uri_roundtrip(raw):
    assert str(Uri.parse(raw)) == raw


def test_uri_bare_form_is_a_project():
    u = Uri.parse("app/memories/facts/x")
    assert u.scope == "app"
    assert u.parts == ("memories", "facts", "x")


def test_uri_rejects_empty():
    with pytest.raises(ValueError):
        Uri.parse("")


def test_uri_hierarchy():
    u = Uri.parse("jarvis://projects/app/memories/commands/test")
    assert u.kind_dir == "memories"
    assert u.name == "test"
    assert u.parent == Uri.parse("jarvis://projects/app/memories/commands")
    assert len(u.ancestors()) == 3
    assert u.is_under(Uri.parse("jarvis://projects/app/memories"))
    assert not u.is_under(Uri.parse("jarvis://global/memories"))


def test_slugify_keeps_korean_and_drops_unsafe():
    assert slugify("테스트 실행 방법!") == "테스트-실행-방법"
    assert slugify("../../etc/passwd") == "etc-passwd"
    assert slugify("") == "item"


def test_node_markdown_roundtrip():
    node = Node(
        uri=Uri.parse("jarvis://projects/app/memories/facts/db"),
        title="DB 선택",
        category="facts",
        abstract="Postgres 를 쓴다",
        overview="개요 본문",
        body="세부 근거\n두 번째 줄",
        tags=["db", "결정"],
        confidence=0.75,
        sources=["jarvis://projects/app/sessions/2026-01-01/x"],
    )
    back = Node.from_markdown(node.to_markdown())
    assert back.uri == node.uri
    assert back.title == node.title
    assert back.abstract == node.abstract
    assert back.overview == node.overview
    assert back.body == node.body
    assert back.tags == node.tags
    assert back.confidence == pytest.approx(0.75)
    assert back.sources == node.sources


def test_node_tier_falls_back_upward():
    node = Node(uri=Uri.parse("jarvis://global/memories/x/y"), abstract="요약만")
    assert node.tier(0) == "요약만"
    assert node.tier(1) == "요약만"
    assert node.tier(2) == "요약만"
