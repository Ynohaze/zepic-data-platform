const elements = {
  form: document.querySelector("#queryForm"),
  question: document.querySelector("#question"),
  submit: document.querySelector("#submitButton"),
  healthDot: document.querySelector("#healthDot"),
  healthLabel: document.querySelector("#healthLabel"),
  runStatus: document.querySelector("#runStatus"),
  runSteps: [...document.querySelectorAll("#runSteps li")],
  results: document.querySelector("#resultSection"),
  empty: document.querySelector("#emptyState"),
  error: document.querySelector("#errorPanel"),
  errorText: document.querySelector("#errorText"),
  answer: document.querySelector("#answerText"),
  resultTime: document.querySelector("#resultTime"),
  metricStrip: document.querySelector("#metricStrip"),
  rowBadge: document.querySelector("#rowBadge"),
  elapsedBadge: document.querySelector("#elapsedBadge"),
  modelBadge: document.querySelector("#modelBadge"),
  requestBadge: document.querySelector("#requestBadge"),
  table: document.querySelector("#resultTable"),
  chart: document.querySelector("#chart"),
  sql: document.querySelector("#sqlText"),
  copySql: document.querySelector("#copySql"),
  sources: document.querySelector("#sourceModels"),
  limitationList: document.querySelector("#limitationList"),
  limitationCount: document.querySelector("#limitationCount"),
  kpiOrderValue: document.querySelector("#kpiOrderValue"),
  kpiOrders: document.querySelector("#kpiOrders"),
  kpiProduced: document.querySelector("#kpiProduced"),
  kpiInventory: document.querySelector("#kpiInventory"),
  dashboardTimestamp: document.querySelector("#dashboardTimestamp"),
  overviewView: document.querySelector("#overviewView"),
  askView: document.querySelector("#askView"),
  orderTrendChart: document.querySelector("#orderTrendChart"),
  statusChart: document.querySelector("#statusChart"),
  productionChart: document.querySelector("#productionChart"),
  inventoryTable: document.querySelector("#inventoryTable"),
  dashboardError: document.querySelector("#dashboardError"),
};

const integerFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const moneyFormatter = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function setHealth(status) {
  elements.healthDot.className = "status-dot";
  if (status === "ready") {
    elements.healthDot.classList.add("status-dot--ready");
    elements.healthLabel.textContent = "Analytics ready";
  } else {
    elements.healthDot.classList.add("status-dot--error");
    elements.healthLabel.textContent = status === "configuration_required"
      ? "Configuration required"
      : "Service unavailable";
  }
}

async function checkHealth() {
  try {
    const response = await fetch("/health", { headers: { Accept: "application/json" } });
    const body = await response.json();
    setHealth(response.ok ? body.status : "error");
  } catch {
    setHealth("error");
  }
}

function renderOverviewSummary(summary) {
  elements.kpiOrderValue.textContent = moneyFormatter.format(Number(summary.booked_order_value));
  elements.kpiOrders.textContent = integerFormatter.format(summary.total_orders);
  elements.kpiProduced.textContent = integerFormatter.format(summary.produced_units);
  elements.kpiInventory.textContent = integerFormatter.format(summary.inventory_units);
  const inventoryTime = summary.inventory_updated_at
    ? new Date(summary.inventory_updated_at).toLocaleString()
    : "unavailable";
  elements.dashboardTimestamp.textContent = summary.latest_order_date
    ? `Orders through ${summary.latest_order_date} · Inventory updated ${inventoryTime}`
    : `Inventory updated ${inventoryTime}`;
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  return element;
}

