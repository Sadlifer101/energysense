"""Synthetic smart-meter dataset generator for EnergySense.

Generates 30 days (720 hourly rows) of believable household electricity
consumption plus the lag features required by the forecasting model.

The data is entirely synthetic. It is produced locally with a fixed random
seed so that every run yields byte-comparable values.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CSV_PATH = DATA_DIR / "smart_meter.csv"

SEED = 42
DAYS = 30
PRE_HISTORY_HOURS = 24  # extra history so lag_24h is defined on row 0
START = datetime(2025, 1, 1, 0, 0, 0)  # first retained observation (00:00)

COLUMNS = [
    "timestamp",
    "hour",
    "day_of_week",
    "temp_c",
    "is_weekend",
    "lag_1h_kwh",
    "lag_24h_kwh",
    "total_kwh",
]


def _gauss(hour: float, center: float, sigma: float) -> float:
    """Gaussian bump used to shape appliance usage windows."""
    return math.exp(-0.5 * ((hour - center) / sigma) ** 2)


def _gauss_circular(hour: float, center: float, sigma: float) -> float:
    """Gaussian bump that wraps around midnight."""
    direct = _gauss(hour, center, sigma)
    wrapped = _gauss(hour - 24.0, center, sigma) + _gauss(hour + 24.0, center, sigma)
    return max(direct, wrapped)


def _raw_series() -> pd.DataFrame:
    """Build the continuous hourly series, including pre-history rows."""
    rng = np.random.default_rng(SEED)

    total_hours = DAYS * 24 + PRE_HISTORY_HOURS
    total_days = total_hours // 24
    start = START - timedelta(hours=PRE_HISTORY_HOURS)

    day_drift = rng.normal(0.0, 0.9, size=total_days + 2)

    rows = []
    for i in range(total_hours):
        ts = start + timedelta(hours=i)
        hour = ts.hour
        dow = ts.weekday()
        weekend = 1 if dow >= 5 else 0
        day_index = i // 24

        # --- temperature: daily cycle peaking mid-afternoon + slow drift ---
        daily = 3.2 * math.sin(2 * math.pi * (hour - 9) / 24.0)
        seasonal = 2.0 * math.sin(2 * math.pi * day_index / 30.0)
        temp = 28.5 + daily + seasonal + float(day_drift[day_index])
        temp += rng.normal(0.0, 0.25)
        temp = float(np.clip(temp, 17.0, 43.0))

        # --- always-on: refrigerator cycling + standby/base load ---
        base = 0.30 + 0.045 * math.sin(2 * math.pi * hour / 5.2)
        fridge = 0.055 * math.sin(2 * math.pi * hour / 3.1 + 0.7) + rng.normal(0.0, 0.018)

        # --- morning geyser (electric water heater) ---
        geyser = 0.85 * _gauss(hour, 6.6, 0.85)

        # --- evening air-conditioning, stronger on hotter days ---
        ac_intensity = 0.32 + 0.075 * max(temp - 26.0, 0.0)
        ac_intensity = min(ac_intensity, 1.15)
        ac_window = _gauss_circular(hour, 19.8, 1.9)
        ac = ac_intensity * ac_window

        # --- washing machine / dryer: weekly laundry routine ---
        wash = 0.0
        if dow == 5 and hour == 11:
            wash = 0.55
        elif dow == 2 and hour == 10:
            wash = 0.45

        # --- EV charging: overnight top-up on weeknights, weekend evening ---
        ev = 0.0
        if not weekend and hour == 22 and rng.random() < 0.85:
            ev = round(float(rng.uniform(2.6, 3.4)), 2)
        elif weekend and hour == 19 and rng.random() < 0.30:
            ev = round(float(rng.uniform(1.4, 2.2)), 2)

        # --- weekend daytime occupancy ---
        weekend_load = 0.14 if (weekend and 10 <= hour <= 16) else 0.0

        # --- lighting bump in the evening ---
        lighting = 0.10 * _gauss_circular(hour, 20.0, 2.4)

        demand = (
            base
            + fridge
            + geyser
            + ac
            + wash
            + ev
            + weekend_load
            + lighting
        )
        demand += rng.normal(0.0, 0.05)
        demand = float(max(demand, 0.08))

        rows.append(
            {
                "timestamp": ts,
                "hour": hour,
                "day_of_week": dow,
                "temp_c": round(temp, 2),
                "is_weekend": weekend,
                "total_kwh": round(demand, 4),
            }
        )

    return pd.DataFrame(rows)


def generate_data(csv_path: Path | str | None = None) -> pd.DataFrame:
    """Generate data/smart_meter.csv and return the 720-row frame.

    The returned frame starts at START (00:00) and ends at 23:00 on day 30,
    so the next 24 hours after the dataset are exactly 00:00-23:00.
    """
    path = Path(csv_path) if csv_path is not None else CSV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    raw = _raw_series()

    # Lag features from the continuous series (pre-history included), then keep
    # exactly 720 usable rows that have no missing values.
    raw["lag_1h_kwh"] = raw["total_kwh"].shift(1)
    raw["lag_24h_kwh"] = raw["total_kwh"].shift(24)

    usable = raw.dropna(subset=["lag_1h_kwh", "lag_24h_kwh"]).copy()
    usable = usable.tail(DAYS * 24).reset_index(drop=True)
    usable = usable[COLUMNS]

    usable["timestamp"] = usable["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")

    usable.to_csv(path, index=False)
    return usable


if __name__ == "__main__":
    frame = generate_data()
    print(f"Wrote {len(frame)} rows to {CSV_PATH}")
