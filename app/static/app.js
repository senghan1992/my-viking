/* myviking 대시보드 — 서가 검색·지식 보기/고치기·클립보드 */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ── 아이콘 (지도 제작실 문법과 동일한 SVG) ──
  const ic = (n) => `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${
    { book: '<path d="M12 6C10.2 4.6 8.1 4.2 4.5 4.3v13.9c3.6-.1 5.7.3 7.5 1.7 1.8-1.4 3.9-1.8 7.5-1.7V4.3c-3.6-.1-5.7.3-7.5 1.7Z"/><path d="M12 6v13.9"/>',
      command: '<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="m7 9.5 3 2.5-3 2.5M13.5 14.5H16.5"/>',
      alert: '<path d="m12 4.5 8.8 15H3.2Z"/><path d="M12 10.2v4.2"/><circle cx="12" cy="17" r=".5" fill="currentColor" stroke="none"/>',
      needle: '<path d="m12 4 2.5 5.4L20 12l-5.5 2.6L12 20l-2.5-5.4L4 12l5.5-2.6Z"/><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none"/>',
      check: '<path d="m5 12.8 4.3 4.3L19 7.3"/>',
      close: '<path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>',
    }[n] || ""}</svg>`;
  const CAT = { knowledge: "지식", commands: "명령", pitfalls: "함정", decisions: "결정" };
  const CAT_ICON = { knowledge: "book", commands: "command", pitfalls: "alert", decisions: "needle" };
  const ST = { established: "확립", fresh: "검증 전", contested: "검증 필요" };

  // ── 토스트 자동 소멸 ──
  const toast = document.getElementById("toast");
  if (toast) setTimeout(() => { toast.style.opacity = "0"; setTimeout(() => toast.remove(), 400); }, 6000);

  // ── 라이브 검색 ──
  const box = document.getElementById("search-box");
  const list = document.getElementById("library-list");
  if (box && list) {
    let timer = null;
    box.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = box.value.trim();
        const base = location.pathname;
        if (!q) { location.href = base; return; }
        const r = await fetch(`${base}/search?q=${encodeURIComponent(q)}`);
        const data = await r.json();
        if (!data.items.length && !data.warnings.length) {
          list.innerHTML = '<div class="empty">관련 지식이 없습니다. 첫 작업이 이 서가의 첫 권이 됩니다.</div>';
          return;
        }
        list.innerHTML = data.items.map((m) => `
          <div class="mem-card status-${m.status}">
            <div class="mem-top"><span class="chip">${ic(CAT_ICON[m.category] || "book")} ${CAT[m.category] || m.category}</span>
              <span class="chip">${ST[m.status] || m.status}</span></div>
            <h3 class="mem-title">${esc(m.title)}</h3>
            <p class="muted small mem-summary">${esc(m.text.slice(0, 140))}</p>
            <div class="mem-actions"><button class="link-btn" data-view="${m.id}">읽기</button></div>
          </div>`).join("");
      }, 250);
    });
  }

  // ── 모달 열기/닫기 ──
  const modal = document.getElementById("mem-modal");
  const mmTitle = document.getElementById("mm-title");
  const mmBody = document.getElementById("mm-body");
  const base = location.pathname.startsWith("/projects/") ? location.pathname.split("/").slice(0, 3).join("/") : "";
  if (modal) {
    modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });
    modal.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => (modal.hidden = true)));

    document.addEventListener("click", async (e) => {
      const view = e.target.closest("[data-view]");
      const edit = e.target.closest("[data-edit]");
      const conf = e.target.closest("[data-confirm]");
      const del = e.target.closest("[data-del]");
      const close = e.target.closest("[data-close]");

      if (view) {
        const r = await fetch(`${base}/memories/${view.dataset.view}`);
        const m = await r.json();
        mmTitle.textContent = m.title;
        const ev = (m.evidence || []).map((x) => `${x.at.slice(0, 16)} · ${x.kind} — ${x.note}`).join("\n");
        mmBody.innerHTML = `
          <p><span class="chip">${ic(CAT_ICON[m.category] || "book")} ${CAT[m.category] || m.category}</span>
             <span class="chip">${ST[m.status] || m.status}</span>
             <span class="muted small">신뢰 ${m.trust} · 확인 ${m.correct_count} · 반박 ${m.wrong_count} · 사용 ${m.use_count}</span></p>
          <p class="muted small">${esc(m.summary)}</p>
          <hr><pre>${esc(m.content)}</pre>
          ${ev ? `<div class="mem-detail-evidence">결과 기록\n${esc(ev)}</div>` : ""}`;
        modal.hidden = false;
      } else if (edit) {
        const r = await fetch(`${base}/memories/${edit.dataset.edit}`);
        const m = await r.json();
        mmTitle.textContent = "지식 고치기";
        mmBody.innerHTML = `
          <form id="mem-edit-form">
            <label>제목<input type="text" name="title" value="${esc(m.title)}" maxlength="200"></label>
            <label>분류
              <select name="category">${["knowledge", "commands", "pitfalls", "decisions"].map((c) =>
                `<option ${c === m.category ? "selected" : ""} value="${c}">${CAT[c]}</option>`).join("")}
              </select></label>
            <label>본문<textarea name="content">${esc(m.content)}</textarea></label>
          </form>`;
        const saveBtn = document.createElement("button");
        saveBtn.className = "btn primary"; saveBtn.innerHTML = `${ic("check")} 저장`;
        saveBtn.addEventListener("click", async () => {
          const form = new FormData(document.getElementById("mem-edit-form"));
          await fetch(`${base}/memories/${m.id}`, { method: "POST", body: form });
          location.reload();
        });
        modal.querySelector(".modal-foot").innerHTML = "";
        modal.querySelector(".modal-foot").appendChild(saveBtn);
        modal.hidden = false;
      } else if (conf) {
        if (confirm("이 지식을 '확립'으로 확인할까요? 결과가 뒷받침된 사실로 취급됩니다.")) {
          await fetch(`${base}/memories/${conf.dataset.confirm}/confirm`, { method: "POST" });
          location.reload();
        }
      } else if (del) {
        if (confirm("이 지식을 삭제할까요?")) {
          await fetch(`${base}/memories/${del.dataset.del}/delete`, { method: "POST" });
          location.reload();
        }
      } else if (close) {
        modal.hidden = true;
        modal.querySelector(".modal-foot").innerHTML = '<button class="btn ghost" data-close>닫기</button>';
      }
    });
  }

  // ── 설정 모달 ──
  const settingsBtn = document.querySelector("[data-settings-btn]");
  const settingsModal = document.getElementById("settings-modal");
  if (settingsBtn && settingsModal) {
    settingsBtn.addEventListener("click", () => (settingsModal.hidden = false));
    settingsModal.querySelectorAll("[data-close]").forEach((b) =>
      b.addEventListener("click", () => (settingsModal.hidden = true)));
    settingsModal.addEventListener("click", (e) => { if (e.target === settingsModal) settingsModal.hidden = true; });
  }

  // ── 도구 선택 탭 (연결 페이지 등) ──
  document.querySelectorAll("[data-tabs]").forEach((tabbar) => {
    const key = tabbar.dataset.tabs;
    const tabs = tabbar.querySelectorAll("[data-tab]");
    tabs.forEach((t) =>
      t.addEventListener("click", (e) => {
        e.preventDefault();
        tabs.forEach((x) => x.classList.toggle("on", x === t));
        document.querySelectorAll(`[data-pane="${key}"]`).forEach((p) => {
          p.hidden = p.dataset.paneId !== t.dataset.tab;
        });
      }));
  });

  // ── 모델 선택 드롭다운 → 주소/모델 자동 채움 (관리자 → 모델 설정) ──
  document.querySelectorAll("[data-pick]").forEach((sel) => {
    sel.addEventListener("change", () => {
      const opt = sel.selectedOptions[0];
      if (!opt) return;
      const p = sel.dataset.pick; // llm | embed
      const base = document.querySelector(`input[name="${p}_base_url"]`);
      const model = document.querySelector(`input[name="${p}_model"]`);
      if (base && opt.dataset.base !== undefined) base.value = opt.dataset.base || "";
      if (model && opt.dataset.model !== undefined) model.value = opt.dataset.model || "";
    });
  });

  // ── 복사 엔진 — 모든 복사 버튼이 지나는 단 하나의 경로 ──────────────────
  // data-copy="직접 값", data-copy-src="CSS 선택자" — 화면의 텍스트를 근원으로 삼는다.
  //   명령을 버튼 속성에 중복 저장하지 않으므로, 복사 결과와 화면이 항상 같은 문자열이다.
  const sr = (() => {
    let el = document.getElementById("copy-status");
    if (!el) {
      el = document.createElement("span");
      el.id = "copy-status";
      el.className = "sr-only";
      el.setAttribute("role", "status");
      el.setAttribute("aria-live", "polite");
      document.body.appendChild(el);
    }
    return el;
  })();
  const say = (t) => { sr.textContent = t; };

  const copyValue = (btn) => {
    const src = btn.dataset.copySrc;
    if (!src) return btn.dataset.copy || "";
    const el = src === "self" ? btn : document.querySelector(src);
    if (!el) return "";
    const raw = el.tagName === "TEXTAREA" || el.tagName === "INPUT" ? el.value : el.textContent;
    return raw.replace(/\s+$/, "").replace(/^\s+/, "");
  };

  // 화면 안(뷰포트 안)에 숨긴 textarea — 일부 브라우저는 화면 밖 요소 복사를 막는다
  const hiddenWell = (text) => {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.className = "copy-well";
    document.body.appendChild(ta);
    return ta;
  };

  function legacyCopy(text) {
    const ta = hiddenWell(text);
    try {
      ta.focus({ preventScroll: true });
      ta.setSelectionRange(0, ta.value.length);
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      ta.remove();
    }
  }

  function selectSource(btn) {
    const src = btn.dataset.copySrc;
    const el = src && src !== "self" ? document.querySelector(src) : null;
    if (!el) return false;
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
    return true;
  }

  // 최후의 창: 브라우저가 복사를 완전히 막은 환경 — 전부 선택된 상태로 열어 사람이 Ctrl+C
  function manualSheet(text) {
    let back = document.getElementById("copy-sheet");
    if (!back) {
      back = document.createElement("div");
      back.id = "copy-sheet";
      back.className = "modal-back";
      back.innerHTML = `
        <div class="modal copy-modal">
          <div class="modal-head"><h2>직접 복사</h2>
            <button class="link-btn" type="button" data-close>닫기</button></div>
          <div class="modal-body">
            <p class="muted small">브라우저가 자동 복사를 막았습니다. 아래는 이미 전체 선택되어 있으니
              <b>Ctrl+C</b>(macOS는 ⌘C) 만 누르면 됩니다.</p>
            <textarea id="copy-manual" readonly spellcheck="false"></textarea>
          </div>
        </div>`;
      document.body.appendChild(back);
      back.addEventListener("click", (e) => {
        if (e.target === back || e.target.closest("[data-close]")) back.hidden = true;
      });
    }
    const ta = back.querySelector("#copy-manual");
    ta.value = text;
    back.hidden = false;
    ta.focus();
    ta.setSelectionRange(0, ta.value.length);
    return true;
  }

  async function toClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return "copied";
      } catch { /* http 로 연 서버 여기로 온다 — 아래 폴백 */ }
    }
    if (legacyCopy(text)) return "copied";
    return "manual";
  }

  const DONE = "복사됨 ✓";
  const PICK = "선택됨 · Ctrl+C";
  // 자기 자기가 복사 근원인 판(pre/code) 은 라벨을 바꾸면 내용이 사라진다 → 배지로 알린다
  const isOwnSource = (btn) => {
    const src = btn.dataset.copySrc;
    if (!src || src === "self") return !!src;
    const el = document.querySelector(src);
    return el === btn || (el && el.contains(btn));
  };

  function mark(btn, how) {
    const own = isOwnSource(btn);
    btn.classList.add("is-copied");
    if (!own) {
      if (!btn.dataset.label) btn.dataset.label = btn.innerHTML;
      btn.innerHTML = how === "copied" ? DONE : PICK;
    }
    clearTimeout(btn._doneT);
    btn._doneT = setTimeout(() => {
      if (!own && btn.dataset.label) btn.innerHTML = btn.dataset.label;
      btn.classList.remove("is-copied");
    }, 1700);
    say(how === "copied" ? "클립보드에 복사했습니다." : "복사할 값이 선택되었습니다. Ctrl+C 로 복사하세요.");
  }

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-copy], [data-copy-src]");
    if (!btn) return;
    e.preventDefault();
    const text = copyValue(btn);
    if (!text) { say("복사할 내용이 비어 있습니다."); return; }
    let how = await toClipboard(text);
    if (how === "manual" && selectSource(btn)) how = "selected";
    if (how === "manual") { manualSheet(text); how = "selected"; }
    mark(btn, how === "copied" ? "copied" : "selected");
  });

  // 직접 복사 판은 Esc 로 닫는다
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const sheet = document.getElementById("copy-sheet");
    if (sheet && !sheet.hidden) sheet.hidden = true;
  });

  // 코드 칩·판 등 버튼이 아닌 복사 대상도 키보드로 열 수 있게
  document.querySelectorAll("[data-copy], [data-copy-src]").forEach((el) => {
    if (el.tagName === "BUTTON" || el.tagName === "A") return;
    el.setAttribute("role", "button");
    el.setAttribute("tabindex", "0");
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.click(); }
    });
  });
})();