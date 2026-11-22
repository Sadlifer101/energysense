/* ==========================================================================
   EnergySense — dashboard client
   Vanilla JS. Talks only to the Flask API on this origin. No fixtures.
   ========================================================================== */

(function () {
  "use strict";

  /* ---------------------------------------------------------------- config */

  var API_DASHBOARD = "/api/dashboard";
  var API_SPIKE = "/api/simulate-spike";
  var DEFAULT_CITY = "Bengaluru";

  var HOURS_FALLBACK = [];
  for (var h = 0; h < 24; h++) {
    HOURS_FALLBACK.push((h < 10 ? "0" : "") + h + ":00");
  }

  var COLOR = {
    stone: "rgba(239, 233, 219, 0.52)",
    parchment: "#efe9db",
    bronze: "#d3a067",
    bronzeSoft: "rgba(211, 160, 103, 0.9)",
    olive: "#8d9578",
    rust: "#a9654b",
    rule: "rgba(239, 233, 219, 0.075)",
    tick: "rgba(239, 233, 219, 0.36)"
  };

  var CLASS_STYLE = {
    protected: { label: "Protected", chip: "chip--olive", color: "#8d9578" },
    shiftable: { label: "Shiftable", chip: "chip--bronze", color: "#c99b68" },
    recommendation: {
      label: "Recommendation only",
      chip: "chip--clay",
      color: "#a9654b"
    }
  };

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  /* ------------------------------------------------------------------ utils */

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }
  function on(el, type, fn, opts) {
    if (el) el.addEventListener(type, fn, opts);
  }
  function esc(value) {
    return String(value === null || value === undefined ? "" : value).replace(
      /[&<>"']/g,
      function (ch) {
        return {
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;"
        }[ch];
      }
    );
  }
  function isNum(value) {
    return typeof value === "number" && isFinite(value);
  }
  function toNum(value) {
    if (typeof value === "number") return isFinite(value) ? value : null;
    if (typeof value === "string" && value.trim() !== "") {
      var n = Number(value);
      return isFinite(n) ? n : null;
    }
    return null;
  }
  function clamp(n, lo, hi) {
    return Math.min(hi, Math.max(lo, n));
  }

  var nfCache = {};
  function nf(value, dp) {
    if (!isNum(value)) return null;
    var key = "d" + dp;
    if (!nfCache[key]) {
      nfCache[key] = new Intl.NumberFormat("en-IN", {
        minimumFractionDigits: dp,
        maximumFractionDigits: dp
      });
    }
    return nfCache[key].format(value);
  }

  function fmtMoney(value) {
    if (!isNum(value)) return null;
    var abs = Math.abs(value);
    if (abs >= 100000) return nf(value, 0);
    return nf(value, 2);
  }
  function fmtKwh(value, dp) {
    if (!isNum(value)) return "—";
    return nf(value, dp === undefined ? 2 : dp);
  }
  function fmtPct(value, dp) {
    if (!isNum(value)) return "—";
    return nf(value, dp === undefined ? 1 : dp);
  }
  function fmtInt(value) {
    if (!isNum(value)) return "—";
    return nf(value, 0);
  }

  function sumSeries(arr) {
    if (!Array.isArray(arr)) return null;
    var total = 0;
    var seen = false;
    for (var i = 0; i < arr.length; i++) {
      if (isNum(arr[i])) {
        total += arr[i];
        seen = true;
      }
    }
    return seen ? total : null;
  }

  function mean(arr) {
    var total = sumSeries(arr);
    if (total === null) return null;
    return total / arr.length;
  }

  function setText(name, text) {
    $$('[data-bind="' + name + '"]').forEach(function (el) {
      el.textContent = text;
    });
  }

  function plural(n, one, many) {
    return n + " " + (n === 1 ? one : many || one + "s");
  }

  /* ------------------------------------------------------------------ state */

  var state = {
    city: DEFAULT_CITY,
    requestedSpike: false,
    spike: false,
    phase: "loading", // loading | ready | error
    busy: false,
    busyReason: null,
    data: null,
    error: null,
    inspectIndex: null,
    seq: 0,
    loadedOnce: false
  };

  var inflight = null;

  /* -------------------------------------------------------------------- api */

  function HttpError(message, status) {
    this.name = "HttpError";
    this.message = message;
    this.status = status;
  }
  HttpError.prototype = Object.create(Error.prototype);

  function abortInflight() {
    if (inflight) {
      try {
        inflight.abort();
      } catch (e) {
        /* ignore */
      }
      inflight = null;
    }
  }

  function readJson(response) {
    return response.text().then(function (text) {
      if (!text) throw new HttpError("The API returned an empty response.", response.status);
      try {
        return JSON.parse(text);
      } catch (e) {
        throw new HttpError(
          "The API returned a response that is not valid JSON.",
          response.status
        );
      }
    });
  }

  function request(url, options, seq) {
    abortInflight();
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    inflight = controller;
    var opts = Object.assign({ cache: "no-store", credentials: "same-origin" }, options || {});
    if (controller) opts.signal = controller.signal;

    return fetch(url, opts).then(function (response) {
      if (seq !== state.seq) throw new HttpError("Superseded", 0);
      if (!response.ok) {
        throw new HttpError(
          "The API responded with HTTP " + response.status + ".",
          response.status
        );
      }
      return readJson(response);
    });
  }

  function looksLikeDashboard(payload) {
    return (
      !!payload &&
      typeof payload === "object" &&
      !Array.isArray(payload) &&
      (Array.isArray(payload.hours) ||
        Array.isArray(payload.baseline_kwh) ||
        (payload.savings && typeof payload.savings === "object") ||
        (payload.model && typeof payload.model === "object"))
    );
  }

  function fetchDashboard(city, spike) {
    var seq = ++state.seq;
    var url =
      API_DASHBOARD +
      "?city=" +
      encodeURIComponent(city) +
      "&spike=" +
      (spike ? "true" : "false");
    return request(url, { headers: { Accept: "application/json" } }, seq);
  }

  function postSpike(city, enabled) {
    var seq = ++state.seq;
    return request(
      API_SPIKE,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ city: city, enabled: !!enabled })
      },
      seq
    );
  }

  /* ------------------------------------------------------------ normalising */

  function numSeries(value) {
    if (!Array.isArray(value) || value.length !== 24) return null;
    var out = [];
    for (var i = 0; i < 24; i++) {
      var n = toNum(value[i]);
      if (n === null) return null;
      out.push(n);
    }
    return out;
  }

  function strList(value) {
    if (!Array.isArray(value)) return [];
    return value
      .map(function (item) {
        if (typeof item === "string") return item;
        if (item === null || item === undefined) return "";
        if (typeof item === "number") return String(item);
        return String(item);
      })
      .filter(function (s) {
        return s.trim() !== "";
      });
  }

  function normalise(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new HttpError("The API returned an unexpected payload shape.", -1);
    }

    var meta = payload.meta && typeof payload.meta === "object" ? payload.meta : {};
    var model = payload.model && typeof payload.model === "object" ? payload.model : {};
    var savings = payload.savings && typeof payload.savings === "object" ? payload.savings : {};

    var hours =
      Array.isArray(payload.hours) && payload.hours.length === 24
        ? payload.hours.map(function (h, i) {
            return typeof h === "string" && h.trim() ? h : HOURS_FALLBACK[i];
          })
        : HOURS_FALLBACK.slice();

    var series = {
      baseline: numSeries(payload.baseline_kwh),
      optimized: numSeries(payload.optimized_kwh),
      tariff: numSeries(payload.tariff_inr_per_kwh),
      carbon: numSeries(payload.carbon_kg_per_kwh)
    };

    var actuators = Array.isArray(payload.actuators) ? payload.actuators.slice() : [];
    var logs = Array.isArray(payload.agent_logs) ? payload.agent_logs.slice() : [];
    var appliances = Array.isArray(payload.appliance_estimates)
      ? payload.appliance_estimates.slice()
      : [];

    return {
      meta: {
        city: typeof meta.city === "string" && meta.city ? meta.city : state.city,
        scenario: typeof meta.scenario === "string" ? meta.scenario : "normal",
        dataset: typeof meta.dataset === "string" ? meta.dataset : "synthetic",
        offline: meta.offline !== false,
        tariffType:
          typeof meta.tariff_type === "string" ? meta.tariff_type : "illustrative"
      },
      model: {
        algorithm:
          typeof model.algorithm === "string" && model.algorithm
            ? model.algorithm
            : null,
        r2: toNum(model.r2),
        mae: toNum(model.mae_kwh),
        trainRows: toNum(model.train_rows),
        testRows: toNum(model.test_rows)
      },
      hours: hours,
      series: series,
      savings: {
        dailyInr: toNum(savings.daily_inr),
        monthlyInr: toNum(savings.monthly_inr),
        monthlyCo2: toNum(savings.monthly_co2_kg),
        peakShiftPct: toNum(savings.peak_shift_pct),
        payback: toNum(savings.plug_payback_months),
        dailyBaseCost: toNum(savings.daily_baseline_cost_inr),
        dailyOptCost: toNum(savings.daily_optimized_cost_inr)
      },
      actuators: actuators,
      logs: logs,
      appliances: appliances,
      assumptions: strList(payload.assumptions)
    };
  }

  /* ------------------------------------------------------- tariff zoning */

  function classifyTariff(tariff) {
    if (!Array.isArray(tariff)) return null;
    var vals = tariff.filter(isNum);
    if (!vals.length) return null;
    var min = Math.min.apply(null, vals);
    var max = Math.max.apply(null, vals);
    var span = max - min;

    var unique = [];
    vals.forEach(function (v) {
      if (unique.indexOf(v) === -1) unique.push(v);
    });
    unique.sort(function (a, b) {
      return a - b;
    });

    var levels;

    if (unique.length >= 2 && unique.length <= 6) {
      /* A stepped slab tariff — classify by slab rank, so the top slab alone
         is "peak" instead of every hour above an arbitrary quantile. */
      levels = tariff.map(function (v) {
        var rank = unique.indexOf(v);
        if (rank === -1) return "low";
        var t = rank / (unique.length - 1);
        return t <= 0.34 ? "low" : t <= 0.67 ? "shoulder" : "peak";
      });
    } else if (span <= 1e-9) {
      levels = tariff.map(function () {
        return "low";
      });
    } else {
      levels = tariff.map(function (v) {
        if (!isNum(v)) return "low";
        var t = (v - min) / span;
        return t <= 0.34 ? "low" : t <= 0.67 ? "shoulder" : "peak";
      });
    }

    var peakIdx = [];
    levels.forEach(function (level, i) {
      if (level === "peak") peakIdx.push(i);
    });
    return {
      min: min,
      max: max,
      span: span,
      slabs: unique.length,
      levels: levels,
      peakIdx: peakIdx
    };
  }

  function hourLabel(hours, index) {
    return hours[index] || HOURS_FALLBACK[index] || "—";
  }

  function rangeLabel(hours, indices) {
    if (!indices || !indices.length) return null;
    if (indices.length === 1) return hourLabel(hours, indices[0]);
    var first = indices[0];
    var last = indices[indices.length - 1];
    var endMinutes = (last + 1) * 60;
    var endHour = Math.floor(endMinutes / 60) % 24;
    var end = (endHour < 10 ? "0" : "") + endHour + ":00";
    return hourLabel(hours, first) + "–" + end;
  }

  /* ============================================================== rendering */

  function renderAll() {
    var d = state.data;
    renderScenario();
    renderStatusChrome(d);
    if (!d) {
      renderEmptyEverything();
      return;
    }
    renderKpis(d);
    renderChart(d);
    renderTable(d);
    renderLogs(d);
    renderAppliances(d);
    renderModel(d);
    renderAdvice(d);
    renderFooterMeta(d);
    setInspect(state.inspectIndex);
  }

  function renderEmptyEverything() {
    renderKpis(null);
    renderChart(null);
    renderTable(null);
    renderLogs(null);
    renderAppliances(null);
    renderModel(null);
    renderAdvice(null);
  }

  /* ---------------------------------------------------------------- motion
     One adapter layer over static/motion.js. Every animated state change in
     this file goes through these helpers, so the same tokens govern tabs,
     content swaps, counted figures and disclosures — and everything falls
     back to an instant change when Motion is absent or motion is reduced. */

  var motion = window.Motion || null;

  var FIGURES = {
    money: function (v) { return fmtMoney(v); },
    pct: function (v) { return fmtPct(v); },
    dp1: function (v) { return nf(v, 1); }
  };

  function readFigure(text) {
    var cleaned = String(text == null ? "" : text).replace(/[^0-9.\-]/g, "");
    if (!cleaned || cleaned === "-" || cleaned === ".") return null;
    var n = Number(cleaned);
    return isFinite(n) ? n : null;
  }

  function swapText(name, value, dir, opts) {
    var el = $('[data-bind="' + name + '"]');
    if (!el) return;
    var next = value == null ? "" : String(value);
    if (el.textContent === next) return;
    if (motion) motion.text(el, next, Object.assign({ dir: dir || 0 }, opts || {}));
    else el.textContent = next;
  }

  function swapElement(el, mutate, dir, opts) {
    if (!el) return;
    if (motion) motion.swap(el, mutate, Object.assign({ dir: dir || 0 }, opts || {}));
    else mutate();
  }

  /* Shared-layout marker for the city tabs: one element, repositioned. */
  function syncSegPill(snap) {
    var pill = $(".seg__pill");
    if (!pill) return;
    var active = $(".seg__btn.is-on");
    if (!active) return;
    if (!motion) {
      pill.style.display = "none";
      return;
    }
    motion.pill(pill, active, { snap: !!snap });
  }

  function initSegPill() {
    var seg = $(".seg");
    if (!seg) return;
    syncSegPill(true);
    window.addEventListener("resize", function () {
      syncSegPill(false);
    });
    if (document.fonts && document.fonts.ready && document.fonts.ready.then) {
      document.fonts.ready.then(function () {
        syncSegPill(true);
      });
    }
  }

  /* The progress hairline is interpolated instead of re-keyframed, so it
     settles into its completion instead of snapping. */
  var progressEl = null;
  var progressRaf = 0;

  function progressStart() {
    progressEl = progressEl || $(".progress");
    if (!progressEl) return;
    if (progressRaf) cancelAnimationFrame(progressRaf);
    progressEl.style.transition = "opacity 0.3s var(--ease)";
    progressEl.style.opacity = "1";

    if (motion && motion.reduced()) {
      progressRaf = 0;
      progressEl.style.width = "84%";
      return;
    }

    var start = 0;
    var from = 4;
    var to = 84;
    var dur = 1600;
    var step = function (t) {
      if (!start) start = t;
      var p = Math.min(1, (t - start) / dur);
      var eased = 1 - Math.pow(1 - p, 1.8);
      progressEl.style.width = (from + (to - from) * eased).toFixed(2) + "%";
      if (p < 1) progressRaf = requestAnimationFrame(step);
      else progressRaf = 0;
    };
    progressEl.style.width = from + "%";
    progressRaf = requestAnimationFrame(step);
  }

  function progressDone() {
    progressEl = progressEl || $(".progress");
    if (!progressEl) return;
    if (progressRaf) cancelAnimationFrame(progressRaf);
    progressRaf = 0;
    progressEl.style.transition = "";
    progressEl.style.width = "100%";
    progressEl.style.opacity = "0";
    window.setTimeout(function () {
      if (progressRaf) return;
      if (
        document.body.classList.contains("is-loading") ||
        document.body.classList.contains("is-updating")
      ) {
        return;
      }
      progressEl.style.width = "0%";
    }, 460);
  }

  /* ------------------------------------------------------------ chrome */

  function renderScenario() {
    var spike = state.spike;
    document.body.setAttribute("data-scenario", spike ? "spike" : "normal");

    /* Spike applied reads as forward motion, cleared as backward. */
    var dir =
      state.scenarioShown === undefined || state.scenarioShown === spike
        ? 0
        : spike
          ? 1
          : -1;
    state.scenarioShown = spike;
    state.scenarioDir = dir;

    swapText(
      "scenario-title",
      spike ? "7 PM peak spike simulated" : "Normal scenario",
      dir
    );
    swapText(
      "scenario-text",
      spike
        ? "A 18:00–22:00 demand spike is applied to the synthetic load profile. Shiftable appliances are held back and re-scheduled outside the peak tariff window."
        : "The agent is scheduling shiftable load around the illustrative time-of-day tariff. No demand spike is being simulated.",
      dir
    );
    setText(
      "scenario-chip",
      spike ? "Peak spike · simulated" : "Normal scenario"
    );

    /* The chip is short enough that an exit phase would only read as lag:
       its new label settles in from the same direction instead. */
    var chipEl = $('[data-bind="scenario-chip"]');
    if (motion && chipEl) {
      if (state.chipShown !== undefined && state.chipShown !== chipEl.textContent) {
        motion.appear(chipEl, dir, { distance: 5 });
      }
      state.chipShown = chipEl.textContent;
    }

    var spikeBtn = $("#btn-spike");
    var resetBtn = $("#btn-reset");
    if (spikeBtn) {
      var active = spike && !state.busy;
      spikeBtn.disabled = state.busy || active;
      spikeBtn.setAttribute("aria-pressed", spike ? "true" : "false");
      spikeBtn.setAttribute("aria-busy", state.busy ? "true" : "false");
      var label = spikeBtn.querySelector("span");
      if (label) {
        var labelText =
          state.busy && state.busyReason === "spike"
            ? "Applying spike…"
            : active
              ? "Peak spike active"
              : "Simulate 7 PM peak spike";
        if (labelText !== label.textContent) {
          if (motion) motion.text(label, labelText, { height: false, distance: 4, duration: 220 });
          else label.textContent = labelText;
        }
      }
    }
    if (resetBtn) {
      resetBtn.disabled = state.busy || !spike;
    }
    var retryBtn = $("#btn-retry");
    if (retryBtn) retryBtn.disabled = state.busy;
  }

  function renderStatusChrome(d) {
    swapText("city-name", (d && d.meta.city) || state.city, state.cityDir || 0);
    setText("date", formatDate(new Date()));

    var city = (d && d.meta.city) || state.city;
    $$(".seg__btn").forEach(function (btn) {
      var selected =
        String(btn.getAttribute("data-city") || "").toLowerCase() ===
        String(city).toLowerCase();
      btn.classList.toggle("is-on", selected);
      btn.setAttribute("aria-checked", selected ? "true" : "false");
      btn.setAttribute("tabindex", selected ? "0" : "-1");
      btn.disabled = state.busy;
    });

    var statusEl = $('[data-bind="offline-status"]');
    if (statusEl) {
      var span = statusEl.querySelector("span:last-child");
      var offline = d ? d.meta.offline : true;
      document.body.classList.toggle("is-offline", !offline);
      if (span) {
        span.textContent = state.phase === "error" ? "Data unavailable" : offline ? "Local · offline" : "Server data";
      }
    }

    if (d && d.model.algorithm) {
      setText("model-short", shortAlgorithm(d.model.algorithm));
    }

    /* Keep the shared marker aligned with whichever tab is actually
       selected in state — never with the one that was clicked. */
    syncSegPill(false);
  }

  function shortAlgorithm(name) {
    var s = String(name);
    if (/random\s*forest/i.test(s)) return "RandomForest";
    if (/gradient/i.test(s)) return "GradientBoost";
    if (/linear/i.test(s)) return "Linear";
    return s.length > 18 ? s.slice(0, 16) + "…" : s;
  }

  function formatDate(date) {
    try {
      return new Intl.DateTimeFormat("en-GB", {
        weekday: "long",
        day: "numeric",
        month: "long",
        year: "numeric"
      }).format(date);
    } catch (e) {
      return date.toDateString();
    }
  }

  /* --------------------------------------------------------------- KPIs */

  function kpiValue(parts) {
    return parts.join("");
  }

  function renderKpis(d) {
    var savingsEl = $('[data-bind="kpi-savings"]');
    var carbonEl = $('[data-bind="kpi-carbon"]');
    var peakEl = $('[data-bind="kpi-peak"]');
    var paybackEl = $('[data-bind="kpi-payback"]');

    function put(el, html, isNA) {
      if (!el) return;
      var prev = el.querySelector("[data-num]");
      var from = prev ? readFigure(prev.textContent) : null;
      el.innerHTML = html;
      el.classList.toggle("is-na", !!isNA);

      var next = el.querySelector("[data-num]");
      if (!next || !motion) return;
      var to = Number(next.getAttribute("data-to"));
      if (!isFinite(to)) return;
      var fmt = FIGURES[next.getAttribute("data-fmt")] || null;
      motion.count(next, to, fmt, {
        from: from === null ? to : from
      });
    }

    if (!d) {
      put(savingsEl, "<span>—</span>");
      put(carbonEl, "<span>—</span>");
      put(peakEl, "<span>—</span>");
      put(paybackEl, "<span>—</span>");
      ["kpi-savings-sub", "kpi-carbon-sub", "kpi-peak-sub", "kpi-payback-sub"].forEach(
        function (k) {
          setText(k, "No data");
        }
      );
      return;
    }

    var s = d.savings;

    /* 1 — monthly savings */
    if (isNum(s.monthlyInr)) {
      put(
        savingsEl,
        kpiValue([
          '<span class="cur" aria-hidden="true">₹</span>',
          "<span data-num data-fmt=\"money\" data-to=\"" + s.monthlyInr + "\">",
          esc(fmtMoney(s.monthlyInr)),
          "</span>"
        ])
      );
    } else {
      put(savingsEl, "<span>Not available</span>", true);
    }
    var savingsSub = [];
    if (isNum(s.dailyOptCost) && isNum(s.dailyBaseCost)) {
      savingsSub.push(
        "<b>₹" +
          esc(fmtMoney(s.dailyOptCost)) +
          "</b> vs <b>₹" +
          esc(fmtMoney(s.dailyBaseCost)) +
          "</b> per day"
      );
    } else if (isNum(s.dailyInr)) {
      savingsSub.push("<b>₹" + esc(fmtMoney(s.dailyInr)) + "</b> per day");
    }
    savingsSub.push(esc(d.meta.tariffType) + " tariff");
    var savingsSubEl = $('[data-bind="kpi-savings-sub"]');
    if (savingsSubEl) savingsSubEl.innerHTML = savingsSub.join(" · ");

    /* 2 — carbon */
    if (isNum(s.monthlyCo2)) {
      put(
        carbonEl,
        kpiValue([
          "<span data-num data-fmt=\"money\" data-to=\"" + s.monthlyCo2 + "\">",
          esc(fmtMoney(s.monthlyCo2)),
          '</span><span class="unit">kg CO₂</span>'
        ])
      );
      setText("kpi-carbon-sub", "at the returned grid carbon intensity");
    } else {
      put(carbonEl, "<span>Not available</span>", true);
      setText("kpi-carbon-sub", "The API returned no carbon figure.");
    }

    /* 3 — peak reduction */
    var tariffInfo = classifyTariff(d.series.tariff);
    var peakWindow =
      tariffInfo && tariffInfo.peakIdx.length
        ? rangeLabel(d.hours, tariffInfo.peakIdx)
        : null;

    if (isNum(s.peakShiftPct)) {
      put(
        peakEl,
        kpiValue([
          "<span data-num data-fmt=\"pct\" data-to=\"" + s.peakShiftPct + "\">",
          esc(fmtPct(s.peakShiftPct)),
          '</span><span class="unit">%</span>'
        ])
      );
      setText(
        "kpi-peak-sub",
        peakWindow
          ? "less energy in the " + peakWindow + " peak window"
          : "of baseline energy in the highest-tariff hours"
      );
    } else {
      put(peakEl, "<span>Not available</span>", true);
      setText("kpi-peak-sub", "The API returned no peak-shift figure.");
    }

    /* 4 — payback */
    if (isNum(s.payback)) {
      put(
        paybackEl,
        kpiValue([
          "<span data-num data-fmt=\"dp1\" data-to=\"" + s.payback + "\">",
          esc(nf(s.payback, 1)),
          '</span><span class="unit">months</span>'
        ])
      );
      setText(
        "kpi-payback-sub",
        isNum(s.dailyInr)
          ? "smart plug at ₹" + fmtMoney(s.dailyInr) + "/day saved"
          : "smart plug against the modelled daily saving"
      );
    } else {
      put(paybackEl, "<span>Not available</span>", true);
      setText(
        "kpi-payback-sub",
        "No payback estimate was returned for this run."
      );
    }
  }

  /* -------------------------------------------------------------- chart */

  var chart = null;

  function destroyChart() {
    if (chart) {
      chart.destroy();
      chart = null;
    }
    var tip = $("#chart-tip");
    if (tip) tip.hidden = true;
  }

  var zoneBandsPlugin = {
    id: "zoneBands",
    beforeDraw: function (c) {
      var opts = c.options.plugins && c.options.plugins.zoneBands;
      if (!opts || !c.chartArea || !c.scales.x) return;
      var x = c.scales.x;
      var area = c.chartArea;
      var step = x.getPixelForValue(1) - x.getPixelForValue(0);
      if (!isFinite(step) || step <= 0) return;

      var ctx = c.ctx;
      ctx.save();

      function paint(indices, color) {
        if (!indices || !indices.length) return;
        ctx.fillStyle = color;
        for (var i = 0; i < indices.length; i++) {
          var cx = x.getPixelForValue(indices[i]);
          var left = Math.max(area.left, cx - step / 2);
          var right = Math.min(area.right, cx + step / 2);
          if (right > left) {
            ctx.fillRect(left, area.top, right - left, area.bottom - area.top);
          }
        }
      }

      paint(opts.solar, "rgba(141, 149, 120, 0.055)");
      paint(opts.peak, "rgba(169, 101, 75, 0.065)");

      if (opts.ribbon && opts.ribbon.length === 24) {
        var ribbonY = area.top - 15;
        for (var j = 0; j < 24; j++) {
          var cxx = x.getPixelForValue(j);
          var l = Math.max(area.left, cxx - step / 2) + 0.75;
          var r = Math.min(area.right, cxx + step / 2) - 0.75;
          if (r <= l) continue;
          ctx.fillStyle = opts.ribbon[j];
          ctx.fillRect(l, ribbonY, r - l, 4);
        }
      }
      ctx.restore();
    }
  };

  var ribbonColor = {
    low: "rgba(141, 149, 120, 0.4)",
    shoulder: "rgba(177, 137, 92, 0.56)",
    peak: "rgba(169, 101, 75, 0.86)"
  };

  function fontStack() {
    return "'DM Sans', ui-sans-serif, system-ui, 'Segoe UI', sans-serif";
  }

  function renderChart(d) {
    var canvas = $("#energy-chart");
    var empty = $('[data-bind="chart-empty"]');
    if (!canvas) return;

    var hasBaseline = !!(d && d.series.baseline);
    var hasOptimized = !!(d && d.series.optimized);

    if (!hasBaseline && !hasOptimized) {
      destroyChart();
      canvas.style.display = "none";
      if (empty) {
        empty.hidden = false;
        setText(
          "chart-empty-msg",
          d
            ? "The API did not return a complete 24-hour series, so no forecast is drawn. The figures elsewhere on this page are still the values the API returned."
            : state.phase === "error"
              ? "No hourly series to draw — the request failed. The retry button above will re-request /api/dashboard."
              : "Waiting for the first response from /api/dashboard."
        );
      }
      return;
    }

    if (typeof window.Chart !== "function") {
      destroyChart();
      canvas.style.display = "none";
      if (empty) {
        empty.hidden = false;
        setText("chart-empty-msg", "The chart library failed to load from /static/vendor.");
      }
      return;
    }

    canvas.style.display = "";
    if (empty) empty.hidden = true;

    var tariffInfo = classifyTariff(d.series.tariff);
    var solarIdx = [];
    for (var i = 10; i < 16; i++) solarIdx.push(i);

    var ribbon = [];
    for (var k = 0; k < 24; k++) {
      var level = tariffInfo ? tariffInfo.levels[k] : "low";
      ribbon.push(ribbonColor[level] || ribbonColor.low);
    }

    var datasets = [];
    if (hasBaseline) {
      datasets.push({
        label: "Baseline",
        data: d.series.baseline,
        borderColor: "rgba(239, 233, 219, 0.5)",
        borderWidth: 1.6,
        borderDash: [5, 4],
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBackgroundColor: "#12140f",
        pointHoverBorderColor: "#efe9db",
        pointHoverBorderWidth: 1.5,
        tension: 0.35,
        fill: false,
        order: 3
      });
    }
    if (hasOptimized) {
      datasets.push({
        label: "Agent optimised",
        data: d.series.optimized,
        borderColor: COLOR.bronze,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4.5,
        pointHoverBackgroundColor: COLOR.bronze,
        pointHoverBorderColor: COLOR.parchment,
        pointHoverBorderWidth: 2,
        tension: 0.35,
        fill: true,
        backgroundColor: function (ctx) {
          var area = ctx.chart.chartArea;
          if (!area) return "rgba(211, 160, 103, 0.12)";
          var g = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
          g.addColorStop(0, "rgba(211, 160, 103, 0.27)");
          g.addColorStop(0.55, "rgba(211, 160, 103, 0.08)");
          g.addColorStop(1, "rgba(211, 160, 103, 0.005)");
          return g;
        },
        order: 2
      });
    }

    var data = { labels: d.hours, datasets: datasets };

    if (chart) {
      chart.data = data;
      chart.options.plugins.zoneBands = {
        peak: tariffInfo ? tariffInfo.peakIdx : [],
        solar: solarIdx,
        ribbon: ribbon
      };
      chart.options.animation = reduceMotion.matches
        ? false
        : { duration: 480, easing: "easeOutCubic" };
      chart.update();
      return;
    }

    window.Chart.defaults.font.family = fontStack();
    window.Chart.defaults.font.size = 10.5;
    window.Chart.register(zoneBandsPlugin);

    chart = new window.Chart(canvas.getContext("2d"), {
      type: "line",
      data: data,
      options: {
        responsive: true,
        maintainAspectRatio: false,
        devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2),
        animation: reduceMotion.matches
          ? false
          : { duration: 620, easing: "easeOutCubic" },
        transitions: reduceMotion.matches
          ? {}
          : { active: { animation: { duration: 160 } } },
        layout: { padding: { top: 22, right: 8, bottom: 0, left: 0 } },
        interaction: { mode: "index", intersect: false, axis: "x" },
        onHover: function (event, elements) {
          var index = elements && elements.length ? elements[0].index : null;
          if (index !== state.inspectIndex) setInspect(index, true);
        },
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false, external: externalTooltip },
          zoneBands: {
            peak: tariffInfo ? tariffInfo.peakIdx : [],
            solar: solarIdx,
            ribbon: ribbon
          }
        },
        scales: {
          x: {
            grid: { color: COLOR.rule, drawTicks: false, lineWidth: 1 },
            border: { display: false },
            ticks: {
              color: COLOR.tick,
              padding: 8,
              maxRotation: 0,
              autoSkip: false,
              font: { family: fontStack(), size: 10 },
              callback: function (value, index) {
                return index % 2 === 0 ? this.getLabelForValue(value) : null;
              }
            }
          },
          y: {
            beginAtZero: true,
            grace: "10%",
            grid: { color: COLOR.rule, drawTicks: false, lineWidth: 1 },
            border: { display: false },
            ticks: {
              color: COLOR.tick,
              padding: 8,
              maxTicksLimit: 5,
              font: { family: fontStack(), size: 10 },
              callback: function (value) {
                return nf(value, 1);
              }
            },
            title: {
              display: true,
              text: "kWh",
              color: COLOR.tick,
              font: { family: fontStack(), size: 9.5 },
              padding: { bottom: 6 }
            }
          }
        }
      }
    });
  }

  function externalTooltip(context) {
    var tip = $("#chart-tip");
    if (!tip) return;
    var tooltip = context.tooltip;
    if (!tooltip || tooltip.opacity === 0) {
      tip.hidden = true;
      return;
    }
    var points = tooltip.dataPoints || [];
    if (!points.length) {
      tip.hidden = true;
      return;
    }
    var index = points[0].dataIndex;
    var d = state.data;
    if (!d) {
      tip.hidden = true;
      return;
    }

    tip.innerHTML = tooltipHtml(d, index);
    tip.hidden = false;

    var c = context.chart;
    var wrap = c.canvas.parentElement;
    var wrapWidth = wrap.clientWidth;
    var tw = tip.offsetWidth;
    var th = tip.offsetHeight;
    var left = c.canvas.offsetLeft + tooltip.caretX - tw / 2;
    left = clamp(left, 6, Math.max(6, wrapWidth - tw - 6));
    var top = c.canvas.offsetTop + tooltip.caretY - th - 14;
    if (top < 2) top = c.canvas.offsetTop + tooltip.caretY + 18;
    tip.style.transform =
      "translate(" + Math.round(left) + "px," + Math.round(top) + "px)";
  }

  function tooltipHtml(d, index) {
    var baseline = d.series.baseline ? d.series.baseline[index] : null;
    var optimized = d.series.optimized ? d.series.optimized[index] : null;
    var tariff = d.series.tariff ? d.series.tariff[index] : null;
    var carbon = d.series.carbon ? d.series.carbon[index] : null;
    var tariffInfo = classifyTariff(d.series.tariff);
    var level = tariffInfo ? tariffInfo.levels[index] : null;

    var rows = [];
    if (isNum(baseline)) {
      rows.push(
        '<div class="tip-row"><span>Baseline</span><b>' +
          esc(fmtKwh(baseline)) +
          " kWh</b></div>"
      );
    }
    if (isNum(optimized)) {
      rows.push(
        '<div class="tip-row"><span>Optimised</span><b>' +
          esc(fmtKwh(optimized)) +
          " kWh</b></div>"
      );
    }
    if (isNum(tariff)) {
      rows.push(
        '<div class="tip-row"><span>Tariff</span><b>₹' +
          esc(nf(tariff, 2)) +
          "/kWh</b></div>"
      );
    }
    if (isNum(carbon)) {
      rows.push(
        '<div class="tip-row"><span>Carbon</span><b>' +
          esc(nf(carbon, 2)) +
          " kg/kWh</b></div>"
      );
    }
    if (isNum(baseline) && isNum(optimized)) {
      var delta = optimized - baseline;
      rows.push(
        '<div class="tip-row tip-row--delta' +
          (delta > 0.0005 ? " is-up" : "") +
          '"><span>Change</span><b>' +
          (delta > 0 ? "+" : delta < 0 ? "−" : "") +
          esc(fmtKwh(Math.abs(delta))) +
          " kWh</b></div>"
      );
    }

    return (
      '<div class="chart-tip__h"><span class="chart-tip__hour">' +
      esc(hourLabel(d.hours, index)) +
      '</span><span class="chart-tip__tariff">' +
      (level ? esc(level) + " tariff" : "hour") +
      "</span></div>" +
      rows.join("")
    );
  }

  /* ------------------------------------------------------------- inspector */

  function cell(label, html, cls) {
    return (
      '<div class="icell"><span>' +
      esc(label) +
      '</span><b class="' +
      (cls || "") +
      '">' +
      html +
      "</b></div>"
    );
  }

  /* Hour stepping is direction-aware: stepping forward pushes the previous
     reading out and brings the next in from the corresponding side. Hover
     reads stay immediate (quick), so pointing at the chart never lags. */
  function setInspect(index, quick) {
    var prev = state.inspectIndex;
    var dir = 0;
    if (
      prev !== null && prev !== undefined &&
      index !== null && index !== undefined &&
      index !== prev
    ) {
      dir = index > prev ? 1 : -1;
    }

    var cells = $('[data-bind="inspect-cells"]');
    if (!cells || !motion || quick) {
      paintInspect(index);
      return;
    }

    state.inspectIndex = index;
    motion.swap(
      cells,
      function () {
        paintInspect(index);
      },
      { dir: dir, distance: 7 }
    );
  }

  function paintInspect(index) {
    state.inspectIndex = index;
    var cells = $('[data-bind="inspect-cells"]');
    var hourEl = $('[data-bind="inspect-hour"]');
    var labelEl = $('[data-bind="inspect-label"]');
    if (!cells) return;

    var d = state.data;
    if (!d) {
      if (hourEl) hourEl.textContent = "—";
      if (labelEl) labelEl.textContent = "No data";
      cells.innerHTML = cell("Baseline", "—") + cell("Optimised", "—");
      return;
    }

    var s = d.series;
    if (index === null || index === undefined || index < 0 || index > 23) {
      if (hourEl) hourEl.textContent = "24 h";
      if (labelEl) labelEl.textContent = "Daily total";

      var baseTotal = sumSeries(s.baseline);
      var optTotal = sumSeries(s.optimized);
      var tariffInfo = classifyTariff(s.tariff);

      cells.innerHTML =
        cell("Baseline", isNum(baseTotal) ? esc(fmtKwh(baseTotal, 1)) + " kWh" : "—") +
        cell("Optimised", isNum(optTotal) ? esc(fmtKwh(optTotal, 1)) + " kWh" : "—") +
        cell(
          "Model error",
          isNum(d.model.mae) ? "±" + esc(nf(d.model.mae, 2)) + " kWh/h" : "—"
        ) +
        cell(
          "Change",
          isNum(baseTotal) && isNum(optTotal)
            ? deltaHtml(optTotal - baseTotal)
            : "—",
          isNum(baseTotal) && isNum(optTotal)
            ? optTotal - baseTotal < 0
              ? "is-pos"
              : optTotal - baseTotal > 0
                ? "is-neg"
                : ""
            : ""
        ) +
        (tariffInfo
          ? cell(
              "Tariff range",
              "₹" + esc(nf(tariffInfo.min, 1)) + "–₹" + esc(nf(tariffInfo.max, 1))
            )
          : "");
      return;
    }

    if (hourEl) hourEl.textContent = hourLabel(d.hours, index);
    if (labelEl) labelEl.textContent = "Hour of day";

    var baseline = s.baseline ? s.baseline[index] : null;
    var optimized = s.optimized ? s.optimized[index] : null;
    var tariff = s.tariff ? s.tariff[index] : null;
    var carbon = s.carbon ? s.carbon[index] : null;

    var parts = [];
    parts.push(cell("Baseline", isNum(baseline) ? esc(fmtKwh(baseline)) + " kWh" : "—"));
    parts.push(
      cell("Optimised", isNum(optimized) ? esc(fmtKwh(optimized)) + " kWh" : "—")
    );
    if (isNum(tariff)) {
      parts.push(cell("Tariff", "₹" + esc(nf(tariff, 2)) + "/kWh", "is-accent"));
    }
    if (isNum(carbon)) {
      parts.push(cell("Carbon", esc(nf(carbon, 2)) + " kg/kWh"));
    }
    if (isNum(baseline) && isNum(optimized)) {
      var dd = optimized - baseline;
      parts.push(
        cell(
          "Change",
          deltaHtml(dd),
          Math.abs(dd) < 0.005 ? "" : dd < 0 ? "is-pos" : "is-neg"
        )
      );
    }
    cells.innerHTML = parts.join("");
  }

  function deltaHtml(delta) {
    if (!isNum(delta)) return "—";
    var sign = delta > 0.0005 ? "+" : delta < -0.0005 ? "−" : "";
    return esc(sign + fmtKwh(Math.abs(delta))) + " kWh";
  }

  function renderTable(d) {
    var body = $('[data-bind="hourly-rows"]');
    if (!body) return;

    if (!d) {
      body.innerHTML = '<tr><td colspan="5">No data</td></tr>';
      return;
    }

    if (!d.series.baseline && !d.series.optimized) {
      body.innerHTML =
        '<tr><td colspan="5">The API returned no hourly series for this scenario.</td></tr>';
      return;
    }

    var rows = [];
    for (var i = 0; i < 24; i++) {
      rows.push(
        "<tr><td>" +
          esc(hourLabel(d.hours, i)) +
          "</td><td>" +
          (d.series.baseline ? esc(nf(d.series.baseline[i], 2)) : "—") +
          "</td><td>" +
          (d.series.optimized ? esc(nf(d.series.optimized[i], 2)) : "—") +
          "</td><td>" +
          (d.series.tariff ? esc(nf(d.series.tariff[i], 2)) : "—") +
          "</td><td>" +
          (d.series.carbon ? esc(nf(d.series.carbon[i], 3)) : "—") +
          "</td></tr>"
      );
    }
    body.innerHTML = rows.join("");
  }

  /* ------------------------------------------------------------ agent logs */

  function protocolKey(raw) {
    var s = String(raw || "").toUpperCase();
    if (s.indexOf("MQTT") !== -1) return "mqtt";
    if (s.indexOf("OCPP") !== -1) return "ocpp";
    return "";
  }

  function protocolLabel(raw) {
    var s = String(raw || "").trim();
    if (!s) return "SIMULATED";
    var isSimulated = /^SIMULATED/i.test(s);
    var core = s.replace(/^SIMULATED[_ ]?/i, "").toUpperCase() || "COMMAND";
    /* Only the command buses are simulated instructions; an "AGENT" entry is
       the agent's own reasoning and must not be labelled as a command. */
    return isSimulated ? "SIMULATED " + core : core;
  }

  function normaliseHour(value) {
    if (typeof value === "number" && isFinite(value)) {
      var n = Math.round(value) % 24;
      return (n < 10 ? "0" : "") + n + ":00";
    }
    if (typeof value === "string" && value.trim()) return value.trim();
    return "--:--";
  }

  function payloadBlock(payload) {
    if (payload === null || payload === undefined || payload === "") return "";

    var rows = [];
    function push(key, val) {
      var text;
      if (val === null || val === undefined) text = "null";
      else if (typeof val === "object") {
        try {
          text = JSON.stringify(val);
        } catch (e) {
          text = String(val);
        }
      } else text = String(val);
      if (text.length > 220) text = text.slice(0, 217) + "…";
      rows.push(
        "<dt>" + esc(key) + "</dt><dd>" + esc(text) + "</dd>"
      );
    }

    if (typeof payload === "object" && !Array.isArray(payload)) {
      var keys = Object.keys(payload);
      if (!keys.length) return "";
      keys.forEach(function (key) {
        push(key, payload[key]);
      });
    } else {
      push("payload", payload);
    }

    return (
      '<details class="log__payload"><summary>payload</summary><dl class="kv">' +
      rows.join("") +
      "</dl></details>"
    );
  }

  function renderLogs(d) {
    var list = $('[data-bind="log"]');
    if (!list) return;

    var logs = d ? d.logs : [];
    setText("agent-count", plural(logs.length, "event"));

    if (!logs.length) {
      list.innerHTML =
        '<li class="log__row" data-level="info"><span class="log__time">--:--</span>' +
        '<div class="log__main"><p class="log__msg">' +
        (d
          ? "The agent recorded no decisions for this scenario."
          : "Waiting for the first agent pass…") +
        "</p></div></li>";
      return;
    }

    list.innerHTML = logs
      .map(function (entry, i) {
        var log = entry && typeof entry === "object" ? entry : {};
        var level = String(log.level || "info").toLowerCase();
        var proto = protocolKey(log.protocol);
        var protocol = log.protocol
          ? '<span class="proto" data-proto="' + proto + '">' +
            esc(protocolLabel(log.protocol)) +
            "</span>"
          : "";
        var message =
          typeof log.message === "string" && log.message.trim()
            ? log.message
            : "(no message)";
        var delay = reduceMotion.matches ? 0 : Math.min(i * 26, 320);

        return (
          '<li class="log__row" data-level="' +
          esc(level) +
          '" style="animation-delay:' +
          delay +
          'ms"><span class="log__time">' +
          esc(normaliseHour(log.hour)) +
          '</span><div class="log__main"><div class="log__head">' +
          protocol +
          '</div><p class="log__msg">' +
          esc(message) +
          "</p>" +
          payloadBlock(log.payload) +
          "</div></li>"
        );
      })
      .join("");
  }

  /* ------------------------------------------------------------- appliances */

  function controlClassFor(actuator) {
    if (!actuator) return "recommendation";

    var status = String(actuator.status || "").toLowerCase();
    var type = String(actuator.control_type || "").toLowerCase();
    var name = String(actuator.name || "").toLowerCase();
    var haystack = status + " " + type + " " + name;

    /* The backend's own status wins: an actuator that reports "Recommendation
       Only" is advisory even when its shiftable flag is true. */
    if (/recommend|advisory|advise|manual|no actuation/.test(haystack)) {
      return "recommendation";
    }
    if (/protect|critical|always|locked|base\s*load|fixed|never|non-?shift/.test(haystack)) {
      return "protected";
    }
    if (
      /defer|shift|schedul|dispatch|controllable|thermal|curtail|charge point|smart plug/.test(
        haystack
      )
    ) {
      return "shiftable";
    }
    return actuator.shiftable === true ? "shiftable" : "protected";
  }

  var CLASS_NOTE = {
    recommendation:
      "Advisory only — the agent recommends a change here; no control path is simulated.",
    protected: "Held constant by the schedule — the agent will not defer it.",
    shiftable: "The agent may move this load; the change is simulated and unsigned."
  };

  function windowText(windowValue) {
    if (windowValue === null || windowValue === undefined) return null;
    if (typeof windowValue === "string") return windowValue.trim() || null;
    if (Array.isArray(windowValue)) {
      var parts = windowValue.filter(function (v) {
        return v !== null && v !== undefined && String(v).trim() !== "";
      });
      return parts.length ? parts.join("–") : null;
    }
    if (typeof windowValue === "object") {
      var start = windowValue.start || windowValue.from || windowValue.begin;
      var end = windowValue.end || windowValue.to || windowValue.finish;
      if (start && end) return String(start) + "–" + String(end);
      if (start) return String(start);
      try {
        var flat = JSON.stringify(windowValue);
        return flat === "{}" ? null : flat;
      } catch (e) {
        return null;
      }
    }
    return String(windowValue);
  }

  function confidencePct(value) {
    var n = toNum(value);
    if (n === null) return null;
    if (n <= 1) n = n * 100;
    return clamp(n, 0, 100);
  }

  function prettyStatus(status) {
    if (status === null || status === undefined) return null;
    var s = String(status).replace(/[_-]+/g, " ").trim();
    if (!s) return null;
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function renderAppliances(d) {
    var devicesEl = $('[data-bind="devices"]');
    var barEl = $('[data-bind="meter-bar"]');
    if (!devicesEl || !barEl) return;

    var estimates = d ? d.appliances : [];
    var actuators = d ? d.actuators : [];

    var actById = {};
    var actByName = {};
    actuators.forEach(function (a) {
      if (!a || typeof a !== "object") return;
      if (a.id !== undefined && a.id !== null) actById[String(a.id)] = a;
      if (typeof a.name === "string") actByName[a.name.toLowerCase()] = a;
    });

    // Build joined rows; include actuators that have no matching estimate too.
    var rows = [];
    var usedActuators = {};

    estimates.forEach(function (est) {
      var e = est && typeof est === "object" ? est : {};
      var idKey = e.id !== undefined && e.id !== null ? String(e.id) : null;
      var nameKey = typeof e.name === "string" ? e.name.toLowerCase() : null;
      var actuator = (idKey && actById[idKey]) || (nameKey && actByName[nameKey]) || null;
      if (actuator) usedActuators[String(actuator.id)] = true;
      rows.push({
        id: idKey,
        name: typeof e.name === "string" && e.name ? e.name : "Unnamed category",
        kwh: toNum(e.daily_kwh),
        confidence: confidencePct(e.confidence),
        confidenceRaw: e.confidence,
        actuator: actuator
      });
    });

    actuators.forEach(function (a) {
      if (!a || typeof a !== "object") return;
      if (a.id !== undefined && usedActuators[String(a.id)]) return;
      rows.push({
        id: a.id !== undefined ? String(a.id) : null,
        name: typeof a.name === "string" && a.name ? a.name : "Actuator",
        kwh: null,
        confidence: null,
        actuator: a
      });
    });

    setText("dev-count", rows.length ? plural(rows.length, "category", "categories") : "none");

    var baseTotal = d ? sumSeries(d.series.baseline) : null;
    var estTotal = 0;
    var estSeen = false;
    rows.forEach(function (r) {
      if (isNum(r.kwh)) {
        estTotal += r.kwh;
        estSeen = true;
      }
    });

    var meterTotal = isNum(baseTotal) ? baseTotal : estSeen ? estTotal : null;
    setText("meter-total", isNum(meterTotal) ? nf(meterTotal, 1) : "—");

    var ratio =
      isNum(meterTotal) && meterTotal > 0 && estSeen ? (estTotal / meterTotal) * 100 : null;

    setText("meter-cover", ratio === null ? "—" : fmtPct(ratio, 0) + "%");

    var noteEl = $('[data-bind="meter-note"]');
    if (noteEl) {
      if (ratio === null) {
        noteEl.hidden = true;
      } else if (ratio > 108) {
        noteEl.hidden = false;
        noteEl.textContent =
          "Category estimates add up to more than the metered total — they are illustrative disaggregations of the aggregate meter, not sub-metered readings.";
      } else if (ratio < 65) {
        noteEl.hidden = false;
        noteEl.textContent =
          "Category estimates cover only part of the metered total; the remainder is unexplained standby and unclassified load.";
      } else {
        noteEl.hidden = true;
      }
    }

    if (!rows.length) {
      devicesEl.innerHTML =
        '<div class="dev"><div class="dev__top"><span class="dev__name">' +
        (d
          ? "The API returned no appliance estimates."
          : "Loading categories…") +
        "</span></div></div>";
      barEl.innerHTML = "";
      return;
    }

    var segments = [];
    var html = [];
    var labelConfidence = [];

    rows.forEach(function (row, idx) {
      var cls = controlClassFor(row.actuator);
      var style = CLASS_STYLE[cls];
      var share =
        isNum(row.kwh) && isNum(meterTotal) && meterTotal > 0
          ? (row.kwh / meterTotal) * 100
          : isNum(row.kwh) && estSeen && estTotal > 0
            ? (row.kwh / estTotal) * 100
            : null;

      if (isNum(row.kwh)) {
        segments.push(
          '<span class="meter__seg" style="width:' +
            Math.max(share || 0, 1.5).toFixed(2) +
            "%;background:" +
            style.color +
            ';opacity:' +
            (0.7 + (idx % 3) * 0.12).toFixed(2) +
            '" title="' +
            esc(row.name) +
            ' · ' +
            esc(fmtKwh(row.kwh, 2)) +
            ' kWh"></span>'
        );
      }

      var status = row.actuator ? prettyStatus(row.actuator.status) : null;
      var windowValue = row.actuator ? windowText(row.actuator.scheduled_window) : null;

      /* Don't print the same words twice (status "Recommendation Only" next to
         the "Recommendation only" class chip). */
      var squash = function (v) {
        return String(v || "").toLowerCase().replace(/[^a-z]/g, "");
      };
      if (status && squash(status) === squash(style.label)) status = null;

      var stateHtml = "";
      if (status) {
        stateHtml =
          '<span class="dev__state"><svg class="ic" aria-hidden="true"><use href="#i-clock"/></svg>' +
          esc(status) +
          "</span>";
      } else if (!row.actuator) {
        stateHtml =
          '<span class="dev__state"><svg class="ic" aria-hidden="true"><use href="#i-info"/></svg>No control path</span>';
      }

      var confHtml =
        row.confidence === null
          ? ""
          : '<span class="conf" title="Estimate confidence"><span class="conf__bar"><i class="conf__fill" style="width:' +
            row.confidence.toFixed(0) +
            '%;background:' +
            style.color +
            '"></i></span>' +
            row.confidence.toFixed(0) +
            "%</span>";

      if (
        row.confidence === null &&
        typeof row.confidenceRaw === "string" &&
        row.confidenceRaw.trim() &&
        labelConfidence.indexOf(row.confidenceRaw.trim()) === -1
      ) {
        labelConfidence.push(row.confidenceRaw.trim());
      }

      var note = CLASS_NOTE[cls] || null;

      html.push(
        '<div class="dev" style="--swatch:' +
          style.color +
          '">' +
          '<div class="dev__top"><span class="dev__name"><i class="dot" aria-hidden="true"></i>' +
          esc(row.name) +
          '</span><span class="dev__kwh">' +
          (isNum(row.kwh) ? esc(nf(row.kwh, 2)) + "<em>kWh/day</em>" : "—") +
          "</span></div>" +
          '<div class="dev__meta"><span class="chip ' +
          style.chip +
          '">' +
          esc(style.label) +
          "</span>" +
          stateHtml +
          (windowValue ? '<span class="dev__win">' + esc(windowValue) + "</span>" : "") +
          confHtml +
          "</div>" +
          (note ? '<p class="dev__note">' + esc(note) + "</p>" : "") +
          "</div>"
      );
    });

    devicesEl.innerHTML = html.join("");
    barEl.innerHTML = segments.join("");
    if (motion) motion.enter(devicesEl, ".dev", { step: 22, distance: 5, max: 9 });

    var confNote = $('[data-bind="conf-note"]');
    if (confNote) {
      if (labelConfidence.length) {
        confNote.hidden = false;
        confNote.textContent =
          "Estimate confidence: " +
          labelConfidence.join(", ") +
          " — the API reports estimate quality as a label rather than a score, so no confidence percentage is shown.";
      } else {
        confNote.hidden = true;
      }
    }
  }

  /* ---------------------------------------------------------------- model */

  function renderModel(d) {
    if (!d) {
      ["model-r2", "model-mae", "model-alg", "model-train", "model-test"].forEach(
        function (k) {
          setText(k, "—");
        }
      );
      setText("eval-note", "Waiting for the evaluation figures.");
      return;
    }

    setText("model-r2", isNum(d.model.r2) ? nf(d.model.r2, 2) : "—");
    setText("model-mae", isNum(d.model.mae) ? nf(d.model.mae, 2) : "—");
    setText("model-alg", d.model.algorithm || "—");
    setText("model-train", isNum(d.model.trainRows) ? fmtInt(d.model.trainRows) : "—");
    setText("model-test", isNum(d.model.testRows) ? fmtInt(d.model.testRows) : "—");

    setText(
      "eval-note",
      isNum(d.model.testRows)
        ? "Scored on " +
            fmtInt(d.model.testRows) +
            " synthetic held-out rows — never on the training rows."
        : "Scored on synthetic held-out rows, not on real meter data."
    );
  }

  /* -------------------------------------------------------- recommendations */

  function buildAdvice(d) {
    var out = [];
    if (!d) return out;

    var s = d.savings;
    var ser = d.series;
    var baseTotal = sumSeries(ser.baseline);
    var optTotal = sumSeries(ser.optimized);
    var tariffInfo = classifyTariff(ser.tariff);
    var carbonInfo = classifyCarbon(ser.carbon);

    /* 1 — shifting versus reduction. This is the distinction the brief asks for. */
    if (isNum(baseTotal) && isNum(optTotal) && baseTotal > 0) {
      var delta = baseTotal - optTotal;
      var deltaPct = (delta / baseTotal) * 100;
      if (Math.abs(deltaPct) < 1) {
        out.push({
          title: "This run shifts energy, it does not reduce it",
          body:
            "Daily demand is effectively unchanged (<b>" +
            esc(fmtKwh(baseTotal, 1)) +
            "</b> → <b>" +
            esc(fmtKwh(optTotal, 1)) +
            "</b> kWh). The saving comes from <em>when</em> the energy is used, not how much — so a target written as “use less electricity” will not move, while a bill or peak-demand target will."
        });
      } else if (delta > 0) {
        out.push({
          title: "Consumption fell as well as shifting",
          body:
            "Daily demand drops from <b>" +
            esc(fmtKwh(baseTotal, 1)) +
            "</b> to <b>" +
            esc(fmtKwh(optTotal, 1)) +
            "</b> kWh (" +
            esc(fmtPct(deltaPct)) +
            "%). That part is genuine reduction, on top of any load moved out of the peak window."
        });
      } else {
        out.push({
          title: "Total demand rose slightly",
          body:
            "The optimised profile totals <b>" +
            esc(fmtKwh(optTotal, 1)) +
            "</b> kWh against a baseline of <b>" +
            esc(fmtKwh(baseTotal, 1)) +
            "</b> (" +
            esc(fmtPct(deltaPct)) +
            "%). Holding and re-energising appliances can add standing losses; the bill can still fall even when total energy rises."
        });
      }
    }

    /* 2 — the peak window. */
    if (tariffInfo && tariffInfo.peakIdx.length && (ser.baseline || ser.optimized)) {
      var peakBase = 0;
      var peakOpt = 0;
      tariffInfo.peakIdx.forEach(function (i) {
        if (ser.baseline && isNum(ser.baseline[i])) peakBase += ser.baseline[i];
        if (ser.optimized && isNum(ser.optimized[i])) peakOpt += ser.optimized[i];
      });
      var removed = peakBase - peakOpt;
      var window = rangeLabel(d.hours, tariffInfo.peakIdx);

      if (removed > 0.005) {
        out.push({
          title: "The agent clears the " + window + " peak",
          body:
            "Those " +
            plural(tariffInfo.peakIdx.length, "hour") +
            " carry <b>" +
            esc(fmtKwh(peakBase, 2)) +
            "</b> kWh at baseline and <b>" +
            esc(fmtKwh(peakOpt, 2)) +
            "</b> kWh on the agent's schedule — <b>" +
            esc(fmtKwh(removed, 2)) +
            "</b> kWh moved to cheaper hours. The appliances still run; only the timing changes."
        });
      } else {
        out.push({
          title: "No load left the " + window + " peak",
          body:
            "At baseline those hours carry <b>" +
            esc(fmtKwh(peakBase, 2)) +
            "</b> kWh and the optimised profile carries <b>" +
            esc(fmtKwh(peakOpt, 2)) +
            "</b> kWh. With a tariff spread of ₹" +
            esc(nf(tariffInfo.min, 2)) +
            "–₹" +
            esc(nf(tariffInfo.max, 2)) +
            "/kWh there may be little for the agent to gain."
        });
      }
    }

    /* 3 — carbon honesty. */
    if (carbonInfo) {
      if (carbonInfo.flat) {
        out.push({
          title: "Timing cannot move carbon in this dataset",
          body:
            "Grid intensity is flat at <b>" +
            esc(nf(carbonInfo.mean, 3)) +
            "</b> kg CO₂/kWh across all 24 hours, so shifting load changes when energy is used, not how much carbon it carries. Any monthly CO₂ figure here therefore comes from energy avoided, not from cleaner timing."
        });
      } else {
        out.push({
          title: "Cleaner hours exist, and timing uses them",
          body:
            "Grid intensity ranges <b>" +
            esc(nf(carbonInfo.min, 3)) +
            "–" +
            esc(nf(carbonInfo.max, 3)) +
            "</b> kg CO₂/kWh. Load moved into the cleaner hours cuts emissions beyond the energy saved — worth checking the optimised profile lines up with those hours."
        });
      }
    }

    /* 4 — payback, honestly null when null. */
    if (isNum(s.payback)) {
      out.push({
        title: "Smart-plug payback ≈ " + nf(s.payback, 1) + " months",
        body:
          "At <b>₹" +
          esc(fmtMoney(s.dailyInr)) +
          "</b> saved per simulated day, the plug returns its assumed cost in " +
          esc(nf(s.payback, 1)) +
          " months. Payback scales linearly with the hardware price, so treat it as an order of magnitude, not a quote."
      });
    } else {
      out.push({
        title: "Smart-plug payback is not available",
        body:
          "The API did not return a payback estimate, so none is shown rather than guessed. A payback needs a hardware price and the modelled daily saving; only the latter is present in this response."
      });
    }

    return out.slice(0, 4);
  }

  function classifyCarbon(carbon) {
    if (!Array.isArray(carbon)) return null;
    var vals = carbon.filter(isNum);
    if (!vals.length) return null;
    var min = Math.min.apply(null, vals);
    var max = Math.max.apply(null, vals);
    var m = mean(carbon);
    var flat = m !== null && Math.abs(m) > 1e-9 ? (max - min) / Math.abs(m) < 0.02 : true;
    return { min: min, max: max, mean: m, flat: flat };
  }

  function renderAdvice(d) {
    var list = $('[data-bind="advice"]');
    if (!list) return;

    var items = buildAdvice(d);
    if (!items.length) {
      list.innerHTML =
        '<li class="advice__item"><span class="advice__num">—</span><div class="advice__body">' +
        "<h3>No schedule to advise on yet</h3><p>" +
        (d
          ? "The API returned no hourly series for this scenario."
          : "Waiting on the simulation API.") +
        "</p></div></li>";
    } else {
      list.innerHTML = items
        .map(function (item, i) {
          return (
            '<li class="advice__item"><span class="advice__num">' +
            (i + 1) +
            '</span><div class="advice__body"><h3>' +
            esc(item.title) +
            "</h3><p>" +
            item.body +
            "</p></div></li>"
          );
        })
        .join("");
    }

    if (motion) motion.enter(list, ".advice__item", { step: 26, distance: 5, max: 6 });

    var assumptionsEl = $('[data-bind="assumptions"]');
    if (!assumptionsEl) return;
    var assumptions = d ? d.assumptions : [];
    if (!assumptions.length) {
      assumptionsEl.innerHTML =
        "<li>" +
        (d
          ? "The API returned no assumptions for this response."
          : "Waiting on the simulation API.") +
        "</li>";
      return;
    }
    assumptionsEl.innerHTML = assumptions
      .map(function (a) {
        return "<li>" + esc(a) + "</li>";
      })
      .join("");
  }

  function renderFooterMeta(d) {
    if (!d) return;
    setText("meta-dataset", d.meta.dataset);
    setText("meta-tariff", d.meta.tariffType);
    setText("meta-scenario", d.meta.scenario);
  }

  /* ------------------------------------------------------------ busy state */

  function setBusy(on, reason) {
    state.busy = !!on;
    state.busyReason = on ? reason || null : null;
    if (on) progressStart();
    else progressDone();
    document.body.classList.toggle("is-updating", !!on && state.loadedOnce);
    document.body.classList.toggle("is-loading", !!on && !state.loadedOnce);
    renderScenario();
    renderStatusChrome(state.data);
  }

  function showError(err) {
    state.phase = "error";
    state.error = err;
    document.body.classList.remove("is-loading", "is-updating");
    document.body.classList.add("is-ready");

    var alert = $("#alert");
    if (alert) {
      var msg = $('[data-bind="alert-msg"]');
      if (msg) {
        msg.textContent =
          (err && err.message ? err.message : "The simulation API could not be reached.") +
          " If the Flask backend is not running yet, start it and retry — this page renders only what the API returns.";
      }
    }
    if (motion) motion.expand(alert, true);
    else if (alert) alert.hidden = false;
    renderEmptyEverything();
    renderStatusChrome(state.data);
    toast("Dashboard data unavailable", true);
  }

  function clearError() {
    var alert = $("#alert");
    if (!alert || alert.hidden) return;
    if (motion) motion.expand(alert, false);
    else alert.hidden = true;
  }

  /* ---------------------------------------------------------------- toast */

  var toastTimer = null;
  function toast(message, isError) {
    var el = $("#toast");
    if (!el) return;
    el.textContent = message;
    el.classList.toggle("is-error", !!isError);
    el.classList.add("is-on");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      el.classList.remove("is-on");
    }, 4200);
  }

  /* -------------------------------------------------------------- loading */

  function loadDashboard(options) {
    var opts = options || {};
    var city = opts.city || state.city;
    var spike = opts.spike === undefined ? state.requestedSpike : !!opts.spike;

    if (state.busy) return Promise.resolve();

    setBusy(true, opts.reason || "load");
    clearError();

    return fetchDashboard(city, spike)
      .then(function (payload) {
        applyPayload(payload, city, spike);
      })
      .catch(function (err) {
        if (err && err.status === 0) return; // superseded
        if (err && err.name === "AbortError") return;
        showError(err);
      })
      .then(function () {
        setBusy(false);
      });
  }

  function applyPayload(payload, city, spikeHint) {
    var data;
    try {
      data = normalise(payload);
    } catch (err) {
      showError(err);
      return;
    }

    state.data = data;
    state.city = data.meta.city || city;
    state.spike = resolveSpike(data.meta.scenario, spikeHint);
    state.requestedSpike = state.spike;
    state.phase = "ready";
    state.loadedOnce = true;
    state.inspectIndex = null;

    document.body.classList.remove("is-loading", "is-updating");
    document.body.classList.add("is-ready");
    clearError();
    renderAll();
  }

  function resolveSpike(scenario, fallback) {
    var s = String(scenario || "").toLowerCase();
    if (s.indexOf("spike") !== -1 || s.indexOf("peak") !== -1) return true;
    if (s.indexOf("normal") !== -1 || s.indexOf("baseline") !== -1) return false;
    return !!fallback;
  }

  /* ---------------------------------------------------------------- events */

  function toggleSpike(enabled) {
    if (state.busy) return;
    var city = state.city;

    setBusy(true, "spike");
    clearError();

    postSpike(city, enabled)
      .then(function (payload) {
        if (looksLikeDashboard(payload)) {
          applyPayload(payload, city, enabled);
          return null;
        }
        // Some backends acknowledge the toggle without echoing the dashboard.
        return fetchDashboard(city, enabled).then(function (fresh) {
          applyPayload(fresh, city, enabled);
        });
      })
      .then(function () {
        setBusy(false);
        toast(
          enabled
            ? "7 PM peak spike simulated — schedule, KPIs and agent log updated."
            : "Back to the normal scenario."
        );
      })
      .catch(function (err) {
        setBusy(false);
        if (err && (err.status === 0 || err.name === "AbortError")) return;
        showError(err);
      });
  }

  function selectCity(city, button) {
    if (!city || state.busy || city === state.city) return;
    var buttons = $$(".seg__btn");
    var prevIndex = buttons.findIndex(function (b) {
      return b.classList.contains("is-on");
    });
    var nextIndex = buttons.indexOf(button);
    state.cityDir =
      prevIndex >= 0 && nextIndex >= 0 && nextIndex !== prevIndex
        ? nextIndex > prevIndex
          ? 1
          : -1
        : 0;

    buttons.forEach(function (btn) {
      var selected = btn === button;
      btn.classList.toggle("is-on", selected);
      btn.setAttribute("aria-checked", selected ? "true" : "false");
      btn.setAttribute("tabindex", selected ? "0" : "-1");
    });
    syncSegPill(false);
    swapText("city-name", city, state.cityDir);
    loadDashboard({ city: city, spike: state.requestedSpike, reason: "city" });
  }

  function wireCitySegments() {
    var seg = $(".seg");
    if (!seg) return;
    var buttons = $$(".seg__btn", seg);

    buttons.forEach(function (btn, index) {
      on(btn, "click", function () {
        selectCity(btn.getAttribute("data-city"), btn);
      });
    });

    on(seg, "keydown", function (event) {
      var current = buttons.findIndex(function (b) {
        return b.classList.contains("is-on");
      });
      if (current < 0) current = 0;
      var next = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        next = (current + 1) % buttons.length;
      } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        next = (current - 1 + buttons.length) % buttons.length;
      } else if (event.key === "Home") {
        next = 0;
      } else if (event.key === "End") {
        next = buttons.length - 1;
      }
      if (next === null) return;
      event.preventDefault();
      buttons[next].focus();
      selectCity(buttons[next].getAttribute("data-city"), buttons[next]);
    });
  }

  function wireChartKeyboard() {
    var canvas = $("#energy-chart");
    if (!canvas) return;
    on(canvas, "keydown", function (event) {
      if (!state.data) return;
      var keys = ["ArrowRight", "ArrowLeft", "Home", "End", "Escape"];
      if (keys.indexOf(event.key) === -1) return;
      event.preventDefault();
      var current = state.inspectIndex;
      var next;
      if (event.key === "Home") next = 0;
      else if (event.key === "End") next = 23;
      else if (event.key === "Escape") next = null;
      else if (current === null || current === undefined) next = 0;
      else next = clamp(current + (event.key === "ArrowRight" ? 1 : -1), 0, 23);
      setInspect(next);
    });
    on(canvas, "blur", function () {
      if (state.inspectIndex !== null) setInspect(null);
    });
  }

  function wireTableAccordion() {
    var details = $(".tablewrap");
    if (!details) return;
    var summary = details.querySelector("summary");
    var content = details.querySelector(".tbl");
    if (!summary || !content) return;

    summary.addEventListener("click", function (event) {
      event.preventDefault();
      var open = !details.open;
      if (motion) motion.accordion(details, content, open);
      else details.open = open;
    });
  }

  function wireButtons() {
    on($("#btn-spike"), "click", function () {
      toggleSpike(true);
    });
    on($("#btn-reset"), "click", function () {
      if (!state.spike) return;
      toggleSpike(false);
    });
    on($("#btn-retry"), "click", function () {
      loadDashboard({ city: state.city, spike: state.requestedSpike, reason: "retry" });
    });
  }

  /* ------------------------------------------------------------------ init */

  function init() {
    wireCitySegments();
    wireButtons();
    wireChartKeyboard();
    wireTableAccordion();
    initSegPill();

    on(reduceMotion, "change", function () {
      if (chart) {
        chart.options.animation = reduceMotion.matches
          ? false
          : { duration: 420, easing: "easeOutCubic" };
        chart.update("none");
      }
    });

    loadDashboard({ city: DEFAULT_CITY, spike: false, reason: "initial" });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  /* Small surface for debugging only — no data is stored here. */
  window.EnergySense = {
    reload: function () {
      loadDashboard({ city: state.city, spike: state.requestedSpike, reason: "manual" });
    },
    state: function () {
      return {
        city: state.city,
        spike: state.spike,
        phase: state.phase,
        busy: state.busy,
        hasData: !!state.data
      };
    }
  };
})();
