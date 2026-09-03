"""README 용 스크린샷을 만듭니다 — 목업이 아니라 **실제 서버와 실제 대시보드**를
시연 데이터로 띄워 찍습니다. UI 가 바뀌면 다시 돌려 이미지를 갱신하세요.

    pip install playwright uvicorn && playwright install chromium
    PYTHONPATH=src python tools/screenshots.py            # docs/img/*.png

격리된 임시 홈을 쓰므로 실제 데이터에 영향이 없습니다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from jarvis.server import create_app  # noqa: E402
from jarvis.service import Jarvis  # noqa: E402

PORT = 8797
AGENT = "claude-code@노트북"


# ---------------------------------------------------------------- 시연 데이터
def seed(home: str) -> None:
    j = Jarvis(home=home)
    j.init_project("backend", "coding", "결제 API 서버")
    j.init_project("frontend", "coding", "웹 대시보드 (Next.js)")
    j.bind_alias("github.com/acme/backend", "backend")
    j.bind_alias("github.com/acme/web", "frontend")

    mem = [
        ("backend", "commands", "테스트 실행", "pytest -q 로 전체를 돌린다. 통합 테스트는 -m integration.",
         "루트에서 실행. PYTHONPATH=src 필요"),
        ("backend", "commands", "로컬 기동", "docker compose up -d 후 make seed 로 샘플 데이터를 넣는다.", ""),
        ("backend", "architecture", "결제 모듈 경계", "payments/ 가 PG 연동 전부를 담당. api/ 는 검증만 하고 위임한다.", ""),
        ("backend", "conventions", "커밋 메시지", "커밋 메시지는 한글로, 첫 줄은 50자 이내.", ""),
        ("backend", "decisions", "큐는 SQS 유지", "Kafka 검토했으나 운영 부담으로 SQS 유지. 재검토 조건: 초당 1천 건 초과.", ""),
        ("backend", "pitfalls", "PG 재시도 금지", "결제 승인 실패 시 재시도하면 이중 결제 위험. 응답코드 0000 외에는 실패 확정.",
         "2026-08 실제 이중 결제 1건 발생 후 확정"),
        ("backend", "pitfalls", "웹훅 시각 스큐", "PG 웹훅 서명 검증은 5분 스큐를 허용해야 한다. 서버 시각이 어긋나면 전부 실패.", ""),
        ("frontend", "conventions", "컴포넌트 명명", "컴포넌트는 PascalCase, 훅은 use 접두. 파일명은 컴포넌트명과 동일.", ""),
        ("frontend", "architecture", "상태 관리", "서버 상태는 TanStack Query, UI 상태만 zustand. 전역 store 금지.", ""),
        ("frontend", "pitfalls", "SSR 에서 window 금지", "useEffect 밖에서 window/document 를 만지면 빌드가 깨진다.", ""),
    ]
    for project, cat, title, stmt, detail in mem:
        j.remember(project, cat, title, stmt, detail=detail)

    work = [
        ("backend", "환불 API 명세 정리해줘", "POST /refunds — 부분 환불 허용, 멱등키 필수. 명세 docs/refunds.md 로 정리.",
         ["docs/refunds.md", "api/refunds.py"], 1.0),
        ("backend", "웹훅 서명 검증이 계속 실패하는데 원인 봐줘", "서명 자체는 맞고 타임스탬프 검증에서 떨어짐. 스큐 허용치 0 → 300초.",
         ["api/webhooks.py"], 0.5),
        ("backend", "웹훅 서명 검증 여전히 실패함. 다시 봐줘", "컨테이너 시각이 2분 어긋나 있었음. NTP 동기화 + 300초 허용으로 해결.",
         ["api/webhooks.py", "deploy/compose.yml"], 1.0),
        ("backend", "결제 승인 실패하면 재시도해야 하나?", "아니요 — 이중 결제 위험(pitfalls 참고). 실패 확정 후 사용자에게 재시도 안내.",
         [], 1.0),
        ("backend", "결제 테이블에 인덱스 추가", "payments(order_id, created_at) 복합 인덱스. 마이그레이션 0042.",
         ["migrations/0042_payments_idx.py"], 1.0),
        ("backend", "정산 배치 느린데 프로파일링 해줘", "N+1 쿼리 — settlement/run.py 에서 건별 조회. selectinload 로 12분→40초.",
         ["settlement/run.py"], 1.0),
        ("frontend", "결제 내역 테이블 무한 스크롤로 바꿔줘", "useInfiniteQuery + IntersectionObserver. 페이지 크기 50.",
         ["components/PaymentsTable.tsx"], 1.0),
        ("frontend", "빌드가 window is not defined 로 깨짐", "ChartPanel 이 모듈 스코프에서 window 접근. dynamic import(ssr:false).",
         ["components/ChartPanel.tsx"], 1.0),
        ("frontend", "다크모드 토글 추가", "next-themes 도입, tailwind dark: 클래스. 시스템 설정 따라감.",
         ["app/layout.tsx", "components/ThemeToggle.tsx"], 0.5),
    ]
    for project, q, a, files, sc in work:
        p = j.prepare(project, q, agent=AGENT)
        j.commit(project, q, a, trace_id=p.trace_id, agent=AGENT, files=files,
                 tokens_in=1800, tokens_out=420, latency_ms=6400)
        j.score(p.trace_id, value=sc, comment="" if sc == 1.0 else "한 번 더 요청함")


# ---------------------------------------------------------------- 서버
def serve(home: str) -> uvicorn.Server:
    app = create_app(home=home)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health").read()
            return srv
        except Exception:
            time.sleep(0.1)
    raise SystemExit("서버가 뜨지 않았습니다")


def api(method: str, path: str, body=None, key: str = ""):
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = "Bearer " + key
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", method=method, headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b"null")


# ---------------------------------------------------------------- 정적 이미지
HERO = """<!doctype html><meta charset="utf-8">
<style>
  body{margin:0;background:#0f1412;font-family:-apple-system,"Segoe UI",Roboto,"Noto Sans KR",sans-serif;color:#e9e6df}
  .hero{width:1600px;height:450px;box-sizing:border-box;padding:64px 80px;
        background:radial-gradient(1200px 500px at 20% 0%,#1d3a2c 0%,#0f1412 60%)}
  h1{margin:0;font-size:64px;letter-spacing:-1px}
  h1 span{color:#6fbf92}
  p.tag{font-size:26px;margin:14px 0 40px;color:#c9c4b8;line-height:1.4}
  .flow{display:flex;align-items:center;gap:22px}
  .box{background:#171d1a;border:1px solid #2a3530;border-radius:14px;padding:22px 26px;min-width:290px}
  .box h3{margin:0 0 8px;font-size:20px;color:#fff}
  .box div{font-size:15.5px;color:#a9b3ad;line-height:1.55}
  .arrow{font-size:34px;color:#6fbf92}
  .mono{font-family:ui-monospace,Menlo,monospace;color:#6fbf92}
  .foot{margin-top:38px;font-size:16px;color:#8d978f}
  .foot b{color:#e9e6df}
</style>
<div class="hero">
  <h1>My<span>Viking</span></h1>
  <p class="tag">내 코딩 에이전트가 <b>자동으로 채우고 자동으로 참조하는</b>, 나만의 프로젝트 지식 창고</p>
  <div class="flow">
    <div class="box"><h3>코딩 에이전트</h3><div>Claude Code · Cursor · Codex<br>셸만 되는 에이전트까지</div></div>
    <div class="arrow">⇄</div>
    <div class="box"><h3>MyViking 서버</h3><div>내 머신의 Docker 하나<br><span class="mono">bash deploy/up.sh</span></div></div>
    <div class="arrow">⇄</div>
    <div class="box"><h3>프로젝트별 지식 창고</h3><div>규칙 · 명령 · 결정 · 함정 · 작업 이력<br>다음 요청이 직전 답변을 채점</div></div>
  </div>
  <div class="foot"><b>넣는 쪽이 자동</b> (훅이 세션·질문·답변을 기록) · <b>꺼내는 쪽도 자동</b> (브리핑·컨텍스트 주입) · <b>스스로 정리</b> (점수 → 신뢰도)</div>
</div>"""

TERMINAL = """<!doctype html><meta charset="utf-8">
<style>
  body{margin:0;background:#0f1412;font-family:-apple-system,"Segoe UI","Noto Sans KR",sans-serif}
  .win{width:1100px;background:#161b19;border-radius:12px;overflow:hidden;border:1px solid #2a3530;
       box-shadow:0 20px 60px rgba(0,0,0,.5)}
  .bar{height:38px;background:#1f2623;display:flex;align-items:center;padding:0 14px;gap:8px;color:#8d978f;font-size:13px}
  .dot{width:12px;height:12px;border-radius:50%}
  pre{margin:0;padding:22px 26px;color:#d8d4ca;font:14.5px/1.6 ui-monospace,Menlo,"D2Coding",monospace;white-space:pre-wrap}
  .c{color:#6fbf92}.w{color:#d99a5c}.d{color:#8d978f}
</style>
<div class="win">
  <div class="bar"><span class="dot" style="background:#ff5f57"></span><span class="dot" style="background:#febc2e"></span>
    <span class="dot" style="background:#28c840"></span>&nbsp; Claude Code — 세션 시작 시 자동으로 주입되는 브리핑</div>
  <pre>__BODY__</pre>
</div>"""


def brief_text(home: str) -> str:
    out = subprocess.run(
        [sys.executable, "-m", "jarvis.cli", "brief", "-p", "backend"],
        env={"JARVIS_HOME": home, "PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True,
    ).stdout
    esc = out.replace("&", "&amp;").replace("<", "&lt;")
    lines = []
    for ln in esc.splitlines():
        if ln.startswith("# "):
            lines.append(f'<span class="c">{ln}</span>')
        elif ln.startswith("## 주의"):
            lines.append(f'<span class="w">{ln}</span>')
        elif ln.startswith("## "):
            lines.append(f'<span class="c">{ln}</span>')
        elif ln.strip().startswith("·"):
            lines.append(ln)
        else:
            lines.append(f'<span class="d">{ln}</span>')
    return '<span class="d">$ jv brief -p backend</span>\n\n' + "\n".join(lines)


# ---------------------------------------------------------------- 촬영
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "img"))
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    home = tempfile.mkdtemp(prefix="myviking-shots-")
    seed(home)
    srv = serve(home)
    base = f"http://127.0.0.1:{PORT}"

    admin = api("POST", "/keys", {"name": "admin"})["key"]  # 인증 켜짐
    api("POST", "/keys", {"name": "지훈-노트북", "projects": ["backend"]}, admin)
    api("POST", "/keys", {"name": "CI-runner", "projects": ["frontend"]}, admin)
    old = api("POST", "/keys", {"name": "옛-데스크톱", "projects": ["backend"]}, admin)
    api("DELETE", f"/keys/{old['id']}", None, admin)

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=2,
                            locale="ko-KR", color_scheme="light")
        pg = ctx.new_page()
        W = 1000

        def shot(name: str, **kw) -> None:
            pg.screenshot(path=str(out / f"{name}.png"), **kw)
            print("  ✓", name)

        pg.goto(base + "/")
        pg.wait_for_timeout(W)
        pg.fill("#key", admin)
        pg.dispatch_event("#key", "change")
        pg.wait_for_timeout(W)
        shot("dashboard")

        pg.click('.proj[data-p="backend"]')
        pg.wait_for_timeout(W)
        shot("connect")
        pg.click("#conn-close")

        pg.click('nav button[data-tab="connections"]')
        pg.wait_for_timeout(W)
        shot("keys")

        pg.click('nav button[data-tab="knowledge"]')
        pg.wait_for_timeout(W)
        pg.select_option("#k-project", "backend")
        pg.wait_for_timeout(W)
        shot("knowledge")

        pg.click('nav button[data-tab="activity"]')
        pg.wait_for_timeout(W + 400)
        shot("activity")

        # 정적 이미지: 히어로 배너, 브리핑 터미널
        hero = ctx.new_page()
        hero.set_content(HERO)
        hero.wait_for_timeout(300)
        hero.locator(".hero").screenshot(path=str(out / "hero.png"))
        print("  ✓ hero")

        term = ctx.new_page()
        term.set_content(TERMINAL.replace("__BODY__", brief_text(home)))
        term.wait_for_timeout(300)
        term.locator(".win").screenshot(path=str(out / "briefing.png"))
        print("  ✓ briefing")

        b.close()
    srv.should_exit = True
    print(f"완료: {out}")


if __name__ == "__main__":
    main()