function renderOrderTrend(points) {
  elements.orderTrendChart.replaceChildren();
  if (!points.length) return;
  const width = 900;
  const height = 235;
  const pad = { top: 12, right: 14, bottom: 26, left: 14 };
  const values = points.map((point) => Number(point.booked_order_value));
  const orders = points.map((point) => point.order_count);
  const maxValue = Math.max(...values, 1);
  const maxOrders = Math.max(...orders, 1);
  const x = (index) => pad.left + (index / Math.max(points.length - 1, 1)) * (width - pad.left - pad.right);
  const yValue = (value) => height - pad.bottom - (value / maxValue) * (height - pad.top - pad.bottom);
  const yOrders = (value) => height - pad.bottom - (value / maxOrders) * (height - pad.top - pad.bottom);
  const svg = svgElement("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
  const defs = svgElement("defs");
  const gradient = svgElement("linearGradient", { id: "orderArea", x1: "0", y1: "0", x2: "0", y2: "1" });
  gradient.append(svgElement("stop", { offset: "0%", "stop-color": "#2f745e" }), svgElement("stop", { offset: "100%", "stop-color": "#b9e4d0", "stop-opacity": "0" }));
  defs.append(gradient);
  svg.append(defs);

  [0, 0.5, 1].forEach((ratio) => {
    const y = pad.top + ratio * (height - pad.top - pad.bottom);
    svg.append(svgElement("line", { x1: pad.left, x2: width - pad.right, y1: y, y2: y, class: "chart-grid-line" }));
  });

  const valuePoints = points.map((point, index) => `${x(index)},${yValue(values[index])}`).join(" ");
  const orderPoints = points.map((point, index) => `${x(index)},${yOrders(orders[index])}`).join(" ");
  const areaPoints = `${pad.left},${height - pad.bottom} ${valuePoints} ${width - pad.right},${height - pad.bottom}`;
  svg.append(svgElement("polygon", { points: areaPoints, class: "chart-area" }));
  svg.append(svgElement("polyline", { points: valuePoints, class: "chart-value-line" }));
  svg.append(svgElement("polyline", { points: orderPoints, class: "chart-order-line" }));

  points.forEach((point, index) => {
    const dot = svgElement("circle", { cx: x(index), cy: yValue(values[index]), r: 2.7, class: "chart-dot" });
    const title = svgElement("title");
    title.textContent = `${point.order_date}: ${moneyFormatter.format(values[index])} · ${point.order_count} orders`;
    dot.append(title);
    svg.append(dot);
  });

  [0, Math.floor((points.length - 1) / 2), points.length - 1].forEach((index) => {
    const label = svgElement("text", { x: x(index), y: height - 6, "text-anchor": index === 0 ? "start" : index === points.length - 1 ? "end" : "middle", class: "chart-axis-label" });
    label.textContent = points[index].order_date.slice(5);
    svg.append(label);
  });
  elements.orderTrendChart.append(svg);
}

function renderStatuses(statuses) {
  elements.statusChart.replaceChildren();
  const total = statuses.reduce((sum, status) => sum + status.order_count, 0) || 1;
  statuses.forEach((status) => {
    const row = document.createElement("div");
    row.className = "status-row";
    const meta = document.createElement("div");
    meta.className = "status-row__meta";
    const name = document.createElement("span");
    name.textContent = status.order_status;
    const value = document.createElement("small");
    const share = (status.order_count / total) * 100;
    value.textContent = `${integerFormatter.format(status.order_count)} · ${share.toFixed(1)}%`;
    meta.append(name, value);
    const track = document.createElement("div");
    track.className = "status-track";
    const fill = document.createElement("span");
    fill.style.width = `${share}%`;
    track.append(fill);
    row.append(meta, track);
    elements.statusChart.append(row);
  });
}

function renderProduction(points) {
  elements.productionChart.replaceChildren();
  const max = Math.max(...points.flatMap((point) => [point.planned_quantity, point.actual_quantity]), 1);
  points.forEach((point) => {
    const day = document.createElement("div");
    day.className = "production-day";
    [["planned", point.planned_quantity], ["actual", point.actual_quantity]].forEach(([type, value]) => {
      const bar = document.createElement("div");
      bar.className = `production-bar${type === "actual" ? " production-bar--actual" : ""}`;
      bar.style.height = `${Math.max(2, (value / max) * 100)}%`;
      const tooltip = document.createElement("span");
      tooltip.textContent = `${point.planned_start_date} · ${type}: ${integerFormatter.format(value)}`;
      bar.append(tooltip);
      day.append(bar);
    });
    elements.productionChart.append(day);
  });
}

function renderInventory(rows) {
  elements.inventoryTable.replaceChildren();
  const columns = ["sku", "product", "available", "retail value"];
  const thead = document.createElement("thead");
  const header = document.createElement("tr");
  columns.forEach((column) => {
    const th = document.createElement("th");
    th.textContent = column;
    header.append(th);
  });
  thead.append(header);
  const tbody = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    [row.sku, row.product_name, integerFormatter.format(row.available_quantity), moneyFormatter.format(Number(row.inventory_retail_value))].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    });
    tbody.append(tr);
  });
  elements.inventoryTable.append(thead, tbody);
}

async function loadDashboardOverview() {
  try {
    const response = await fetch("/api/dashboard/overview", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("Dashboard request failed");
    const overview = await response.json();
    renderOverviewSummary(overview.summary);
    renderOrderTrend(overview.daily_orders);
    renderStatuses(overview.order_status);
    renderProduction(overview.production);
    renderInventory(overview.low_inventory);
    elements.dashboardError.classList.add("is-hidden");
  } catch {
    elements.dashboardTimestamp.textContent = "Database unavailable";
    elements.dashboardError.classList.remove("is-hidden");
  }
}

function setRunState(state) {
  elements.runStatus.className = "run-status";
  elements.runStatus.textContent = state === "running" ? "Running" : state === "complete" ? "Complete" : state === "error" ? "Stopped" : "Ready";
  if (state !== "ready") elements.runStatus.classList.add(`is-${state}`);

  elements.runSteps.forEach((step, index) => {
    step.classList.remove("is-active", "is-complete");
    if (state === "running" && index === 0) step.classList.add("is-active");
    if (state === "complete") step.classList.add("is-complete");
  });
}

function cellText(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function renderTable(columns, rows) {
  elements.table.replaceChildren();
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  columns.forEach((column) => {
    const th = document.createElement("th");
    th.textContent = column.replaceAll("_", " ");
    headerRow.append(th);
  });
  thead.append(headerRow);

  const tbody = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    columns.forEach((column) => {
      const td = document.createElement("td");
      td.textContent = cellText(row[column]);
      tr.append(td);
    });
    tbody.append(tr);
  });
  elements.table.append(thead, tbody);
}

