"use strict";
const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const pad = (n) => String(n).padStart(2, "0");
const fmtClock = (s) => { const d = new Date(s * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
const fmtFull = (s) => { const d = new Date(s * 1000); return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${fmtClock(s)}`; };
function fmtBps(v) {
  if (v == null) return "—";
  for (const [u, d] of [["Gbps", 1e9], ["Mbps", 1e6], ["kbps", 1e3]]) if (Math.abs(v) >= d) return (v / d).toFixed(2) + " " + u;
  return v.toFixed(0) + " bps";
}
async function api(path, opts) {
  const r = await fetch(path, opts && opts.body ? { method: "POST", headers: { "content-type": "application/json" }, ...opts } : opts);
  return r.json();
}

/* ---------- abas ---------- */
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll(".panel").forEach((p) => (p.hidden = p.id !== "tab-" + b.dataset.tab));
  if (b.dataset.tab === "events") loadEvents();
  if (b.dataset.tab === "hist" && !histLoaded) quickRange(1);
  if (b.dataset.tab === "cfg") loadConfig();
}));

/* ---------- gráficos ---------- */
function makeChart(canvas, series, yTitle, yFmt, yMax) {
  return new Chart(canvas, {
    type: "line",
    data: { datasets: series.map((s) => ({ label: s.label, data: [], borderColor: s.color, backgroundColor: s.color,
      borderWidth: s.dash ? 1.5 : 2, borderDash: s.dash ? [6, 4] : [], pointRadius: 0, spanGaps: false, tension: 0.15 })) },
    options: {
      animation: false, maintainAspectRatio: false, parsing: false, normalized: true,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        x: { type: "linear", title: { display: true, text: "Horário" }, ticks: { maxTicksLimit: 8, callback: (v) => fmtClock(v) }, grid: { color: css("--line") } },
        y: { beginAtZero: true, max: yMax, title: { display: true, text: yTitle }, ticks: { callback: yFmt }, grid: { color: css("--line") } },
      },
      plugins: {
        legend: { labels: { usePointStyle: true, boxHeight: 6, filter: (item, d) => d.datasets[item.datasetIndex].data.length > 0 } },
        tooltip: { callbacks: { title: (it) => fmtFull(it[0].parsed.x), label: (it) => `${it.dataset.label}: ${yFmt === fmtBps ? fmtBps(it.parsed.y) : it.parsed.y.toFixed(1) + " %"}` } },
      },
    },
  });
}
const trafSeries = () => [{ label: "IN (entrada)", color: css("--in") }, { label: "OUT (saída)", color: css("--out") },
  { label: "Limite IN", color: css("--in"), dash: true }, { label: "Limite OUT", color: css("--out"), dash: true }];
const cpuSeries = () => [{ label: "CPU", color: css("--cpu") }, { label: "Limite CPU", color: css("--bad"), dash: true }];
const pct = (v) => v + "%";
const cTraf = makeChart($("cTraf"), trafSeries(), "Taxa (bps)", fmtBps);
const cCpu = makeChart($("cCpu"), cpuSeries(), "Utilização (%)", pct, 100);
const hTraf = makeChart($("hTraf"), trafSeries(), "Taxa (bps)", fmtBps);
const hCpu = makeChart($("hCpu"), cpuSeries(), "Utilização (%)", pct, 100);

/** Converte amostras em pontos {x,y}; insere null quando há lacuna maior que maxGap (s). */
function series(samples, key, maxGap) {
  const out = []; let prev = null;
  for (const s of samples) {
    if (prev !== null && s.ts - prev > maxGap) out.push({ x: prev + 0.001, y: null });
    out.push({ x: s.ts, y: s[key] });
    prev = s.ts;
  }
  return out;
}
const flat = (x0, x1, v) => (v > 0 ? [{ x: x0, y: v }, { x: x1, y: v }] : []);
function paint(tc, cc, samples, maxGap, th) {
  const x0 = samples.length ? samples[0].ts : 0, x1 = samples.length ? samples[samples.length - 1].ts : 1;
  tc.data.datasets[0].data = series(samples, "in_bps", maxGap);
  tc.data.datasets[1].data = series(samples, "out_bps", maxGap);
  tc.data.datasets[2].data = flat(x0, x1, th.threshold_in_bps);
  tc.data.datasets[3].data = flat(x0, x1, th.threshold_out_bps);
  cc.data.datasets[0].data = series(samples, "cpu", maxGap);
  cc.data.datasets[1].data = flat(x0, x1, th.threshold_cpu);
  tc.update("none"); cc.update("none");
}

/* ---------- tempo real ---------- */
let session = 0, lastSeq = 0, lastAlert = 0, live = [], st = null;
async function tick() {
  try {
    const d = await api(`/api/live?since=${lastSeq}&session=${session}&alert=${lastAlert}`);
    if (d.session !== session) { live = []; session = d.session; }
    for (const s of d.samples) { live.push(s); lastSeq = s.seq; }
    if (live.length > 720) live = live.slice(-720);
    if (!live.length) lastSeq = 0;
    st = d.status;
    renderStatus(d.status);
    const gap = Math.max(3 * d.status.interval, 3);
    paint(cTraf, cCpu, live, gap, d.status.thresholds);
    const last = live[live.length - 1];
    $("rIn").textContent = last ? fmtBps(last.in_bps) : "—";
    $("rOut").textContent = last ? fmtBps(last.out_bps) : "—";
    $("rCpu").textContent = last && last.cpu != null ? last.cpu.toFixed(0) + " %" : "—";
    $("rLast").textContent = d.status.last_ok ? fmtFull(d.status.last_ok) : "—";
    const th = d.status.thresholds;
    document.querySelector(".ro.in").classList.toggle("over", !!(last && th.threshold_in_bps > 0 && last.in_bps > th.threshold_in_bps));
    document.querySelector(".ro.out").classList.toggle("over", !!(last && th.threshold_out_bps > 0 && last.out_bps > th.threshold_out_bps));
    document.querySelector(".ro.cpu").classList.toggle("over", !!(last && th.threshold_cpu > 0 && last.cpu > th.threshold_cpu));
    for (const a of d.alerts) { lastAlert = a.id; a.kind === "alert" ? queuePopup(a) : toast(a.text); }
  } catch (e) {
    renderStatus({ state: "error", last_error: "Sem comunicação com o servidor da aplicação.", router: "—", interface: "—" });
  }
}
function renderStatus(s) {
  const err = s.last_error && s.state === "running";
  const pill = $("pill");
  pill.className = "pill " + (err ? "error" : s.state);
  pill.textContent = err ? "Falha SNMP" : { running: "Coletando", paused: "Pausado", stopped: "Parado", error: "Sem servidor" }[s.state];
  $("target").textContent = `${s.router} · ${s.interface} · a cada ${s.interval}s${s.simulate ? " · SIMULAÇÃO" : ""}`;
  $("ifLabel").textContent = s.interface || "";
  $("bStart").disabled = s.state === "running";
  $("bStart").textContent = s.state === "paused" ? "Retomar" : "Iniciar";
  $("bPause").disabled = s.state !== "running";
  const b = $("banner");
  b.hidden = !s.last_error;
  if (s.last_error) b.textContent = "Falha na coleta SNMP: " + s.last_error + " A coleta continua tentando.";
}
$("bStart").onclick = async () => { await api("/api/start", { body: "{}" }); tick(); };
$("bPause").onclick = async () => { await api("/api/pause", { body: "{}" }); tick(); };
$("bReset").onclick = async () => { await api("/api/reset", { body: "{}" }); live = []; lastSeq = 0; tick(); };
setInterval(tick, 1000); tick();

/* ---------- pop-ups e avisos ---------- */
const queue = [];
function queuePopup(a) { queue.push(a); if (!$("popup").open) showPopup(); }
function showPopup() {
  const a = queue.shift(); if (!a) return;
  $("pBody").innerHTML = "";
  for (const [k, v] of [["Métrica", a.label], ["Valor observado", a.value], ["Limite", a.limit], ["Horário", a.when]]) {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = k; dd.textContent = v; $("pBody").append(dt, dd);
  }
  $("pMore").textContent = queue.length ? `Mais ${queue.length} alerta(s) na fila.` : "";
  $("popup").showModal();
}
$("pClose").onclick = () => { $("popup").close(); showPopup(); };
function toast(text) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = text; $("toasts").append(t);
  setTimeout(() => t.remove(), 12000);
}

/* ---------- histórico ---------- */
let histLoaded = false;
const toLocalInput = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
function quickRange(h) {
  const end = new Date(), start = new Date(end - h * 3600e3);
  $("hStart").value = toLocalInput(start); $("hEnd").value = toLocalInput(end);
  loadHistory();
}
document.querySelectorAll(".quick button").forEach((b) => b.onclick = () => quickRange(+b.dataset.h));
$("histForm").onsubmit = (e) => { e.preventDefault(); loadHistory(); };
async function loadHistory() {
  histLoaded = true;
  const s = new Date($("hStart").value).getTime() / 1000, e = new Date($("hEnd").value).getTime() / 1000;
  const d = await api(`/api/history?start=${s}&end=${e}`);
  const empty = $("histEmpty");
  if (d.error) { $("histInfo").textContent = ""; empty.textContent = d.error; empty.hidden = false; $("histCharts").hidden = true; return; }
  $("histInfo").textContent = `Período: ${fmtFull(s)} até ${fmtFull(e)}` + (d.total
    ? ` · ${d.total} amostras (primeira ${fmtFull(d.first)}, última ${fmtFull(d.last)})` + (d.bucket_s ? ` · agrupadas em médias de ${Math.round(d.bucket_s)}s para exibição` : "") : "");
  empty.hidden = d.total > 0; $("histCharts").hidden = d.total === 0;
  if (!d.total) { empty.textContent = "Nenhuma amostra neste período. Inicie a coleta ou escolha outro intervalo."; return; }
  const samples = d.points.map((p) => ({ ts: p[0], in_bps: p[1], out_bps: p[2], cpu: p[3] }));
  const deltas = samples.slice(1).map((x, i) => x.ts - samples[i].ts).sort((a, b) => a - b);
  const gap = Math.max(3 * (deltas[Math.floor(deltas.length / 2)] || 1), 3);
  const th = (st && st.thresholds) || { threshold_in_bps: 0, threshold_out_bps: 0, threshold_cpu: 0 };
  paint(hTraf, hCpu, samples, gap, th);
}

/* ---------- eventos ---------- */
const KINDS = { alert: "Alerta", recovered: "Normalizado", email_ok: "E-mail enviado", email_error: "Falha de e-mail", snmp_error: "Falha SNMP",
  recovered_comm: "SNMP restabelecido", counter_reset: "Contador reiniciado", device_reboot: "Roteador reiniciou", iface_changed: "Interface alterada", cpu_unsupported: "CPU indisponível" };
async function loadEvents() {
  const ev = await api("/api/events?limit=100"), body = $("evBody"); body.innerHTML = "";
  $("evEmpty").hidden = ev.length > 0;
  for (const e of ev) {
    const tr = document.createElement("tr");
    for (const t of [fmtFull(e.ts), KINDS[e.kind] || e.kind, e.message]) { const td = document.createElement("td"); td.textContent = t; tr.append(td); }
    tr.children[1].className = e.level; body.append(tr);
  }
}
$("evReload").onclick = loadEvents;

/* ---------- configurações ---------- */
const UNITS = [["bps", 1], ["kbps", 1e3], ["Mbps", 1e6], ["Gbps", 1e9]];
for (const id of ["uIn", "uOut"]) for (const [n, m] of UNITS) $(id).add(new Option(n, m));
function setBps(inp, unit, bps) {
  let u = 1; for (const [, m] of UNITS) if (bps >= m && bps % m === 0) u = m;
  if (bps === 0) u = 1e6;
  $(unit).value = u; $(inp).value = bps / u;
}
const getBps = (inp, unit) => (parseFloat($(inp).value) || 0) * parseFloat($(unit).value);
function toggleV3() { const v3 = $("sVer").value === "3"; document.querySelectorAll(".v3").forEach((e) => e.hidden = !v3); document.querySelectorAll(".v2").forEach((e) => e.hidden = v3); }
$("sVer").onchange = toggleV3;
async function loadConfig() {
  const c = await api("/api/config");
  $("aPopup").checked = c.alerts.popup; $("aEmail").checked = c.alerts.email;
  setBps("tIn", "uIn", c.alerts.threshold_in_bps); setBps("tOut", "uOut", c.alerts.threshold_out_bps);
  $("tCpu").value = c.alerts.threshold_cpu; $("cool").value = c.alerts.cooldown_s; $("interval").value = c.collect_interval;
  $("eTo").value = c.email.to; $("eHost").value = c.email.smtp_host; $("ePort").value = c.email.smtp_port; $("eSec").value = c.email.security;
  $("eUser").value = c.email.smtp_user; $("ePass").value = c.email.smtp_password; $("eFrom").value = c.email.from;
  const s = c.snmp;
  $("sHost").value = s.host; $("sPort").value = s.port; $("sVer").value = s.version; $("sIf").value = s.interface; $("sComm").value = s.community;
  $("sUser").value = s.v3_user; $("sAuth").value = s.v3_auth_protocol; $("sAuthPw").value = s.v3_auth_password;
  $("sPriv").value = s.v3_priv_protocol; $("sPrivPw").value = s.v3_priv_password; $("sTo").value = s.timeout; $("sRe").value = s.retries;
  $("sim").checked = c.simulate; toggleV3();
}
function formData() {
  return {
    collect_interval: $("interval").value, simulate: $("sim").checked,
    alerts: { popup: $("aPopup").checked, email: $("aEmail").checked, threshold_in_bps: getBps("tIn", "uIn"),
      threshold_out_bps: getBps("tOut", "uOut"), threshold_cpu: $("tCpu").value || 0, cooldown_s: $("cool").value || 0 },
    email: { to: $("eTo").value, smtp_host: $("eHost").value, smtp_port: $("ePort").value, security: $("eSec").value,
      smtp_user: $("eUser").value, smtp_password: $("ePass").value, from: $("eFrom").value },
    snmp: { host: $("sHost").value, port: $("sPort").value, version: $("sVer").value, interface: $("sIf").value, community: $("sComm").value,
      v3_user: $("sUser").value, v3_auth_protocol: $("sAuth").value, v3_auth_password: $("sAuthPw").value,
      v3_priv_protocol: $("sPriv").value, v3_priv_password: $("sPrivPw").value, timeout: $("sTo").value, retries: $("sRe").value },
  };
}
function res(el, ok, msg) { el.textContent = msg; el.className = "res " + (ok ? "ok" : "err"); }
$("cfgForm").onsubmit = async (e) => {
  e.preventDefault();
  const r = await api("/api/config", { body: JSON.stringify(formData()) }), box = $("cfgErrors");
  if (r.errors) { box.innerHTML = ""; r.errors.forEach((m) => { const li = document.createElement("li"); li.textContent = m; box.append(li); }); box.hidden = false; res($("saveRes"), false, "Não salvo."); return; }
  box.hidden = true; res($("saveRes"), true, "Configurações salvas. Valem a partir da próxima coleta."); loadConfig();
};
async function saveFirst() { const r = await api("/api/config", { body: JSON.stringify(formData()) }); return !r.errors || (res($("saveRes"), false, r.errors.join(" ")), false); }
$("bTestSnmp").onclick = async () => { if (!(await saveFirst())) return; res($("snmpRes"), true, "Testando…"); const r = await api("/api/test_snmp", { body: "{}" }); res($("snmpRes"), r.ok, r.message); };
$("bTestMail").onclick = async () => { if (!(await saveFirst())) return; res($("mailRes"), true, "Enviando…"); const r = await api("/api/test_email", { body: "{}" }); res($("mailRes"), r.ok, r.message); };
$("bIfs").onclick = async () => {
  if (!(await saveFirst())) return; res($("snmpRes"), true, "Consultando interfaces…");
  const r = await api("/api/interfaces");
  if (!r.ok) return res($("snmpRes"), false, r.message);
  $("ifList").innerHTML = ""; r.interfaces.forEach((n) => $("ifList").append(new Option(n)));
  res($("snmpRes"), true, "Interfaces encontradas: " + r.interfaces.join(", "));
};
loadConfig();
