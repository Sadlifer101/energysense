"""RandomForest forecasting model for EnergySense.

Trains on the synthetic smart-meter dataset, persists the fitted model and
genuine held-out metrics, and produces recursive 24-hour forecasts with
correctly updated autoregressive lag features.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score

import data_gen

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CSV_PATH = DATA_DIR / "smart_meter.csv"
MODEL_PATH = DATA_DIR / "model.pkl"
METRICS_PATH = DATA_DIR / "metrics.json"

FEATURES = [
    "hour",
    "day_of_week",
    "temp_c",
    "is_weekend",
    "lag_1h_kwh",
    "lag_24h_kwh",
]
TARGET = "total_kwh"

ALGORITHM = "RandomForestRegressor"
N_ESTIMATORS = 100
RANDOM_STATE = 42
TEST_FRACTION = 0.20

# Simplified scenario knob shared with app.py: an extra EV session at 19:00.
SPIKE_HOUR = 19
SPIKE_KWH = 3.5

_CACHE: dict[str, Any] = {}


def _load_frame() -> pd.DataFrame:
    if not CSV_PATH.exists():
        data_gen.generate_data(CSV_PATH)
    frame = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
    missing = [c for c in [TARGET, *FEATURES] if c not in frame.columns]
    if missing:
        raise ValueError(f"Dataset is missing required columns: {missing}")
    return frame


def train_model() -> dict[str, Any]:
    """Train the RandomForest on a chronological 80/20 split and persist it.

    Returns the metrics dictionary. Also writes data/model.pkl and
    data/metrics.json.
    """
    frame = _load_frame()

    split_index = int(round(len(frame) * (1.0 - TEST_FRACTION)))
    train = frame.iloc[:split_index]
    test = frame.iloc[split_index:]

    x_train = train[FEATURES]
    y_train = train[TARGET]
    x_test = test[FEATURES]
    y_test = test[TARGET]

    regressor = RandomForestRegressor(
        n_estimators=N_ESTIMATORS,
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    regressor.fit(x_train, y_train)

    predictions = regressor.predict(x_test)
    r2 = float(r2_score(y_test, predictions))
    mae = float(mean_absolute_error(y_test, predictions))

    metrics: dict[str, Any] = {
        "algorithm": ALGORITHM,
        "r2": round(r2, 6),
        "mae_kwh": round(mae, 6),
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "dataset_rows": int(len(frame)),
        "n_estimators": N_ESTIMATORS,
        "random_state": RANDOM_STATE,
        "features": list(FEATURES),
        "target": TARGET,
        "split": "chronological 80/20, shuffle=False",
        "dataset": "synthetic",
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spike_scenario": {"hour": SPIKE_HOUR, "extra_kwh": SPIKE_KWH},
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    bundle = {
        "model": regressor,
        "features": list(FEATURES),
        "target": TARGET,
        "metrics": metrics,
    }
    joblib.dump(bundle, MODEL_PATH)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    _CACHE.clear()
    _CACHE["bundle"] = bundle
    return metrics


def load_bundle() -> dict[str, Any]:
    """Load the persisted model bundle, training it first if necessary."""
    bundle = _CACHE.get("bundle")
    if bundle is not None:
        return bundle
    if not MODEL_PATH.exists():
        train_model()
        return _CACHE["bundle"]
    try:
        bundle = joblib.load(MODEL_PATH)
    except Exception:
        train_model()
        return _CACHE["bundle"]
    _CACHE["bundle"] = bundle
    return bundle


def load_metrics() -> dict[str, Any]:
    """Return the persisted metrics, training first if they are absent."""
    metrics = _CACHE.get("metrics")
    if metrics is not None:
        return metrics
    if not METRICS_PATH.exists():
        train_model()
        return _CACHE["metrics"]
    try:
        metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    except Exception:
        train_model()
        return _CACHE["metrics"]
    _CACHE["metrics"] = metrics
    return metrics


def model_summary() -> dict[str, Any]:
    """The `model` block of the canonical API response."""
    metrics = load_metrics()
    return {
        "algorithm": str(metrics.get("algorithm", ALGORITHM)),
        "r2": float(metrics.get("r2", 0.0)),
        "mae_kwh": float(metrics.get("mae_kwh", 0.0)),
        "train_rows": int(metrics.get("train_rows", 0)),
        "test_rows": int(metrics.get("test_rows", 0)),
    }


def _future_temperature_by_hour(frame: pd.DataFrame) -> dict[int, float]:
    """Reproducible future-temperature assumption: hour-of-day climatology."""
    table = frame.groupby("hour")["temp_c"].mean().to_dict()
    overall = float(frame["temp_c"].mean())
    return {hour: float(table.get(hour, overall)) for hour in range(24)}


def predict_next_24h(spike_injected: bool = False) -> list[float]:
    """Forecast the next 24 hours (00:00-23:00) as nonnegative kWh values.

    Autoregressive features are updated with each produced prediction, so
    lag_1h_kwh is always the previous step's value (observed or predicted)
    and lag_24h_kwh is the value 24 hours earlier.
    """
    bundle = load_bundle()
    regressor = bundle["model"]
    features = bundle.get("features", FEATURES)

    frame = _load_frame()
    temperatures = _future_temperature_by_hour(frame)

    history: list[float] = [float(v) for v in frame[TARGET].tolist()]
    last_timestamp = pd.Timestamp(frame["timestamp"].iloc[-1])
    if len(history) < 24:
        raise ValueError("Not enough history to compute 24h lag features.")

    predictions: list[float] = []
    for step in range(1, 25):
        timestamp = last_timestamp + timedelta(hours=step)
        hour = int(timestamp.hour)
        day_of_week = int(timestamp.weekday())
        is_weekend = 1 if day_of_week >= 5 else 0
        row = pd.DataFrame(
            [
                {
                    "hour": hour,
                    "day_of_week": day_of_week,
                    "temp_c": temperatures[hour],
                    "is_weekend": is_weekend,
                    "lag_1h_kwh": history[-1],
                    "lag_24h_kwh": history[-24],
                }
            ]
        )[features]

        value = float(regressor.predict(row)[0])

        # Scenario assumption: an extra 3.5 kWh EV-charging event at 19:00.
        if spike_injected and hour == SPIKE_HOUR:
            value += SPIKE_KWH

        value = float(max(value, 0.0))
        predictions.append(round(value, 4))
        history.append(predictions[-1])

    return predictions


def ensure_artifacts() -> dict[str, Any]:
    """Create data/smart_meter.csv and trained artifacts when absent."""
    if not CSV_PATH.exists():
        data_gen.generate_data(CSV_PATH)
    if not MODEL_PATH.exists() or not METRICS_PATH.exists():
        return train_model()
    return load_metrics()


if __name__ == "__main__":
    summary = ensure_artifacts()
    print(json.dumps(summary, indent=2))
    print("next 24h:", predict_next_24h())
