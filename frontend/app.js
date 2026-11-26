"use strict";

const $ = id => document.getElementById(id);
const el = (name, value, className) => {
  const node = document.createElement(name);
  if (value !== undefined) node.textContent = value;
  if (className) node.className = className;
  return node;
};
const hour = t => String(t).padStart(2, "0") + ":00";
const decimal = (v, n = 2) => Number(v).toFixed(n);
const percent = v => (100 * v).toFixed(1) + "%";
const seq = n => Array.from({length: n}, (_, i) => i);
const clone = value => structuredClone(value);
let house;
let plan;
let plannedHouse;
let previousCutoff = 0;
let studyData;
let scenarioTicket = 0;
let dispatchBusy = false;

async function api(url, body) {
  const response = await fetch(url, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(d => d.loc.join(".") + " · " + d.msg).join(" | ") : data.detail;
    throw new Error(detail || ("HTTP " + response.status));
  }
  return data;
}

function status(id, message = "", error = false) {
  $(id).textContent = message;
  $(id).classList.toggle("error", error);
}

function refreshDispatch() {
  $("solve").disabled = dispatchBusy || !house;
  $("roll").disabled = dispatchBusy || !plan || !house;
  $("export").disabled = !plan;
}

function table(labels, data, classes = "") {
  const node = el("table", undefined, classes);
  const head = node.createTHead().insertRow();
  for (const label of labels) head.append(el("th", label));
  const body = node.createTBody();
  for (const row of data) {
    const tr = body.insertRow();
    for (const value of row) {
      const cell = tr.insertCell();
      if (value instanceof Node) cell.append(value);
      else cell.textContent = value;
    }
  }
  return node;
}

function metrics(target, entries) {
  $(target).replaceChildren(...entries.map(([label, value, sub]) => {
    const card = el("div", undefined, "panel metric");
    card.append(el("span", label), el("strong", value), el("small", sub));
    return card;
  }));
}

function chart(target, {bars, lines, aria}) {
  const w = 860, h = 216, left = 38, right = 12, top = 15, bottom = 29;
  const n = bars?.length || lines[0].data.length;
  const width = (w - left - right) / n;
  const ymax = Math.ceil(Math.max(1, ...(bars || []), ...lines.flatMap(s => s.data)));
  const x = i => left + width * (i + 0.5);
  const y = v => h - bottom - v / ymax * (h - top - bottom);
  const svgElement = (name, attrs, text) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    for (const [key, val] of Object.entries(attrs)) node.setAttribute(key, val);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const svg = svgElement("svg", {viewBox: "0 0 " + w + " " + h, class: "chart", role: "img", "aria-label": aria});
  for (let i = 0; i <= 4; i++) {
    const t = ymax * i / 4;
    svg.append(svgElement("line", {x1: left, y1: y(t), x2: w - right, y2: y(t), stroke: "#e2ebe3"}));
    svg.append(svgElement("text", {x: left - 9, y: y(t) + 4, "text-anchor": "end", "font-size": "11", fill: "#52685a"}, Number(t.toFixed(2))));
  }
  if (bars) bars.forEach((v, i) => {
    const rect = svgElement("rect", {x: x(i) - width * .3, y: y(v), width: width * .6, height: y(0) - y(v), rx: 2, fill: "#3b855e"});
    rect.append(svgElement("title", {}, hour(i) + " · " + decimal(v) + " kW grid import"));
    svg.append(rect);
  });
  for (const series of lines) {
    const points = series.data.map((v, i) => x(i) + "," + y(v)).join(" ");
    const path = svgElement("polyline", {points, fill: "none", stroke: series.color, "stroke-width": 2.5, "stroke-linejoin": "round"});
    path.append(svgElement("title", {}, series.label));
    svg.append(path);
  }
  const step = n > 25 ? 6 : 3;
  for (let i = 0; i < n; i += step) {
    svg.append(svgElement("text", {x: x(i), y: h - 8, "text-anchor": "middle", "font-size": 11, fill: "#52685a"}, hour(i)));
  }
  $(target).replaceChildren(svg);
}

function expected(h, arrays) {
  const weights = h.solar.reduce((sum, s) => sum + s.weight, 0);
  return seq(h.baseline.length).map(t => arrays.reduce((sum, row, i) => sum + row[t] * h.solar[i].weight, 0) / (10 * weights));
}

