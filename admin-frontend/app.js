const API_BASE = "/api";
const STORAGE_KEY = "tmatch_admin_id";

const state = {
  adminId: null,
  reports: [],
  activeReport: null,
  auditMode: "target",
};

const els = {
  loginModal: document.getElementById("login-modal"),
  loginInput: document.getElementById("admin-id-input"),
  loginBtn: document.getElementById("btn-login"),
  loginError: document.getElementById("login-error"),
  refreshBtn: document.getElementById("btn-refresh"),
  logoutBtn: document.getElementById("btn-logout"),
  reportsList: document.getElementById("reports-list"),
  reportsMeta: document.getElementById("reports-meta"),
  userCard: document.getElementById("user-card"),
  userMeta: document.getElementById("user-meta"),
  auditList: document.getElementById("audit-list"),
  summaryCount: document.getElementById("summary-count"),
  summaryNote: document.getElementById("summary-note"),
  summaryUser: document.getElementById("summary-user"),
  summaryUserNote: document.getElementById("summary-user-note"),
  toast: document.getElementById("toast"),
  auditChips: document.querySelectorAll("[data-audit-filter]"),
};

function showToast(message, tone = "info") {
  els.toast.textContent = message;
  els.toast.style.borderColor = tone === "error" ? "rgba(255,92,92,0.6)" : "";
  els.toast.classList.add("show");
  setTimeout(() => els.toast.classList.remove("show"), 2400);
}

function setLoginVisible(visible) {
  els.loginModal.classList.toggle("hidden", !visible);
}

function setAdminId(value) {
  state.adminId = value;
  if (value) {
    localStorage.setItem(STORAGE_KEY, value);
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
}

async function apiFetch(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    "X-User-ID": state.adminId || "",
    ...(options.headers || {}),
  };
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message =
      payload?.detail || payload?.error?.message || "Ошибка запроса";
    throw new Error(message);
  }
  if (payload?.error) {
    throw new Error(payload.error.message || "Ошибка запроса");
  }
  return payload;
}

function formatDate(value) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString("ru-RU");
  } catch {
    return value;
  }
}

function updateSummary() {
  els.summaryCount.textContent = state.reports.length;
  els.summaryNote.textContent = `Последнее обновление: ${formatDate(
    new Date().toISOString()
  )}`;
  if (state.activeReport) {
    els.summaryUser.textContent = `#${state.activeReport.reported_id}`;
    els.summaryUserNote.textContent = state.activeReport.reason;
  } else {
    els.summaryUser.textContent = "—";
    els.summaryUserNote.textContent = "Выберите репорт для деталей";
  }
}

function renderReports() {
  els.reportsList.innerHTML = "";
  if (!state.reports.length) {
    els.reportsList.innerHTML = '<div class="empty">Пока нет репортов.</div>';
    els.reportsMeta.textContent = "0";
    updateSummary();
    return;
  }

  els.reportsMeta.textContent = `${state.reports.length} в очереди`;
  state.reports.forEach((report) => {
    const card = document.createElement("div");
    card.className = "report-card";
    if (state.activeReport && state.activeReport.id === report.id) {
      card.classList.add("active");
    }
    card.innerHTML = `
      <div class="report-meta">
        <span>#${report.id} · ${formatDate(report.created_at)}</span>
        <span>Reporter ${report.reporter_id}</span>
      </div>
      <div class="report-reason">${report.reason}</div>
      <div class="muted">Target: ${report.reported_id}</div>
      <div class="muted">${report.comment ? report.comment : "Без комментария"}</div>
      <div class="report-actions">
        <button class="btn small outline" data-action="open">Детали</button>
        <button class="btn small success" data-action="block">Блок</button>
        <button class="btn small danger" data-action="reject">Reject</button>
      </div>
    `;
    card.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => handleReportAction(report, btn.dataset.action));
    });
    els.reportsList.appendChild(card);
  });
  updateSummary();
}

