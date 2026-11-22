# AGENTS.md — instructions for AI coding agents

EnergySense is a local, offline Flask application: a synthetic smart-meter forecast plus a
tariff-aware load-shifting simulation, with an editorial landing page and a data dashboard.

**Preserve behaviour.** This repository is a finished demonstration, not a scaffold. Prefer the
smallest change that satisfies the request and leave unrelated code alone.

---

## Stack and entry points

| Item | Value |
|---|---|
| Runtime | Python 3.11+ (verified 3.14.7), no virtualenv committed |
| Frameworks | Flask 3.1.x; numpy / pandas / scikit-learn / joblib |
| Front end | Jinja templates + vanilla ES2020 JS + hand-written CSS — **no build step, no npm, no bundler** |
| Charts | Chart.js 4, vendored in `static/vendor/chartjs/` |
| Tests | `pytest` (`tests/test_api.py`, `tests/test_agent.py`) |
| Entry point | `python app.py` → `model.ensure_artifacts()` → Flask on `127.0.0.1:5000` |
| Config | **None** — no `os.environ` / dotenv usage anywhere |

Routes (`app.py`): `GET /` (landing), `GET /app` (dashboard), `GET /api/dashboard`,
`POST /api/simulate-spike`, `GET /api/health`.

---

## Architecture map

| Path | Responsibility |
|---|---|
| `app.py` | Flask app, page routes, JSON API, error handlers (`{"error", "status"}` on `/api/*`), `ensure_setup()` |
| `model.py` | Dataset loading, `RandomForestRegressor` training/persistence (`data/model.pkl`, `data/metrics.json`), `predict_next_24h`, cold-start `ensure_artifacts()` |
| `data_gen.py` | Deterministic synthetic dataset generator (720 hourly rows / 30 days) written to `data/smart_meter.csv` |
| `tariffs.py` | Per-city tariff + grid-carbon profiles, `resolve_city()` (default `Bengaluru`, case-insensitive) |
| `agent.py` | Optimisation agent: re-times flexible loads; imported defensively (`_get_agent()`), app has a fallback path |
| `templates/index.html`, `templates/dashboard.html` | Page markup; dashboard owns an inline SVG icon sprite |
| `static/landing/`, `static/dashboard/` | Page CSS/JS (self-contained, no imports across pages) |
| `static/motion.js` | Shared motion runtime used by both pages |
| `static/vendor/` | Chart.js and fonts (local, licensed; never replace with a CDN) |
| `data/` | Generated artifacts only — git-ignored, safe to delete |
| `tests/` | Backend/ML/API contract tests (no browser tests) |
| `_verify_core.py`, `_verify_http.py`, `_verify_spike.py` | Developer smoke scripts |

---

## Contracts that must not change

- **`/api/dashboard` payload key order** is contract-defined and asserted by tests:
  `meta`, `model`, `hours`, `baseline_kwh`, `optimized_kwh`, `tariff_inr_per_kwh`,
  `carbon_kg_per_kwh`, `savings`, `actuators`, `agent_logs`, `appliance_estimates`, `assumptions`.
  `app.json.sort_keys = False` exists for this reason.
- **API errors stay JSON** on `/api/*`: `{"error": "<message>", "status": <int>}`.
- **Input validation**: unknown `city` → 400; malformed `spike`/`enabled`/JSON body → 400.
- **No device control**: actuator/log entries are dry-run records about a *simulated* household.
  No MQTT, OCPP or device API may be contacted, and copy must not imply live metering.
- **Synthetic-data honesty**: every figure is modelled from synthetic data. Do not add claims of
  real measurement, savings guarantees, or live tariffs.
- **Offline runtime**: no CDN links, no remote fonts, no telemetry, no analytics.

---

## Front-end conventions

- One animation owner per property. `static/motion.js` owns JS-driven opacity/transform work;
  Chart.js owns chart animation; log rows use their existing CSS animation. Do not stack a third
  layer on the same element.
- Motion tokens are mirrored: `static/motion.js` (`TOKENS`) ↔ `--mo-*` variables in
  `static/landing/landing.css` and `static/dashboard/dashboard.css`. Change both when retuning.
- Every motion path must degrade under `prefers-reduced-motion: reduce` **and** when the frame
  clock is suspended (see the frame watchdog and settle guards) — state must never depend on an
  animation completing.
- Active tab marker (`.seg__pill`) is a shared-layout element: reposition it, do not recreate it.
- Dashboard content is rendered from the API payload; do not hard-code numbers into templates.
- Keep the existing design language: warm charcoal / bronze / parchment, Fraunces + DM Sans,
  editorial spacing. This is not a redesign target.

---

## Coding conventions

- Python: 3.11+ syntax, `from __future__ import annotations`, module-level constants, typed helper
  signatures where already used, no new third-party dependencies without a strong reason (and
  never for something the standard library or existing deps already do).
- JavaScript: ES2020 IIFEs per page file, `"use strict"`, no framework, no transpiling. Keep
  feature detection (`if (window.Motion)`, `"IntersectionObserver" in window`).
- CSS: custom properties for tokens, no preprocessor, no `@import` of remote styles;
  self-hosted assets only.
- Templates: Jinja with literal asset paths (`/static/...`); do not introduce a new asset
  pipeline or hashing scheme.
- Comments explain *why*, matching the existing tone. Do not add narration comments.

---

## Commands (verified)

```bash
pip install -r requirements.txt                     # deps
python -c "import model; print(model.ensure_artifacts())"   # generate data + train
python app.py                                       # serve http://127.0.0.1:5000
python -m pytest -q                                 # full suite
python -m pytest tests/test_api.py -q -k "dashboard" # focused
python _verify_core.py                              # in-process sanity
python _verify_http.py                              # HTTP smoke (server must run)
```

There is no build, lint, typecheck or format command configured in this repository.

---

## Working rules for agents

1. **Targeted inspection only.** Search for the specific symbol/file you need
   (e.g. `grep -n "function renderKpis" static/dashboard/dashboard.js`) instead of scanning the
   whole tree, and read line ranges rather than entire large files. CSS files are ~50 KB each;
   JS files are ~70 KB.
2. **No unrelated refactors.** Do not reformat, rename, reorganise directories, split modules,
   migrate frameworks or "clean up" working code as a side effect.
3. **No configuration drift.** Do not change the bind address/port, city defaults, tariff tables,
   spike assumptions or dataset seed unless the request is specifically about them.
4. **Tests first when touching backend behaviour.** Run the focused test file, then the full suite
   if the change is broad. Do not run full builds repeatedly.
5. **Data artifacts are disposable.** Never commit `data/*.csv`, `data/*.pkl` or
   `data/*.json`; regenerate them instead.
6. **Secrets.** Never commit `.env`, credentials, tokens or keys, and never print them. The app
   needs none. `.gitignore` already excludes `.env*`, `*.pem`, `*.key`.
7. **Runtime caches.** Jinja caches templates per process: restart the server after template edits.
   Static assets have no cache-busting, so hard-reload the browser when checking CSS/JS changes.
8. **Finish cleanly.** Leave no temporary scripts, fixtures, debug `console.log` calls, or
   instrumentation in the tree; delete scratch files you created.
