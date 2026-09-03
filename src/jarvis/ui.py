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
  .chip .x { background:none; border:none; padding:0 0 0 4px; color:var(--muted); font-size:13px; line-height:1; cursor:pointer; }
  .chip .x:hover { color:var(--bad); }
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
  <span id="me" class="chip" title="" hidden></span>
  <span id="quality" class="chip" title=""></span>
  <span id="status" class="chip"></span>
</header>

<nav>
  <button data-tab="projects" class="on">프로젝트</button>
  <button data-tab="connections">연결</button>
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
          <div class="fld"><label>이 프로젝트 종류 (모르면 coding)</label><select id="np-template"></select></div>
        </div>
        <div class="fld"><label>설명 (선택)</label><input id="np-desc" placeholder="결제 API 서버"></div>
        <div class="fld"><label>이 프로젝트의 git 주소 (GitHub 등, 선택) — 넣으면 어느 머신에서든 이 저장소가 자동으로 이 프로젝트로 연결됩니다</label>
          <input id="np-repo" placeholder="git@github.com:me/backend.git"></div>
        <div class="row"><button class="primary" id="np-create">만들기</button>
          <span id="np-msg" style="color:var(--muted)"></span></div>
      </div>
    </section>
    <section id="aliases-section" hidden>
      <h2>저장소 연결 — 어느 git 주소가 어느 프로젝트로 가나</h2>
      <div class="panel pad" id="aliases-panel">불러오는 중...</div>
    </section>
    <section>
      <h2>백업 — 볼륨이 사라져도 살아남는 사본</h2>
      <div class="panel pad" id="bk-panel">불러오는 중...</div>
    </section>
  </div>

  <!-- ============ 연결 (접속 키 관리) ============ -->
  <div class="tab" id="tab-connections">
    <section>
      <h2>접속 키 — 누가 이 서버에 연결할 수 있나</h2>
      <div class="panel pad" id="keys-panel">불러오는 중...</div>
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
      <h2>다시 요청된 일 — 아직 해결되지 않았을 수 있습니다</h2>
      <div class="panel scroll"><table id="threads"></table></div>
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
    <section><h2>활동 중인 에이전트 (기록 기준)</h2>
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

// How the answer landed, judged by what got asked next rather than by anyone
// filing a rating — which is the only judgement that reliably exists.
const OUTCOMES = {
  reworked: ["다시 요청됨", "bad", "사용자가 문제를 언급하며 다시 요청했습니다"],
  repeated: ["같은 요청 반복", "warn", "같은 요청이 곧 다시 들어왔습니다"],
  moved_on: ["넘어감", "ok", "사용자가 다른 주제로 넘어갔습니다"],
};
function outcomeChip(trace) {
  const kind = (trace.metadata || {}).implicit_outcome
    && trace.metadata.implicit_outcome.kind;
  if (!kind) return `<span style="color:var(--muted)">—</span>`;
  const [label, cls, why] = OUTCOMES[kind] || [kind, "", ""];
  return `<span class="chip ${cls}" title="${esc(why)}">${esc(label)}</span>`;
}

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
        ${p.first_try_rate == null ? "" : `<span>한 번에 <b>${pct(p.first_try_rate)}</b></span>`}
        <span class="chip">${esc(p.template)}</span>
      </div>
      <div class="st" style="margin-top:6px">
        ${p.aliases.length ? `<span class="chip">연결됨</span>` : `<span class="chip warn">연결 대기</span>`}
        ${p.tasks
          ? `<span class="chip">캡처 중 · 최근 ${esc(when(p.last_active))}</span>`
          : (p.aliases.length
              ? `<span class="chip warn">캡처 없음 — 카드를 열어 ②훅 단계를 다시 확인</span>`
              : "")}
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
let connEmbedKey = null;  // 특정 키(팀원용으로 방금 발급한 것)를 심을 때만 설정
async function openConnection(project, embedKey) {
  connProject = project;
  connEmbedKey = embedKey || null;
  $("conn-title").textContent = project + " — 연결정보";
  $("conn-body").innerHTML = "불러오는 중...";
  $("conn").showModal();
  await renderConnection();
}

