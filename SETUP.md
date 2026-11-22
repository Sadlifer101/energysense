# Developer Setup — EnergySense

A from-scratch guide for reproducing the EnergySense development environment. Every command
below was checked against this repository; anything that could not be verified locally is marked
explicitly.

**What you end up with:** a local Flask server on `http://127.0.0.1:5000` serving the landing page,
the dashboard, and the JSON API, backed by a synthetic dataset and a locally trained model. No
external services, no credentials, no database.

---

## 1. Verified environment

Recorded on the machine this repository was prepared on:

| Component | Version | How it was checked |
|---|---|---|
| OS | Windows 10/11 x64 | `python --version`, project run |
| Python | **3.14.7** | `python --version` |
| Git | 2.54.0 | `git --version` |
| Flask | 3.1.3 | `import flask` |
| numpy | 2.5.5 | `import numpy` |
| pandas | 3.1.1 | `import pandas` |
| scikit-learn | 1.9.1 | `import sklearn` |
| joblib | 1.6.0 | `import joblib` |
| pytest | 9.2.0 | `import pytest` |
| GitHub CLI | 2.x | only needed to push, not to run |

Supported in principle wherever the above wheels exist: Python **3.11–3.14** on Windows, macOS or
Linux. Versions older than 3.11 are untested and may not have current pandas/scikit-learn wheels.

---

## 2. Operating-system considerations

| OS | Shell | Notes |
|---|---|---|
| Windows 10/11 | PowerShell or cmd | Activation paths differ (`.venv\Scripts\...`). PowerShell may block the activation script; see Troubleshooting. |
| macOS | zsh / bash | `python3 -m venv`. Apple's system Python is not used — install 3.11+ separately if needed. |
| Linux | bash | `python3 -m venv`; install `python3-venv` if missing on Debian/Ubuntu. |

Path handling in the code is `pathlib`-based, so no path rewriting is required per OS. The server
binds `127.0.0.1:5000` only (not `0.0.0.0`), so nothing is exposed to the network.

---

## 3. Runtime installation

### Windows

1. Install Python 3.11+ from python.org (tick "Add python.exe to PATH"), or:
   ```powershell
   winget install Python.Python.3.14
   ```
2. Verify:
   ```powershell
   python --version
   py --version
   ```

### macOS

```bash
brew install python@3.13
python3 --version
```

### Linux (Debian/Ubuntu)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
python3 --version
```

### Git

Windows: `winget install Git.Git` (or git-scm.com). macOS: `xcode-select --install` or
`brew install git`. Linux: `sudo apt install git`. Verify with `git --version`.

---

## 4. Clone the project

```bash
git clone https://github.com/Sadlifer101/energysense.git
cd energysense
```

---

## 5. Create the virtual environment

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

Confirm the prompt shows `(.venv)` and that `python -c "import sys; print(sys.prefix)"` points into
the project's `.venv` directory.

---

## 6. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` is the only dependency manifest (there is no `package.json`, no lockfile, no
Poetry/pipenv file). It contains the runtime set plus `pytest` for the test suite:

```
Flask>=3.0,<4.0
numpy>=1.24
pandas>=2.0
scikit-learn>=1.3
joblib>=1.3
pytest>=7.4
```

**Checkpoint:**

```bash
python -c "import flask, numpy, pandas, sklearn, joblib, pytest; print('deps ok')"
```

---

## 7. Configuration

**No environment variables and no `.env` are required** — verified: no `os.environ`, `os.getenv`
or `dotenv` usage exists in the codebase. `.env.example` exists only to document this.

Nothing else needs configuring: there is no database, no message broker, no API key, no external
service. Optional knobs are module constants, not settings:

| Knob | File | Current value |
|---|---|---|
| Host / port | `app.py` (`app.run(...)` in `__main__`) | `127.0.0.1`, `5000` |
| Cities | `tariffs.py` (`CITIES`, per-city tariff/carbon profiles) | Bengaluru, Mumbai, Delhi |
| Spike assumption | `model.py` (`SPIKE_HOUR`, `SPIKE_KWH`) | 19:00, 3.5 kWh |
| Dataset seed/size | `data_gen.py` | 720 hourly rows over 30 days |

---

## 8. Backend initialization (dataset + model)

The app self-initialises: `python app.py` calls `model.ensure_artifacts()`, which generates
`data/smart_meter.csv` and trains `data/model.pkl` when they are missing. To do it ahead of time:

```bash
python -c "import model; print(model.ensure_artifacts())"
```

Equivalent entries:

```bash
python model.py       # trains if needed, prints metrics + next-24h forecast
python data_gen.py    # writes data/smart_meter.csv only
```

Expected artifacts after this step:

```
data/smart_meter.csv   720 rows (30 contiguous days, hourly)
data/model.pkl         RandomForestRegressor bundle
data/metrics.json      held-out metrics, e.g. R2 ≈ 0.708, MAE ≈ 0.122 kWh
```

All three are git-ignored build outputs; deleting `data/` is safe and simply forces regeneration.

---

## 9. Run locally

```bash
python app.py
```

```
EnergySense backend ready | dataset=synthetic | rows=720 | R2=0.708334 | MAE=0.121892 kWh | offline
Dashboard API: http://127.0.0.1:5000/api/dashboard?city=Bengaluru&spike=false
 * Running on http://127.0.0.1:5000
```

| URL | What it is |
|---|---|
| http://127.0.0.1:5000/ | Landing page |
| http://127.0.0.1:5000/app | Dashboard (loads data from `/api/dashboard`) |
| http://127.0.0.1:5000/api/health | Health, artifacts, cities |

