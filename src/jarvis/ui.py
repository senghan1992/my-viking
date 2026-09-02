"""The dashboard.

MyViking is infrastructure for coding agents, not an app people work in. So the
front page answers the only question a person comes here with: *what do I paste
into my agent so this project starts learning?*

Three tabs:

* 프로젝트  — create one, read its connection info, copy it. This is the job.
* 지식     — what the agents have accumulated (browse, and correct if you want).
* 활동     — traces and latency, for when an answer looked wrong or slow.

Everything else runs itself: usage reinforces, disuse decays, outcomes adjust
confidence, and contradictions supersede. Nothing in here is a chore queue.

Served by the same process as the API — nothing extra to run, no build step.
The API key, if the server requires one, stays in the browser's localStorage.
"""

from __future__ import annotations

DASHBOARD_HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MyViking</title>
<style>
  :root {
    --bg:#fbfaf8; --panel:#fff; --line:#e6e2dc; --ink:#1c1a17; --muted:#6f6a62;
    --accent:#2f6f4f; --warn:#a4551f; --bad:#a32d2d; --chip:#f1efea;
    --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg:#16150f; --panel:#1e1d17; --line:#302e26; --ink:#ece8de; --muted:#9a9488;
      --accent:#6fbf92; --warn:#d99a5c; --bad:#e07a7a; --chip:#262419;
    }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font:14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", sans-serif; }
  header { display:flex; gap:10px; align-items:center; flex-wrap:wrap; padding:12px 20px;
    border-bottom:1px solid var(--line); background:var(--panel); position:sticky; top:0; z-index:10; }
  h1 { font-size:15px; margin:0 8px 0 0; } h1 span { color:var(--muted); font-weight:400; }
  select, input, button, textarea { font:inherit; color:var(--ink); background:var(--bg);
    border:1px solid var(--line); border-radius:6px; padding:6px 9px; }
  textarea { width:100%; font-family:var(--mono); font-size:12.5px; resize:vertical; }
  button { cursor:pointer; background:var(--chip); }
  button:hover { border-color:var(--accent); }
  button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  button.small { padding:3px 8px; font-size:12px; }
  .grow { flex:1; }
  nav { display:flex; gap:2px; padding:0 20px; background:var(--panel);
    border-bottom:1px solid var(--line); position:sticky; top:53px; z-index:9; }
  nav button { background:none; border:none; border-bottom:2px solid transparent;
    border-radius:0; padding:9px 14px; color:var(--muted); font-weight:600; font-size:13px; }
  nav button.on { color:var(--ink); border-bottom-color:var(--accent); }
  nav button .n { display:inline-block; margin-left:6px; padding:0 6px; border-radius:999px;
    background:var(--warn); color:#fff; font-size:11px; }
  main { padding:20px; max-width:1200px; margin:0 auto; }
  .tab { display:none; } .tab.on { display:block; }
  section { margin-top:20px; }
  section:first-child { margin-top:0; }
  section > h2 { font-size:13px; text-transform:uppercase; letter-spacing:.07em;
    color:var(--muted); margin:0 0 8px; font-weight:600; }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:10px; }
  .pad { padding:14px 16px; }
  .scroll { overflow-x:auto; }
  .grid { display:grid; gap:12px; grid-template-columns:repeat(auto-fill, minmax(260px,1fr)); }
  .proj { background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:14px 16px; cursor:pointer; text-align:left; }
  .proj:hover { border-color:var(--accent); }
  .proj .nm { font-size:15px; font-weight:600; }
  .proj .ds { color:var(--muted); font-size:12.5px; margin:2px 0 8px; min-height:1.4em; }
  .proj .st { display:flex; gap:10px; flex-wrap:wrap; color:var(--muted); font-size:12px; }
  .proj .st b { color:var(--ink); font-variant-numeric:tabular-nums; }
  .cards { display:grid; gap:12px; grid-template-columns:repeat(auto-fit, minmax(150px,1fr)); }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
  .card .k { color:var(--muted); font-size:11.5px; text-transform:uppercase; letter-spacing:.06em; }
  .card .v { font-size:23px; font-weight:600; margin-top:4px; font-variant-numeric:tabular-nums; }
  .card .s { color:var(--muted); font-size:12px; margin-top:2px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }
  th { color:var(--muted); font-weight:600; font-size:11.5px; text-transform:uppercase; letter-spacing:.05em; }
  tr:last-child td { border-bottom:none; }
  tbody tr.click { cursor:pointer; } tbody tr.click:hover { background:var(--chip); }
  td.wrap { white-space:normal; max-width:520px; }
  .mono { font-family:var(--mono); font-size:12px; }
  .chip { display:inline-block; padding:1px 7px; border-radius:999px; background:var(--chip);
    font-size:11.5px; border:1px solid var(--line); }
  .chip.bad { border-color:var(--bad); color:var(--bad); }
  .chip.warn { border-color:var(--warn); color:var(--warn); }
  .chip.ok { border-color:var(--accent); color:var(--accent); }
  .ok { color:var(--accent); } .mid { color:var(--warn); } .bad { color:var(--bad); }
  .bars { display:flex; align-items:flex-end; gap:3px; height:56px; padding:12px; }
  .bars div { flex:1; background:var(--accent); opacity:.75; border-radius:2px 2px 0 0; min-height:2px; }
  dialog { border:1px solid var(--line); border-radius:12px; background:var(--panel);
    color:var(--ink); padding:0; width:min(880px,94vw); max-height:88vh; }
  dialog::backdrop { background:rgba(0,0,0,.45); }
  .dlg-head { display:flex; gap:8px; align-items:center; padding:12px 16px; flex-wrap:wrap;
    border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--panel); z-index:2; }
  .dlg-body { padding:14px 16px; overflow:auto; max-height:74vh; }
  pre { background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:10px;
    overflow-x:auto; font-family:var(--mono); font-size:12px; margin:6px 0; white-space:pre-wrap; }
  .step { display:grid; grid-template-columns:92px 1fr 78px; gap:10px; padding:7px 0;
    border-bottom:1px dashed var(--line); align-items:baseline; }
  .empty { padding:26px; color:var(--muted); text-align:center; }
  .empty b { color:var(--ink); }
  .err { padding:10px 14px; color:var(--bad); }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .fld { margin:10px 0; } .fld label { display:block; font-size:11.5px; color:var(--muted);
    text-transform:uppercase; letter-spacing:.05em; margin-bottom:3px; }
  .fld input, .fld select { width:100%; }
  .split { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .why { background:var(--chip); border-left:3px solid var(--warn); padding:8px 12px;
    border-radius:0 6px 6px 0; margin:8px 0; font-size:13px; }
  .step-n { display:flex; gap:10px; align-items:baseline; margin:16px 0 6px; }
  .step-n b { display:inline-flex; width:22px; height:22px; border-radius:999px;
    background:var(--accent); color:#fff; align-items:center; justify-content:center;
    font-size:12px; flex:none; }
  .step-n span { color:var(--muted); font-size:12.5px; }
  .copy { position:relative; }
  .copy button { position:absolute; top:10px; right:10px; }
  details.sess { border-bottom:1px solid var(--line); }
  details.sess:last-child { border-bottom:none; }
  details.sess > summary { cursor:pointer; padding:10px 14px; display:flex; gap:8px;
    align-items:center; flex-wrap:wrap; font-size:13px; }
  details.sess > summary:hover { background:var(--chip); }
  details.sess[open] > summary { border-bottom:1px dashed var(--line); }
  .sess-body { padding:8px 14px 14px; }
  .qa { padding:8px 0; border-bottom:1px dashed var(--line); }
  .qa:last-child { border-bottom:none; }
  .qa .q { font-weight:600; }
  .qa .a { color:var(--muted); font-size:12.5px; margin-top:2px; white-space:pre-wrap; }
  .tree { display:grid; grid-template-columns:210px 1fr; }
  .tree > .side { border-right:1px solid var(--line); max-height:66vh; overflow:auto; }
  .side a { display:flex; justify-content:space-between; gap:8px; padding:7px 12px;
    border-bottom:1px solid var(--line); cursor:pointer; color:var(--ink); }
  .side a:hover { background:var(--chip); } .side a.on { background:var(--chip); font-weight:600; }
  .side a .c { color:var(--muted); font-size:12px; }
  @media (max-width:760px) { .tree { grid-template-columns:1fr; } .split { grid-template-columns:1fr; } }
</style>
</head>
<body>
<header>
  <h1>MyViking <span id="ver"></span></h1>
  <button id="refresh" class="small">새로고침</button>
  <span class="grow"></span>
  <input id="key" type="password" placeholder="API 키 (필요한 경우)" size="16">
  <span id="status" class="chip"></span>
</header>

<nav>
  <button data-tab="projects" class="on">프로젝트</button>
  <button data-tab="knowledge">지식<span class="n" id="badge" hidden></span></button>
  <button data-tab="activity">활동</button>
</nav>

<main>
  <div id="error"></div>

  <!-- ============ 프로젝트 ============ -->
  <div class="tab on" id="tab-projects">
    <section>
      <h2>프로젝트</h2>
      <div class="grid" id="projects"></div>
    </section>
    <section>
      <h2>새 프로젝트</h2>
      <div class="panel pad">
        <div class="split">
          <div class="fld"><label>이름 (영문·숫자·하이픈)</label>
            <input id="np-name" placeholder="backend"></div>
          <div class="fld"><label>메모리 스키마</label><select id="np-template"></select></div>
        </div>
        <div class="fld"><label>설명 (선택)</label><input id="np-desc" placeholder="결제 API 서버"></div>
        <div class="fld"><label>git remote (선택) — 넣으면 어느 머신에서든 이 저장소가 자동으로 이 프로젝트로 연결됩니다</label>
          <input id="np-repo" placeholder="git@github.com:me/backend.git"></div>
        <div class="row"><button class="primary" id="np-create">만들기</button>
          <span id="np-msg" style="color:var(--muted)"></span></div>
      </div>
    </section>
  </div>

  <!-- ============ 지식 ============ -->
  <div class="tab" id="tab-knowledge">
    <section>
      <h2>에이전트가 알고 있는 것
        <select id="k-project" style="margin-left:8px"></select>
      </h2>
      <div class="panel tree">
        <div class="side" id="cats"></div>
        <div class="scroll"><table id="mems"></table></div>
      </div>
    </section>
    <section>
      <h2>점검이 필요한 것 — 자동으로 정해지지 않는 것만</h2>
      <div class="panel scroll"><table id="review"></table></div>
    </section>
  </div>

  <!-- ============ 활동 ============ -->
  <div class="tab" id="tab-activity">
    <section>
      <h2>응답 품질과 속도
        <select id="a-project" style="margin-left:8px"></select>
        <select id="days" style="margin-left:6px">
          <option value="1">24시간</option><option value="7" selected>7일</option>
          <option value="30">30일</option><option value="0">전체</option></select>
      </h2>
      <div class="cards" id="cards"></div>
    </section>
    <section><h2>일별 추이 (막대 = 평균 응답 시간)</h2>
      <div class="panel"><div class="bars" id="spark"></div></div></section>
    <section><h2>시간이 어디에 쓰이나</h2>
      <div class="panel scroll"><table id="steps"></table></div></section>
    <section><h2>작업 세션 — 새 세션이 참고하는 이전 작업</h2>
      <div class="panel" id="sessions"></div></section>
    <section><h2>개별 요청</h2>
      <div class="panel scroll"><table id="traces"></table></div></section>
    <section><h2>연결된 에이전트</h2>
      <div class="panel scroll"><table id="agents"></table></div></section>
  </div>
</main>

<!-- ============ 연결정보 ============ -->
<dialog id="conn">
  <div class="dlg-head">
    <strong id="conn-title"></strong>
    <span class="grow"></span>
    <select id="conn-client"></select>
    <button id="conn-close">닫기</button>
  </div>
  <div class="dlg-body" id="conn-body"></div>
</dialog>

<dialog id="mem">
  <div class="dlg-head">
    <strong id="mem-title">메모리</strong>
    <span class="grow"></span>
    <button class="primary" id="mem-confirm">맞음</button>
    <button id="mem-save">수정 저장</button>
    <button id="mem-archive">보관</button>
    <button id="mem-close">닫기</button>
  </div>
  <div class="dlg-body" id="mem-body"></div>
</dialog>

<dialog id="dlg">
  <div class="dlg-head">
    <strong id="dlg-title">트레이스</strong>
    <span class="grow"></span>
    <button data-score="1">도움됨</button>
    <button data-score="0.5">보통</button>
    <button data-score="0">틀렸음</button>
    <button id="dlg-close">닫기</button>
  </div>
  <div class="dlg-body" id="dlg-body"></div>
</dialog>

<script>
const $ = (id) => document.getElementById(id);
const keyBox = $("key");
keyBox.value = localStorage.getItem("jv_key") || "";
keyBox.onchange = () => { localStorage.setItem("jv_key", keyBox.value.trim()); load(); };

async function api(path, opts = {}) {
  const headers = Object.assign({ "content-type": "application/json" }, opts.headers || {});
  const k = keyBox.value.trim();
  if (k) headers["authorization"] = "Bearer " + k;
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 300)}`);
  return res.status === 204 ? null : res.json();
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ms = (n) => n >= 1000 ? (n / 1000).toFixed(1) + "s" : Math.round(n) + "ms";
const pct = (n) => (n * 100).toFixed(0) + "%";
const short = (u) => String(u).replace(/^jarvis:\/\/(projects\/)?/, "");
const when = (s) => s ? String(s).slice(5, 16).replace("T", " ") : "—";
const scoreClass = (v) => v == null ? "" : v >= 0.7 ? "ok" : v >= 0.4 ? "mid" : "bad";

// Only reasons the automatic rules genuinely cannot settle appear by default.
const REASONS = {
  conflict:  ["상충",     "bad",  "서로 반대되는 내용이 남아 있습니다. 어느 쪽이 맞는지 정해주세요."],
  harmful:   ["나쁜 결과", "bad",  "이 지식이 들어간 작업들의 평가가 낮습니다."],
  unproven:  ["미검증",   "warn", "여러 번 쓰였지만 결과 평가가 없습니다."],
  fading:    ["잊히는 중", "",     "오래 쓰이지 않아 신뢰도가 떨어졌습니다. 곧 보관됩니다."],
  unconfirmed: ["미확인", "", "에이전트가 기록했고 사람이 보지 않았습니다 (정상 상태)."],
};
const reasonChip = (r) => {
  const [label, cls] = REASONS[r] || [r, ""];
  return `<span class="chip ${cls}">${esc(label)}</span>`;
};

function table(el, cols, rows, onClick, emptyHtml) {
  if (!rows.length) {
    el.innerHTML = `<tbody><tr><td class="empty">${emptyHtml || "아직 기록이 없습니다"}</td></tr></tbody>`;
    return;
  }
  el.innerHTML =
    "<thead><tr>" + cols.map((c) => `<th>${esc(c.label)}</th>`).join("") + "</tr></thead><tbody>" +
    rows.map((r, i) => `<tr class="${onClick ? "click" : ""}" data-i="${i}">` +
      cols.map((c) => `<td class="${c.cls || ""}">${c.get(r)}</td>`).join("") + "</tr>").join("") +
    "</tbody>";
  if (onClick) el.querySelectorAll("tbody tr").forEach((tr) =>
    tr.onclick = () => onClick(rows[Number(tr.dataset.i)]));
}
const card = (k, v, s, cls = "") =>
  `<div class="card"><div class="k">${esc(k)}</div><div class="v ${cls}">${esc(v)}</div><div class="s">${esc(s)}</div></div>`;

function copyBlock(text, lang = "") {
  const id = "c" + Math.random().toString(36).slice(2, 8);
  return `<div class="copy"><pre id="${id}">${esc(text)}</pre>
    <button class="small" onclick="copyFrom('${id}', this)">복사</button></div>`;
}
window.copyFrom = async (id, btn) => {
  try {
    await navigator.clipboard.writeText($(id).textContent);
    btn.textContent = "복사됨"; setTimeout(() => btn.textContent = "복사", 1400);
  } catch { btn.textContent = "직접 선택하세요"; }
};

let PROJECTS = [];
let TEMPLATES = [];
let tab = "projects";

document.querySelectorAll("nav button").forEach((b) => b.onclick = () => {
  tab = b.dataset.tab;
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x.id === "tab-" + tab));
  load();
});

// ---------------- 프로젝트 ----------------
function renderProjects() {
  if (!PROJECTS.length) {
    $("projects").innerHTML = `<div class="panel empty" style="grid-column:1/-1">
      <b>아직 프로젝트가 없습니다.</b><br>아래에서 하나 만들고, 연결정보를 코딩 에이전트에 넣으면<br>
      그때부터 지식이 알아서 쌓입니다.</div>`;
    return;
  }
  $("projects").innerHTML = PROJECTS.map((p) => `
    <button class="proj" data-p="${esc(p.project)}">
      <div class="nm">${esc(p.project)}</div>
      <div class="ds">${esc(p.description || "")}</div>
      <div class="st">
        <span><b>${p.memories}</b> 지식</span>
        <span><b>${p.tasks}</b> 작업</span>
        <span class="chip">${esc(p.template)}</span>
      </div>
      <div class="st" style="margin-top:6px">
        <span>최근 ${esc(when(p.last_active))}</span>
        ${p.aliases.length ? `<span class="chip">연결됨</span>` : `<span class="chip warn">연결 대기</span>`}
      </div>
    </button>`).join("");
  $("projects").querySelectorAll(".proj").forEach((b) =>
    b.onclick = () => openConnection(b.dataset.p));
}

$("np-create").onclick = async () => {
  const name = $("np-name").value.trim();
  if (!name) { $("np-msg").textContent = "이름을 입력하세요."; return; }
  $("np-msg").textContent = "만드는 중...";
  try {
    await api("/projects", { method: "POST", body: JSON.stringify({
      project: name,
      template: $("np-template").value,
      description: $("np-desc").value.trim(),
    })});
    const repo = $("np-repo").value.trim();
    if (repo) await api("/aliases", { method: "POST", body: JSON.stringify({ alias: repo, project: name })});
    $("np-name").value = ""; $("np-desc").value = ""; $("np-repo").value = "";
    $("np-msg").textContent = "";
    await boot();
    openConnection(name);
  } catch (e) { $("np-msg").innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
};

// ---------------- 연결정보 ----------------
let connProject = null;
async function openConnection(project) {
  connProject = project;
  $("conn-title").textContent = project + " — 연결정보";
  $("conn-body").innerHTML = "불러오는 중...";
  $("conn").showModal();
  await renderConnection();
}

async function renderConnection() {
  const client = $("conn-client").value || "claude-code";
  try {
    const key = keyBox.value.trim();
    const c = await api(`/projects/${encodeURIComponent(connProject)}/connection`
      + `?client=${encodeURIComponent(client)}&key=${encodeURIComponent(key)}`
      + `&base_url=${encodeURIComponent(location.origin)}`);
    if (!$("conn-client").options.length) {
      $("conn-client").innerHTML = c.clients.map((x) =>
        `<option ${x === client ? "selected" : ""}>${esc(x)}</option>`).join("");
    }
    const needKey = c.auth_required && !c.has_key;
    $("conn-body").innerHTML = `
      ${needKey ? `<div class="why">이 서버는 API 키를 요구합니다. 위 입력란에 키를 넣으면
        아래 설정에 자동으로 포함됩니다. 키가 없으면 <span class="mono">jv key create &lt;이름&gt;</span>
        으로 발급하세요.</div>` : ""}

      <div class="step-n"><b>1</b><div><strong>MCP 서버 등록</strong>
        <span>— ${esc(c.where)}</span></div></div>
      ${copyBlock(c.setup)}

      <div class="step-n"><b>2</b><div><strong>에이전트 지시문</strong>
        <span>— 저장소의 <span class="mono">${esc(c.instruction_file)}</span> 에 추가</span></div></div>
      <div style="color:var(--muted);font-size:12.5px;margin:-2px 0 4px">
        이 단계를 빼면 도구는 연결되지만 에이전트가 호출하지 않아 지식이 쌓이지 않습니다.
      </div>
      ${copyBlock(c.instructions)}

      <div class="step-n"><b>3</b><div><strong>끝</strong>
        <span>— 이제 그 저장소에서 작업하면 지식이 알아서 쌓이고 정리됩니다</span></div></div>

      <div class="panel pad" style="margin-top:12px">
        <div class="row" style="justify-content:space-between">
          <div><strong style="font-size:13px">저장소 연결</strong>
            <div style="color:var(--muted);font-size:12.5px">
              git remote 를 등록하면 프로젝트 이름 없이도 어느 머신에서든 이 프로젝트로 연결됩니다.
            </div></div>
        </div>
        <div class="row" style="margin-top:8px">
          <input id="cn-alias" placeholder="git@github.com:me/repo.git" style="flex:1;min-width:220px">
          <button class="small" id="cn-add">등록</button>
        </div>
        <div class="row" style="margin-top:8px">
          ${c.aliases.length
            ? c.aliases.map((a) => `<span class="chip mono">${esc(a.alias)}</span>`).join("")
            : `<span style="color:var(--muted);font-size:12.5px">아직 등록된 remote 가 없습니다.</span>`}
        </div>
      </div>

      <div style="margin-top:12px;color:var(--muted);font-size:12.5px">
        MCP 엔드포인트: <span class="mono">${esc(c.mcp_url)}</span>
      </div>`;
    $("cn-add").onclick = async () => {
      const alias = $("cn-alias").value.trim();
      if (!alias) return;
      await api("/aliases", { method: "POST", body: JSON.stringify({ alias, project: connProject })});
      await boot(); await renderConnection();
    };
  } catch (e) { $("conn-body").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
$("conn-client").onchange = renderConnection;
$("conn-close").onclick = () => $("conn").close();

// ---------------- 지식 ----------------
let dbCategory = null;
async function loadKnowledge() {
  const project = $("k-project").value;
  if (!project) {
    $("cats").innerHTML = `<div class="empty">프로젝트를 선택하세요</div>`;
    $("mems").innerHTML = ""; $("review").innerHTML = "";
    return;
  }
  const [profile, mems, review] = await Promise.all([
    api(`/projects/${encodeURIComponent(project)}/profile`),
    api(`/projects/${encodeURIComponent(project)}/memories?limit=500`),
    api(`/projects/${encodeURIComponent(project)}/review`),
  ]);
  const counts = {};
  mems.forEach((m) => counts[m.category] = (counts[m.category] || 0) + 1);
  const cats = profile.categories.map((c) => c.name);
  if (dbCategory && !cats.includes(dbCategory)) dbCategory = null;
  const warn = new Set(profile.categories.filter((c) => c.warn).map((c) => c.name));

  $("cats").innerHTML =
    `<a class="${dbCategory === null ? "on" : ""}" data-c="">전체 <span class="c">${mems.length}</span></a>` +
    cats.map((c) => `<a class="${dbCategory === c ? "on" : ""}" data-c="${esc(c)}">${
      warn.has(c) ? "⚠ " : ""}${esc(c)} <span class="c">${counts[c] || 0}</span></a>`).join("");
  $("cats").querySelectorAll("a").forEach((a) => a.onclick = () => {
    dbCategory = a.dataset.c || null; loadKnowledge();
  });

  const rows = dbCategory ? mems.filter((m) => m.category === dbCategory) : mems;
  table($("mems"),
    [{ label: "카테고리", get: (r) => `<span class="chip">${warn.has(r.category) ? "⚠ " : ""}${esc(r.category)}</span>` },
     { label: "지식", get: (r) => `<b>${esc(r.title)}</b><div style="color:var(--muted)">${esc((r.abstract || "").slice(0, 120))}</div>`, cls: "wrap" },
     { label: "신뢰", get: (r) => r.confidence.toFixed(2) },
     { label: "사용", get: (r) => r.hits },
     { label: "수정", get: (r) => esc((r.updated || "").slice(0, 10)), cls: "mono" }],
    rows, (r) => openMemory(r.uri),
    `<b>아직 비어 있습니다.</b><br>에이전트를 연결하고 작업하면 여기에 쌓입니다.`);

  $("badge").hidden = !review.length;
  $("badge").textContent = review.length;
  table($("review"),
    [{ label: "이유", get: (r) => r.reasons.map(reasonChip).join(" ") },
     { label: "카테고리", get: (r) => `<span class="chip">${esc(r.category)}</span>` },
     { label: "지식", get: (r) => `<b>${esc(r.title)}</b><div style="color:var(--muted)">${esc((r.abstract || "").slice(0, 110))}</div>`, cls: "wrap" },
     { label: "사용", get: (r) => r.uses },
     { label: "점수", get: (r) => r.avg_score == null ? "—" : `<b class="${scoreClass(r.avg_score)}">${r.avg_score.toFixed(2)}</b>` }],
    review, (r) => openMemory(r.uri),
    `<b>점검할 것이 없습니다.</b><br>지식 저장소는 스스로 정리되고 있습니다 —
     쓰이면 강화되고, 안 쓰이면 잊히고, 나중 지시가 이전 것을 대체합니다.`);
}

// ---------------- 메모리 편집 ----------------
let currentMem = null;
async function openMemory(uri) {
  currentMem = uri;
  $("mem-title").textContent = short(uri);
  $("mem-body").innerHTML = "불러오는 중...";
  $("mem").showModal();
  try {
    const d = await api("/memories/detail?uri=" + encodeURIComponent(uri));
    const profile = await api(`/projects/${encodeURIComponent(d.project)}/profile`);
    const why = d.reasons.filter((r) => r !== "unconfirmed").map((r) =>
      `<div class="why">${reasonChip(r)} ${esc((REASONS[r] || [, , ""])[2])}</div>`).join("");
    const clash = d.conflict ? `
      <div class="split">
        <div><label style="font-size:11.5px;color:var(--muted)">이전</label><pre>${esc(d.conflict.existing)}</pre></div>
        <div><label style="font-size:11.5px;color:var(--muted)">이후</label><pre>${esc(d.conflict.incoming)}</pre></div>
      </div>
      <div style="color:var(--muted);font-size:12.5px;margin:-4px 0 8px">${
        d.conflict.resolution === "superseded"
          ? "나중 내용으로 자동 대체되었습니다. 이전 것은 보관함에 남아 있습니다."
          : "아직 정해지지 않았습니다."}
        ${d.conflict.other ? `<a onclick="openMemory('${esc(d.conflict.other)}')" class="mono" style="cursor:pointer;text-decoration:underline">상대 열기</a>` : ""}
      </div>` : "";
    $("mem-body").innerHTML = `
      ${why}${clash}
      <div class="row" style="margin:8px 0">
        <span class="chip">${d.origin === "manual" ? "직접 작성" : "에이전트가 기록"}</span>
        <span class="chip">사용 ${d.hits}회</span>
        <span class="chip">작업 ${d.impact.uses}건에 포함</span>
        ${d.impact.avg_score == null ? "" : `<span class="chip ${scoreClass(d.impact.avg_score)}">평균 점수 ${d.impact.avg_score}</span>`}
        <span class="chip">L0 ${d.tokens.l0} / L2 ${d.tokens.l2} 토큰</span>
      </div>
      <div class="split">
        <div class="fld"><label>제목 (파일명이 됩니다)</label><input id="f-title" value="${esc(d.title)}"></div>
        <div class="fld"><label>카테고리</label><select id="f-cat">${
          profile.categories.map((c) => `<option ${c.name === d.category ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></div>
      </div>
      <div class="fld"><label>요약 — 검색에서 이 문장이 먼저 읽힙니다 (L0)</label>
        <textarea id="f-abstract" rows="2">${esc(d.abstract)}</textarea></div>
      <div class="fld"><label>본문 — 근거·명령어·경로 (L2)</label>
        <textarea id="f-body" rows="10">${esc(d.body)}</textarea></div>
      <div class="fld"><label>신뢰도 ${d.confidence}</label>
        <input id="f-conf" type="range" min="0" max="1" step="0.05" value="${d.confidence}"></div>
      <div style="color:var(--muted);font-size:12px">
        파일: <span class="mono">${esc(d.path)}</span>
      </div>`;
  } catch (e) { $("mem-body").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
const memErr = (e) => $("mem-body").insertAdjacentHTML("afterbegin", `<div class="err">${esc(e.message)}</div>`);
$("mem-confirm").onclick = async () => {
  try { await api("/memories/confirm", { method: "POST", body: JSON.stringify({ uri: currentMem })});
    $("mem").close(); load(); } catch (e) { memErr(e); }
};
$("mem-save").onclick = async () => {
  try {
    const res = await api("/memories", { method: "PATCH", body: JSON.stringify({
      uri: currentMem, title: $("f-title").value, statement: $("f-abstract").value,
      body: $("f-body").value, category: $("f-cat").value, confidence: Number($("f-conf").value),
    })});
    $("mem").close(); if (res.moved_from) dbCategory = null; load();
  } catch (e) { memErr(e); }
};
$("mem-archive").onclick = async () => {
  if (!confirm("보관함으로 옮깁니다. 검색에서 제외되지만 파일은 남습니다.")) return;
  try { await api("/memories?uri=" + encodeURIComponent(currentMem) + "&archive=true", { method: "DELETE" });
    $("mem").close(); load(); } catch (e) { memErr(e); }
};
$("mem-close").onclick = () => $("mem").close();

// ---------------- 활동 ----------------
async function loadActivity() {
  const project = $("a-project").value;
  const days = $("days").value;
  const q = `?project=${encodeURIComponent(project)}&days=${days}`;
  const [m, ts, traces, agents, sessions] = await Promise.all([
    api("/metrics" + q),
    api(`/timeseries?project=${encodeURIComponent(project)}&days=14`),
    api(`/traces?project=${encodeURIComponent(project)}&limit=60`),
    api("/agents"),
    api(`/worksessions?project=${encodeURIComponent(project)}&limit=12`),
  ]);
  const avg = m.scores.length
    ? m.scores.reduce((a, s) => a + s.avg * s.count, 0) / m.scores.reduce((a, s) => a + s.count, 0) : null;
  $("cards").innerHTML = [
    card("응답 p50", ms(m.answer_ms.p50), `p95 ${ms(m.answer_ms.p95)}`),
    card("재사용률", pct(m.reuse.rate), `${m.reuse.hits} / ${m.traces} 건`),
    card("컨텍스트 조립 p50", ms(m.context_ms.p50), `p95 ${ms(m.context_ms.p95)}`),
    card("평균 점수", avg == null ? "—" : avg.toFixed(2),
         avg == null ? "에이전트가 점수를 보내면 표시됩니다" : m.scores.map((s) => `${s.name} ${s.count}건`).join(", "),
         scoreClass(avg)),
    card("작업", String(m.traces), m.errors ? `오류 ${m.errors}건` : "오류 없음"),
    card("토큰", (m.tokens.in + m.tokens.out).toLocaleString(),
         `입력 ${m.tokens.in.toLocaleString()} / 출력 ${m.tokens.out.toLocaleString()}`),
  ].join("");

  const max = Math.max(1, ...ts.map((d) => d.avg_total_ms || d.avg_ms));
  $("spark").innerHTML = ts.length
    ? ts.map((d) => `<div style="height:${Math.max(2, ((d.avg_total_ms || d.avg_ms) / max) * 100)}%" title="${d.day} · ${d.traces}건 · ${ms(d.avg_total_ms || d.avg_ms)} · 재사용 ${pct(d.reuse_rate)}"></div>`).join("")
    : `<div class="empty" style="flex:1">데이터 없음</div>`;

  table($("steps"),
    [{ label: "단계", get: (r) => `<span class="chip">${esc(r.type)}</span>` },
     { label: "횟수", get: (r) => r.count }, { label: "평균", get: (r) => ms(r.avg_ms) },
     { label: "최대", get: (r) => ms(r.max_ms) }], m.steps);

  table($("traces"),
    [{ label: "시각", get: (r) => esc(when(r.started)), cls: "mono" },
     { label: "프로젝트", get: (r) => esc(r.scope) },
     { label: "질문", get: (r) => esc((r.input || "").slice(0, 90)), cls: "wrap" },
     { label: "결과", get: (r) => r.cache_hit ? `<span class="chip ok">재사용</span>` : `<span class="chip">생성</span>` },
     { label: "조립", get: (r) => ms(r.latency_ms) },
     { label: "전체", get: (r) => ms(r.total_ms || r.latency_ms) },
     { label: "점수", get: (r) => r.avg_score == null ? "—" : `<b class="${scoreClass(r.avg_score)}">${r.avg_score.toFixed(2)}</b>` }],
    traces, openTrace,
    `<b>아직 작업 기록이 없습니다.</b><br>에이전트를 연결하면 호출이 여기에 남습니다.`);

  $("sessions").innerHTML = sessions.length
    ? sessions.map((sx) => `
      <details class="sess">
        <summary>
          <span class="mono">${esc(when(sx.started))}</span>
          <b>${esc(sx.agent || "(에이전트 미표기)")}</b>
          <span class="chip">${sx.traces}건</span>
          ${sx.reused ? `<span class="chip ok">재사용 ${sx.reused}</span>` : ""}
          ${sx.avg_score == null ? "" : `<span class="chip ${scoreClass(sx.avg_score)}">평균 ${sx.avg_score}</span>`}
          <span style="color:var(--muted)">${esc(sx.project)}</span>
        </summary>
        <div class="sess-body">
          ${sx.work.map((w) => `
            <div class="qa">
              <div class="q">${w.reused ? "↻ " : ""}${esc(w.question || "")}
                ${w.score == null ? "" : `<span class="chip ${scoreClass(w.score)}">${w.score}</span>`}</div>
              <div class="a">${esc((w.answer || "(답변 미기록)").slice(0, 400))}</div>
            </div>`).join("")}
        </div>
      </details>`).join("")
    : `<div class="empty"><b>아직 작업 세션이 없습니다.</b><br>
        에이전트가 작업하면 한 자리(세션)씩 묶여 여기에 쌓이고,<br>
        새 세션의 첫 호출에서 <code>catch_up</code> 으로 전달됩니다.</div>`;

  table($("agents"),
    [{ label: "에이전트", get: (r) => `<span class="mono">${esc(r.name)}</span>` },
     { label: "프로젝트", get: (r) => esc((r.projects || []).join(", ")) },
     { label: "호출", get: (r) => r.calls },
     { label: "최근", get: (r) => esc(when(r.last_seen)), cls: "mono" }],
    agents, null,
    `<b>연결된 에이전트가 없습니다.</b><br>프로젝트 탭에서 연결정보를 복사해 넣으세요.`);
}

let currentTrace = null;
async function openTrace(row) {
  currentTrace = row.id;
  $("dlg-title").textContent = row.id;
  $("dlg-body").innerHTML = "불러오는 중...";
  $("dlg").showModal();
  try {
    const t = await api("/traces/" + encodeURIComponent(row.id));
    const steps = t.observations.map((o) => `
      <div class="step"><span class="chip">${esc(o.type)}</span>
        <div><b>${esc(o.name)}</b><div class="mono" style="color:var(--muted)">${esc((o.output || "").slice(0, 220))}</div></div>
        <div style="text-align:right">${ms(o.latency_ms)}</div></div>`).join("");
    const ctx = t.context.length
      ? t.context.map((c) => `<div class="row"><span class="chip">L${c.tier}</span><a onclick="openMemory('${esc(c.uri)}')" class="mono" style="cursor:pointer;text-decoration:underline">${esc(short(c.uri))}</a><span style="color:var(--muted)">${c.tokens}t</span></div>`).join("")
      : `<div style="color:var(--muted)">컨텍스트 없음</div>`;
    const scores = t.scores.length
      ? t.scores.map((s) => `<div class="row"><span class="chip ${scoreClass(s.value)}">${esc(s.name)} ${s.value}</span><span>${esc(s.comment)}</span></div>`).join("")
      : `<div style="color:var(--muted)">점수 없음 — 보통은 에이전트가 jarvis_score 로 보냅니다</div>`;
    $("dlg-body").innerHTML = `
      <div class="row" style="margin-bottom:10px">
        <span class="chip">${esc(t.scope)}</span>
        <span class="chip">${t.cache_hit ? "재사용" : "생성"}</span>
        <span class="chip">조립 ${ms(t.latency_ms)}</span>
        <span class="chip">전체 ${ms(t.total_ms || t.latency_ms)}</span>
        ${t.agent ? `<span class="chip">${esc(t.agent)}</span>` : ""}</div>
      <h3 style="font-size:12px;color:var(--muted)">질문</h3><pre>${esc(t.input)}</pre>
      <h3 style="font-size:12px;color:var(--muted)">답변</h3><pre>${esc((t.output || "(없음)").slice(0, 3000))}</pre>
      <h3 style="font-size:12px;color:var(--muted)">단계</h3>${steps || "<div style='color:var(--muted)'>없음</div>"}
      <h3 style="font-size:12px;color:var(--muted);margin-top:14px">이 답에 쓰인 지식 (클릭해 수정)</h3>${ctx}
      <h3 style="font-size:12px;color:var(--muted);margin-top:14px">점수</h3>${scores}`;
  } catch (e) { $("dlg-body").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
document.querySelectorAll("[data-score]").forEach((b) => b.onclick = async () => {
  if (!currentTrace) return;
  try {
    await api("/scores", { method: "POST", body: JSON.stringify({
      trace_id: currentTrace, name: "helpfulness", value: Number(b.dataset.score) })});
    await openTrace({ id: currentTrace }); load();
  } catch (e) { $("dlg-body").insertAdjacentHTML("afterbegin", `<div class="err">${esc(e.message)}</div>`); }
});
$("dlg-close").onclick = () => $("dlg").close();

// ---------------- boot ----------------
function fillProjectSelects() {
  const opts = PROJECTS.map((p) => `<option>${esc(p.project)}</option>`).join("");
  for (const id of ["k-project", "a-project"]) {
    const el = $(id), prev = el.value;
    el.innerHTML = (id === "a-project" ? `<option value="">전체</option>` : "") + opts;
    if (prev && [...el.options].some((o) => o.value === prev)) el.value = prev;
  }
}

async function boot() {
  PROJECTS = await api("/projects");
  if (!TEMPLATES.length) {
    TEMPLATES = await api("/templates");
    $("np-template").innerHTML = TEMPLATES.map((t) =>
      `<option value="${esc(t.template)}" ${t.template === "coding" ? "selected" : ""}>${
        esc(t.template)} — ${esc(t.categories.join(", "))}</option>`).join("");
  }
  fillProjectSelects();
}

async function load() {
  $("error").innerHTML = "";
  try {
    const h = await api("/health");
    $("ver").textContent = "v" + h.version;
    $("status").textContent = h.auth_required ? "인증 필요" : "인증 없음";
    await boot();
    if (tab === "projects") renderProjects();
    else if (tab === "knowledge") await loadKnowledge();
    else await loadActivity();
    if (tab !== "knowledge") {
      const s = await api("/review/summary");
      $("badge").hidden = !s.total; $("badge").textContent = s.total;
    }
  } catch (e) {
    $("error").innerHTML = `<div class="err">불러오지 못했습니다: ${esc(e.message)}</div>`;
  }
}
$("refresh").onclick = load;
$("k-project").onchange = loadKnowledge;
$("a-project").onchange = loadActivity;
$("days").onchange = loadActivity;
window.openMemory = openMemory;
load();
</script>
</body>
</html>
"""
