"""캡처된 실제 API 응답을 실제 대시보드 코드에 넣어 클릭 가능한 프리뷰를 만듭니다.

목업이 아니라는 것이 요점입니다: ui.py 의 HTML/CSS/JS 를 그대로 쓰고 fetch 만
가로챕니다. 데이터를 다시 캡처하려면 서버를 띄우고 tools/capture_api.py 를 쓰세요.

    python tools/build_preview.py <api.json> <out.html>
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from jarvis.ui import DASHBOARD_HTML as H  # noqa: E402

FRAME_CSS = """
/* ---- 프리뷰 프레임: 제품 UI 가 아니라 이 페이지에만 있는 고지 ---- */
.pv-note { display:flex; gap:12px; align-items:baseline; flex-wrap:wrap;
  background:var(--chip); border-bottom:1px solid var(--line);
  border-left:3px solid var(--accent); padding:10px 20px; font-size:12.5px;
  color:var(--muted); line-height:1.5; }
.pv-note b { color:var(--ink); font-size:13px; }
.pv-note code { font-family:var(--mono); font-size:11.5px;
  background:var(--bg); border:1px solid var(--line); border-radius:4px; padding:1px 5px; }
.pv-note .sep { color:var(--line); }
.pv-toast { position:fixed; left:50%; bottom:24px; transform:translateX(-50%);
  background:var(--ink); color:var(--bg); padding:9px 16px; border-radius:999px;
  font-size:13px; z-index:99; box-shadow:0 6px 24px rgba(0,0,0,.25);
  opacity:0; pointer-events:none; transition:opacity .18s ease; }
.pv-toast.on { opacity:1; }
@media (prefers-reduced-motion: reduce) { .pv-toast { transition:none; } }
"""

FRAME_HTML = """
<div class="pv-note">
  <b>클릭 가능한 프리뷰</b>
  <span>실제 서버의 대시보드 코드에 실제 API 응답을 그대로 넣었습니다. 목업이 아닙니다.</span>
  <span class="sep">|</span>
  <span>탭 · 프로젝트 카드 · <code>연결정보</code> · 지식 항목 · 작업 세션 · 트레이스 모두 열립니다.</span>
  <span class="sep">|</span>
  <span>저장·평가 버튼은 읽기 전용입니다.</span>
</div>
"""

SHIM = r"""
// ---------------------------------------------------------------------------
// 프리뷰 전용: fetch 를 가로채 캡처된 실제 응답을 돌려줍니다. 이 블록 아래의
// 대시보드 코드는 서버에서 돌아가는 것과 한 글자도 다르지 않습니다.
// ---------------------------------------------------------------------------
const PREVIEW_API = __API__;

function pvToast(msg) {
  let el = document.querySelector(".pv-toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "pv-toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add("on");
  clearTimeout(el._t);
  el._t = setTimeout(function () { el.classList.remove("on"); }, 2200);
}

// Look-ups tolerate a different origin and a missing key: the connection panel
// asks for whatever address the browser is on, which here is the artifact's.
function pvNormalise(path) {
  const parts = path.split("?");
  const keep = [];
  for (const pair of new URLSearchParams(parts[1] || "")) {
    if (pair[0] === "base_url" || pair[0] === "key") continue;
    keep.push(pair[0] + "=" + pair[1]);
  }
  keep.sort();
  return parts[0] + (keep.length ? "?" + keep.join("&") : "");
}

const PREVIEW_INDEX = new Map();
Object.keys(PREVIEW_API).forEach(function (k) { PREVIEW_INDEX.set(pvNormalise(k), k); });

function pvLookup(path) {
  if (path in PREVIEW_API) return PREVIEW_API[path];
  const norm = pvNormalise(path);
  if (PREVIEW_INDEX.has(norm)) return PREVIEW_API[PREVIEW_INDEX.get(norm)];
  if (norm.indexOf("/connection") !== -1) {
    const client = new URLSearchParams(path.split("?")[1] || "").get("client") || "claude-code";
    const base = norm.split("?")[0];
    const hit = Object.keys(PREVIEW_API).find(function (k) {
      return k.indexOf(base) === 0 && k.indexOf("client=" + client) !== -1;
    });
    if (hit) return PREVIEW_API[hit];
  }
  return undefined;
}

window.fetch = function (url, opts) {
  opts = opts || {};
  const path = String(url).replace(location.origin, "");
  const method = (opts.method || "GET").toUpperCase();
  const json = function (data, status) {
    return Promise.resolve(new Response(JSON.stringify(data), {
      status: status, headers: { "content-type": "application/json" },
    }));
  };
  if (method !== "GET") {
    pvToast("프리뷰에서는 저장되지 않습니다. 실제 서버에서는 즉시 반영됩니다.");
    return json({ ok: true, preview: true }, 200);
  }
  const data = pvLookup(path);
  if (data === undefined) return json({ detail: "프리뷰에 없는 응답: " + path }, 404);
  return json(data, 200);
};
"""


def build(api_path: str, out_path: str) -> pathlib.Path:
    cap = json.load(open(api_path, encoding="utf-8"))
    style = re.search(r"<style>(.*?)</style>", H, re.S).group(1)
    body = re.search(r"<body>(.*?)<script>", H, re.S).group(1)
    script = re.search(r"<script>(.*?)</script>\s*</body>", H, re.S).group(1)
    shim = SHIM.replace("__API__", json.dumps(cap, ensure_ascii=False))
    out = (
        "<title>MyViking</title>\n<style>\n"
        + style + "\n" + FRAME_CSS + "\n</style>\n"
        + FRAME_HTML + "\n" + body
        + "\n<script>\n" + shim + "\n</script>\n"
        + "<script>\n" + script + "\n</script>\n"
    )
    path = pathlib.Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(out, encoding="utf-8")
    return path


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(1)
    p = build(sys.argv[1], sys.argv[2])
    print(f"작성: {p} ({round(p.stat().st_size / 1024, 1)} KB)")
