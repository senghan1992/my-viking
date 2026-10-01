#!/usr/bin/env node
/**
 * pi 확장 스모크 테스트 — scribe 가 깐 scribe.ts 를 실제로 로드해서
 * '작업 세션은 관찰만 올리고, 서기 세션은 서기 도구를 쓴다' 는 컨셉을 검증한다.
 *
 * 서버도 pi 도 필요 없다 — pi ExtensionAPI 를 가짜로 대고 fetch 를 가로챈다.
 *   node tools/pi-extension-smoke.mjs [확장파일]
 * 확장 파일을 생략하면 scribe 가 설치하는 위치(~/.pi/agent/extensions/scribe.ts)를 쓰고,
 * 없으면 템플릿을 직접 조립해 테스트한다.
 */
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";

/**
 * esbuild/typebox 는 pi 가 깔아둔 걸 재활용한다 (이 저장소에 node_modules 는 없다).
 *   PI_ROOT=/path/to/pi-coding-agent node tools/pi-extension-smoke.mjs
 */
function findPiRoot() {
  const cands = [];
  if (process.env.PI_ROOT) cands.push(process.env.PI_ROOT);
  try {
    const npmRoot = execFileSync("npm", ["root", "-g"], { encoding: "utf8" }).trim();
    cands.push(join(npmRoot, "@earendil-works", "pi-coding-agent"));
  } catch { /* npm 없음 */ }
  const dir = dirname(process.execPath);
  cands.push(join(dir, "..", "lib", "node_modules", "@earendil-works", "pi-coding-agent"));
  cands.push("/root/.nvm/versions/node/v24.18.0/lib/node_modules/@earendil-works/pi-coding-agent");
  for (const c of cands) if (c && existsSync(join(c, "node_modules", "esbuild"))) return c;
  throw new Error("pi 설치 폴더(esbuild 가 들어 있는 곳)을 찾지 못했습니다. PI_ROOT 로 지정하세요.");
}

const PI_ROOT = findPiRoot();
const { build } = await import(pathToFileURL(join(PI_ROOT, "node_modules", "esbuild", "lib", "main.js")).href);

// ── 1. 확장 소스 확보 (설치본 없으면 scribe 로부터 템플릿을 뽑는다) ─────────
function extensionSource(explicit) {
  if (explicit && existsSync(explicit)) return readFileSync(explicit, "utf8");
  const installed = join(homedir(), ".pi", "agent", "extensions", "scribe.ts");
  if (existsSync(installed)) return readFileSync(installed, "utf8");
  const py = process.env.PYV || "python3";
  const out = execFileSync(py, ["-c", "import scribe.cli as c; import sys; sys.stdout.write(c._PI_EXT_TEMPLATE.replace('@CREATED@','smoke'))"],
    { cwd: join(import.meta.dirname, ".."), encoding: "utf8" });
  return out;
}
// ── 2. TS → ESM 변환 후 로드 ────────────────────────────────────────
// 확장은 로드 시점에 env 를 읽는다 — 시나리오마다 새로 import 해야 한다 (캐시 무효화)
let EXT_SEQ = 0;
async function loadExtension(src) {
  const dir = mkdtempSafe("mv-ext-smoke-");
  const tsPath = join(dir, "scribe.ts");
  EXT_SEQ += 1;
  const jsPath = join(dir, `scribe-${EXT_SEQ}.mjs`);
  writeFileSync(tsPath, src);
  await build({
    entryPoints: [tsPath], outfile: jsPath, bundle: true, format: "esm", platform: "node",
    external: ["@earendil-works/pi-coding-agent", "typebox", "node:fs", "node:os", "node:path"],
    alias: { typebox: join(PI_ROOT, "node_modules/typebox/build/index.mjs") },
  });
  return (await import(pathToFileURL(jsPath).href)).default;
}

function mkdtempSafe(prefix) {
  const dir = join(tmpdir(), prefix + Math.random().toString(36).slice(2));
  mkdirSync(dir, { recursive: true });
  return dir;
}

// ── 3. pi ExtensionAPI 가짜 ───────────────────────────────────────
function fakePi(log) {
  const handlers = {};
  return {
    handlers,
    toolDefs: log.toolDefs,
    on(event, fn) { (handlers[event] ||= []).push(fn); },
    registerTool(t) { log.tools.push(t.name); log.toolDefs[t.name] = t; },
    registerCommand(name, opts) { log.commands.push(name); log.command = opts; },
    registerFlag() {}, getFlag() { return undefined; },
    async sendMessage(msg) { log.sent.push(msg.content.slice(0, 40)); },
    async exec(cmd, args) { log.execs.push([cmd, ...args]); return { stdout: "", stderr: "", code: 0 }; },
    setActiveTools() {}, getActiveTools() { return []; },
  };
}

