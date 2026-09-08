"""가입·로그인·권한 (사람별 분리)."""
from conftest import create_key, create_project


def test_signup_login_logout(client):
    c, _ = client
    assert c.get("/signup").status_code == 200

    # 가입
    r = c.post("/signup", data={"name": "홍길동", "email": "hong@test.com", "password": "secret12"}, follow_redirects=False)
    assert r.status_code == 303
    assert "viking_session" in r.headers.get("set-cookie", "")

    # 중복 이메일 거부
    r = c.post("/signup", data={"name": "홍길동2", "email": "hong@test.com", "password": "secret12"})
    assert "이미 가입" in r.text

    # 로그아웃
    r = c.post("/logout", follow_redirects=False)
    assert r.status_code == 303

    # 대시보드는 로그인 필요
    assert c.get("/", follow_redirects=False).status_code == 303

    # 잘못된 비밀번호
    r = c.post("/login", data={"email": "hong@test.com", "password": "wrong!"})
    assert "틀렸습니다" in r.text

    # 로그인
    assert c.post("/login", data={"email": "hong@test.com", "password": "secret12"}, follow_redirects=False).status_code == 303
    assert c.get("/").status_code == 200


def test_first_user_is_admin_second_is_not(client, user1, user2):
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    assert c.get("/admin").status_code == 200
    c.post("/logout")
    c.post("/login", data={"email": "b@test.com", "password": "password2"})
    assert c.get("/admin").status_code == 403


def test_project_isolation(client, user1, user2):
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "비밀 프로젝트")

    # B 는 A 의 프로젝트를 못 봄
    c.post("/logout")
    c.post("/login", data={"email": "b@test.com", "password": "password2"})
    assert c.get(f"/projects/{slug}").status_code == 403
    assert c.get(f"/projects/{slug}/connect").status_code == 403
    assert c.get(f"/projects/{slug}/export.md").status_code == 403

    # A 는 자기 것 OK
    c.post("/logout")
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    assert c.get(f"/projects/{slug}").status_code == 200


def test_invalid_api_key(client, user1):
    c, _ = client
    c.post("/login", data={"email": "a@test.com", "password": "password1"})
    slug = create_project(c, "키 프로젝트")
    r = c.get("/api/v1/projects/" + slug + "/brief")
    assert r.status_code == 401
    r = c.get("/api/v1/projects/" + slug + "/brief",
              headers={"Authorization": "Bearer jv_wrongkey"})
    assert r.status_code == 401