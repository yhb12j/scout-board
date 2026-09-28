const state = {
  dataset: JSON.parse(localStorage.getItem("scout_dataset") || "null"),
  planQuery: "",
  plan: null,
  records: [],
};

const $ = (id) => document.getElementById(id);

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => showTab(button.dataset.tab));
});

$("create-form").addEventListener("submit", createDataset);
$("plan-form").addEventListener("submit", planQuery);
$("collect-button").addEventListener("click", collectQuery);
$("open-showcase").addEventListener("click", () => showTab("showcase"));
$("reload-records").addEventListener("click", loadRecords);
$("limit").addEventListener("change", loadRecords);
$("download-json").addEventListener("click", () => download("json"));
$("download-csv").addEventListener("click", () => download("csv"));
$("reload-audit").addEventListener("click", loadAudit);
$("modal-close").addEventListener("click", () => $("modal").close());
$("query").addEventListener("input", syncCollectButton);

start();

async function start() {
  showActive();
  const health = await api("/health");
  const banner = $("banner");
  if (!health.ok) {
    banner.hidden = false;
    banner.textContent = "Нет связи с сервером.";
  } else if (!health.data.gnews_configured) {
    banner.hidden = false;
    banner.textContent = "Источник новостей не настроен.";
  } else if (!health.data.llm_configured) {
    banner.hidden = false;
    banner.textContent = "Планировщик не настроен. Проверка запроса всё равно выполняется.";
  }
  await loadDatasets();
}

function showTab(name) {
  ["datasets", "collect", "showcase", "audit"].forEach((tab) => {
    $("panel-" + tab).hidden = tab !== name;
  });
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.tab === name);
  });
  if (name === "showcase") loadRecords();
  if (name === "audit") loadAudit();
  if (name === "collect") showActive();
}

function showActive() {
  const label = state.dataset
    ? state.dataset.name + " · " + state.dataset.source
    : "не выбран";
  $("active-label").textContent = label;
  $("collect-dataset").textContent = state.dataset
    ? state.dataset.name + " · " + state.dataset.source
    : "Набор не выбран.";
}

async function api(path, options = {}) {
  try {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const text = await response.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); } catch { data = { detail: text }; }
    }
    return { ok: response.ok, status: response.status, data };
  } catch {
    return { ok: false, status: 0, data: { detail: "Нет связи с сервером." } };
  }
}

function errorText(data, status) {
  if (!data) return "Ошибка " + status;
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    return data.detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  }
  if (data.reason) return data.reason;
  return "Ошибка " + status;
}

function showMessage(id, text, isError) {
  const node = $(id);
  node.hidden = !text;
  node.textContent = text || "";
  node.classList.toggle("is-error", Boolean(text) && Boolean(isError));
}

async function loadDatasets() {
  const result = await api("/datasets");
  if (!result.ok) {
    showMessage("datasets-message", errorText(result.data, result.status), true);
    return;
  }
  const rows = result.data || [];
  if (state.dataset && !rows.some((row) => row.dataset_id === state.dataset.dataset_id)) {
    state.dataset = null;
    localStorage.removeItem("scout_dataset");
    showActive();
  }
  $("datasets-empty").hidden = rows.length > 0;
  const body = $("datasets-body");
  body.replaceChildren();
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    if (state.dataset && state.dataset.dataset_id === row.dataset_id) tr.className = "is-selected";
    ["name", "source", "created_at", "dataset_id"].forEach((key) => {
      const td = document.createElement("td");
      td.textContent = row[key];
      tr.appendChild(td);
    });
    const action = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Выбрать";
    button.addEventListener("click", () => selectDataset(row));
    action.appendChild(button);
    tr.appendChild(action);
    body.appendChild(tr);
  });
}

function selectDataset(row) {
  state.dataset = {
    dataset_id: row.dataset_id,
    name: row.name,
    source: row.source,
  };
  localStorage.setItem("scout_dataset", JSON.stringify(state.dataset));
  showActive();
  showMessage("datasets-message", "Выбран набор «" + row.name + "».");
  loadDatasets();
}