function showPlan(result, h, label) {
  metrics("metrics", [
    ["Electricity cost", decimal(result.electricity_cents / 100), "Currency units · expected"],
    ["Grid imports", decimal(result.grid_kwh) + " kWh", "Across the full schedule"],
    ["Solar self-consumption", result.solar_share == null ? "—" : percent(result.solar_share), decimal(result.solar_kwh) + " kWh PV used"],
    ["Expected peak import", decimal(result.expected_peak_kw) + " kW", "Scenario-weighted daily peak"],
  ]);
  const slots = seq(h.baseline.length);
  chart("balance", {
    bars: expected(h, result.imports),
    lines: [
      {label: "PV forecast", color: "#ce8a2f", data: expected(h, h.solar.map(s => s.pv))},
      {label: "Household load", color: "#314c63", data: result.demand.map(x => x / 10)},
    ],
    aria: "Hourly grid imports, forecast PV supply, and household load",
  });
  const labels = {ev: "ev", essential: "essential", thermal_proxy: "proxy"};
  $("schedule-table").replaceChildren(table(["Appliance", ...slots.map(t => String(t).padStart(2, "0"))], h.appliances.map(a => [
    a.id + (a.kind === "ev" ? " · " + decimal(result.ev_kwh[a.id], 1) + " kWh" : ""),
    ...slots.map(t => {
      const on = result.schedule[a.id].includes(t);
      const s = el("span", undefined, "slot" + (on ? " on " + (labels[a.kind] || "") : ""));
      s.title = a.id + " · " + hour(t) + " · " + (on ? "On" : "Off");
      s.setAttribute("aria-label", s.title);
      return s;
    }),
  ]), "schedule-grid"));
  $("schedule-status").textContent = label;
  const headroom = slots.map(t => h.transformer[t] - h.neighbor[t] - Math.max(...result.imports.map(r => r[t])));
  const min = Math.min(...headroom);
  $("headroom").replaceChildren(el("strong", decimal(min / 10) + " kW"), el("p", "Minimum at " + hour(headroom.indexOf(min))));
}

async function loadScenario() {
  const ticket = ++scenarioTicket;
  dispatchBusy = true;
  refreshDispatch();
  status("status", "Loading scenario…");
  try {
    const next = await api("/api/scenario/" + $("scenario").value);
    if (ticket !== scenarioTicket) return;
    house = next;
    $("data-origin").textContent = $("scenario").value === "measured_uci"
      ? "Measured baseline · synthetic inputs" : "Synthetic inputs";
    plan = plannedHouse = undefined;
    previousCutoff = 0;
    $("cutoff").min = 0;
    for (const id of ["metrics", "balance", "schedule-table", "headroom", "changes", "schedule-status"]) $(id).replaceChildren();
    $("cutoff").max = house.baseline.length;
    renderConfig(house);
    await dispatch();
  } catch (error) {
    if (ticket === scenarioTicket) status("status", error.message, true);
  } finally {
    if (ticket === scenarioTicket) {
      dispatchBusy = false;
      refreshDispatch();
    }
  }
}

async function dispatch() {
  if (!house) return;
  const ticket = scenarioTicket;
  const h = clone(house);
  const solver = $("solver").value;
  dispatchBusy = true;
  refreshDispatch();
  status("status", "Optimizing schedule…");
  try {
    const run = await api("/api/solve", {house: h, solver});
    if (ticket !== scenarioTicket) return;
    if (!run.result) throw new Error("No feasible schedule returned · " + run.status);
    plannedHouse = h;
    plan = run.result;
    previousCutoff = 0;
    $("cutoff").min = 0;
    $("changes").replaceChildren();
    showPlan(plan, plannedHouse, run.status);
    status("status", h.name + " · classical solve in " + decimal(run.seconds * 1000, 1) + " ms");
  } catch (error) {
    if (ticket === scenarioTicket) status("status", error.message, true);
  } finally {
    if (ticket === scenarioTicket) {
      dispatchBusy = false;
      refreshDispatch();
    }
  }
}

function integerList(value) {
  if (!value.trim()) return [];
  const parts = value.split(",").map(t => t.trim());
  if (parts.some(v => !/^-?\d+$/.test(v))) throw new Error("Expected a comma-separated list of integers.");
  return parts.map(Number);
}