async function handleReportAction(report, action) {
  if (action === "open") {
    state.activeReport = report;
    await loadUserDetails(report.reported_id);
    await loadAudit(report.reported_id);
    renderReports();
    return;
  }
  if (action === "block") {
    await apiFetch("/blocks", {
      method: "POST",
      body: JSON.stringify({ target_id: report.reported_id }),
    });
    showToast(`Пользователь ${report.reported_id} заблокирован`, "ok");
    await apiFetch("/reports/reject", {
      method: "POST",
      body: JSON.stringify({ report_id: report.id }),
    });
  }
  if (action === "reject") {
    await apiFetch("/reports/reject", {
      method: "POST",
      body: JSON.stringify({ report_id: report.id }),
    });
    showToast(`Репорт ${report.id} отклонен`, "ok");
  }
  await loadReports();
}

async function loadReports() {
  const payload = await apiFetch("/reports");
  state.reports = payload.data || [];
  if (
    state.activeReport &&
    !state.reports.find((item) => item.id === state.activeReport.id)
  ) {
    state.activeReport = null;
    els.userCard.innerHTML =
      '<div class="empty">Выберите репорт, чтобы увидеть детали.</div>';
    els.auditList.innerHTML = '<div class="empty">Нет данных аудита.</div>';
  }
  renderReports();
}

async function loadUserDetails(userId) {
  const payload = await apiFetch(`/users/${userId}`);
  const user = payload.data;
  if (!user) {
    els.userCard.innerHTML = '<div class="empty">Нет данных пользователя.</div>';
    return;
  }
  els.userMeta.textContent = `ID ${user.id} · ${user.city || "—"}`;
  const photo = user.primary_photo_url || (user.photos?.[0]?.url ?? "");
  const tags = (user.tags || []).map((tag) => `<span class="tag">${tag}</span>`).join("");
  els.userCard.innerHTML = `
    <div class="user-header">
      <div class="user-photo" style="background-image: url('${photo}')"></div>
      <div class="user-info">
        <h3>${user.name}</h3>
        <div class="muted">@${user.nickname}</div>
        <div class="muted">${user.gender || "—"} · ${user.age ?? "—"} лет</div>
      </div>
    </div>
    <div class="muted">${user.bio || "Описание не заполнено."}</div>
    <div class="tag-list">${tags || '<span class="muted">Без тегов</span>'}</div>
  `;
}

async function loadAudit(userId) {
  const query =
    state.auditMode === "actor"
      ? `/audit-log?actor_id=${userId}`
      : `/audit-log?target_id=${userId}`;
  const payload = await apiFetch(query);
  const items = payload.data || [];
  if (!items.length) {
    els.auditList.innerHTML = '<div class="empty">Нет данных аудита.</div>';
    return;
  }
  els.auditList.innerHTML = items
    .map(
      (item) => `
      <div class="audit-row">
        <strong>${item.event_type}</strong>
        <div class="muted">Actor: ${item.actor_id ?? "—"} · Target: ${
          item.target_id ?? "—"
        }</div>
        <div class="muted">${formatDate(item.created_at)}</div>
        <div class="muted">${JSON.stringify(item.metadata || {})}</div>
      </div>`
    )
    .join("");
}

async function bootstrap() {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved) {
    setAdminId(saved);
  }
  setLoginVisible(!state.adminId);
  if (state.adminId) {
    await loadReports().catch((err) => showToast(err.message, "error"));
  }
}

els.loginBtn.addEventListener("click", async () => {
  const value = els.loginInput.value.trim();
  if (!value) {
    els.loginError.textContent = "Введите ID";
    return;
  }
  setAdminId(value);
  els.loginError.textContent = "";
  setLoginVisible(false);
  try {
    await loadReports();
  } catch (err) {
    setLoginVisible(true);
    showToast(err.message, "error");
  }
});

els.logoutBtn.addEventListener("click", () => {
  setAdminId(null);
  setLoginVisible(true);
});

els.refreshBtn.addEventListener("click", async () => {
  await loadReports().catch((err) => showToast(err.message, "error"));
  if (state.activeReport) {
    await loadUserDetails(state.activeReport.reported_id);
    await loadAudit(state.activeReport.reported_id);
  }
});

els.auditChips.forEach((chip) => {
  chip.addEventListener("click", async () => {
    els.auditChips.forEach((item) => item.classList.remove("active"));
    chip.classList.add("active");
    state.auditMode = chip.dataset.auditFilter;
    if (state.activeReport) {
      await loadAudit(state.activeReport.reported_id);
    }
  });
});

bootstrap().catch((err) => showToast(err.message, "error"));
