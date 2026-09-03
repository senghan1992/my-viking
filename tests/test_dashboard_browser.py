"""대시보드를 진짜 브라우저로 걸어 본다 — 초보(키 없음) · 관리자(전체 접근) ·
스코프 사용자(한 프로젝트) 세 사람의 연결/키 관리 경로.

`DASHBOARD_HTML` 은 빌드 단계 없는 한 덩어리 스크립트라 문법 검사만으로는 런타임
오류(잘못된 id, 403 으로 죽는 탭)를 못 잡는다. 이 테스트는 콘솔 오류 하나라도 나면
실패한다. playwright 나 크로미움이 없는 환경에서는 건너뛴다.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest

pw = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from jarvis.server import create_app  # noqa: E402

PORT = 8798


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    app = create_app(home=str(tmp_path_factory.mktemp("home")))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}"
    for _ in range(50):
        try:
            urllib.request.urlopen(url + "/health").read()
            break
        except Exception:
            time.sleep(0.1)
    yield url
    srv.should_exit = True


def _api(base, method, path, body=None, key=None):
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = "Bearer " + key
    req = urllib.request.Request(
        base + path, method=method,
        data=json.dumps(body).encode() if body is not None else None, headers=headers,
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b"null")


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # 브라우저 바이너리 없음
            pytest.skip(f"chromium 없음: {e}")
        yield b
        b.close()


def test_three_personas_walk_the_connection_gui(base, browser):
    _api(base, "POST", "/projects", {"project": "backend"})
    _api(base, "POST", "/projects", {"project": "blog"})
    admin_key = _api(base, "POST", "/keys", {"name": "admin"})["key"]  # 인증 켜짐

    errors: list[str] = []
    pg = browser.new_page()
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))
    pg.on("dialog", lambda d: d.accept())
    W = 700  # ms — 각 상호작용 뒤 fetch 가 끝나길 기다리는 여유

    # ---- 초보: 키 없이 들어오면 날 401 대신 안내 패널 ----
    pg.goto(base + "/")
    pg.wait_for_timeout(W)
    txt = pg.inner_text("#error")
    assert "API 키가 필요합니다" in txt and "jv key create" in txt and "401" not in txt

    # ---- 관리자: 헤더 칩, 저장소 연결 표, 키 발급→인계, 폐기 ----
    pg.fill("#key", admin_key)
    pg.dispatch_event("#key", "change")
    pg.wait_for_timeout(W)
    assert "전체 접근" in pg.inner_text("#me")
    _api(base, "POST", "/aliases", {"alias": "github.com/me/backend", "project": "backend"}, admin_key)
    pg.click("#refresh")
    pg.wait_for_timeout(W)
    assert not pg.is_hidden("#aliases-section")
    assert "github.com/me/backend" in pg.inner_text("#aliases-table")

    pg.click('nav button[data-tab="connections"]')
    pg.wait_for_timeout(W)
    assert pg.is_visible("#nk-create")
    pg.fill("#nk-name", "지훈-노트북")
    pg.select_option("#nk-projects", ["backend"])
    pg.click("#nk-create")
    pg.wait_for_timeout(W)
    assert "다시 볼 수 없습니다" in pg.inner_text("#nk-result")
    scoped_key = pg.inner_text("#nk-result pre").strip()
    tbl = pg.inner_text("#keys-table")
    assert "지훈-노트북" in tbl and "미사용" in tbl and "backend" in tbl
    pg.click("#nk-conn-go")  # 방금 발급한 키로 연결 설정
    pg.wait_for_timeout(W)
    body = pg.inner_text("#conn-body")
    assert "전용 키" in body and scoped_key in body and "실제 키가 들어 있습니다" in body
    pg.click("#conn-close")

    pg.click('nav button[data-tab="projects"]')
    pg.wait_for_timeout(W)
    pg.click('.proj[data-p="blog"]')
    pg.wait_for_timeout(W)
    assert "이 브라우저의 키" in pg.inner_text("#conn-body")  # 관리자 키 경고
    pg.fill("#cn-key-name", "민수-데스크톱")
    pg.click("#cn-key-mint")
    pg.wait_for_timeout(W)
    body = pg.inner_text("#conn-body")
    assert "전용 키" in body and "이 브라우저의 키" not in body
    pg.click("#conn-close")
    pg.click('.proj[data-p="backend"]')
    pg.wait_for_timeout(W)
    pg.click("#conn-body [data-unbind]")  # 별칭 해제 (confirm 자동 수락)
    pg.wait_for_timeout(W)
    assert "아직 등록된 remote 가 없습니다" in pg.inner_text("#conn-body")
    pg.click("#conn-close")

    pg.click('nav button[data-tab="connections"]')
    pg.wait_for_timeout(W)
    pg.locator("#keys-table tr", has_text="민수-데스크톱").locator("[data-revoke]").click()
    pg.wait_for_timeout(W)
    assert "폐기됨" in pg.locator("#keys-table tr", has_text="민수-데스크톱").inner_text()

    # ---- 스코프 사용자: 자기 울타리만 보이고, 어느 탭도 죽지 않는다 ----
    pg.fill("#key", scoped_key)
    pg.dispatch_event("#key", "change")
    pg.wait_for_timeout(W)
    assert "지훈-노트북" in pg.inner_text("#me") and "backend" in pg.inner_text("#me")
    assert "관리자" in pg.inner_text("#keys-panel")
    pg.click('nav button[data-tab="projects"]')
    pg.wait_for_timeout(W)
    assert pg.is_hidden("#aliases-section")
    cards = pg.inner_text("#projects")
    assert "backend" in cards and "blog" not in cards
    pg.click('nav button[data-tab="activity"]')
    pg.wait_for_timeout(W + 300)
    assert "불러오지 못했습니다" not in pg.inner_text("#error")
    assert pg.is_visible("#cards")

    hard = [e for e in errors if "Failed to load resource" not in e]  # 의도된 4xx 응답 로그 제외
    assert not hard, hard
