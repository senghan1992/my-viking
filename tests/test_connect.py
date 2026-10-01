"""연결 탭 — 한 판에서 끝내는 연결(명령판·키 보관함) + 발급 후에도 다시 복사."""
import re

from conftest import create_key, create_project

CMD = re.compile(
    r'id="connect-cmd"[^>]*>(?P<pre>curl -fsSL [^<]*install\.sh \| bash -s -- --url [^<]*?)'
    r'<span class="k" id="connect-key">(?P<key>jv_[0-9a-f]{20,})</span>'
    r'(?P<post> --project [^<]*)</pre>'
)


def _key_of(html: str) -> str:
    m = CMD.search(html)
    assert m, "명령판에 복사 가능한 키가 박혀 있어야 합니다"
    return m.group("key")


def _plain(html: str) -> str:
    """태그를 벗긴 텍스트 — 사용자가 실제로 복사하게 되는 문자열."""
    return re.sub(r"<[^>]+>", "", html)


def test_connect_page_is_one_copy_ready_command(client, user1):
    """연결 탭은 설명서가 아니다 — 복사하면 그대로 실행되는 명령 한 줄이 중심이다."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "연결 테스트")
    html = c.get(f"/projects/{slug}/connect").text

    m = CMD.search(html)
    assert m, "연결 명령판이 없습니다"
    assert m.group("pre").startswith("curl -fsSL ")
    assert m.group("post") == f" --project {slug}"
    assert "jv_..." not in html                      # placesholder 로 복사되는 일 없다

    # 복사는 화면의 텍스트를 근원으로 삼는다 (버튼 속성에 명령을 중복 저장하지 않음)
    assert html.count('data-copy-src="#connect-cmd"') == 2      # 판 자체 + 명령 복사 버튼
    assert 'data-copy-src="#connect-key"' in html               # 키만 복사
    assert 'data-copy="' not in html
    # 첫 화면은 단순하게 — 나머지 에이전트 안내는 접혀 있다
    assert '<details class="more">' in html
    assert 'data-tab="mcp"' in html and 'data-tab="shell"' in html
    assert 'data-tab="pi"' not in html               # pi 는 기본 경로: 접지 않고 명령 아래에
    assert "/myviking use" in html and "jv secretary once" in html


def test_issued_key_is_still_copyable_afterwards(client, user1):
    """발급 직후가 아니어도 언제든 다시 복사할 수 있다 — 연결 탭이 곧 키 보관함."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "재복사 테스트")
    raw = create_key(c, slug, "내 노트북")

    for _ in range(2):                               # 화면을 다시 열어도 그대로다
        html = c.get(f"/projects/{slug}/connect").text
        assert raw in html
        assert f"--key {raw} --project {slug}" in _plain(html)
        assert "평문 미보관" not in html

    # 없는 키 id 를 물어도 가장 최근 유효 키로 되돌아온다
    assert _key_of(c.get(f"/projects/{slug}/connect?key=9999").text) == raw
    # 발급 화면(key_reveal) 은 사라졌다 — POST 후 연결 탭으로 되돌아온다
    r = c.post(f"/projects/{slug}/keys", data={"name": "다시"}, follow_redirects=False)
    assert r.status_code == 303 and "/connect?key=" in r.headers["location"]


def test_keyring_lists_keys_and_switches_active_key(client, user1):
    """키가 여러 개면 명령에 쓸 키를 고른다. 평문은 고른 키 하나만 명령판에 나온다."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "keyring")            # 기본 연결 키가 자동 발급됨
    first = _key_of(c.get(f"/projects/{slug}/connect").text)
    second = create_key(c, slug, "회사 PC")
    assert first != second

    # 기본은 가장 최근 키
    assert _key_of(c.get(f"/projects/{slug}/connect").text) == second
    # first 로 전환하면 명령판의 키가 바뀐다
    html = c.get(f"/projects/{slug}/connect?key={_id_of(c, slug, '기본 연결')}").text
    assert _key_of(html) == first
    assert "이 키로 연결" in html                    # 나머지 키는 링크로 전환
    plate = _plain(html[html.index('id="connect-cmd"'):html.index("</pre>", html.index('id="connect-cmd"'))])
    assert plate.count("jv_") == 1                   # 명령판에는 고른 키 하나만
    assert second not in plate


def _id_of(c, slug, name) -> int:
    from app import db

    return db.one(
        "SELECT k.id FROM api_keys k JOIN projects p ON p.id=k.project_id"
        " WHERE p.slug=? AND k.name=?", (slug, name))["id"]


def test_revoke_leaves_nothing_to_copy(client, user1):
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "폐기 후")
    html = c.get(f"/projects/{slug}/connect").text
    kid = re.search(r"/keys/(\d+)/revoke", html).group(1)
    c.post(f"/projects/{slug}/keys/{kid}/revoke")
    html = c.get(f"/projects/{slug}/connect").text
    assert 'id="connect-cmd"' not in html             # 복사할 명령이 사라진다
    assert "아직 복사할 명령이 없습니다" in html
    assert "새 키 발급" in html                        # 회복로도 함께 보인다


def test_legacy_key_without_sealed_plain_text_offers_reissue(client, user1):
    """봉인 평문이 없는 구버전 키 — 재발급으로 회복한다."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "구버전 키")
    from app import db

    pid = db.one("SELECT id FROM projects WHERE slug=?", (slug,))["id"]
    db.execute(
        "INSERT INTO api_keys(project_id, user_id, name, key_hash, key_prefix, key_secret, created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (pid, 1, "옛날 키", "deadbeef", "jv_oldpr", None, db.now()),
    )
    html = c.get(f"/projects/{slug}/connect").text
    assert "평문 미보관" in html and "재발급" in html

    kid = re.search(r"/keys/(\d+)/rotate", html).group(1)
    r = c.post(f"/projects/{slug}/keys/{kid}/rotate")
    assert r.status_code == 200 and _key_of(r.text)
    assert db.one("SELECT revoked_at FROM api_keys WHERE name='옛날 키'")["revoked_at"]


def test_install_script_endpoint(client, user1):
    """GET /install.sh — 키 없이 스크립트만 내려주며 jv connect 를 호출한다."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    r = c.get("/install.sh")
    assert r.status_code == 200
    assert "text/x-shellscript" in r.headers["Content-Type"]
    assert "jv connect" in r.text and "pip install" in r.text
    assert "MYVIKING_KEY" not in r.text              # 키가 서버 URL 에 실리지 않는다


def test_project_creation_prepares_the_command(client, user1):
    """서가 만들기 = 키까지 자동 발급 → 곧바로 연결 탭 (가입→복사 단계 수를 줄인다)."""
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    r = c.post("/projects", data={"name": "자동 키", "description": ""}, follow_redirects=False)
    assert r.status_code == 303 and "/connect?key=" in r.headers["location"]
    html = c.get(r.headers["location"]).text
    assert _key_of(html) and "연결 명령이 준비됐습니다" in html


def test_connect_page_requires_login(client):
    c, _ = client
    assert c.get("/projects/whatever/connect", follow_redirects=False).status_code == 303