async function renderConnection() {
  const client = $("conn-client").value || "claude-code";
  try {
    // 심을 키는 URL 이 아니라 본문으로 보낸다 (프록시·브라우저 기록에 안 남게).
    const embed = connEmbedKey || keyBox.value.trim();
    const c = await api(`/projects/${encodeURIComponent(connProject)}/connection`, {
      method: "POST",
      body: JSON.stringify({ client, key: embed, base_url: location.origin }),
    });
    if (!$("conn-client").options.length) {
      $("conn-client").innerHTML = c.clients.map((x) =>
        `<option ${x === client ? "selected" : ""}>${esc(x)}</option>`).join("");
    }
    const needKey = c.auth_required && !c.has_key;
    const installCmd = `jv agent hooks --install --url ${location.origin}`
      + (embed ? ` --key ${embed}` : "");
    $("conn-body").innerHTML = `
      <div style="font-size:12.5px;color:var(--muted);margin-bottom:10px;line-height:1.7">
        아래를 위에서부터 복사해 붙이면 됩니다. ① 에이전트에 도구를 연결하고
        ② 자동 기록을 켜고 ③ 언제 쓸지 알려줍니다. 전부 <b>이 프로젝트 저장소 폴더 안</b>에서 합니다.
      </div>
      ${connEmbedKey ? `<div class="why">이 설정에는 방금 발급한 <b>전용 키</b>가 들어 있습니다 —
        이 사람(또는 이 기기)에게 그대로 전달하세요.</div>` : ""}
      ${needKey ? `<div class="why">이 서버는 API 키를 요구합니다. 화면 오른쪽 위 칸에 키를 넣으면
        아래 설정에 자동으로 포함됩니다. 키가 없으면 <b>연결</b> 탭에서 새로 발급하세요.</div>` : ""}
      ${(!connEmbedKey && c.has_key && ME && ME.admin && ME.name) ? `
      <div class="why">지금 아래 설정에는 <b>이 브라우저의 키(${esc(ME.name)} · 전체 접근)</b>가 들어갑니다.
        내 기기용이면 그대로 쓰세요. <b>다른 사람이나 다른 기기에 줄 설정</b>이면 그 사람 전용 키로 만드세요 —
        나중에 그 키만 폐기할 수 있습니다.
        <div class="row" style="margin-top:6px">
          <input id="cn-key-name" placeholder="예: 지훈-노트북" style="flex:1;min-width:160px">
          <button class="small" id="cn-key-mint">이 프로젝트 전용 키 발급 → 설정 만들기</button>
        </div></div>` : ""}

      <div class="step-n"><b>1</b><div><strong>MCP 서버 등록</strong>
        <span>— ${esc(c.where)}</span></div></div>
      ${copyBlock(c.setup)}
      ${c.has_key ? `<div style="color:var(--muted);font-size:11.5px;margin:-4px 0 8px">
        ⚠ 아래 설정들에는 실제 키가 들어 있습니다 — 공개 저장소에 커밋하거나 채팅에 붙이지 마세요.</div>` : ""}

      ${c.hooks_setup ? `
      <div class="step-n"><b>2</b><div><strong>자동 캡처 훅 (권장)</strong>
        <span>— 이걸 켜야 '자동으로' 기록됩니다</span></div></div>
      <div style="color:var(--muted);font-size:12.5px;margin:-2px 0 6px">
        세션이 시작되면 이전 작업 브리핑이 자동 주입되고, 모든 질문·답변이 자동으로 기록됩니다.
        에이전트가 도구 호출을 잊어도 기록이 남습니다.
      </div>
      <div style="font-size:12.5px;margin:0 0 4px"><b>권장:</b> 그 저장소 폴더에서 아래 한 줄 실행
        <span style="color:var(--muted)">(에이전트 머신에 <span class="mono">pip install my-viking</span> 이 되어 있어야 합니다)</span></div>
      ${copyBlock(installCmd)}
      <details style="margin:6px 0"><summary style="cursor:pointer;font-size:12.5px;color:var(--muted)">직접 병합하려면 (JSON)</summary>
        <div style="color:var(--muted);font-size:12px;margin:4px 0">저장소의 <span class="mono">.claude/settings.json</span> 을 열어
          — 파일이 없으면 새로 만들고, 이미 있으면 <span class="mono">hooks</span> 항목만 추가하세요.</div>
        ${copyBlock(c.hooks_setup)}
      </details>` : ""}

      <div class="step-n"><b>${c.hooks_setup ? 3 : 2}</b><div><strong>에이전트 지시문</strong>
        <span>— 저장소의 <span class="mono">${esc(c.instruction_file)}</span> 에 추가</span></div></div>
      <div style="color:var(--muted);font-size:12.5px;margin:-2px 0 4px">
        ${c.instructions_hooks
          ? "훅이 기록을 자동화하므로 지시문은 에이전트만 판단할 수 있는 두 가지(확정된 지식 기록, 결과 평가)만 남습니다. 훅 없이 쓰려면 <span class='mono'>jv agent config</span> 의 수동 지시문을 사용하세요."
          : "이 단계를 빼면 도구는 연결되지만 에이전트가 호출하지 않아 지식이 쌓이지 않습니다."}
      </div>
      ${copyBlock(c.instructions_hooks || c.instructions)}

      <div class="step-n"><b>${c.hooks_setup ? 4 : 3}</b><div><strong>끝</strong>
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
            ? c.aliases.map((a) => `<span class="chip mono">${esc(a.alias)}
                <button class="x" data-unbind="${esc(a.alias)}" title="이 연결 해제">×</button></span>`).join("")
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
    $("conn-body").querySelectorAll("[data-unbind]").forEach((b) => b.onclick = async () => {
      if (!confirm(`'${b.dataset.unbind}' 연결을 해제할까요? 그 저장소에서의 작업이 더는 이 프로젝트로 오지 않습니다.`)) return;
      await api("/aliases?alias=" + encodeURIComponent(b.dataset.unbind), { method: "DELETE" });
      await boot(); await renderConnection();
    });
    const mint = $("cn-key-mint");
    if (mint) mint.onclick = async () => {
      const name = $("cn-key-name").value.trim();
      if (!name) { $("cn-key-name").focus(); return; }
      mint.disabled = true; mint.textContent = "발급 중...";
      try {
        const r = await api("/keys", { method: "POST",
          body: JSON.stringify({ name, projects: [connProject] })});
        await openConnection(connProject, r.key);  // 그 사람 키가 들어간 설정으로 다시 그림
      } catch (e) { alert(e.message); mint.disabled = false; mint.textContent = "이 프로젝트 전용 키 발급 → 설정 만들기"; }
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
  const [profile, mems, review, brief] = await Promise.all([
    api(`/projects/${encodeURIComponent(project)}/profile`),
    api(`/projects/${encodeURIComponent(project)}/memories?limit=500`),
    api(`/projects/${encodeURIComponent(project)}/review`),
    api(`/projects/${encodeURIComponent(project)}/brief`),
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

  table($("threads"),
    [{ label: "결말", get: (r) => `<span class="chip ${(OUTCOMES[r.kind] || ["", ""])[1]}">${esc((OUTCOMES[r.kind] || [r.kind])[0])}</span>` },
     { label: "요청", get: (r) => `<b>${esc(r.question || "")}</b><div style="color:var(--muted)">${esc((r.answer || "").slice(0, 130))}</div>`, cls: "wrap" },
     { label: "근거", get: (r) => `<span style="color:var(--muted)">${esc(r.why || "")}</span>`, cls: "wrap" },
     { label: "시각", get: (r) => esc(when(r.at)), cls: "mono" }],
    brief.open_threads || [], null,
    `<b>다시 요청된 일이 없습니다.</b><br>요청이 한 번에 해결되고 있다는 뜻입니다.`);

  const threadCount = (brief.open_threads || []).length;
  $("badge").hidden = !(review.length + threadCount);
  $("badge").textContent = review.length + threadCount;
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
    // /agents 는 전체 집계라 스코프 키에는 403 — 그 한 칸만 비우고 나머지는 살린다.
    api("/agents").catch(() => []),
    api(`/worksessions?project=${encodeURIComponent(project)}&limit=12`),
  ]);
  const avg = m.scores.length
    ? m.scores.reduce((a, s) => a + s.avg * s.count, 0) / m.scores.reduce((a, s) => a + s.count, 0) : null;
  const oc = m.outcomes || {};
  const judged = Object.values(oc).reduce((a, b) => a + b, 0);
  $("cards").innerHTML = [
    card("한 번에 해결", m.first_try_rate == null ? "—" : pct(m.first_try_rate),
         judged ? `다시 요청 ${(oc.reworked || 0) + (oc.repeated || 0)} / 판정 ${judged}건`
                : "다음 요청이 판정 근거입니다",
         m.first_try_rate == null ? "" : scoreClass(m.first_try_rate)),
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
     { label: "결말", get: (r) => outcomeChip(r) },
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
    `<b>아직 기록된 에이전트 활동이 없습니다.</b><br>연결 후 첫 작업이 들어오면 여기에 나타납니다.`);
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
      ${(t.metadata || {}).implicit_outcome ? `
        <div class="why">${outcomeChip(t)}
          ${esc(t.metadata.implicit_outcome.why)}<br>
          <span style="color:var(--muted)">다음 요청: ${esc(t.metadata.implicit_outcome.next_question)}</span>
        </div>` : ""}
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

// ---------------- 연결 (접속 키 관리) ----------------
// A self-hosted server holds every project's context; once it leaves localhost
// it needs per-person, per-machine, individually revocable keys. That whole
// lifecycle used to be CLI-only — this is the GUI for it.
function authHelpPanel() {
  return `<div class="panel pad">
    <div style="font-size:13px"><b>이 서버는 API 키가 필요합니다.</b></div>
    <div style="color:var(--muted);font-size:12.5px;margin:6px 0;line-height:1.7">
      서버를 <span class="mono">--public</span> 으로 처음 켰을 때 터미널에 출력된
      <span class="mono">jv_…</span> 키를 오른쪽 위 칸에 붙여넣으세요.
      잃어버렸다면 서버에서 아래 명령으로 새로 발급합니다:
    </div>
    ${copyBlock("docker compose exec myviking jv key create default")}
  </div>`;
}
const isAuthErr = (e) => String((e && e.message) || "").startsWith("401");

function keyStateChip(k) {
  if (k.revoked) return `<span class="chip bad">폐기됨</span>`;
  if (!k.last_used) return `<span class="chip warn">미사용</span>`;
  return `<span class="chip ok">활성</span>`;
}
function keyScopeChip(projects) {
  if (!projects || projects === "*") return `<span class="chip">전체 접근</span>`;
  return projects.split(",").filter(Boolean).map((p) =>
    `<span class="chip mono">${esc(p)}</span>`).join(" ");
}

async function loadKeys() {
  const box = $("keys-panel");
  try {
    await api("/keys");  // 접근 가능한지 먼저 확인 (스코프 키는 403)
  } catch (e) {
    if (isAuthErr(e)) { box.innerHTML = authHelpPanel(); return; }
    if (String(e.message).startsWith("403")) {
      box.innerHTML = `<div class="why">접속 키는 <b>전체 접근(관리자) 키</b>로만 관리할 수 있습니다.
        오른쪽 위 칸에 관리자 키를 넣으세요 — 서버를 처음 연 사람이 받은 키입니다.</div>`;
      return;
    }
    box.innerHTML = `<div class="err">${esc(e.message)}</div>`;
    return;
  }
  const projOpts = PROJECTS.map((p) =>
    `<option value="${esc(p.project)}">${esc(p.project)}</option>`).join("");
  box.innerHTML = `
    <div style="font-size:12.5px;color:var(--muted);margin-bottom:12px;line-height:1.7">
      사람마다·기기마다 <b>따로</b> 키를 발급하세요. 그래야 한 사람이 떠나거나 키가 새면
      그 키만 폐기하고 나머지는 그대로 둘 수 있습니다. 범위를 좁히면 그 키는 지정한
      프로젝트에만 닿습니다 (최소 권한).
    </div>
    <div class="split">
      <div class="fld"><label>이름 (누구/어느 기기인지)</label>
        <input id="nk-name" placeholder="예: 지훈-노트북"></div>
      <div class="fld"><label>범위</label>
        <label style="text-transform:none;font-size:12.5px;color:var(--ink);display:block;margin-bottom:4px">
          <input type="checkbox" id="nk-all" style="width:auto"> 전체 접근 (모든 프로젝트)</label>
        <select id="nk-projects" multiple size="3" style="width:100%">${projOpts}</select>
        <div style="color:var(--muted);font-size:11.5px;margin-top:3px">여러 개는 Ctrl/⌘ 로 다중 선택</div>
      </div>
    </div>
    <div class="row"><button class="primary" id="nk-create">키 발급</button>
      <span id="nk-msg" style="color:var(--muted)"></span></div>
    <div id="nk-result"></div>
    <div class="scroll" style="margin-top:14px"><table id="keys-table"></table></div>`;

  $("nk-all").onchange = () => { $("nk-projects").disabled = $("nk-all").checked; };
  $("nk-create").onclick = async () => {
    const name = $("nk-name").value.trim();
    if (!name) { $("nk-msg").textContent = "이름을 입력하세요."; return; }
    const all = $("nk-all").checked;
    const projects = all ? ["*"] : [...$("nk-projects").selectedOptions].map((o) => o.value);
    if (!all && !projects.length) { $("nk-msg").textContent = "범위를 고르거나 '전체 접근'을 켜세요."; return; }
    $("nk-msg").textContent = "발급 중...";
    try {
      const r = await api("/keys", { method: "POST", body: JSON.stringify({ name, projects })});
      $("nk-msg").textContent = ""; $("nk-name").value = "";
      renderNewKey(r, all ? [] : projects);
      await loadKeysTable();
    } catch (e) { $("nk-msg").innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
  };
  await loadKeysTable();
}

function renderNewKey(r, projects) {
  const scopeList = projects.length ? projects : PROJECTS.map((p) => p.project);
  const pick = scopeList.length
    ? `<div class="row" style="margin-top:8px">
        <span style="font-size:12.5px;color:var(--muted)">이 키로 연결 설정 만들기:</span>
        <select id="nk-conn-proj">${scopeList.map((p) => `<option>${esc(p)}</option>`).join("")}</select>
        <button class="small" id="nk-conn-go">연결 설정 보기</button></div>`
    : "";
  $("nk-result").innerHTML = `
    <div class="panel pad" style="margin-top:10px;border-color:var(--accent)">
      <div style="font-size:13px"><b>${esc(r.name)}</b> 키를 발급했습니다.</div>
      <div style="color:var(--warn);font-size:12.5px;margin:4px 0 6px">
        이 값은 다시 볼 수 없습니다. 지금 복사해 전달하세요.</div>
      ${copyBlock(r.key)}
      ${pick}
    </div>`;
  const go = $("nk-conn-go");
  if (go) go.onclick = () => openConnection($("nk-conn-proj").value, r.key);
}

async function loadKeysTable() {
  const keys = await api("/keys");
  table($("keys-table"),
    [{ label: "이름", get: (r) => `<b>${esc(r.name)}</b>` },
     { label: "범위", get: (r) => keyScopeChip(r.projects) },
     { label: "사용", get: (r) => r.calls },
     { label: "마지막 사용", get: (r) => esc(when(r.last_used)), cls: "mono" },
     { label: "상태", get: (r) => keyStateChip(r) },
     { label: "", get: (r) => r.revoked ? "" : `<button class="small" data-revoke="${esc(r.id)}">폐기</button>` }],
    keys, null,
    `<b>아직 발급된 키가 없습니다.</b><br>위에서 사람·기기별로 하나씩 발급하세요.`);
  $("keys-table").querySelectorAll("[data-revoke]").forEach((b) => b.onclick = async () => {
    if (!confirm("이 키를 폐기하면 즉시 접속이 막힙니다. 계속할까요?")) return;
    try {
      await api("/keys/" + encodeURIComponent(b.dataset.revoke), { method: "DELETE" });
      await loadKeysTable();
    } catch (e) { alert(e.message); }
  });
}

// ---------------- 내 키 (헤더 칩) ----------------
// 스코프 사용자는 자기 울타리를 보고, 관리자는 자기가 전체 접근 키로 들어와
// 있음을 의식한다 (그 키를 남에게 나눠주기 전에).
let ME = null;
async function loadMe() {
  const chip = $("me");
  try { ME = await api("/me"); } catch (e) { ME = null; chip.hidden = true; return; }
  if (!ME.auth_required || !ME.name) { chip.hidden = true; return; }
  chip.hidden = false;
  if (ME.admin) {
    chip.textContent = `${ME.name} · 전체 접근`;
    chip.className = "chip ok";
    chip.title = "이 브라우저의 키는 모든 프로젝트와 키 관리에 접근할 수 있습니다";
  } else {
    chip.textContent = `${ME.name} · ${ME.projects.join(", ")}`;
    chip.className = "chip";
    chip.title = "이 키가 닿을 수 있는 프로젝트만 보입니다";
  }
}

// ---------------- 저장소 연결 전체 보기 ----------------
// 프로젝트별 다이얼로그를 하나씩 여는 대신 '어느 remote 가 어느 프로젝트로 가나'를
// 한 표로. 전체 목록은 전체 접근 키만 볼 수 있으므로 스코프 키에겐 섹션을 숨긴다.
async function loadAliases() {
  const sec = $("aliases-section");
  let rows;
  try { rows = await api("/aliases"); } catch (e) { sec.hidden = true; return; }
  sec.hidden = false;
  const box = $("aliases-panel");
  box.innerHTML = `
    <div style="font-size:12.5px;color:var(--muted);margin-bottom:8px;line-height:1.7">
      에이전트는 작업 중인 저장소의 git 주소로 프로젝트를 찾습니다. 여기 없는 저장소는
      이름이 같은 프로젝트로 추측하거나 새 프로젝트를 만듭니다 — 의도와 다르면 각 프로젝트
      카드를 열어 '저장소 연결'에서 등록하세요.
    </div>
    <div class="scroll"><table id="aliases-table"></table></div>`;
  table($("aliases-table"),
    [{ label: "git 주소 / 경로", get: (r) => esc(r.alias), cls: "mono" },
     { label: "종류", get: (r) => `<span class="chip">${esc(r.kind || "repo")}</span>` },
     { label: "프로젝트", get: (r) => `<b>${esc(r.project)}</b>` },
     { label: "", get: (r) => `<button class="small" data-unbind="${esc(r.alias)}">해제</button>` }],
    rows, null,
    `<b>아직 연결된 저장소가 없습니다.</b><br>프로젝트 카드를 열어 git 주소를 등록하면 여기 모입니다.`);
  $("aliases-table").querySelectorAll("[data-unbind]").forEach((b) => b.onclick = async () => {
    if (!confirm(`'${b.dataset.unbind}' 연결을 해제할까요?`)) return;
    try {
      await api("/aliases?alias=" + encodeURIComponent(b.dataset.unbind), { method: "DELETE" });
      await boot(); renderProjects(); await loadAliases();
    } catch (e) { alert(e.message); }
  });
}

// 평문 HTTP 로 루프백 아닌 곳에 접속하면 키가 그대로 노출된다 — 배너로 경고.
function renderInsecureWarning() {
  const host = location.hostname;
  const loopback = ["localhost", "127.0.0.1", "::1", "[::1]"].includes(host);
  let el = $("insecure");
  if (location.protocol === "http:" && !loopback) {
    if (!el) {
      el = document.createElement("div");
      el.id = "insecure"; el.className = "err";
      el.style.cssText = "background:var(--chip);border-radius:8px;margin-bottom:12px";
      document.querySelector("main").prepend(el);
    }
    el.innerHTML = `⚠ 평문 HTTP 로 접속 중입니다 (<span class="mono">${esc(location.host)}</span>).
      여기 넣는 API 키와 복사한 설정이 네트워크에 그대로 노출됩니다 — HTTPS(TLS)로 접속하세요.`;
  } else if (el) { el.remove(); }
}

// ---------------- boot ----------------
function fillProjectSelects() {
  const opts = PROJECTS.map((p) => `<option>${esc(p.project)}</option>`).join("");
  // '전체' 집계는 전체 접근 키만 가능 — 스코프 사용자에겐 그 선택지를 아예 안 보인다
  // (보이면 기본값이 되어 활동 탭이 403 으로 열리지도 못한다).
  const allOk = !ME || ME.admin;
  for (const id of ["k-project", "a-project"]) {
    const el = $(id), prev = el.value;
    el.innerHTML = (id === "a-project" && allOk ? `<option value="">전체</option>` : "") + opts;
    if (prev && [...el.options].some((o) => o.value === prev)) el.value = prev;
  }
}

// ---------------- 백업 ----------------
let bkPoll = null;

async function loadBackup() {
  try {
    renderBackup(await api("/backup/status"));
  } catch (e) {
    $("bk-panel").innerHTML = `<span class="bad">${esc(e.message)}</span>`;
  }
}

function renderBackup(st) {
  if (st.connected && st.provider === "gdrive") renderBackupConnected(st);
  else if (st.provider === "local") renderBackupLocal(st);
  else renderBackupWizard(st);
}

function bkLastLine(st) {
  if (!st.last_run) return "아직 없음";
  const when = st.last_run.slice(0, 16).replace("T", " ");
  const result = st.last_status === "ok" ? "성공" : `<span class="bad">${esc(st.last_status)}</span>`;
  const file = st.last_name
    ? ` · <span class="mono">${esc(st.last_name)}</span> (${(st.last_size / 1e6).toFixed(1)}MB)` : "";
  return `${when} — ${result}${file}`;
}

function bkScheduleRow(st) {
  return `
    <div class="row" style="margin-top:10px;border-top:1px solid var(--line);padding-top:10px">
      <label style="font-size:12.5px;color:var(--muted)">주기(시간)</label>
      <input id="bk-every" style="width:70px" value="${Number(st.every_hours)}">
      <label style="font-size:12.5px;color:var(--muted)">보관 개수</label>
      <input id="bk-keep" style="width:70px" value="${Number(st.keep)}">
      <button class="small" id="bk-save">저장</button>
      <span id="bk-msg" style="color:var(--muted);font-size:12.5px"></span>
    </div>`;
}

function bkWireCommon() {
  const save = $("bk-save");
  if (save) save.onclick = async () => {
    try {
      await api("/backup/config", { method: "POST", body: JSON.stringify({
        every_hours: parseFloat($("bk-every").value) || null,
        keep: parseInt($("bk-keep").value, 10) || null,
      })});
      $("bk-msg").textContent = "저장했습니다.";
    } catch (e) { $("bk-msg").innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
  };
  const run = $("bk-run");
  if (run) run.onclick = async () => {
    run.disabled = true; run.textContent = "백업 중...";
    try {
      const res = await api("/backup/run", { method: "POST" });
      // The point of the button is *seeing it land*: refresh the remote
      // listing immediately so the new archive shows up in front of you.
      await bkLoadList(`방금 <span class="mono">${esc(res.name)}</span> 을 올렸습니다. 아래 목록에 보이면 성공입니다.`);
    } catch (e) { $("bk-msg").innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
    run.disabled = false; run.textContent = "지금 백업";
    const s = await api("/backup/status");
    $("bk-last").innerHTML = "마지막 백업: " + bkLastLine(s);
  };
  const list = $("bk-refresh-list");
  if (list) list.onclick = () => bkLoadList();
  const dis = $("bk-disconnect");
  if (dis) dis.onclick = async () => {
    if (!confirm("Google Drive 연결을 해제할까요? 이미 올라간 백업은 드라이브에 그대로 남습니다.")) return;
    await api("/backup/disconnect", { method: "POST" });
    await loadBackup();
  };
  const toGd = $("bk-to-gdrive");
  if (toGd) toGd.onclick = async () => {
    // 디렉터리 설정은 남겨둔 채 연결 마법사로 돌아간다.
    await api("/backup/config", { method: "POST", body: JSON.stringify({ provider: "none" }) });
    await loadBackup();
  };
}

async function bkLoadList(note) {
  const box = $("bk-list");
  box.innerHTML = `<span style="color:var(--muted);font-size:12.5px">목록을 불러오는 중...</span>`;
  try {
    const files = await api("/backup/list");
    box.innerHTML = `
      ${note ? `<div style="font-size:12.5px;margin-bottom:6px">${note}</div>` : ""}
      ${files.length
        ? `<table style="font-size:12.5px">${files.map((f) =>
            `<tr><td class="mono">${esc(f.name)}</td>
             <td style="text-align:right;color:var(--muted)">${(Number(f.size) / 1e6).toFixed(1)}MB</td></tr>`).join("")}
           </table>
           <div style="color:var(--muted);font-size:12px;margin-top:4px">
             원격에 실제로 존재하는 파일 목록입니다 (서버 기록이 아니라 방금 조회한 것).</div>`
        : `<span style="color:var(--muted);font-size:12.5px">아직 원격에 백업이 없습니다. "지금 백업"으로 하나 올려보세요.</span>`}`;
  } catch (e) { box.innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
}

// ----- 연결됨: 어디로 가는지가 한눈에 보여야 한다 -----
function renderBackupConnected(st) {
  $("bk-panel").innerHTML = `
    <div class="row" style="justify-content:space-between;align-items:flex-start">
      <div>
        <div><strong style="font-size:13px">Google Drive</strong>
          <span class="chip">연결됨 · ${Number(st.every_hours)}시간마다 자동</span></div>
        <div style="margin-top:8px;font-size:12.5px;line-height:1.9">
          <div>계정&nbsp;&nbsp;<span class="mono">${esc(st.account || "(확인 안 됨)")}</span></div>
          <div>폴더&nbsp;&nbsp;<span class="mono">${esc(st.folder)}</span>
            ${st.folder_url
              ? ` — <a href="${esc(st.folder_url)}" target="_blank">Drive 에서 직접 확인 ↗</a>`
              : ""}</div>
          <div id="bk-last" style="color:var(--muted)">마지막 백업: ${bkLastLine(st)}</div>
        </div>
      </div>
      <div class="row">
        <button class="small primary" id="bk-run">지금 백업</button>
        <button class="small" id="bk-refresh-list">폴더 내용 확인</button>
        <button class="small" id="bk-disconnect">연결 해제</button>
      </div>
    </div>
    <div id="bk-list" style="margin-top:8px"></div>
    ${bkScheduleRow(st)}`;
  bkWireCommon();
}

// ----- local 디렉터리로 받는 경우 -----
function renderBackupLocal(st) {
  $("bk-panel").innerHTML = `
    <div class="row" style="justify-content:space-between;align-items:flex-start">
      <div>
        <div><strong style="font-size:13px">디렉터리 백업</strong>
          <span class="chip">연결됨</span></div>
        <div style="margin-top:8px;font-size:12.5px;line-height:1.9">
          <div>경로&nbsp;&nbsp;<span class="mono">${esc(st.local_path || "(미설정)")}</span> <span style="color:var(--muted)">(서버 기준 경로)</span></div>
          <div id="bk-last" style="color:var(--muted)">마지막 백업: ${bkLastLine(st)}</div>
        </div>
      </div>
      <div class="row">
        <button class="small primary" id="bk-run">지금 백업</button>
        <button class="small" id="bk-refresh-list">폴더 내용 확인</button>
        <button class="small" id="bk-to-gdrive">Google Drive 로 전환</button>
      </div>
    </div>
    <div id="bk-list" style="margin-top:8px"></div>
    ${bkScheduleRow(st)}`;
  bkWireCommon();
}

// ----- 연결 전: 3단계 마법사 -----
function renderBackupWizard(st) {
  $("bk-panel").innerHTML = `
    <div style="color:var(--muted);font-size:12.5px;margin-bottom:12px">
      docker 를 내렸다 올려도 데이터 볼륨은 남습니다. 이 백업은 볼륨·호스트를 잃었을 때를
      위한 서버 밖 사본입니다. 연결하면 <b>내 Google Drive 의
      "<span class="mono">${esc(st.folder)}</span>" 폴더</b>에 ${Number(st.every_hours)}시간마다
      스냅샷이 올라가고, ${Number(st.keep)}개를 넘으면 오래된 것부터 지워집니다.
      토큰은 이 앱이 만든 폴더만 접근할 수 있습니다 (drive.file 범위).
    </div>

    <div class="step-n"><b>1</b><div><strong>Google Cloud 에서 OAuth 클라이언트 만들기</strong>
      <span>— 처음 한 번만</span></div></div>
    <div style="font-size:12.5px;color:var(--muted);margin:2px 0 10px;line-height:1.9">
      ① <a href="https://console.cloud.google.com/apis/library/drive.googleapis.com" target="_blank">Google Drive API ↗</a> 를 "사용 설정"<br>
      ② <a href="https://console.cloud.google.com/apis/credentials" target="_blank">사용자 인증 정보 ↗</a> 에서
      "사용자 인증 정보 만들기 → OAuth 클라이언트 ID" — 애플리케이션 유형은 <b>"TV 및 제한된 입력 장치"</b><br>
      ③ 만들어진 <b>클라이언트 ID</b> 와 <b>클라이언트 보안 비밀번호</b>를 복사
    </div>

    <div class="step-n"><b>2</b><div><strong>이 서버에 알려주기</strong></div></div>
    <div class="row" style="margin:2px 0 10px">
      <input id="bk-cid" placeholder="클라이언트 ID (….apps.googleusercontent.com)" style="flex:1;min-width:220px">
      <input id="bk-csec" placeholder="클라이언트 보안 비밀번호 (GOCSPX-…)" type="password" style="width:220px">
      <button class="small primary" id="bk-connect">연결 시작</button>
    </div>

    <div class="step-n"><b>3</b><div><strong>내 구글 계정으로 승인</strong>
      <span>— 휴대폰이든 노트북이든 아무 브라우저에서</span></div></div>
    <div id="bk-auth" style="font-size:13px;margin-top:2px;color:var(--muted)">
      연결 시작을 누르면 여기에 열 주소와 입력할 코드가 표시됩니다.
    </div>`;

  // 새로고침으로 끊긴 승인 대기가 있으면 코드를 다시 보여주고 이어서 기다린다.
  if (st.pending_auth && st.pending_user_code) {
    bkShowCode(st.pending_verification_url, st.pending_user_code, 5, true);
  }

  $("bk-connect").onclick = async () => {
    const cid = $("bk-cid").value.trim(), sec = $("bk-csec").value.trim();
    if (!cid || !sec) {
      $("bk-auth").innerHTML = `<span class="bad">클라이언트 ID 와 보안 비밀번호를 모두 넣어야 합니다.</span>`;
      return;
    }
    $("bk-connect").disabled = true;
    try {
      const info = await api("/backup/connect/start", { method: "POST",
        body: JSON.stringify({ client_id: cid, client_secret: sec }) });
      bkShowCode(info.verification_url, info.user_code, info.interval || 5, false);
    } catch (e) {
      $("bk-auth").innerHTML = `<span class="bad">${esc(e.message)}</span>`;
      $("bk-connect").disabled = false;
    }
  };
}

function bkShowCode(url, code, interval, resumed) {
  $("bk-auth").innerHTML = `
    ${resumed ? `<div style="color:var(--muted);font-size:12.5px;margin-bottom:6px">
       진행 중이던 연결이 있어 이어서 기다립니다. 처음부터 하려면 2단계를 다시 실행하세요.</div>` : ""}
    <div style="line-height:2">
      ① <a href="${esc(url)}" target="_blank">${esc(url)} ↗</a> 를 열고<br>
      ② 이 코드를 입력: <span class="mono" style="font-size:20px;letter-spacing:3px;padding:2px 8px;border:1px solid var(--line);border-radius:6px">${esc(code)}</span>
    </div>
    <div id="bk-wait" style="color:var(--muted);font-size:12.5px;margin-top:6px">
      승인을 기다리는 중… 승인이 끝나면 자동으로 다음 단계(연결 확인)로 넘어갑니다.
    </div>`;
  clearInterval(bkPoll);
  bkPoll = setInterval(async () => {
    try {
      const r = await api("/backup/connect/poll", { method: "POST" });
      if (r.status === "ok") {
        clearInterval(bkPoll);
        // 연결됨 카드로 전환 + 목적지를 즉시 검증해 보여준다.
        await loadBackup();
        await bkLoadList(
          `연결됐습니다${r.account ? ` — <span class="mono">${esc(r.account)}</span>` : ""}.` +
          ` 백업은 위 폴더로 올라갑니다. 현재 폴더 내용:`);
      } else if (r.status === "error") {
        clearInterval(bkPoll);
        $("bk-auth").innerHTML =
          `<span class="bad">승인이 거절되었거나 만료됐습니다 (${esc(r.error || "")}). 2단계부터 다시 시도하세요.</span>`;
        const cn = $("bk-connect");
        if (cn) cn.disabled = false;
      }
      // pending 이면 계속 기다린다.
    } catch (e) { /* 일시적 네트워크 문제 — 다음 폴링에서 재시도 */ }
  }, Math.max(3, interval) * 1000);
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

function renderQuality(q) {
  const el = $("quality");
  if (!q) { el.textContent = ""; el.className = "chip"; el.title = ""; return; }
  if (q.full_quality) {
    el.textContent = "품질 최상";
    el.className = "chip ok";
    el.title = `증류: ${q.llm_provider}/${q.llm_model} · 임베딩: ${q.embed_provider}/${q.embed_model || "-"} (${q.embed_dim}차원)`;
    return;
  }
  el.textContent = "품질 기본";
  el.className = "chip warn";
  const lines = (q.notes || []).slice();
  lines.push(q.reindex_automatic ? "프로바이더를 바꾸면 색인은 자동 재생성됩니다." : "");
  el.title = lines.filter(Boolean).join("\n");
}

async function load() {
  $("error").innerHTML = "";
  let h;
  try { h = await api("/health"); }
  catch (e) { $("error").innerHTML = `<div class="err">서버에 닿지 못했습니다: ${esc(e.message)}</div>`; return; }
  $("ver").textContent = "v" + h.version;
  $("status").textContent = h.auth_required ? "인증 필요" : "인증 없음";
  renderQuality(h.quality);
  renderInsecureWarning();
  // 인증이 켜진 서버에 키 없이 들어오면 boot() 부터 401 로 죽는다 — 날 오류 대신 안내.
  if (h.auth_required && !keyBox.value.trim()) { $("error").innerHTML = authHelpPanel(); return; }
  try {
    await loadMe();
    await boot();
    if (tab === "projects") { renderProjects(); await loadAliases(); await loadBackup(); }
    else if (tab === "connections") await loadKeys();
    else if (tab === "knowledge") await loadKnowledge();
    else await loadActivity();
  } catch (e) {
    if (isAuthErr(e)) { $("error").innerHTML = authHelpPanel(); return; }
    $("error").innerHTML = `<div class="err">불러오지 못했습니다: ${esc(e.message)}</div>`;
    return;
  }
  if (tab !== "knowledge") {
    try {
      const s = await api("/review/summary");
      $("badge").hidden = !s.total; $("badge").textContent = s.total;
    } catch (e) { $("badge").hidden = true; }  // 스코프 키는 전체 집계 불가 — 배지 숨김
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