function fakeCtx({ cwd, sessionId, transcript, hasUI = false }) {
  return {
    cwd, hasUI, mode: "print",
    ui: { notify: (m) => fakeCtx.notes.push(m), input: async () => "", select: async () => undefined },
    sessionManager: { getSessionId: () => sessionId, getSessionFile: () => transcript },
  };
}
fakeCtx.notes = [];

// fetch 가로채기 — 어떤 API 가 어떤 body 로 불렸는지 기록
function stubFetch(log) {
  globalThis.fetch = async (url, init) => {
    const rec = { url: String(url), method: init?.method || "GET", body: init?.body ? JSON.parse(init.body) : null };
    log.calls.push(rec);
    const reply = {
      orientation: "📚 브리핑", pending: 3, repeat_hits: 2, ok: true, logged: [1],
      uri: "scribe://1/memories/pitfalls/1", memory_id: 1, status: "established", items: [], warnings: [],
    };
    return { ok: true, json: async () => reply, async text() { return "ok"; } };
  };
}

// ── 4. 시나리오 ───────────────────────────────────────────────────
// 확장은 로드 시점에 homedir() 을 고정해서 쓴다 — HOME 은 로드 전에 하나만 만든다.
const TEST_HOME = mkdtempSafe("mv-home-");
process.env.HOME = TEST_HOME;
mkdirSync(join(TEST_HOME, ".scribe"), { recursive: true });
writeFileSync(join(TEST_HOME, ".scribe", "connections.json"), JSON.stringify({
  connections: [{ id: "http://srv|p1", name: "E2E", url: "http://srv", key: "sc_testkeytestkeytest", project: "p1" }],
}));

function folderWithLink(id = "http://srv|p1") {
  const dir = mkdtempSafe("mv-proj-");
  mkdirSync(join(dir, ".git"));
  writeFileSync(join(dir, ".scribe-connection.json"), JSON.stringify({ connection: id }));
  return dir;
}

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, pass: !!cond, detail });
  console.log(`${cond ? "✓" : "✗"} ${name}${cond ? "" : "  ← " + detail}`);
}

async function newLog() {
  return { tools: [], toolDefs: {}, commands: [], calls: [], execs: [], sent: [] };
}