async function updatePlan() {
  if (!plan || !house) return;
  const ticket = scenarioTicket;
  dispatchBusy = true;
  refreshDispatch();
  status("status", "Reoptimizing remaining hours…");
  try {
    const cutoff = $("cutoff").valueAsNumber;
    const pct = $("cloud").valueAsNumber;
    if (!Number.isInteger(cutoff) || cutoff < previousCutoff || cutoff > plannedHouse.baseline.length || !(pct >= 0 && pct <= 100)) {
      throw new Error("Enter a valid hour and solar percentage.");
    }
    const changed = clone(house);
    for (const s of changed.solar) s.pv = s.pv.map((v, t) => t < cutoff ? v : Math.round(v * pct / 100));
    const typed = $("actual").value.trim();
    const actual = typed ? typed.split(",").map(n => {
      if (!n.trim() || !Number.isFinite(Number(n))) throw new Error("Realized PV values must be numeric.");
      return Math.round(Number(n) * 10);
    }) : plannedHouse.solar[0].pv.slice(0, cutoff);
    const rolled = await api("/api/reschedule", {original: plannedHouse, changed, schedule: plan.schedule, cutoff, actual_pv: actual, previous_cutoff: previousCutoff});
    if (ticket !== scenarioTicket) return;
    plan = rolled.updated;
    previousCutoff = rolled.cutoff;
    $("cutoff").min = previousCutoff;
    $("actual").value = "";
    house = plannedHouse = rolled.house;
    renderConfig(house);
    showPlan(plan, house, rolled.status);
    $("changes").replaceChildren(
      el("p", "Forecast-adjusted plan from " + hour(cutoff) + ". Expected grid energy " +
         decimal(rolled.previous.grid_kwh) + " → " + decimal(rolled.updated.grid_kwh) + " kWh across each plan’s forecast."),
      table(["Appliance", "Previous hours", "Updated hours", "Reason"], rolled.changes.map(c => [
        c.id, c.previous?.map(hour).join(", ") || "—", c.updated?.map(hour).join(", ") || "—", c.reason,
      ])),
    );
    status("status", "Revised plan · " + rolled.status);
  } catch (error) {
    if (ticket === scenarioTicket) status("status", error.message, true);
  } finally {
    if (ticket === scenarioTicket) {
      dispatchBusy = false;
      refreshDispatch();
    }
  }
}

const applianceFields = [
  ["id", "Appliance"], ["kind", "Service type"], ["power", "Power"], ["earliest", "Earliest"],
  ["latest", "Finish by"], ["duration", "Hours"], ["preferred", "Preferred"], ["priority", "Priority"],
  ["energy", "EV energy"], ["efficiency", "EV efficiency %"], ["forbidden", "Unavailable hours"], ["fixed", "Essential hours"],
];
const kinds = {cycle: "Non-interruptible", interruptible: "Interruptible", ev: "Electric vehicle",
               thermal_proxy: "Thermal proxy", essential: "Essential"};
const lists = new Set(["forbidden", "fixed"]);
const profileNames = {baseline: "Baseline demand", tariff: "Electricity tariff", feeder: "Feeder limit",
                      transformer: "Transformer limit", neighbor: "Neighbor demand", peak_price: "Peak price / kW"};

function configAppliance(a) {
  const tr = $("appliance-editor").tBodies[0].insertRow();
  applianceFields.forEach(([key, label]) => {
    const node = el(key === "kind" ? "select" : "input");
    if (key === "kind") {
      Object.keys(kinds).forEach(k => node.add(new Option(kinds[k], k)));
    } else {
      node.type = key === "id" || lists.has(key) ? "text" : "number";
    }
    node.setAttribute("aria-label", label);
    node.dataset.field = key;
    node.value = Array.isArray(a[key]) ? a[key].join(", ") : (a[key] ?? "");
    tr.insertCell().append(node);
  });
  const remove = el("button", "Remove", "secondary");
  remove.addEventListener("click", () => tr.remove());
  tr.insertCell().append(remove);
}

function renderConfig(h) {
  const skeleton = table([...applianceFields.map(f => f[1]), ""], [[]]);
  skeleton.tBodies[0].deleteRow(0);
  const old = $("appliance-editor");
  skeleton.id = old.id;
  old.replaceWith(skeleton);
  h.appliances.forEach(configAppliance);
  const wrapper = $("profile-editor");
  wrapper.replaceChildren();
  const add = (name, value, key, solar) => {
    const label = el("label", name);
    const input = el("input");
    input.dataset.field = key;
    if (solar !== undefined) input.dataset.solar = solar;
    input.value = Array.isArray(value) ? value.join(", ") : value;
    label.append(input);
    wrapper.append(label);
  };
  Object.entries(profileNames).forEach(([k, label]) => add(label, h[k], k));
  h.solar.forEach((s, i) => {
    add(s.name + " · PV", s.pv, "pv", i);
    add(s.name + " · weight", s.weight, "weight", i);
  });
  $("config").value = JSON.stringify(h, null, 2);
}

