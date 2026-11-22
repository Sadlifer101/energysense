# EnergySense

Household energy intelligence: a Flask application that forecasts 24 hours of household
electricity demand from a synthetic smart-meter dataset, then schedules flexible loads around
time-of-day tariffs and grid carbon intensity — entirely offline, on one machine.

Built for an ideathon around **SDG 7** (affordable, reliable and clean energy for all).

---

## Overview

### The problem

A household cannot see *when* its electricity is expensive or carbon-heavy, so flexible loads
(water heating, laundry, EV charging) run at the worst possible hour. The information exists in
the meter data; it is just never turned into a decision the household can act on.

### What EnergySense does

1. **Observe** — trains a RandomForest on a synthetic smart-meter dataset and reads the daily
   load shape hour by hour.
2. **Anticipate** — forecasts the next 24 hours of consumption (R² ≈ 0.71, MAE ≈ 0.12 kWh/h on
   the held-out split; values are printed at startup and shown in the dashboard).
3. **Adapt** — simulates re-timing flexible appliances out of the peak-tariff window, and reports
   modelled cost, carbon and peak-shift effects — with the assumptions stated in the UI.

### Key capabilities

- **Landing page** (`/`) — editorial walkthrough of the system, with an interactive illustrative
  shift model (kWh → ₹ peak/off-peak → kg CO₂e).
- **Dashboard** (`/app`) — 24-hour forecast vs. agent-optimised schedule on a Chart.js line chart
  with tariff/carbon bands, KPI strip, agent decision log, appliance disaggregation, model
  evaluation and advice.
- **JSON API** — `/api/dashboard`, `/api/simulate-spike`, `/api/health` (contract documented below).
- **Scenario simulation** — a 19:00 peak spike can be applied and cleared; schedule, KPIs and log
  update together.
- **Offline by design** — no CDN at runtime; Chart.js and both fonts are vendored.
- **Motion system** — one small vanilla runtime (`static/motion.js`) owns transitions
  (interruptible springs, shared-layout tab marker, direction-aware content swaps) and collapses
  to instant state changes under `prefers-reduced-motion`.

### Technology stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.14.7 (verified); 3.11+ recommended |
| Web framework | Flask 3.1.3 |
| ML / data | scikit-learn 1.9.1 (RandomForestRegressor), pandas 3.1.1, numpy 2.5.5, joblib 1.6.0 |
| Tests | pytest 9.2.0 |
| Front end | Server-rendered Jinja templates, vanilla ES2020 JavaScript, hand-authored CSS — **no build step, no bundler, no npm** |
| Charts | Chart.js 4 (vendored at `static/vendor/chartjs/chart.umd.js`) |
| Type / imagery | Fraunces + DM Sans (self-hosted WOFF2), local WebP assets |
| Persistence | Files only — `data/smart_meter.csv`, `data/model.pkl`, `data/metrics.json` (no database) |

### Project structure

```
.
├── app.py                 Flask app: page routes, JSON API, error handlers, __main__ entry
├── model.py               Synthetic dataset loading, RandomForest training/prediction, metrics
├── data_gen.py            Deterministic synthetic smart-meter generator (720 rows / 30 days)
├── tariffs.py             Per-city tariff + carbon profiles and city resolution
├── agent.py               Optimisation agent (schedules flexible loads); optional at runtime
├── requirements.txt       Runtime + test dependencies (only dependency source of truth)
├── templates/
│   ├── index.html         Landing page markup
│   └── dashboard.html     Dashboard markup (inline SVG sprite, panels, table)
├── static/
│   ├── motion.js          Shared motion runtime (springs, swaps, indicator, disclosures)
│   ├── landing/           landing.css, landing.js, self-hosted fonts and images
│   ├── dashboard/         dashboard.css, dashboard.js
│   └── vendor/            Chart.js and fonts (local copies, licences included)
├── tests/
│   ├── test_api.py        Dataset / model / API contract tests
│   └── test_agent.py      Optimisation-agent and savings-arithmetic tests
├── data/                  Generated at runtime — not versioned
└── _verify_core.py, _verify_http.py, _verify_spike.py   Developer smoke scripts
```

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11 or newer — verified on **3.14.7** | Needs wheels for numpy/pandas/scikit-learn; 3.11–3.13 are the safest range on older platforms |
| pip | Bundled with Python | `python -m pip` |
| Git | Any recent version | Only to clone |
| OS | Windows, macOS or Linux | No OS-specific code; paths are `pathlib`-based |
| Browser | A current Chrome, Edge, Firefox or Safari | Uses ES2020, Web Animations API, `<details>`, `color-mix()` |
| PostgreSQL / Redis / Docker | **Not used** | There is no database, queue or container setup |

No Node.js, npm, or Python packages beyond `requirements.txt` are required.

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/Sadlifer101/energysense.git
```

### 2. Enter the project directory

```bash
cd energysense
```

### 3. Create and activate a virtual environment

Windows (PowerShell):

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Windows (cmd.exe):

```bat
py -3.14 -m venv .venv
.venv\Scripts\activate.bat
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 5. Configure environment variables

**None are required.** The application reads no environment variables (`os.environ` / `dotenv`
are not used anywhere in the codebase). `.env.example` documents this; there is no `.env` to
create, no API key, and no external service to connect.

### 6. Initialize the dataset and model

This is automatic: `python app.py` generates the dataset and trains the model on first start
(about a second of CPU on a typical laptop). To do it explicitly before starting:

```bash
python -c "import model; print(model.ensure_artifacts())"
```

Artifacts written (all git-ignored, safe to delete and rebuild):

| File | Contents |
|---|---|
| `data/smart_meter.csv` | 720 synthetic hourly rows (30 contiguous days) |
| `data/model.pkl` | Trained `RandomForestRegressor` bundle |
| `data/metrics.json` | Held-out R² / MAE |