async function scenarioWorker(factory) {
  const log = await newLog();
  stubFetch(log);
  const pi = fakePi(log);
  factory(pi);
  const dir = folderWithLink();
  const ctx = fakeCtx({ cwd: dir, sessionId: "sess-w", transcript: join(dir, "session.jsonl") });

  await Promise.all(pi.handlers.session_start.map((h) => h({ type: "session_start", reason: "startup" }, ctx)));
  check("작업 세션: 워커 도구가 등록된다", log.tools.includes("scribe_brief") && log.tools.includes("scribe_note"), log.tools.join(","));
  check("작업 세션: 서기 도구는 등록되지 않는다", !log.tools.includes("scribe_file"));
  check("작업 세션: 브리핑이 주입된다 (/brief role=worker)", log.calls.some((c) => c.url.includes("/brief") && c.url.includes("role=worker")));
  check("작업 세션: /scribe 명령이 있다", log.commands.includes("scribe"));

  log.calls.length = 0;
  await Promise.all(pi.handlers.before_agent_start.map((h) => h({ type: "before_agent_start", prompt: "마이그레이션 어떻게 실행해?" }, ctx)));
  await Promise.all(pi.handlers.turn_end.map((h) => h({
    type: "turn_end", turnIndex: 1,
    message: { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "alembic upgrade head 실행" }, { type: "toolCall", name: "bash" }] },
    toolResults: [],
  }, ctx)));

  const obs = log.calls.find((c) => c.url.includes("/observe"));
  check("작업 세션: 턴 끝난 후 /observe 로 관찰을 올린다", !!obs, JSON.stringify(log.calls.map((c) => c.url)));
  check("작업 세션: /commit(자동 증류)은 호출하지 않는다", !log.calls.some((c) => c.url.includes("/commit")));
  check("관찰에 질문·답이 모두 있다",
    obs && obs.body.items.some((i) => i.kind === "prompt") && obs.body.items.some((i) => i.kind === "reply"),
    JSON.stringify(obs?.body));
  check("관찰에 세션 트랜스크립트 경로가 함께 간다(세션 공유)", obs && obs.body.transcript.endsWith("session.jsonl"), obs?.body?.transcript);

  // pi 슬래시 명령은 관찰하지 않는다
  log.calls.length = 0;
  await Promise.all(pi.handlers.before_agent_start.map((h) => h({ type: "before_agent_start", prompt: "/scribe status" }, ctx)));
  await Promise.all(pi.handlers.turn_end.map((h) => h({ type: "turn_end", turnIndex: 2, message: { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "설명" }] }, toolResults: [] }, ctx)));
  check("pi 슬래시 명령은 관찰하지 않는다", !log.calls.some((c) => c.url.includes("/observe")));

  // 턴이 끝까지 안 간 경우(오류/abort) — agent_settled 에서 질문만이라도 남기고 비운다
  log.calls.length = 0;
  await Promise.all(pi.handlers.before_agent_start.map((h) => h({ type: "before_agent_start", prompt: "끝까지 안 간 질문" }, ctx)));
  await Promise.all(pi.handlers.turn_end.map((h) => h({ type: "turn_end", turnIndex: 4, message: { role: "assistant", stopReason: "toolUse", content: [] }, toolResults: [] }, ctx)));
  check("도구 진행 턴에서는 아직 관찰하지 않는다", !log.calls.some((c) => c.url.includes("/observe")));
  await Promise.all((pi.handlers.agent_settled || []).map((h) => h({ type: "agent_settled" }, ctx)));
  const flushed = log.calls.find((c) => c.url.includes("/observe"));
  check("agent_settled 에서 질문을 관찰로 남긴다", !!flushed && flushed.body.items[0].text === "끝까지 안 간 질문",
    JSON.stringify(flushed?.body));
  log.calls.length = 0;
  await Promise.all(pi.handlers.before_agent_start.map((h) => h({ type: "before_agent_start", prompt: "다음 질문" }, ctx)));
  await Promise.all(pi.handlers.turn_end.map((h) => h({ type: "turn_end", turnIndex: 5, message: { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "다음 답" }] }, toolResults: [] }, ctx)));
  const next = log.calls.find((c) => c.url.includes("/observe"));
  check("막혔던 턴이 다음 질문을 가로막지 않는다", !!next && next.body.items[0].text === "다음 질문",
    JSON.stringify(next?.body));

  // 도구가 연결된 폴더에서만 동작 + note 경로는 /observe
  
  log.calls.length = 0;
  await log.command.handler("note 이건 기록할 가치 있음", ctx);
  check("/scribe note 는 지식을 만들지 않고 관찰에 넣는다",
    log.calls.some((c) => c.url.includes("/observe") && c.body.items[0].kind === "note"),
    JSON.stringify(log.calls.map((c) => c.url)));

  log.calls.length = 0; log.execs.length = 0;
  await log.command.handler("secretary once", ctx);
  check("/scribe secretary once 는 scribe 를 백그라운드 서기로 부른다",
    log.execs.some((e) => e[0] === "scribe" && e[1] === "secretary" && e[2] === "once"), JSON.stringify(log.execs));

  // 이번 턴에 건드린 파일·실패한 도구까지 관찰에 들어간다 (재발 실수의 원료)
  log.calls.length = 0;
  await Promise.all(pi.handlers.before_agent_start.map((h) => h({ type: "before_agent_start", prompt: "설정 바꿔줘" }, ctx)));
  await Promise.all(pi.handlers.tool_call.map((h) => h({ type: "tool_call", toolName: "edit", input: { path: "app/settings.py" }, toolCallId: "c1" }, ctx)));
  await Promise.all(pi.handlers.tool_execution_end.map((h) => h({ type: "tool_execution_end", toolName: "bash", args: { command: "alembic upgrade head" }, isError: true, result: {}, toolCallId: "c2" }, ctx)));
  await Promise.all(pi.handlers.turn_end.map((h) => h({ type: "turn_end", turnIndex: 3, message: { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "바꿨습니다" }] }, toolResults: [] }, ctx)));
  const rich = log.calls.find((c) => c.url.includes("/observe"))?.body;
  check("관찰에 건드린 파일이 들어간다", rich?.items?.some((i) => (i.files || []).includes("app/settings.py")), JSON.stringify(rich));
  check("관찰에 실패한 도구(무엇을 하다가)가 error 로 들어간다",
    rich?.items?.some((i) => i.kind === "error" && i.text.includes("bash") && i.text.includes("alembic")),
    JSON.stringify(rich?.items?.filter((i) => i.kind === "error")));

  rmSync(dir, { recursive: true, force: true });
}