async function createDataset(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {
    name: form.name.value.trim(),
    source: form.source.value.trim(),
  };
  const result = await api("/datasets", { method: "POST", body: JSON.stringify(payload) });
  if (!result.ok) {
    showMessage("datasets-message", errorText(result.data, result.status), true);
    return;
  }
  form.name.value = "";
  showMessage("datasets-message", "Набор создан. Идентификатор: " + result.data.dataset_id);
  await loadDatasets();
  selectDataset({
    dataset_id: result.data.dataset_id,
    name: payload.name,
    source: payload.source,
    created_at: "",
  });
}

async function planQuery(event) {
  event.preventDefault();
  $("saved-box").hidden = true;
  const query = $("query").value.trim();
  $("plan-button").disabled = true;
  const result = await api("/ai/plan_and_collect", { method: "POST", body: JSON.stringify({ query }) });
  $("plan-button").disabled = false;
  if (!result.ok) {
    showMessage("collect-message", errorText(result.data, result.status), true);
    return;
  }
  showMessage("collect-message", "");
  state.plan = result.data;
  state.planQuery = query;
  renderPlan(result.data);
  syncCollectButton();
}

function renderPlan(plan) {
  $("plan-box").hidden = false;
  $("plan-confidence").textContent = confidenceRu(plan.confidence);
  $("plan-fields").textContent = (plan.fields_to_keep || []).join(", ") || "—";
  const steps = $("plan-steps");
  steps.replaceChildren();
  (plan.plan_steps || []).forEach((step) => {
    const li = document.createElement("li");
    li.textContent = step;
    steps.appendChild(li);
  });
  $("plan-url").textContent = plan.api_url || "Запрос к источнику не сформирован.";
  $("plan-raw").textContent = JSON.stringify(plan, null, 2);

  const review = $("review-box");
  review.hidden = !plan.needs_review;
  $("review-reason").textContent = plan.reason || "";
  const hints = $("review-hints");
  hints.replaceChildren();
  (plan.hints || []).forEach((hint) => {
    const li = document.createElement("li");
    li.textContent = hint;
    hints.appendChild(li);
  });
}

function confidenceRu(value) {
  if (value === "high") return "высокая";
  if (value === "medium") return "средняя";
  if (value === "low") return "низкая";
  return value || "—";
}

function syncCollectButton() {
  const button = $("collect-button");
  const warning = $("collect-warning");
  const query = $("query").value.trim();
  if (!state.dataset) {
    button.disabled = true;
    warning.textContent = "Сначала выберите набор.";
    return;
  }
  if (!state.plan || query !== state.planQuery) {
    button.disabled = true;
    warning.textContent = state.plan
      ? "Запрос изменён. Постройте план ещё раз."
      : "Сначала постройте план.";
    return;
  }
  if (state.plan.needs_review) {
    button.disabled = true;
    warning.textContent = "Кнопка «Собрать» заблокирована: запрос требует ручной проверки.";
    return;
  }
  button.disabled = false;
  warning.textContent = "План готов.";
}

async function collectQuery() {
  if (!state.dataset || !state.plan || state.plan.needs_review) return;
  const button = $("collect-button");
  button.disabled = true;
  const result = await api("/datasets/" + state.dataset.dataset_id + "/collect", {
    method: "POST",
    body: JSON.stringify({ query: state.planQuery }),
  });
  if (result.status === 422 && result.data && result.data.status === "needs_review") {
    state.plan = { ...state.plan, needs_review: true, reason: result.data.reason, hints: result.data.hints };
    renderPlan(state.plan);
    syncCollectButton();
    return;
  }
  if (!result.ok) {
    showMessage("collect-message", errorText(result.data, result.status), true);
    syncCollectButton();
    return;
  }
  showMessage("collect-message", "");
  $("saved-box").hidden = false;
  const saved = result.data.records_saved;
  $("saved-text").textContent = saved
    ? "Готово. Сохранено записей: " + saved + ". Откройте витрину."
    : "Источник ответил, но подходящих материалов нет. Расширьте тему или период.";
  syncCollectButton();
}