### 7. Run the development server

```bash
python app.py
```

Expected output:

```
EnergySense backend ready | dataset=synthetic | rows=720 | R2=0.708334 | MAE=0.121892 kWh | offline
Dashboard API: http://127.0.0.1:5000/api/dashboard?city=Bengaluru&spike=false
 * Running on http://127.0.0.1:5000
```

### 8. Open the application

| URL | View |
|---|---|
| http://127.0.0.1:5000/ | Landing page |
| http://127.0.0.1:5000/app | Dashboard |
| http://127.0.0.1:5000/api/health | Health + artifacts summary |
| http://127.0.0.1:5000/api/dashboard?city=Bengaluru&spike=false | Dashboard payload |

Stop the server with `Ctrl+C`.

---

## Configuration

There is no configuration file and no environment variable. Everything that looks configurable is
a module constant:

| Setting | Location | Default |
|---|---|---|
| Bind host / port | `app.py` (`if __name__ == "__main__"`) | `127.0.0.1:5000` |
| Default city | `tariffs.resolve_city(default=...)`, `static/dashboard/dashboard.js` | `Bengaluru` |
| Supported cities | `tariffs.CITIES` | `Bengaluru`, `Mumbai`, `Delhi` |
| Dataset size / seed | `data_gen.py` | 720 rows, fixed seed → reproducible |
| Model hyper-parameters | `model.py` (`RandomForestRegressor`) | see source |
| Peak-spike assumption | `model.py` (`SPIKE_HOUR = 19`, `SPIKE_KWH = 3.5`) | 3.5 kWh at 19:00 |

Changing the port means editing `app.py`; there is no environment override.

---

## API contract

All error responses on `/api/*` are JSON: `{"error": "<message>", "status": <int>}`.

| Method & path | Parameters | Response |
|---|---|---|
| `GET /api/dashboard` | `city` (optional, default `Bengaluru`), `spike` (optional bool: `true/false/1/0/yes/no`) | Dashboard payload, top-level keys in order: `meta`, `model`, `hours`, `baseline_kwh`, `optimized_kwh`, `tariff_inr_per_kwh`, `carbon_kg_per_kwh`, `savings`, `actuators`, `agent_logs`, `appliance_estimates`, `assumptions` |
| `POST /api/simulate-spike` | JSON body `{"city": "...", "enabled": true}` (`spike` also accepted) | Same payload, scenario applied |
| `GET /api/health` | — | `status`, `offline`, `dataset`, `agent_available`, `model`, `cities`, `artifacts` |

Unknown `city` → `400`. Malformed JSON body → `400`. Unknown `/api/*` route → JSON `404`.

---

## Production build / deployment

**There is no build step.** Templates, CSS, JS, fonts and Chart.js are served directly by Flask;
the only "build" is the first-run dataset generation and model training described above.

**No production deployment configuration is included** in this repository: no Dockerfile, no
Procfile, no WSGI/ASGI server, no reverse-proxy or TLS config. `python app.py` starts Flask's
built-in development server, which is appropriate for local, single-user, offline use — not for
public hosting. Deploying it would require adding a WSGI server and process supervision of your
choosing; that path has not been implemented or verified here.

---

## Testing

```bash
python -m pytest -q
```

Targeted subsets (faster):

```bash
python -m pytest tests/test_api.py -q -k "dashboard"
python -m pytest tests/test_agent.py -q
```

Developer smoke scripts:

```bash
python _verify_core.py     # in-process: dataset, metrics and agent sanity
python _verify_spike.py    # in-process: baseline vs 19:00 spike predictions
python _verify_http.py     # over HTTP: pages, static assets, API contract (server must be running)
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: No module named 'flask'` | Dependencies installed outside the venv, or venv not activated. Activate it and re-run `pip install -r requirements.txt`. |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then activate again. |
| `[Errno 98] Address already in use` / `[WinError 10048]` | Something already listens on 5000. Stop it (`netstat -ano \| findstr :5000`, then `taskkill /PID <pid> /F`), or change `port=` in `app.py`. |
| Dashboard shows "The dashboard could not be loaded" | `/api/dashboard` failed. Check the server console for the traceback; make sure the server is still running. |
| First page load is slow | Cold start trains the RandomForest the first time. `data/model.pkl` is reused afterwards. |
| `PermissionError: [WinError 5] Access is denied` from joblib/scikit-learn | scikit-learn's parallel `predict` opens OS pipes. Restricted sandboxes/CI containers can block that; run the app outside the sandbox, or on a host that permits pipe creation. |
| Charts do not render | `static/vendor/chartjs/chart.umd.js` missing or blocked; it is a local file — verify it is present and served with a 200. |
| Numbers look stale after a scenario toggle | A stale tab served by an older process; restart `python app.py` (templates are cached per process) and hard-reload. |
| Model metrics look different after deleting `data/` | Expected: the dataset generator is seeded and the model split is chronological, so values should reproduce; check `data/metrics.json`. |

---

## Verified facts and limitations

- Verified on this machine: Python 3.14.7, Flask 3.1.3, numpy 2.5.5, pandas 3.1.1,
  scikit-learn 1.9.1, joblib 1.6.0, pytest 9.2.0.
- Verified endpoints: `/`, `/app`, `/api/dashboard`, `/api/simulate-spike`, `/api/health`.
- No lockfile is committed; `requirements.txt` uses minimum-version ranges. Pin versions in your
  environment if you need bit-for-bit reproducibility.
- All figures are **modelled from synthetic data**. Nothing here is a live utility feed, real
  household measurement, or device control: the agent's actions are dry-run records and no MQTT,
  OCPP or device API is contacted.