Stop with `Ctrl+C`. Reload with `python app.py` again. Jinja templates are cached per process, so
restart the server after editing `templates/*.html`.

---

## 10. Verify the installation

**Health check** (PowerShell):

```powershell
Invoke-WebRequest http://127.0.0.1:5000/api/health -UseBasicParsing | Select-Object -ExpandProperty Content
```

**Health check** (macOS / Linux):

```bash
curl -s http://127.0.0.1:5000/api/health
```

**Full developer smoke test** (server running):

```bash
python _verify_http.py
```

Expected tail: all `PASS` lines, including `GET /`, `GET /app`, every static asset and the API
contract keys.

Other scripts:

```bash
python _verify_core.py    # dataset/model/agent sanity, in-process
python _verify_spike.py   # baseline vs spike predictions, in-process
```

---

## 11. Tests

The suite uses pytest and needs no server and no configuration:

```bash
python -m pytest -q
```

Focused runs:

```bash
python -m pytest tests/test_api.py -q       # dataset, model, API contract
python -m pytest tests/test_agent.py -q     # optimisation agent, savings arithmetic
python -m pytest tests/test_api.py -q -k "dashboard"
```

Notes:

- Tests import `app`, `model`, `data_gen`, `tariffs`, `agent` directly and reuse the artifacts in
  `data/`; the first run may retrain the model if artifacts are absent.
- The model split is chronological and the dataset generator is seeded, so results are
  reproducible.
- No pytest config file, markers, fixtures directory or `conftest.py` exists.

---

## 12. Build verification

There is **no front-end build**: templates, CSS, JavaScript, fonts, images and Chart.js are served
from `static/` and `templates/` as written. "Verifying the build" means verifying the static
assets are present and served:

```bash
python _verify_http.py
```

That script checks each asset referenced by the pages (landing CSS/JS, dashboard CSS/JS, Chart.js,
fonts, WebP images) returns HTTP 200 with a non-empty body.

For the Python side, the equivalent "build" is the artifact step in §8; a successful
`model.ensure_artifacts()` plus a green `pytest` run is the full build verification.

---

## 13. Troubleshooting

| Symptom | Diagnosis and fix |
|---|---|
| `No module named flask` / `numpy` / `sklearn` | The venv is not active, or `pip install -r requirements.txt` was run against another interpreter. Activate `.venv`, then `python -m pip install -r requirements.txt`. |
| PowerShell: `Activate.ps1 cannot be loaded because running scripts is disabled` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then `.\.venv\Scripts\Activate.ps1`. |
| `pip` builds numpy/pandas from source and fails | The interpreter has no matching wheel. Use Python 3.11–3.14, 64-bit, and a recent pip (`python -m pip install -U pip`). |
| `Address already in use` / `WinError 10048` | Port 5000 is taken. Windows: `netstat -ano \| findstr :5000` then `taskkill /PID <pid> /F`; macOS/Linux: `lsof -i :5000`. Or edit the `port=` argument in `app.py`. |
| Dashboard shows "The dashboard could not be loaded" | `/api/dashboard` returned an error. Read the server console; check the payload with `curl "http://127.0.0.1:5000/api/dashboard?city=Bengaluru&spike=false"`. |
| First page load is slow, later ones fast | Cold start trains the RandomForest. Subsequent runs load `data/model.pkl`. |
| `PermissionError: [WinError 5] Access is denied` inside joblib/scikit-learn | scikit-learn's parallel predict opens OS pipes; some sandboxes/containers block that. Run outside the sandbox or on a normal host. |
| `KeyError`/`ValueError` about dataset columns | `data/smart_meter.csv` is stale or hand-edited. Delete `data/` and rerun `python -c "import model; model.ensure_artifacts()"`. |
| Charts blank, other UI fine | `static/vendor/chartjs/chart.umd.js` missing/not served; confirm `http://127.0.0.1:5000/static/vendor/chartjs/chart.umd.js`. |
| Edited HTML not reflected | Templates are cached per process; restart `python app.py` and hard-reload the browser. |
| Motion appears instant rather than animated | `prefers-reduced-motion: reduce` is set in the OS/browser, or the window's frame clock is suspended (background/occluded window). Both are intentional fallbacks. |

---

## 14. Verified vs. requires external access

| Step | Status |
|---|---|
| Python/venv/dependency install | **Verified locally** |
| Dataset + model generation | **Verified locally** (`data/metrics.json`, R² ≈ 0.708) |
| Running the server and all five routes | **Verified locally** |
| Test suite, smoke scripts | **Verified locally** |
| Front end, assets, motion behaviour | **Verified locally** in a browser |
| Cloning this public repository | **Verified**: anonymous `git clone --depth 1` into an empty directory gave 42 files |
| Cold start from the clone | **Verified**: `model.ensure_artifacts()` reproduced `rows=720`, R² ≈ 0.708, MAE ≈ 0.122 kWh |
| Running the cloned tree | **Verified**: `/`, `/app`, `/api/health`, `/api/dashboard?city=Mumbai&spike=true` all returned `200` on a spare port |
| Brand-new OS install | **Not verified** — the clone test reused this machine's Python and installed packages |
| Pushing to GitHub | Requires GitHub authentication (`gh auth login` or a personal access token with `repo` scope); not needed to run the app |
| Production/public deployment | **Not supported in this repository** (no WSGI server, Dockerfile or proxy config) |
