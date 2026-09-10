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

    # 핵심 명령 안내 — 빠른 연결(install.sh) + 수동 경로
    assert "/install.sh" in html
    assert "curl -fsSL" in html
    assert "jv hook install" in html
    assert "viking_brief" in html
    assert "MYVIKING_URL" in html
    assert "jv brief" in html
    assert "pip install git+https://github.com/senghan1992/my-viking.git" in html

    # 키 발급 섹션
    assert "연결 키" in html and "새 키 발급" in html

def test_install_script_endpoint(client, user1):
    """GET /install.sh — 키 없이 스크립트만 내려주며 jv connect 를 호출한다."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    r = c.get("/install.sh")
    assert r.status_code == 200
    assert "text/x-shellscript" in r.headers["Content-Type"]
    text = r.text
    assert "jv connect" in text
    assert "pip install" in text
    assert "MYVIKING_KEY" not in text  # 키가 서버 URL 에 실리지 않는다


def test_key_reveal_has_baked_commands(client, user1):
    """키 발급 직후 화면 — 키가 이미 들어간 연결 명령과 탭이 나온다."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "키 화면 테스트")
    r = c.post(f"/projects/{slug}/keys", data={"name": "내 노트북"})
    html = r.text
    assert "자동 연결 (권장)" in html
    assert 'data-pane-id="quick"' in html
    assert 'data-pane-id="mcp"' in html
    # 발급 키가 명령에 박혀 있다 (jv_... 자리가 아니라 실제 키)
    assert "jv_..." not in html
    assert "curl -fsSL" in html and "/install.sh | bash -s -- --url" in html
    import re
    m = re.search(r"--key (jv_[0-9a-f]{20,})", html)
    assert m, "발급된 키가 연결 명령에 포함되어야 한다"
    assert "MYVIKING_KEY" in html  # MCP JSON 도 키 포함
