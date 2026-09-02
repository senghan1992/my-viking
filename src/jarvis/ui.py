"""The dashboard.

Ordered around how this actually gets used: you are not watching while your
agents work, so the front page is not a live monitor. It is a **review queue** —
what did my agents write into the database, and is it right?

Four tabs, in the order you need them:

* 검토      — memories wanting a decision (conflicts, unproven, unconfirmed)
* 데이터베이스 — browse and edit everything the agents know
* 작업 기록  — traces and latency, for when something felt slow
* 프롬프트   — the saved prompt library

Served by the same process as the API: nothing extra to run, no build step. The
API key, if the server requires one, stays in the browser's localStorage.
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
    border:1px solid var(--line); border-radius:6px; padding:5px 9px; }
  textarea { width:100%; font-family:var(--mono); font-size:12.5px; resize:vertical; }
  button { cursor:pointer; background:var(--chip); }
  button:hover { border-color:var(--accent); }
  button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  button.danger:hover { border-color:var(--bad); color:var(--bad); }
  .grow { flex:1; }
  nav { display:flex; gap:2px; padding:0 20px; background:var(--panel);
    border-bottom:1px solid var(--line); position:sticky; top:53px; z-index:9; }
  nav button { background:none; border:none; border-bottom:2px solid transparent;
    border-radius:0; padding:9px 14px; color:var(--muted); font-weight:600; font-size:13px; }
  nav button.on { color:var(--ink); border-bottom-color:var(--accent); }
  nav button .n { display:inline-block; margin-left:6px; padding:0 6px; border-radius:999px;
    background:var(--bad); color:#fff; font-size:11px; }
  main { padding:20px; max-width:1400px; margin:0 auto; }
  .tab { display:none; } .tab.on { display:block; }
  .cards { display:grid; gap:12px; grid-template-columns:repeat(auto-fit, minmax(160px,1fr)); }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
  .card .k { color:var(--muted); font-size:11.5px; text-transform:uppercase; letter-spacing:.06em; }
  .card .v { font-size:24px; font-weight:600; margin-top:4px; font-variant-numeric:tabular-nums; }
  .card .s { color:var(--muted); font-size:12px; margin-top:2px; }
  section { margin-top:20px; }
  section > h2 { font-size:13px; text-transform:uppercase; letter-spacing:.07em;
    color:var(--muted); margin:0 0 8px; font-weight:600; }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:10px; overflow:hidden; }
  .scroll { overflow-x:auto; }
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
  .bars div:hover { opacity:1; }
  dialog { border:1px solid var(--line); border-radius:12px; background:var(--panel);
    color:var(--ink); padding:0; width:min(920px,94vw); max-height:88vh; }
  dialog::backdrop { background:rgba(0,0,0,.45); }
  .dlg-head { display:flex; gap:8px; align-items:center; padding:12px 16px; flex-wrap:wrap;
    border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--panel); z-index:2; }
  .dlg-body { padding:14px 16px; overflow:auto; max-height:72vh; }
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
  .tree { display:grid; grid-template-columns:230px 1fr; gap:0; }
  .tree > .side { border-right:1px solid var(--line); max-height:70vh; overflow:auto; }
  .side a { display:flex; justify-content:space-between; gap:8px; padding:7px 12px;
    border-bottom:1px solid var(--line); cursor:pointer; color:var(--ink); text-decoration:none; }
  .side a:hover { background:var(--chip); } .side a.on { background:var(--chip); font-weight:600; }
  .side a .c { color:var(--muted); font-size:12px; }
  @media (max-width:760px) { .tree { grid-template-columns:1fr; } .split { grid-template-columns:1fr; } }
</style>
</head>
<body>
<header>
  <h1>MyViking <span id="ver"></span></h1>
  <select id="project"></select>
  <button id="refresh">새로고침</button>
  <span class="grow"></span>
  <input id="key" type="password" placeholder="API 키 (필요한 경우)" size="16">
  <span id="status" class="chip"></span>
</header>

<nav>
  <button data-tab="review" class="on">검토<span class="n" id="badge" hidden></span></button>
  <button data-tab="db">데이터베이스</button>
  <button data-tab="traces">작업 기록</button>
  <button data-tab="prompts">프롬프트</button>
</nav>

<main>
  <div id="error"></div>

  <div class="tab on" id="tab-review">
    <section>
      <h2>확인이 필요한 것</h2>
      <div class="panel scroll"><table id="review"></table></div>
    </section>
    <section>
      <h2>이유별 건수</h2>
      <div class="cards" id="review-cards"></div>
    </section>
  </div>

  <div class="tab" id="tab-db">
    <section>
      <h2>에이전트가 알고 있는 것</h2>
      <div class="panel tree">
        <div class="side" id="cats"></div>
        <div class="scroll"><table id="mems"></table></div>
      </div>
    </section>
  </div>

  <div class="tab" id="tab-traces">
    <section>
      <h2>응답 품질과 속도 <select id="days" style="margin-left:8px">
        <option value="1">24시간</option><option value="7" selected>7일</option>
        <option value="30">30일</option><option value="0">전체</option></select></h2>
      <div class="cards" id="cards"></div>
    </section>
    <section><h2>일별 추이 (막대 = 평균 응답 시간)</h2>
      <div class="panel"><div class="bars" id="spark"></div></div></section>
    <section><h2>시간이 어디에 쓰이나</h2>
      <div class="panel scroll"><table id="steps"></table></div></section>
    <section><h2>최근 작업</h2>
      <div class="panel scroll"><table id="traces"></table></div></section>
    <section><h2>메모리 기여도</h2>
      <div class="panel scroll"><table id="impact"></table></div></section>
    <section><h2>연결된 에이전트</h2>
      <div class="panel scroll"><table id="agents"></table></div></section>
  </div>

  <div class="tab" id="tab-prompts">
    <section>
      <h2>저장된 프롬프트</h2>
      <div class="panel scroll"><table id="prompts"></table></div>
    </section>
  </div>
</main>

<dialog id="mem">
  <div class="dlg-head">
    <strong id="mem-title">메모리</strong>
    <span class="grow"></span>
    <button class="primary" id="mem-confirm">맞음 — 확인</button>
    <button id="mem-save">수정 저장</button>
    <button class="danger" id="mem-archive">보관</button>
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
const short = (uri) => String(uri).replace(/^jarvis:\/\/(projects\/)?/, "");
const scoreClass = (v) => v === null || v === undefined ? "" : v >= 0.7 ? "ok" : v >= 0.4 ? "mid" : "bad";

// Each reason states the decision it is asking for, not just a label.
const REASONS = {
  conflict:    ["상충",     "bad",  "같은 주제에 서로 반대되는 내용이 들어왔습니다. 어느 쪽이 맞는지 정해주세요."],
  divergent:   ["내용 갈림", "bad",  "같은 주제인데 다른 내용이 한 파일에 쌓였습니다. 최신 내용만 남기세요."],
  harmful:     ["나쁜 결과", "bad",  "이 메모리가 들어간 작업들의 평가가 낮습니다. 틀렸는지 확인하세요."],
  unproven:    ["미검증",   "warn", "여러 번 쓰였지만 결과 평가가 없습니다. 자주 쓰이는 것과 맞는 것은 다릅니다."],
  unconfirmed: ["확인 대기", "",     "에이전트가 대화에서 뽑아낸 것으로, 아직 사람이 보지 않았습니다."],
  fading:      ["잊히는 중", "warn", "오래 쓰이지 않아 신뢰도가 떨어졌습니다. 필요하면 확인해 살리세요."],
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

// ---- tabs ----
let tab = "review";
document.querySelectorAll("nav button").forEach((b) => b.onclick = () => {
  tab = b.dataset.tab;
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x.id === "tab-" + tab));
  load();
});

// ---- review ----
async function loadReview(project) {
  const summary = await api("/review/summary" + (project ? `?project=${encodeURIComponent(project)}` : ""));
  $("badge").hidden = !summary.total;
  $("badge").textContent = summary.total;

  const order = ["conflict", "divergent", "harmful", "unproven", "unconfirmed", "fading"];
  $("review-cards").innerHTML = order
    .filter((r) => summary.by_reason[r])
    .map((r) => card(REASONS[r][0], String(summary.by_reason[r]), REASONS[r][2], REASONS[r][1]))
    .join("") || card("깨끗함", "0", "확인할 것이 없습니다");

  const projects = project ? [project] : Object.keys(summary.projects);
  let rows = [];
  for (const p of projects) {
    const items = await api(`/projects/${encodeURIComponent(p)}/review?limit=40`);
    rows = rows.concat(items.map((it) => Object.assign(it, { project: p })));
  }
  rows.sort((a, b) => b.priority - a.priority);

  table($("review"),
    [{ label: "이유", get: (r) => r.reasons.map(reasonChip).join(" ") },
     { label: "프로젝트", get: (r) => esc(r.project) },
     { label: "카테고리", get: (r) => `<span class="chip">${esc(r.category)}</span>` },
     { label: "내용", get: (r) => `<b>${esc(r.title)}</b><div style="color:var(--muted)">${esc((r.abstract || "").slice(0, 110))}</div>`, cls: "wrap" },
     { label: "출처", get: (r) => r.origin === "manual" ? "직접 작성" : "에이전트" },
     { label: "사용", get: (r) => r.uses },
     { label: "점수", get: (r) => r.avg_score === null ? "—" : `<b class="${scoreClass(r.avg_score)}">${r.avg_score.toFixed(2)}</b>` },
     { label: "신뢰", get: (r) => r.confidence.toFixed(2) }],
    rows, (r) => openMemory(r.uri),
    `<b>확인할 것이 없습니다.</b><br>에이전트가 새로 기록하면 여기에 쌓입니다.`);
}

// ---- database browser ----
let dbCategory = null;
async function loadDb(project) {
  if (!project) {
    $("cats").innerHTML = `<div class="empty">프로젝트를 선택하세요</div>`;
    $("mems").innerHTML = "";
    return;
  }
  const [profile, mems] = await Promise.all([
    api(`/projects/${encodeURIComponent(project)}/profile`),
    api(`/projects/${encodeURIComponent(project)}/memories?limit=500`),
  ]);
  const counts = {};
  mems.forEach((m) => counts[m.category] = (counts[m.category] || 0) + 1);
  const cats = profile.categories.map((c) => c.name);
  if (dbCategory && !cats.includes(dbCategory)) dbCategory = null;

  $("cats").innerHTML =
    `<a class="${dbCategory === null ? "on" : ""}" data-c="">전체 <span class="c">${mems.length}</span></a>` +
    cats.map((c) => `<a class="${dbCategory === c ? "on" : ""}" data-c="${esc(c)}">${esc(c)} <span class="c">${counts[c] || 0}</span></a>`).join("");
  $("cats").querySelectorAll("a").forEach((a) => a.onclick = () => {
    dbCategory = a.dataset.c || null;
    loadDb(project);
  });

  const rows = dbCategory ? mems.filter((m) => m.category === dbCategory) : mems;
  table($("mems"),
    [{ label: "카테고리", get: (r) => `<span class="chip">${esc(r.category)}</span>` },
     { label: "제목", get: (r) => `<b>${esc(r.title)}</b><div style="color:var(--muted)">${esc((r.abstract || "").slice(0, 120))}</div>`, cls: "wrap" },
     { label: "신뢰", get: (r) => r.confidence.toFixed(2) },
     { label: "사용", get: (r) => r.hits },
     { label: "토큰", get: (r) => `${r.tokens.l0}/${r.tokens.l2}` },
     { label: "수정", get: (r) => esc((r.updated || "").slice(0, 10)), cls: "mono" }],
    rows, (r) => openMemory(r.uri),
    `<b>아직 비어 있습니다.</b><br>에이전트가 <code>jarvis_remember</code> 로 기록하거나,<br><code>jv mem add</code> 로 직접 넣을 수 있습니다.`);
}

// ---- memory editor ----
let currentMem = null;
async function openMemory(uri) {
  currentMem = uri;
  $("mem-title").textContent = short(uri);
  $("mem-body").innerHTML = "불러오는 중...";
  $("mem").showModal();
  try {
    const d = await api("/memories/detail?uri=" + encodeURIComponent(uri));
    const profile = await api(`/projects/${encodeURIComponent(d.project)}/profile`);
    const why = d.reasons.map((r) => `<div class="why">${reasonChip(r)} ${esc((REASONS[r] || [, , ""])[2])}</div>`).join("");
    const clash = d.conflict ? `
      <div class="split">
        <div><label style="font-size:11.5px;color:var(--muted)">기존</label><pre>${esc(d.conflict.existing)}</pre></div>
        <div><label style="font-size:11.5px;color:var(--muted)">새로 들어온 것</label><pre>${esc(d.conflict.incoming)}</pre></div>
      </div>` : "";
    $("mem-body").innerHTML = `
      ${why}${clash}
      <div class="row" style="margin:8px 0">
        <span class="chip">${d.origin === "manual" ? "직접 작성" : "에이전트가 기록"}</span>
        <span class="chip ${d.reviewed ? "ok" : "warn"}">${d.reviewed ? "확인됨" : "미확인"}</span>
        <span class="chip">사용 ${d.hits}회</span>
        <span class="chip">작업 ${d.impact.uses}건에 포함</span>
        <span class="chip">L0 ${d.tokens.l0} / L1 ${d.tokens.l1} / L2 ${d.tokens.l2} 토큰</span>
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
        파일: <span class="mono">${esc(d.path)}</span><br>
        출처 세션: ${d.sources.length ? d.sources.map((x) => `<span class="mono">${esc(short(x))}</span>`).join(", ") : "없음"}
      </div>`;
  } catch (e) {
    $("mem-body").innerHTML = `<div class="err">${esc(e.message)}</div>`;
  }
}

$("mem-confirm").onclick = async () => {
  try {
    await api("/memories/confirm", { method: "POST", body: JSON.stringify({ uri: currentMem }) });
    $("mem").close(); load();
  } catch (e) { $("mem-body").insertAdjacentHTML("afterbegin", `<div class="err">${esc(e.message)}</div>`); }
};
$("mem-save").onclick = async () => {
  try {
    const res = await api("/memories", { method: "PATCH", body: JSON.stringify({
      uri: currentMem,
      title: $("f-title").value,
      statement: $("f-abstract").value,
      body: $("f-body").value,
      category: $("f-cat").value,
      confidence: Number($("f-conf").value),
    })});
    $("mem").close();
    if (res.moved_from) dbCategory = null;
    load();
  } catch (e) { $("mem-body").insertAdjacentHTML("afterbegin", `<div class="err">${esc(e.message)}</div>`); }
};
$("mem-archive").onclick = async () => {
  if (!confirm("보관함으로 옮깁니다. 검색에서 제외되지만 파일은 남습니다. 계속할까요?")) return;
  try {
    await api("/memories?uri=" + encodeURIComponent(currentMem) + "&archive=true", { method: "DELETE" });
    $("mem").close(); load();
  } catch (e) { $("mem-body").insertAdjacentHTML("afterbegin", `<div class="err">${esc(e.message)}</div>`); }
};
$("mem-close").onclick = () => $("mem").close();

// ---- traces ----
async function loadTraces(project) {
  const days = $("days").value;
  const q = `?project=${encodeURIComponent(project)}&days=${days}`;
  const [m, ts, traces, agents] = await Promise.all([
    api("/metrics" + q),
    api(`/timeseries?project=${encodeURIComponent(project)}&days=14`),
    api(`/traces?project=${encodeURIComponent(project)}&limit=60`),
    api("/agents"),
  ]);
  const avg = m.scores.length
    ? m.scores.reduce((a, s) => a + s.avg * s.count, 0) / m.scores.reduce((a, s) => a + s.count, 0) : null;
  $("cards").innerHTML = [
    card("응답 p50", ms(m.answer_ms.p50), `p95 ${ms(m.answer_ms.p95)}`),
    card("재사용 응답 p50", m.reuse.hits ? ms(m.answer_ms.p50_reused) : "—",
         m.reuse.hits ? `생성 ${ms(m.answer_ms.p50_generated)}` : "재사용 없음"),
    card("컨텍스트 조립 p50", ms(m.context_ms.p50), `p95 ${ms(m.context_ms.p95)}`),
    card("재사용률", pct(m.reuse.rate), `${m.reuse.hits} / ${m.traces} 건`),
    card("평균 점수", avg === null ? "—" : avg.toFixed(2),
         avg === null ? "점수 미기록" : m.scores.map((s) => `${s.name} ${s.count}건`).join(", "), scoreClass(avg)),
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
    [{ label: "시각", get: (r) => esc((r.started || "").slice(5, 19).replace("T", " ")), cls: "mono" },
     { label: "프로젝트", get: (r) => esc(r.scope) },
     { label: "질문", get: (r) => esc((r.input || "").slice(0, 90)), cls: "wrap" },
     { label: "결과", get: (r) => r.cache_hit ? `<span class="chip ok">재사용</span>` : `<span class="chip">생성</span>` },
     { label: "조립", get: (r) => ms(r.latency_ms) },
     { label: "전체", get: (r) => ms(r.total_ms || r.latency_ms) },
     { label: "점수", get: (r) => r.avg_score == null ? "—" : `<b class="${scoreClass(r.avg_score)}">${r.avg_score.toFixed(2)}</b>` }],
    traces, openTrace,
    `<b>아직 작업 기록이 없습니다.</b><br>에이전트가 <code>jarvis_context</code> 를 호출하면 여기에 남습니다.`);

  if (project) {
    const impact = await api(`/projects/${encodeURIComponent(project)}/impact?limit=25`);
    table($("impact"),
      [{ label: "컨텍스트", get: (r) => `<span class="mono">${esc(short(r.uri))}</span>`, cls: "wrap" },
       { label: "사용", get: (r) => r.uses }, { label: "점수받음", get: (r) => r.scored },
       { label: "평균 점수", get: (r) => r.avg_score === null ? '<span class="chip">미검증</span>' : `<b class="${scoreClass(r.avg_score)}">${r.avg_score.toFixed(2)}</b>` }],
      impact);
  } else {
    $("impact").innerHTML = `<tbody><tr><td class="empty">프로젝트를 선택하면 표시됩니다</td></tr></tbody>`;
  }

  table($("agents"),
    [{ label: "에이전트", get: (r) => `<span class="mono">${esc(r.name)}</span>` },
     { label: "프로젝트", get: (r) => esc((r.projects || []).join(", ")) },
     { label: "호출", get: (r) => r.calls },
     { label: "최근", get: (r) => esc((r.last_seen || "").slice(5, 19).replace("T", " ")), cls: "mono" }],
    agents, null,
    `<b>연결된 에이전트가 없습니다.</b><br><code>jv agent config</code> 로 설정을 받아 붙이세요.`);
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
      : `<div style="color:var(--muted)">아직 점수가 없습니다 — 위 버튼으로 남기면 메모리 신뢰도에 반영됩니다</div>`;
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
      <h3 style="font-size:12px;color:var(--muted);margin-top:14px">이 답에 쓰인 컨텍스트 (클릭해 수정)</h3>${ctx}
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

// ---- prompts ----
async function loadPrompts(project) {
  if (!project) { $("prompts").innerHTML = `<tbody><tr><td class="empty">프로젝트를 선택하세요</td></tr></tbody>`; return; }
  const rows = await api(`/projects/${encodeURIComponent(project)}/prompts`);
  table($("prompts"),
    [{ label: "이름", get: (r) => `<b>${esc(r.name)}</b>` },
     { label: "스코프", get: (r) => `<span class="chip">${esc(r.scope)}</span>` },
     { label: "설명", get: (r) => esc(r.description || r.title), cls: "wrap" },
     { label: "변수", get: (r) => (r.vars || []).map((v) => `<span class="chip">${esc(v)}</span>`).join(" ") },
     { label: "사용", get: (r) => r.uses }, { label: "v", get: (r) => r.version },
     { label: "토큰", get: (r) => r.tokens }],
    rows, null,
    `<b>저장된 프롬프트가 없습니다.</b><br><code>jv prompt save</code> 로 반복하는 지시문을 넣어두세요.`);
}

// ---- boot ----
async function load() {
  const project = $("project").value;
  $("error").innerHTML = "";
  try {
    const h = await api("/health");
    $("ver").textContent = "v" + h.version;
    $("status").textContent = h.auth_required ? "인증 필요" : "인증 없음";
    if (tab === "review") await loadReview(project);
    else if (tab === "db") await loadDb(project);
    else if (tab === "traces") await loadTraces(project);
    else await loadPrompts(project);
    // The review badge should be visible from any tab.
    if (tab !== "review") {
      const s = await api("/review/summary" + (project ? `?project=${encodeURIComponent(project)}` : ""));
      $("badge").hidden = !s.total; $("badge").textContent = s.total;
    }
  } catch (e) {
    $("error").innerHTML = `<div class="err">불러오지 못했습니다: ${esc(e.message)}</div>`;
  }
}
$("refresh").onclick = load;
$("project").onchange = load;
$("days").onchange = load;

(async () => {
  try {
    const projects = await api("/projects");
    $("project").innerHTML = `<option value="">전체 프로젝트</option>` +
      projects.map((p) => `<option value="${esc(p.project)}">${esc(p.project)}</option>`).join("");
    if (projects.length === 1) $("project").value = projects[0].project;
  } catch (e) { /* load() surfaces auth problems */ }
  load();
})();
window.openMemory = openMemory;
</script>
</body>
</html>
"""
