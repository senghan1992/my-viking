/* myviking 대시보드 — 서가 검색·지식 보기/고치기·클립보드 */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

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
        const label = { knowledge: "📖 지식", commands: "🛠️ 명령", pitfalls: "⚠️ 함정", decisions: "🧭 결정" };
        const st = { established: "확립", fresh: "검증 전", contested: "검증 필요" };
        list.innerHTML = data.items.map((m) => `
          <div class="mem-card status-${m.status}">
            <div class="mem-top"><span class="chip">${label[m.category] || m.category}</span>
              <span class="chip status-chip">${st[m.status] || m.status}</span></div>
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
          <p><span class="chip">${esc(m.category)}</span> <span class="chip">${esc(m.status)}</span>
             <span class="muted small">신뢰 ${m.trust} · 확인 ${m.correct_count} · 반박 ${m.wrong_count} · 사용 ${m.use_count}</span></p>
          <p class="muted small">${esc(m.summary)}</p>
          <hr><pre>${esc(m.content)}</pre>
          ${ev ? `<div class="mem-detail-evidence">📜 결과 기록\n${esc(ev)}</div>` : ""}`;
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
                `<option ${c === m.category ? "selected" : ""} value="${c}">${c}</option>`).join("")}
              </select></label>
            <label>본문<textarea name="content">${esc(m.content)}</textarea></label>
          </form>`;
        const saveBtn = document.createElement("button");
        saveBtn.className = "btn primary"; saveBtn.textContent = "저장";
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

  // ── 클립보드 ──
  document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    await navigator.clipboard.writeText(b.dataset.copy);
    const old = b.textContent;
    b.textContent = "복사됨 ✓";
    setTimeout(() => (b.textContent = old), 1500);
  }));
})();