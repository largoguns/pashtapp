/* PashtAPP · interacción del cliente (sin dependencias salvo htmx y Chart.js). */
(() => {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // ---------------------------------------------------------------- Service Worker
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
  }

  // ---------------------------------------------------------------- Bottom-sheet
  const sheet = () => $("#sheet");
  function openSheet() {
    const s = sheet();
    if (!s) return;
    s.classList.add("open");
    $("#sheet-backdrop")?.classList.add("open");
    document.body.style.overflow = "hidden";
    setTimeout(() => $("#quick-amount")?.focus(), 60);
  }
  function closeSheet() {
    sheet()?.classList.remove("open");
    $("#sheet-backdrop")?.classList.remove("open");
    document.body.style.overflow = "";
  }
  function resetQuickForm() {
    const f = $("#quick-form");
    if (!f) return;
    const date = $("#quick-date")?.value;
    f.reset();
    if (date) $("#quick-date").value = date;
    toggleSplit();
  }
  function toggleSplit() {
    const split = $('#quick-form input[name="mode"][value="split"]');
    $("#split-options")?.classList.toggle("hidden", !split?.checked);
    $("#split-options")?.classList.toggle("flex", !!split?.checked);
  }

  // ---------------------------------------------------------------- Modal
  function closeModal() {
    const m = $("#modal");
    if (m) m.innerHTML = "";
  }

  document.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("[data-sheet-open]")) { e.preventDefault(); openSheet(); return; }
    if (t.closest("[data-sheet-close]")) { e.preventDefault(); closeSheet(); return; }
    if (t.closest("[data-modal-close]")) { e.preventDefault(); closeModal(); return; }
    const fill = t.closest("[data-fill]");
    if (fill) {
      const input = $(fill.dataset.fill);
      if (input) {
        input.value = fill.dataset.value.replace(".", ",");
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.form?.requestSubmit();
      }
      return;
    }
    const copy = t.closest("[data-copy]");
    if (copy) {
      const src = $(copy.dataset.copy);
      navigator.clipboard?.writeText(src.value).then(() => (copy.textContent = "¡Copiado!"));
    }
  });

  document.addEventListener("change", (e) => {
    if (e.target.matches('#quick-form input[name="mode"]')) toggleSplit();
    // Las categorías marcadas como fijas proponen "Gasto fijo" automáticamente.
    if (e.target.matches('#quick-form input[name="category_id"]')) {
      const fixed = $('#quick-form input[name="is_fixed"]');
      if (fixed) fixed.checked = e.target.dataset.fixed === "1";
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeSheet(); closeModal(); }
    // Atajo de escritorio: "n" abre el formulario de nuevo movimiento.
    if (e.key === "n" && !e.metaKey && !e.ctrlKey && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) {
      if (sheet()) { e.preventDefault(); openSheet(); }
    }
  });

  // ---------------------------------------------------------------- Selector de mes/año
  // La capa se centra bajo el selector; si así se sale de la ventana, se desplaza hasta caber.
  function keepInViewport(panel, gap = 8) {
    panel.style.marginLeft = "0px";
    const r = panel.getBoundingClientRect();
    let shift = 0;
    if (r.right > window.innerWidth - gap) shift = window.innerWidth - gap - r.right;
    if (r.left + shift < gap) shift = gap - r.left;
    panel.style.marginLeft = `${shift}px`;
  }
  window.addEventListener("resize", () => $$("[data-picker-panel]:not([hidden])").forEach((p) => keepInViewport(p)));

  function closePickers(except) {
    $$("[data-picker-panel]").forEach((p) => {
      if (p === except) return;
      p.hidden = true;
      p.closest("[data-picker]")?.querySelector("[data-picker-toggle]")?.setAttribute("aria-expanded", "false");
    });
  }
  document.addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-picker-toggle]");
    if (toggle) {
      const panel = toggle.closest("[data-picker]").querySelector("[data-picker-panel]");
      closePickers(panel);
      panel.hidden = !panel.hidden;
      toggle.setAttribute("aria-expanded", String(!panel.hidden));
      if (!panel.hidden) keepInViewport(panel);
      if (!panel.hidden) panel.querySelector("[aria-selected=true], a")?.focus();
      return;
    }
    const yearTab = e.target.closest("[data-picker-year]");
    if (yearTab) {
      const panel = yearTab.closest("[data-picker-panel]");
      const y = yearTab.dataset.pickerYear;
      $$("[data-picker-year]", panel).forEach((b) => {
        const on = b === yearTab;
        b.setAttribute("aria-selected", String(on));
        b.classList.toggle("bg-slate-700", on);
        b.classList.toggle("text-white", on);
        b.classList.toggle("text-slate-400", !on);
      });
      $$("[data-picker-months]", panel).forEach((g) => (g.hidden = g.dataset.pickerMonths !== y));
      return;
    }
    if (!e.target.closest("[data-picker-panel]")) closePickers();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePickers(); });

  // ---------------------------------------------------------------- HTMX hooks
  document.addEventListener("htmx:afterRequest", (e) => {
    const elt = e.detail.elt;
    if (!e.detail.successful) return;
    if (elt.id === "quick-form") { resetQuickForm(); closeSheet(); }
    if (elt.closest?.("[data-close-on-success]") || elt.matches?.("[data-modal] [hx-delete]")) closeModal();
  });

  document.addEventListener("htmx:responseError", (e) => {
    let msg = "Error " + e.detail.xhr.status;
    try { msg = JSON.parse(e.detail.xhr.responseText).detail || msg; } catch (_) {}
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join(" · ");
    showToast(msg);
  });
  document.addEventListener("htmx:sendError", () => showToast("Sin conexión con el servidor"));

  document.addEventListener("htmx:afterSettle", () => {
    autoHideToast();
    applyFilters();
    renderDonut();
  });

  // ---------------------------------------------------------------- Toast
  let toastTimer;
  function autoHideToast() {
    const t = $("#toast [data-autohide]");
    if (!t) return;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.remove(), 2600);
  }
  function showToast(text) {
    const box = $("#toast");
    if (!box) return;
    box.innerHTML = "";
    const div = document.createElement("div");
    div.dataset.autohide = "";
    div.className = "pointer-events-auto rounded-xl bg-rose-500 px-4 py-2 text-sm font-medium text-white shadow-lg";
    div.textContent = text;
    box.appendChild(div);
    autoHideToast();
  }

  // ---------------------------------------------------------------- Filtros tabla variables
  const filters = { q: "", cat: "", status: "" };
  document.addEventListener("input", (e) => {
    const f = e.target.dataset?.filter;
    if (!f) return;
    filters[f] = e.target.value.trim().toLowerCase();
    applyFilters();
  });
  function applyFilters() {
    const table = $("#variable-table [data-filterable]");
    if (!table) return;
    let shown = 0;
    const rows = $$("tr[data-search]", table);
    rows.forEach((tr) => {
      const ok = (!filters.q || tr.dataset.search.includes(filters.q))
        && (!filters.cat || tr.dataset.cat === filters.cat)
        && (!filters.status || tr.dataset.status === filters.status);
      tr.classList.toggle("hidden", !ok);
      if (ok) shown++;
    });
    $("tr[data-nomatch]", table)?.classList.toggle("hidden", !(rows.length && !shown));
  }

  // ---------------------------------------------------------------- Gráficos
  const eur = (v) => (v ?? 0).toLocaleString("es-ES", { style: "currency", currency: "EUR" });
  // Paleta categórica validada (modo oscuro), orden fijo: el color sigue al año, no a su posición.
  const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
  const INK = { primary: "#f1f5f9", secondary: "#94a3b8", grid: "rgba(148,163,184,.12)" };
  let donutChart, utilityChart, utilityMetric = "amount";

  function readJSON(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (_) { return null; }
  }

  function chartDefaults() {
    if (!window.Chart) return false;
    Chart.defaults.color = INK.secondary;
    Chart.defaults.font.family = "Inter, system-ui, sans-serif";
    Chart.defaults.borderColor = INK.grid;
    return true;
  }

  function renderDonut() {
    const canvas = $("#donut");
    const data = readJSON("donut-data");
    if (!canvas || !data || !chartDefaults()) return;
    const total = data.values.reduce((a, b) => a + b, 0);
    const cfg = {
      labels: data.labels,
      datasets: [{ data: data.values, backgroundColor: data.colors, borderColor: "#0f172a", borderWidth: 2, hoverOffset: 6 }],
    };
    if (donutChart) { donutChart.data = cfg; donutChart.update(); }
    else {
      donutChart = new Chart(canvas, {
        type: "doughnut",
        data: cfg,
        options: {
          cutout: "68%", maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: (c) => ` ${c.label}: ${eur(c.parsed)} (${total ? Math.round((c.parsed / total) * 100) : 0}%)` } },
          },
        },
      });
    }
    const totalEl = $("#donut-total");
    if (totalEl) totalEl.textContent = eur(total);
    const legend = $("#donut-legend");
    if (legend) {
      legend.innerHTML = "";
      data.labels.forEach((label, i) => {
        const li = document.createElement("li");
        li.className = "flex items-center justify-between gap-2";
        li.innerHTML = `<span class="flex min-w-0 items-center gap-2"><span class="h-2.5 w-2.5 shrink-0 rounded-full"></span><span class="truncate"></span></span><span class="tabular-nums text-slate-300"></span>`;
        li.querySelector(".rounded-full").style.background = data.colors[i];
        li.querySelector(".truncate").textContent = label;
        li.lastElementChild.textContent = `${eur(data.values[i])} · ${total ? Math.round((data.values[i] / total) * 100) : 0}%`;
        legend.appendChild(li);
      });
      if (!data.labels.length) legend.innerHTML = '<li class="text-center text-slate-500">Sin gastos este mes</li>';
    }
  }

  function renderUtility() {
    const canvas = $("#utility-chart");
    const data = readJSON("utility-data");
    if (!canvas || !data || !chartDefaults()) return;
    const years = data.years.slice(-SERIES.length);
    const unit = utilityMetric === "kwh" ? "kWh" : "€";
    const datasets = years.map((y, i) => ({
      label: String(y),
      data: data.by_year[y][utilityMetric],
      borderColor: SERIES[i],
      backgroundColor: SERIES[i],
      borderWidth: 2,
      pointRadius: 3,
      pointHoverRadius: 6,
      tension: 0.25,
      spanGaps: true,
    }));
    const options = {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        y: { beginAtZero: true, ticks: { callback: (v) => `${v} ${unit}` } },
        x: { grid: { display: false } },
      },
      plugins: {
        legend: { display: years.length > 1, position: "bottom", labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true } },
        tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${c.parsed.y == null ? "—" : (unit === "€" ? eur(c.parsed.y) : `${c.parsed.y} kWh`)}` } },
      },
    };
    if (utilityChart) utilityChart.destroy();
    utilityChart = new Chart(canvas, { type: "line", data: { labels: data.months, datasets }, options });
  }

  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-utility-metric]");
    if (!b) return;
    utilityMetric = b.dataset.utilityMetric;
    $$("[data-utility-metric]").forEach((x) => {
      const on = x === b;
      x.classList.toggle("bg-slate-700", on);
      x.classList.toggle("text-slate-400", !on);
    });
    renderUtility();
  });

  function renderSavings() {
    const canvas = $("#savings-chart");
    const data = readJSON("savings-data");
    if (!canvas || !data || !chartDefaults()) return;
    new Chart(canvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: [
          { label: "Saldo", data: data.real, borderColor: SERIES[2], backgroundColor: SERIES[2], borderWidth: 2,
            pointRadius: 3, pointHoverRadius: 6, tension: 0.2 },
          { label: "Proyección", data: data.projection, borderColor: SERIES[2], backgroundColor: SERIES[2],
            borderWidth: 2, borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 5, tension: 0.2 },
        ],
      },
      options: {
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: { y: { beginAtZero: true, ticks: { callback: (v) => eur(v) } }, x: { grid: { display: false } } },
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true } },
          tooltip: { filter: (i) => i.parsed.y != null, callbacks: { label: (c) => ` ${c.dataset.label}: ${eur(c.parsed.y)}` } },
        },
      },
    });
  }

  // Chart.js se carga con defer: esperar a que todo esté listo.
  window.addEventListener("load", () => { renderDonut(); renderUtility(); renderSavings(); autoHideToast(); });
})();
