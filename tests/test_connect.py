"""에이전트 연결 페이지 — 툴 선택 탭 + 단계별 가이드 렌더."""
from conftest import create_project


def test_connect_page_tabs(client, user1):
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "연결 테스트")
    html = c.get(f"/projects/{slug}/connect").text

    # 툴 선택 탭 4종
    for tab in ["claude", "pi", "mcp", "shell"]:
        assert f'data-tab="{tab}"' in html
    # 각 페인 존재, 기본은 claude (나머지 hidden)
    assert 'data-pane-id="claude"' in html
    assert 'data-pane-id="pi" hidden' in html
    assert 'data-pane-id="mcp" hidden' in html
    assert 'data-pane-id="shell" hidden' in html

    # 핵심 명령 안내
    assert "pip install git+https://github.com/senghan1992/my-viking.git" in html
    assert "jv hook install" in html
    assert "jv pi install" in html
    assert "viking_brief" in html
    assert "MYVIKING_URL" in html
    assert "jv brief" in html

    # 키 발급 섹션
    assert "연결 키" in html and "새 키 발급" in html