async function scenarioSecretary(factory) {
  const log = await newLog();
  stubFetch(log);
  const pi = fakePi(log);
  factory(pi);
  const dir = folderWithLink("http://srv|p1");
  const ctx = fakeCtx({ cwd: dir, sessionId: "sess-secretary", transcript: join(dir, "session.jsonl") });

  await Promise.all(pi.handlers.session_start.map((h) => h({ type: "session_start", reason: "startup" }, ctx)));
  check("서기 세션: 서기 도구가 등록된다",
    ["scribe_inbox", "scribe_file", "scribe_ack", "scribe_report", "scribe_session"].every((t) => log.tools.includes(t)),
    log.tools.join(","));
  check("서기 세션: 워커 도구(note)는 없다", !log.tools.includes("scribe_note"));

  await Promise.all(pi.handlers.before_agent_start.map((h) => h({ type: "before_agent_start", prompt: "관찰함 정리해" }, ctx)));
  await Promise.all(pi.handlers.turn_end.map((h) => h({ type: "turn_end", turnIndex: 1, message: { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "정리했습니다" }] }, toolResults: [] }, ctx)));
  check("서기 세션은 자기 작업을 관찰로 올리지 않는다(되먹임 없음)",
    !log.calls.some((c) => c.url.includes("/observe")), JSON.stringify(log.calls.map((c) => c.url)));
  check("서기 세션은 작업 브리핑을 주입받지 않는다", !log.calls.some((c) => c.url.includes("/brief")));

  // 서기 업무 자체를 실행해 본다 — inbox → file(등재) → report(통보)
  log.calls.length = 0;
  await pi.toolDefs.scribe_inbox.execute("t1", {}, undefined, undefined, ctx);
  const inboxCall = log.calls.find((c) => c.url.includes("/inbox"));
  check("scribe_inbox 는 이 세션 id 로 관찰함을 선점한다",
    inboxCall && inboxCall.url.includes("worker=sess-secretary"), inboxCall?.url);

  await pi.toolDefs.scribe_file.execute("t2", {
    title: "재시도 금지 규칙", content: "승인 실패 응답에서는 재시도하지 않는다",
    category: "pitfalls", occurrences: 3, observation_ids: [11, 12], reason: "3번 반복됨",
  }, undefined, undefined, ctx);
  const fileCall = log.calls.find((c) => c.url.includes("/remember"));
  check("scribe_file 은 source=secretary 로 등재하고 관찰 id 를 함께 보낸다",
    fileCall && fileCall.body.source === "secretary" && fileCall.body.observation_ids.join() === "11,12"
      && fileCall.body.occurrences === 3, JSON.stringify(fileCall?.body));

  log.calls.length = 0;
  await pi.toolDefs.scribe_report.execute("t3", { report: "1권 등재", found: 2, skipped_ids: [13], skip_reason: "일회성" },
    undefined, undefined, ctx);
  const ackCall = log.calls.find((c) => c.url.includes("/inbox/ack"));
  check("scribe_report 은 보고와 함께 버린 관찰을 통보한다",
    ackCall && ackCall.body.report === "1권 등재" && ackCall.body.results[0].ids.join() === "13"
      && ackCall.body.results[0].action === "skipped", JSON.stringify(ackCall?.body));

  rmSync(dir, { recursive: true, force: true });
}

async function scenarioUnconnected(factory) {
  const log = await newLog();
  stubFetch(log);
  const pi = fakePi(log);
  factory(pi);
  const free = mkdtempSafe("mv-free-");
  const ctx = fakeCtx({ cwd: free, sessionId: "sess-free", transcript: "" });
  await Promise.all(pi.handlers.session_start.map((h) => h({ type: "session_start", reason: "startup" }, ctx)));
  check("연결 안 한 폴더: scribe 도구가 아예 등록되지 않는다", log.tools.length === 0, log.tools.join(","));
  await Promise.all(pi.handlers.turn_end.map((h) => h({ type: "turn_end", turnIndex: 1, message: { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "답" }] }, toolResults: [] }, ctx)));
  check("연결 안 한 폴더: 서버 호출이 0건", log.calls.length === 0, JSON.stringify(log.calls));
}

async function main() {
  const src = extensionSource(process.argv[2]);
  check("pi 확장: 폴더 서기 설정 오버라이드를 읽는다", src.includes(".scribe-secretary.json"), "folder secretary override missing");
  console.log("── 작업 세션 (worker) ──");
  await scenarioWorker(await loadExtension(src));
  console.log("── 서기 세션 (SCRIBE_ROLE=secretary) ──");
  process.env.SCRIBE_ROLE = "secretary";
  await scenarioSecretary(await loadExtension(src));
  delete process.env.SCRIBE_ROLE;
  console.log("── 연결하지 않은 폴더 ──");
  await scenarioUnconnected(await loadExtension(src));
  const failed = results.filter((r) => !r.pass);
  console.log(`\n${results.length - failed.length}/${results.length} 통과`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => { console.error("스모크 실행 오류:", e); process.exit(2); });
