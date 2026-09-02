"""The dashboard.

Served by the same process as the API so there is nothing extra to run and no
build step. Ordered by the question you actually open it to answer: *is my agent
getting good answers, and where is the time going?* Token accounting is present
but demoted — it is a cost report, not a quality report.

The API key, if the server requires one, is held in the browser's localStorage
and never leaves it.
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
    --bg: #fbfaf8; --panel: #ffffff; --line: #e6e2dc; --ink: #1c1a17;
    --muted: #6f6a62; --accent: #2f6f4f; --warn: #a4551f; --bad: #a32d2d;
    --chip: #f1efea; --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #16150f; --panel: #1e1d17; --line: #302e26; --ink: #ece8de;
      --muted: #9a9488; --accent: #6fbf92; --warn: #d99a5c; --bad: #e07a7a;
      --chip: #262419;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", sans-serif; }
  header { display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
           padding: 14px 20px; border-bottom: 1px solid var(--line);
           background: var(--panel); position: sticky; top: 0; z-index: 10; }
  h1 { font-size: 15px; margin: 0 12px 0 0; letter-spacing: .02em; }
  h1 span { color: var(--muted); font-weight: 400; }
  select, input, button { font: inherit; color: var(--ink); background: var(--bg);
    border: 1px solid var(--line); border-radius: 6px; padding: 5px 9px; }
  button { cursor: pointer; background: var(--chip); }
  button:hover { border-color: var(--accent); }
  .grow { flex: 1; }
  main { padding: 20px; max-width: 1400px; margin: 0 auto; }
  .cards { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
  .card .k { color: var(--muted); font-size: 11.5px; text-transform: uppercase; letter-spacing: .06em; }
  .card .v { font-size: 24px; font-weight: 600; margin-top: 4px; font-variant-numeric: tabular-nums; }
  .card .s { color: var(--muted); font-size: 12px; margin-top: 2px; }
  section { margin-top: 22px; }
  section > h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .07em;
                 color: var(--muted); margin: 0 0 8px; font-weight: 600; }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
  .scroll { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line); white-space: nowrap; }
  th { color: var(--muted); font-weight: 600; font-size: 11.5px; text-transform: uppercase; letter-spacing: .05em; }
  tr:last-child td { border-bottom: none; }
  tbody tr.click { cursor: pointer; }
  tbody tr.click:hover { background: var(--chip); }
  td.wrap { white-space: normal; max-width: 460px; }
  .mono { font-family: var(--mono); font-size: 12px; }
  .chip { display: inline-block; padding: 1px 7px; border-radius: 999px;
          background: var(--chip); font-size: 11.5px; border: 1px solid var(--line); }
  .ok { color: var(--accent); } .mid { color: var(--warn); } .bad { color: var(--bad); }
  .bars { display: flex; align-items: flex-end; gap: 3px; height: 56px; padding: 12px; }
  .bars div { flex: 1; background: var(--accent); opacity: .75; border-radius: 2px 2px 0 0; min-height: 2px; }
  .bars div:hover { opacity: 1; }
  dialog { border: 1px solid var(--line); border-radius: 12px; background: var(--panel);
           color: var(--ink); padding: 0; width: min(920px, 94vw); max-height: 88vh; }
  dialog::backdrop { background: rgba(0,0,0,.45); }
  .dlg-head { display: flex; gap: 10px; align-items: center; padding: 12px 16px;
              border-bottom: 1px solid var(--line); position: sticky; top: 0; background: var(--panel); }
  .dlg-body { padding: 14px 16px; overflow: auto; max-height: 74vh; }
  pre { background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
        padding: 10px; overflow-x: auto; font-family: var(--mono); font-size: 12px; margin: 6px 0; }
  .step { display: grid; grid-template-columns: 92px 1fr 78px; gap: 10px;
          padding: 7px 0; border-bottom: 1px dashed var(--line); align-items: baseline; }
  .empty { padding: 22px; color: var(--muted); text-align: center; }
  .err { padding: 10px 14px; color: var(--bad); }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
</style>
</head>
<body>
<header>
  <h1>MyViking <span id="ver"></span></h1>
  <select id="project"><option value="">전체 프로젝트</option></select>
  <select id="days">
    <option value="1">24시간</option>
    <option value="7" selected>7일</option>
    <option value="30">30일</option>
    <option value="0">전체</option>
  </select>
  <button id="refresh">새로고침</button>
  <span class="grow"></span>
  <input id="key" type="password" placeholder="API 키 (필요한 경우)" size="18">
  <span id="status" class="chip"></span>
</header>

<main>
  <div id="error"></div>

  <section>
    <h2>응답 품질과 속도</h2>
    <div class="cards" id="cards"></div>
  </section>

  <section>
    <h2>일별 추이 (막대 = 평균 응답 시간)</h2>
    <div class="panel"><div class="bars" id="spark"></div></div>
  </section>

  <section>
    <h2>시간이 어디에 쓰이나</h2>
    <div class="panel scroll"><table id="steps"></table></div>
  </section>

  <section>
    <h2>최근 작업</h2>
    <div class="panel scroll"><table id="traces"></table></div>
  </section>

  <section>
    <h2>메모리 기여도 — 좋은 결과에 함께 있던 컨텍스트</h2>
    <div class="panel scroll"><table id="impact"></table></div>
  </section>

  <section>
    <h2>연결된 에이전트</h2>
    <div class="panel scroll"><table id="agents"></table></div>
  </section>
</main>

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
keyBox.addEventListener("change", () => {
  localStorage.setItem("jv_key", keyBox.value.trim());
  load();
});

async function api(path, opts = {}) {
  const headers = Object.assign({ "content-type": "application/json" }, opts.headers || {});
  const key = keyBox.value.trim();
  if (key) headers["authorization"] = "Bearer " + key;
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ms = (n) => n >= 1000 ? (n / 1000).toFixed(1) + "s" : Math.round(n) + "ms";
const pct = (n) => (n * 100).toFixed(0) + "%";
const short = (uri) => String(uri).replace(/^jarvis:\/\/(projects\/)?/, "");

function scoreClass(v) {
  if (v === null || v === undefined) return "";
  return v >= 0.7 ? "ok" : v >= 0.4 ? "mid" : "bad";
}

function table(el, cols, rows, onClick) {
  if (!rows.length) { el.outerHTML = `<table id="${el.id}"><tbody><tr><td class="empty">아직 기록이 없습니다</td></tr></tbody></table>`; return; }
  el.innerHTML =
    "<thead><tr>" + cols.map((c) => `<th>${esc(c.label)}</th>`).join("") + "</tr></thead>" +
    "<tbody>" + rows.map((r, i) =>
      `<tr class="${onClick ? "click" : ""}" data-i="${i}">` +
      cols.map((c) => `<td class="${c.cls || ""}">${c.get(r)}</td>`).join("") + "</tr>").join("") +
    "</tbody>";
  if (onClick) el.querySelectorAll("tbody tr").forEach((tr) =>
    tr.onclick = () => onClick(rows[Number(tr.dataset.i)]));
}

let currentTrace = null;

async function load() {
  const project = $("project").value;
  const days = $("days").value;
  const q = `?project=${encodeURIComponent(project)}&days=${days}`;
  $("error").innerHTML = "";
  try {
    const health = await api("/health");
    $("ver").textContent = "v" + health.version;
    $("status").textContent = health.auth_required ? "인증 필요" : "인증 없음";

    const [m, ts, traces, agents] = await Promise.all([
      api("/metrics" + q),
      api("/timeseries" + `?project=${encodeURIComponent(project)}&days=14`),
      api(`/traces?project=${encodeURIComponent(project)}&limit=60`),
      api("/agents"),
    ]);

    const avgScore = m.scores.length
      ? m.scores.reduce((a, s) => a + s.avg * s.count, 0) / m.scores.reduce((a, s) => a + s.count, 0)
      : null;

    $("cards").innerHTML = [
      card("응답 p50", ms(m.answer_ms.p50), `p95 ${ms(m.answer_ms.p95)}`),
      card("재사용 응답 p50", m.reuse.hits ? ms(m.answer_ms.p50_reused) : "—",
           m.reuse.hits ? `생성 ${ms(m.answer_ms.p50_generated)}` : "재사용 없음"),
      card("컨텍스트 조립 p50", ms(m.context_ms.p50), `p95 ${ms(m.context_ms.p95)}`),
      card("재사용률", pct(m.reuse.rate), `${m.reuse.hits} / ${m.traces} 건`),
      card("평균 점수", avgScore === null ? "—" : avgScore.toFixed(2),
           avgScore === null ? "점수 미기록" : m.scores.map((s) => `${s.name} ${s.count}건`).join(", "),
           scoreClass(avgScore)),
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
       { label: "횟수", get: (r) => r.count },
       { label: "평균", get: (r) => ms(r.avg_ms) },
       { label: "최대", get: (r) => ms(r.max_ms) }],
      m.steps);

    table($("traces"),
      [{ label: "시각", get: (r) => esc((r.started || "").slice(5, 19).replace("T", " ")), cls: "mono" },
       { label: "프로젝트", get: (r) => esc(r.scope) },
       { label: "질문", get: (r) => esc((r.input || "").slice(0, 90)), cls: "wrap" },
       { label: "결과", get: (r) => r.cache_hit ? `<span class="chip ok">재사용 ${esc(r.cache_hit)}</span>` : `<span class="chip">생성</span>` },
       { label: "조립", get: (r) => ms(r.latency_ms) },
       { label: "전체", get: (r) => ms(r.total_ms || r.latency_ms) },
       { label: "점수", get: (r) => r.avg_score === null || r.avg_score === undefined ? "—" : `<b class="${scoreClass(r.avg_score)}">${r.avg_score.toFixed(2)}</b>` },
       { label: "단계", get: (r) => r.steps }],
      traces, openTrace);

    if (project) {
      const impact = await api(`/projects/${encodeURIComponent(project)}/impact?limit=25`);
      table($("impact"),
        [{ label: "컨텍스트", get: (r) => `<span class="mono">${esc(short(r.uri))}</span>`, cls: "wrap" },
         { label: "사용", get: (r) => r.uses },
         { label: "점수받음", get: (r) => r.scored },
         { label: "평균 점수", get: (r) => r.avg_score === null ? '<span class="chip">미검증</span>' : `<b class="${scoreClass(r.avg_score)}">${r.avg_score.toFixed(2)}</b>` },
         { label: "평균 토큰", get: (r) => r.avg_tokens }],
        impact);
    } else {
      $("impact").innerHTML = `<tbody><tr><td class="empty">프로젝트를 선택하면 표시됩니다</td></tr></tbody>`;
    }

    table($("agents"),
      [{ label: "에이전트", get: (r) => `<span class="mono">${esc(r.name)}</span>` },
       { label: "프로젝트", get: (r) => esc((r.projects || []).join(", ")) },
       { label: "호출", get: (r) => r.calls },
       { label: "최근", get: (r) => esc((r.last_seen || "").slice(5, 19).replace("T", " ")), cls: "mono" }],
      agents);
  } catch (e) {
    $("error").innerHTML = `<div class="err">불러오지 못했습니다: ${esc(e.message)}</div>`;
  }
}

function card(k, v, s, cls = "") {
  return `<div class="card"><div class="k">${esc(k)}</div><div class="v ${cls}">${esc(v)}</div><div class="s">${esc(s)}</div></div>`;
}

async function openTrace(row) {
  currentTrace = row.id;
  $("dlg-title").textContent = row.id;
  $("dlg-body").innerHTML = "불러오는 중...";
  $("dlg").showModal();
  try {
    const t = await api("/traces/" + encodeURIComponent(row.id));
    const steps = t.observations.map((o) => `
      <div class="step">
        <span class="chip">${esc(o.type)}</span>
        <div><b>${esc(o.name)}</b><div class="mono" style="color:var(--muted)">${esc((o.output || "").slice(0, 220))}</div></div>
        <div style="text-align:right">${ms(o.latency_ms)}</div>
      </div>`).join("");
    const ctx = t.context.length
      ? t.context.map((c) => `<div class="row"><span class="chip">L${c.tier}</span><span class="mono">${esc(short(c.uri))}</span><span style="color:var(--muted)">${c.tokens}t · ${c.score.toFixed(3)}</span></div>`).join("")
      : `<div style="color:var(--muted)">컨텍스트 없음</div>`;
    const scores = t.scores.length
      ? t.scores.map((s) => `<div class="row"><span class="chip ${scoreClass(s.value)}">${esc(s.name)} ${s.value}</span><span>${esc(s.comment)}</span><span style="color:var(--muted)">${esc(s.source)}</span></div>`).join("")
      : `<div style="color:var(--muted)">아직 점수가 없습니다 — 위 버튼으로 남길 수 있습니다</div>`;
    $("dlg-body").innerHTML = `
      <div class="row" style="margin-bottom:10px">
        <span class="chip">${esc(t.scope)}</span>
        <span class="chip">${t.cache_hit ? "재사용 " + esc(t.cache_hit) : "생성"}</span>
        <span class="chip">조립 ${ms(t.latency_ms)}</span>
        <span class="chip">전체 ${ms(t.total_ms || t.latency_ms)}</span>
        ${t.agent ? `<span class="chip">${esc(t.agent)}</span>` : ""}
      </div>
      <h3 style="font-size:12px;color:var(--muted)">질문</h3><pre>${esc(t.input)}</pre>
      <h3 style="font-size:12px;color:var(--muted)">답변</h3><pre>${esc((t.output || "(없음)").slice(0, 3000))}</pre>
      <h3 style="font-size:12px;color:var(--muted)">단계</h3>${steps || "<div style='color:var(--muted)'>없음</div>"}
      <h3 style="font-size:12px;color:var(--muted);margin-top:14px">사용된 컨텍스트</h3>${ctx}
      <h3 style="font-size:12px;color:var(--muted);margin-top:14px">점수</h3>${scores}`;
  } catch (e) {
    $("dlg-body").innerHTML = `<div class="err">${esc(e.message)}</div>`;
  }
}

document.querySelectorAll("[data-score]").forEach((b) => b.onclick = async () => {
  if (!currentTrace) return;
  try {
    await api("/scores", {
      method: "POST",
      body: JSON.stringify({ trace_id: currentTrace, name: "helpfulness", value: Number(b.dataset.score) }),
    });
    await openTrace({ id: currentTrace });
    load();
  } catch (e) {
    $("dlg-body").insertAdjacentHTML("afterbegin", `<div class="err">${esc(e.message)}</div>`);
  }
});
$("dlg-close").onclick = () => $("dlg").close();
$("refresh").onclick = load;
$("project").onchange = load;
$("days").onchange = load;

(async () => {
  try {
    const projects = await api("/projects");
    $("project").innerHTML = `<option value="">전체 프로젝트</option>` +
      projects.map((p) => `<option value="${esc(p.project)}">${esc(p.project)}</option>`).join("");
  } catch (e) { /* auth may be required; load() surfaces it */ }
  load();
  setInterval(load, 20000);
})();
</script>
</body>
</html>
"""