function applyConfig() {
  try {
    const updated = JSON.parse($("config").value);
    updated.appliances = [...$("appliance-editor").tBodies[0].rows].map(row =>
      Object.fromEntries([...row.querySelectorAll("[data-field]")].map(input => {
        const key = input.dataset.field;
        const value = input.value;
        return [key, lists.has(key) ? integerList(value) :
          (key === "id" || key === "kind" ? value : (key === "preferred" && value === "" ? null : input.valueAsNumber))];
      }))
    );
    [...$("profile-editor").querySelectorAll("input")].forEach(input => {
      const key = input.dataset.field;
      const target = input.dataset.solar === undefined ? updated : updated.solar[Number(input.dataset.solar)];
      target[key] = key === "peak_price" || key === "weight" ? Number(input.value) : integerList(input.value);
    });
    house = updated;
    renderConfig(house);
    refreshDispatch();
    status("config-status", "Configuration applied. Optimize a new schedule or replan the existing one from the current hour.");
  } catch (error) {
    status("config-status", error.message, true);
  }
}

function download() {
  if (!plan) return;
  const quote = s => {
    const raw = String(s);
    const value = /^[=+@\-\t\r]/.test(raw) ? "'" + raw : raw;
    return '"' + value.replaceAll('"', '""') + '"';
  };
  const lines = [["appliance", "active_slots", "ev_battery_kwh"],
    ...Object.entries(plan.schedule).map(([id, slots]) => [id, slots.map(hour).join(" "), plan.ev_kwh[id] ?? ""])];
  const blob = new Blob([lines.map(line => line.map(quote).join(",")).join("\n") + "\n"], {type: "text/csv"});
  const url = URL.createObjectURL(blob);
  const link = el("a");
  link.href = url;
  link.download = "quantumnest-schedule.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 500);
}

function renderQuantum(q, h) {
  const norm = 10 * h.solar.reduce((sum, s) => sum + s.weight, 0);
  const best = q.result?.objective;
  const exact = q.comparison_optimum;
  $("q-comparison").replaceChildren(table(
    ["Best sampled objective", "CP-SAT optimum", "Relative gap", "Optimizer evaluations"],
    [[best == null ? "No feasible shot" : decimal(best / norm) + " cents",
      exact == null ? "Unavailable" : decimal(exact / norm) + " cents",
      best != null && exact ? percent((best - exact) / exact) : "—", q.evaluation_count]],
  ));
  metrics("q-metrics", [
    ["Feasible shots", percent(q.feasible_probability), q.shots + " measurements"],
    ["Optimal shots", q.optimal_probability == null ? "—" : percent(q.optimal_probability), "Against classical optimum"],
    ["Decision qubits", q.decision_qubits, q.auxiliary_qubits + " auxiliary"],
    ["Transpiled CX gates", q.two_qubit_gates, "Gate depth " + q.transpiled_depth],
  ]);
  const top = Object.entries(q.counts).sort((a, b) => b[1] - a[1]).slice(0, 20);
  $("histogram").replaceChildren(...top.map(([bits, count]) => {
    const row = el("div", undefined, "hist-row");
    const track = el("div", undefined, "hist-track");
    const bar = el("div");
    bar.style.width = percent(count / q.shots);
    track.append(bar);
    row.append(el("span", bits || "∅"), track, el("span", percent(count / q.shots)));
    return row;
  }));
  const offset = q.cost_offset;
  $("matrix-note").textContent = "Upper-triangular convention. Constant offset " + offset + ". Select a cell for its variable labels.";
  $("cell-note").textContent = "";
  $("matrix").replaceChildren(table(["q", ...q.labels.map((_, j) => j)], q.matrix.map((row, i) => [
    i, ...row.map((v, j) => {
      const btn = el("button", v, v ? "nonzero" : "");
      btn.setAttribute("aria-label", "Coefficient " + i + ", " + j + " equals " + v);
      btn.addEventListener("click", () => $("cell-note").textContent = q.labels[i] + " × " + q.labels[j] + " · " + v);
      return btn;
    }),
  ]), "matrix-table"));
  $("circuit").textContent = q.circuit || "All choices fixed in preprocessing.";
  $("transpiled").textContent = q.transpiled || "No circuit required.";
  const excluded = q.shots - Math.round(q.feasible_probability * q.shots);
  const schedule = q.result ? " · Best sampled schedule: " + Object.entries(q.result.schedule)
    .map(([id, slots]) => id + " " + slots.map(hour).join(", ")).join(" / ") : " · No feasible shot";
  status("q-status", "Qiskit Aer · " + q.noise + " · " + excluded + " infeasible shots" + schedule);
  $("q-output").hidden = false;
}