async function loadRecords() {
  if (!state.dataset) {
    showMessage("showcase-message", "Сначала выберите набор.", true);
    $("records-empty").hidden = false;
    return;
  }
  const limit = $("limit").value;
  const result = await api("/datasets/" + state.dataset.dataset_id + "/records?limit=" + limit);
  if (!result.ok) {
    showMessage("showcase-message", errorText(result.data, result.status), true);
    return;
  }
  showMessage("showcase-message", "");
  state.records = result.data || [];
  renderRecords(state.records);
}

function renderRecords(rows) {
  $("records-empty").hidden = rows.length > 0;
  const fields = [];
  rows.forEach((row) => {
    Object.keys(row.record_json || {}).forEach((key) => {
      if (!fields.includes(key)) fields.push(key);
    });
  });
  $("showcase-caption").textContent = fields.length
    ? "Показаны только сохранённые поля: " + fields.join(", ")
    : "";
  const head = $("records-head");
  const body = $("records-body");
  head.replaceChildren();
  body.replaceChildren();
  const hr = document.createElement("tr");
  ["created_at", "dataset_id", ...fields, "record_json", ""].forEach((name) => {
    const th = document.createElement("th");
    th.textContent = name;
    hr.appendChild(th);
  });
  head.appendChild(hr);
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    addCell(tr, row.created_at);
    addCell(tr, row.dataset_id);
    fields.forEach((key) => addCell(tr, (row.record_json || {})[key] || ""));
    const short = JSON.stringify(row.record_json || {});
    addCell(tr, short.length > 90 ? short.slice(0, 90) + "…" : short);
    const td = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Открыть запись";
    button.addEventListener("click", () => openModal("Запись " + row.id, row));
    td.appendChild(button);
    tr.appendChild(td);
    body.appendChild(tr);
  });
}

function addCell(tr, text) {
  const td = document.createElement("td");
  td.textContent = text == null ? "" : String(text);
  tr.appendChild(td);
}

async function download(format) {
  if (!state.dataset) {
    showMessage("showcase-message", "Сначала выберите набор.", true);
    return;
  }
  const limit = $("limit").value;
  const response = await fetch(
    "/datasets/" + state.dataset.dataset_id + "/export?format=" + format + "&limit=" + limit
  );
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    showMessage("showcase-message", errorText(data, response.status), true);
    return;
  }
  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "records." + format;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function loadAudit() {
  const audit = await api("/audit/runs?limit=100");
  const agents = await api("/agent_runs?limit=50");
  const auditBody = $("audit-body");
  const agentBody = $("agent-body");
  auditBody.replaceChildren();
  agentBody.replaceChildren();
  (audit.data || []).forEach((row) => {
    const tr = document.createElement("tr");
    [row.created_at, row.action, row.status, row.duration_ms, row.error || ""].forEach((value) => addCell(tr, value));
    const td = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Подробнее";
    button.addEventListener("click", () => openModal(row.action, { input: row.input, output: row.output, error: row.error }));
    td.appendChild(button);
    tr.appendChild(td);
    auditBody.appendChild(tr);
  });
  (agents.data || []).forEach((row) => {
    const tr = document.createElement("tr");
    const query = row.query.length > 80 ? row.query.slice(0, 80) + "…" : row.query;
    [row.created_at, query, row.needs_review ? "true" : "false", row.error || ""].forEach((value) => addCell(tr, value));
    const td = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "План";
    button.addEventListener("click", () => openModal("agent_run", row));
    td.appendChild(button);
    tr.appendChild(td);
    agentBody.appendChild(tr);
  });
}

function openModal(title, value) {
  $("modal-title").textContent = title;
  $("modal-body").textContent = JSON.stringify(value, null, 2);
  $("modal").showModal();
}
