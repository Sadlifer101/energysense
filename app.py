"""EnergySense Flask backend.

Serves the two page routes and the JSON API described by the shared
architecture contract. Everything runs locally and offline: the synthetic
dataset, the trained model and the optimization agent all live in this
project directory.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Iterable

from flask import Flask, jsonify, render_template, request
from jinja2 import TemplateNotFound
from werkzeug.exceptions import HTTPException

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import model as ml_model  # noqa: E402
import tariffs  # noqa: E402

HOURS = [f"{hour:02d}:00" for hour in range(24)]

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
# Preserve the canonical, contract-defined key order in JSON responses.
app.json.sort_keys = False

_AGENT: dict[str, Any] = {"module": None}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _error(message: str, status: int) -> tuple[Any, int]:
    return jsonify({"error": message, "status": status}), status


def _parse_bool(value: Any, default: bool = False, field: str = "flag") -> bool:
    """Parse booleans from query strings and JSON bodies."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "":
            return default
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off"}:
            return False
    raise ValueError(
        f"Field '{field}' must be a boolean (true/false), received {value!r}."
    )


def _get_agent():
    """Return Chat 02's agent module when it is available.

    Negative lookups are not cached so a concurrently created agent.py is
    picked up without restarting the server.
    """
    module = _AGENT.get("module")
    if module is not None:
        return module
    try:
        candidate = importlib.import_module("agent")
    except Exception:
        return None
    if not callable(getattr(candidate, "optimize_schedule", None)):
        return None
    _AGENT["module"] = candidate
    return candidate


def _daily_cost(values: Iterable[float], prices: list[float]) -> float:
    return round(sum(float(v) * float(p) for v, p in zip(values, prices)), 2)


def _coerce_series(
    value: Any, length: int, fallback: list[float]
) -> tuple[list[float], bool]:
    """Coerce an hourly series to `length` nonnegative floats."""
    if not isinstance(value, (list, tuple)) or len(value) != length:
        return list(fallback), False
    series: list[float] = []
    for item in value:
        try:
            series.append(round(max(float(item), 0.0), 4))
        except (TypeError, ValueError):
            return list(fallback), False
    return series, True


_SAVINGS_KEYS = (
    "daily_inr",
    "monthly_inr",
    "monthly_co2_kg",
    "peak_shift_pct",
    "plug_payback_months",
    "daily_baseline_cost_inr",
    "daily_optimized_cost_inr",
)