function renderMetrics(columns, rows) {
  elements.metricStrip.replaceChildren();
  if (rows.length !== 1) return;
  columns.slice(0, 5).forEach((column) => {
    const metric = document.createElement("span");
    metric.className = "metric";
    const label = document.createTextNode(`${column.replaceAll("_", " ")}: `);
    const value = document.createElement("b");
    value.textContent = cellText(rows[0][column]);
    metric.append(label, value);
    elements.metricStrip.append(metric);
  });
}

function renderChart(columns, rows) {
  elements.chart.replaceChildren();
  elements.chart.classList.add("is-hidden");
  if (rows.length < 2 || rows.length > 60) return;

  const numericColumn = columns.find((column) => rows.some((row) => {
    const value = Number(row[column]);
    return row[column] !== null && row[column] !== "" && Number.isFinite(value);
  }));
  if (!numericColumn) return;

  const values = rows.map((row) => Number(row[numericColumn]) || 0);
  const max = Math.max(...values);
  if (max <= 0) return;

  rows.forEach((row, index) => {
    const bar = document.createElement("div");
    bar.className = "chart-bar";
    bar.style.height = `${Math.max(3, (values[index] / max) * 100)}%`;
    const tooltip = document.createElement("span");
    const label = cellText(row[columns[0]]);
    tooltip.textContent = `${label}: ${cellText(row[numericColumn])}`;
    bar.append(tooltip);
    elements.chart.append(bar);
  });
  elements.chart.setAttribute("aria-label", `${numericColumn.replaceAll("_", " ")} across ${rows.length} rows`);
  elements.chart.classList.remove("is-hidden");
}

function renderResult(data) {
  elements.answer.textContent = data.answer;
  elements.resultTime.textContent = new Date(data.result_timestamp).toLocaleString();
  elements.rowBadge.textContent = `${data.row_count} row${data.row_count === 1 ? "" : "s"}${data.truncated ? " · capped" : ""}`;
  elements.elapsedBadge.textContent = `${data.query_elapsed_ms} ms DB · ${data.total_elapsed_ms} ms total`;
  elements.modelBadge.textContent = data.model;
  elements.requestBadge.textContent = `Run ${data.request_id}`;
  elements.sql.textContent = data.generated_sql;

  elements.sources.replaceChildren();
  data.source_models.forEach((source) => {
    const tag = document.createElement("span");
    tag.textContent = source;
    elements.sources.append(tag);
  });

  elements.limitationList.replaceChildren();
  data.limitations.forEach((limitation) => {
    const item = document.createElement("li");
    item.textContent = limitation;
    elements.limitationList.append(item);
  });
  elements.limitationCount.textContent = `(${data.limitations.length})`;

  renderMetrics(data.columns, data.rows);
  renderTable(data.columns, data.rows);
  renderChart(data.columns, data.rows);
  elements.empty.classList.add("is-hidden");
  elements.error.classList.add("is-hidden");
  elements.results.classList.remove("is-hidden");
}

function errorMessage(body, status) {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (detail?.message) return detail.message;
  if (status === 503) return "The analytics provider is not configured.";
  return "The bounded run stopped before producing a trusted answer.";
}

async function runAnalysis(question) {
  setRunState("running");
  elements.submit.disabled = true;
  elements.submit.querySelector("span").textContent = "Running…";
  elements.error.classList.add("is-hidden");

  try {
    const response = await fetch("/api/chat/query", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ question }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(errorMessage(body, response.status));
    renderResult(body);
    setRunState("complete");
  } catch (error) {
    elements.errorText.textContent = error.message;
    elements.error.classList.remove("is-hidden");
    setRunState("error");
  } finally {
    elements.submit.disabled = false;
    elements.submit.querySelector("span").textContent = "Run analysis";
  }
}

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = elements.question.value.trim();
  if (question) runAnalysis(question);
});

elements.question.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    elements.form.requestSubmit();
  }
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => {
    elements.question.value = button.dataset.question;
    elements.question.focus();
  });
});

document.querySelectorAll("[data-view-target]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = button.dataset.viewTarget;
    elements.overviewView.classList.toggle("is-hidden", target !== "overview");
    elements.askView.classList.toggle("is-hidden", target !== "ask");
    document.querySelectorAll("[data-view-target]").forEach((item) => {
      item.classList.toggle("is-active", item === button);
    });
    if (target === "ask") elements.question.focus();
  });
});

elements.copySql.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(elements.sql.textContent);
    elements.copySql.textContent = "Copied";
    window.setTimeout(() => { elements.copySql.textContent = "Copy"; }, 1200);
  } catch {
    elements.copySql.textContent = "Unavailable";
  }
});

setRunState("ready");
checkHealth();
loadDashboardOverview();