async function quantum() {
  $("q-run").disabled = true;
  $("q-output").hidden = true;
  $("q-metrics").replaceChildren();
  status("q-status", "Optimizing variational angles and sampling the compiled circuit…");
  try {
    const chosen = $("q-case").value;
    const h = chosen === "current" ? clone(house) : await api("/api/scenario/" + chosen);
    if (!h) throw new Error("Load a scenario first.");
    const q = await api("/api/solve", {house: h, solver: $("q-solver").value,
      noise: $("q-noise").value, layers: $("q-layers").valueAsNumber, shots: 512});
    renderQuantum(q, h);
  } catch (error) {
    status("q-status", error.message, true);
  } finally {
    $("q-run").disabled = false;
  }
}

async function research() {
  try {
    studyData ||= (await api("/api/study")).summary;
    const rows = studyData.filter(r => r.study === $("study-select").value);
    if (!rows.length) {
      $("study-table").textContent = "No experiment results are available.";
      return;
    }
    const select = $("metric-select");
    const value = select.value;
    select.replaceChildren(...[...new Set(rows.map(r => r.metric))].map(key => new Option(key.replaceAll("_", " "), key)));
    if ([...select.options].some(o => o.value === value)) select.value = value;
    $("study-table").replaceChildren(table(["Original bits", "Method", "Layers", "Noise", "Included / attempted", "Mean", "SD", "95% interval"],
      rows.filter(r => r.metric === select.value).map(r => [r.size, r.algorithm, r.layers, r.noise,
        r.n + " / " + r.attempted, decimal(r.mean, 4), r.sd === "" ? "—" : decimal(r.sd, 4),
        r.low95 === "" ? "—" : decimal(r.low95, 4) + " to " + decimal(r.high95, 4),
      ])));
  } catch (error) {
    $("study-table").textContent = error.message;
  }
}

function view() {
  const requested = location.hash.slice(1);
  const links = [...document.querySelectorAll("[data-view]")];
  const active = links.find(a => a.dataset.view === requested) || links[0];
  links.forEach(a => {
    $(a.dataset.view).hidden = a !== active;
    a.classList.toggle("active", a === active);
    if (a === active) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  $("breadcrumb").textContent = "Workspace / " + active.textContent.trim().split(" ").slice(1).join(" ");
  if (active.dataset.view === "study") void research();
}

$("scenario").addEventListener("change", loadScenario);
$("solve").addEventListener("click", dispatch);
$("roll").addEventListener("click", updatePlan);
$("q-run").addEventListener("click", quantum);
$("export").addEventListener("click", download);
$("save-config").addEventListener("click", applyConfig);
$("import-json").addEventListener("click", () => {
  try {
    renderConfig(JSON.parse($("config").value));
    status("config-status", "JSON loaded into the editor.");
  } catch (error) {
    status("config-status", error.message, true);
  }
});
$("add-appliance").addEventListener("click", () => configAppliance({
  id: "New appliance", kind: "cycle", power: 10, earliest: 8, latest: Math.min(24, house.baseline.length),
  duration: 1, priority: 0, energy: 0, efficiency: 90, forbidden: [], fixed: [],
}));
for (const id of ["study-select", "metric-select"]) $(id).addEventListener("change", research);
window.addEventListener("hashchange", view);
view();
void loadScenario();

fetch("/assets/legacy-profiles.json").then(response => response.json()).then(legacy => {
  chart("legacy-chart", {lines: [
    {label: "Normalized solar profile", color: "#ce8a2f", data: legacy.solar},
    {label: "Original tariff values", color: "#314c63", data: legacy.tariff},
  ], aria: "Historical synthetic normalized PV and nominal tariff profiles"});
});