def _coerce_savings(
    value: Any, baseline: list[float], optimized: list[float], prices: list[float]
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    result: dict[str, Any] = {}
    for key in _SAVINGS_KEYS:
        if key == "plug_payback_months":
            item = raw.get(key)
            if item is None:
                result[key] = None
                continue
            try:
                result[key] = round(float(item), 2)
            except (TypeError, ValueError):
                result[key] = None
            continue
        if key in raw:
            try:
                result[key] = round(float(raw[key]), 2)
            except (TypeError, ValueError):
                result[key] = 0.0
            continue
        # Genuine backend calculations for fields the optimizer omits.
        if key == "daily_baseline_cost_inr":
            result[key] = _daily_cost(baseline, prices)
        elif key == "daily_optimized_cost_inr":
            result[key] = _daily_cost(optimized, prices)
        else:
            result[key] = 0.0
    return result


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _fallback_optimization(baseline: list[float], prices: list[float]) -> dict[str, Any]:
    """Degraded response used only when Chat 02's optimizer is unavailable.

    This is not an optimizer: consumption is unchanged and no savings are
    claimed. It keeps the API contract valid during cross-chat development.
    """
    return {
        "optimized_kwh": list(baseline),
        "savings": {
            "daily_inr": 0.0,
            "monthly_inr": 0.0,
            "monthly_co2_kg": 0.0,
            "peak_shift_pct": 0.0,
            "plug_payback_months": None,
            "daily_baseline_cost_inr": _daily_cost(baseline, prices),
            "daily_optimized_cost_inr": _daily_cost(baseline, prices),
        },
        "actuators": [],
        "agent_logs": [
            {
                "timestamp": HOURS[0],
                "level": "warn",
                "message": (
                    "Optimization agent (agent.py) is not available; returning the "
                    "unoptimized baseline schedule."
                ),
            }
        ],
        "appliance_estimates": [],
        "agent_available": False,
    }


def _run_optimizer(
    baseline: list[float], city: str, prices: list[float]
) -> dict[str, Any]:
    """Delegate scheduling to Chat 02's agent.optimize_schedule()."""
    agent = _get_agent()
    if agent is None:
        return _fallback_optimization(baseline, prices)

    try:
        result = agent.optimize_schedule(list(baseline), city)
    except Exception as exc:  # pragma: no cover - defensive integration guard
        fallback = _fallback_optimization(baseline, prices)
        fallback["agent_logs"] = [
            {
                "timestamp": HOURS[0],
                "level": "error",
                "message": f"Optimization agent failed ({type(exc).__name__}: {exc}). "
                "Returning the unoptimized baseline schedule.",
            }
        ]
        return fallback

    if not isinstance(result, dict):
        return _fallback_optimization(baseline, prices)

    optimized, valid = _coerce_series(
        result.get("optimized_kwh"), len(baseline), baseline
    )
    return {
        "optimized_kwh": optimized,
        "savings": _coerce_savings(result.get("savings"), baseline, optimized, prices),
        "actuators": _as_list(result.get("actuators")),
        "agent_logs": _as_list(result.get("agent_logs")),
        "appliance_estimates": _as_list(result.get("appliance_estimates")),
        "agent_available": True,
        "optimized_valid": valid,
    }


def _assumptions(city: str, spike: bool) -> list[str]:
    metrics = ml_model.load_metrics()
    notes = [
        "Consumption data is synthetic (data_gen.py, seed 42, 30 days / 720 hourly rows) - no real meter data is used.",
        f"Forecast: RandomForestRegressor trained on {metrics.get('dataset_rows', 720)} synthetic rows and evaluated on a chronological 80/20 hold-out split.",
        "Future temperatures use the dataset's hour-of-day temperature climatology as a reproducible, offline assumption.",
        "Tariffs are illustrative ideathon assumptions, not verified official domestic tariffs.",
        "Carbon intensity per kWh is an illustrative scenario value and does not represent measured grid carbon intensity.",
        "All computation happens locally and offline; no remote API calls are made.",
        "Appliance attribution and scheduling come from the local optimization agent.",
    ]
    tariff = tariffs.get_city_tariff(city)
    notes.append(
        f"{tariff['city']}: illustrative base tariff INR {tariff['base_inr_per_kwh']:.2f}/kWh, "
        f"peak {tariff['peak_surcharge_pct']:.0f}% surcharge, solar {tariff['solar_discount_pct']:.0f}% discount."
    )
    if spike:
        notes.append(
            "Scenario: an extra 3.5 kWh EV-charging event at 19:00 is added to the baseline. "
            "This is a simulated demonstration event, not a detected real event."
        )
    return notes


def build_dashboard(city: str, spike: bool) -> dict[str, Any]:
    """Assemble the canonical dashboard response entirely from real computation."""
    canonical_city = tariffs.resolve_city(city)

    baseline = ml_model.predict_next_24h(spike_injected=spike)
    baseline = [round(max(float(v), 0.0), 4) for v in baseline]
    if len(baseline) != 24:
        raise RuntimeError(
            f"Forecast must contain 24 hourly values, received {len(baseline)}."
        )

    profiles = tariffs.hourly_profiles(canonical_city)
    prices = profiles["tariff_inr_per_kwh"]
    carbon = profiles["carbon_kg_per_kwh"]

    optimization = _run_optimizer(baseline, canonical_city, prices)

    return {
        "meta": {
            "city": canonical_city,
            "scenario": "spike" if spike else "normal",
            "dataset": "synthetic",
            "offline": True,
            "tariff_type": "illustrative",
        },
        "model": ml_model.model_summary(),
        "hours": list(HOURS),
        "baseline_kwh": baseline,
        "optimized_kwh": optimization["optimized_kwh"],
        "tariff_inr_per_kwh": prices,
        "carbon_kg_per_kwh": carbon,
        "savings": optimization["savings"],
        "actuators": optimization["actuators"],
        "agent_logs": optimization["agent_logs"],
        "appliance_estimates": optimization["appliance_estimates"],
        "assumptions": _assumptions(canonical_city, spike),
    }


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------
_PLACEHOLDER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>EnergySense</title>
<style>body{{font-family:system-ui,sans-serif;margin:0;min-height:100vh;display:grid;
place-items:center;background:#0b0b0c;color:#f2efe9}}
main{{max-width:34rem;padding:3rem;line-height:1.6}}
code{{background:#1b1b1e;padding:.15rem .4rem;border-radius:.25rem}}</style></head>
<body><main><h1>EnergySense</h1>
<p>The backend API is running. The front-end view <code>templates/{name}</code>
has not been added to this project yet.</p>
<p>Try the JSON API: <code>/api/dashboard?city=Bengaluru&amp;spike=false</code></p>
</main></body></html>"""


def _render_page(name: str):
    try:
        return render_template(name)
    except TemplateNotFound:
        return _PLACEHOLDER.format(name=name), 200


@app.get("/")
def index():
    return _render_page("index.html")


@app.get("/app")
def dashboard_page():
    return _render_page("dashboard.html")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/dashboard")
def api_dashboard():
    try:
        city = tariffs.resolve_city(request.args.get("city"))
        spike = _parse_bool(request.args.get("spike"), default=False, field="spike")
    except ValueError as exc:
        return _error(str(exc), 400)
    return jsonify(build_dashboard(city, spike))


@app.post("/api/simulate-spike")
def api_simulate_spike():
    payload = request.get_json(silent=True)
    if payload is None:
        return _error("Malformed request body: expected a JSON object.", 400)
    if not isinstance(payload, dict):
        return _error("Malformed request body: expected a JSON object.", 400)

    try:
        city = tariffs.resolve_city(payload.get("city"))
        enabled_value = payload.get("enabled", payload.get("spike"))
        enabled = _parse_bool(enabled_value, default=False, field="enabled")
    except ValueError as exc:
        return _error(str(exc), 400)

    return jsonify(build_dashboard(city, enabled))


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------
@app.errorhandler(HTTPException)
def _handle_http_error(exc: HTTPException):
    if request.path.startswith("/api/"):
        return _error(exc.description or exc.name, exc.code or 500)
    return exc


@app.errorhandler(Exception)
def _handle_unexpected_error(exc: Exception):  # pragma: no cover - safety net
    if request.path.startswith("/api/"):
        return _error(f"Internal server error: {exc}", 500)
    raise exc


@app.get("/api/health")
def api_health():
    metrics = ml_model.load_metrics()
    return jsonify(
        {
            "status": "ok",
            "offline": True,
            "dataset": "synthetic",
            "agent_available": _get_agent() is not None,
            "model": ml_model.model_summary(),
            "cities": list(tariffs.CITIES),
            "artifacts": {
                "dataset": ml_model.CSV_PATH.exists(),
                "model": ml_model.MODEL_PATH.exists(),
                "metrics": ml_model.METRICS_PATH.exists(),
            },
        }
    )


# ---------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------
def ensure_setup() -> dict[str, Any]:
    """Generate the dataset and train the model on first start."""
    return ml_model.ensure_artifacts()


if __name__ == "__main__":
    summary = ensure_setup()
    print(
        "EnergySense backend ready | dataset=synthetic | "
        f"rows={summary.get('dataset_rows')} | "
        f"R2={summary.get('r2')} | MAE={summary.get('mae_kwh')} kWh | offline"
    )
    print("Dashboard API: http://127.0.0.1:5000/api/dashboard?city=Bengaluru&spike=false")
    app.run(host="127.0.0.1", port=5000, debug=False)
