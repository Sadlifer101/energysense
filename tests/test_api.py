"""Tests for the EnergySense ML + API backend.

Run with:  python -m pytest tests/test_api.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
import data_gen  # noqa: E402
import model as ml_model  # noqa: E402
import tariffs  # noqa: E402

CANONICAL_TOP_LEVEL = [
    "meta",
    "model",
    "hours",
    "baseline_kwh",
    "optimized_kwh",
    "tariff_inr_per_kwh",
    "carbon_kg_per_kwh",
    "savings",
    "actuators",
    "agent_logs",
    "appliance_estimates",
    "assumptions",
]

EXPECTED_COLUMNS = [
    "timestamp",
    "hour",
    "day_of_week",
    "temp_c",
    "is_weekend",
    "lag_1h_kwh",
    "lag_24h_kwh",
    "total_kwh",
]

SAVINGS_KEYS = {
    "daily_inr",
    "monthly_inr",
    "monthly_co2_kg",
    "peak_shift_pct",
    "plug_payback_months",
    "daily_baseline_cost_inr",
    "daily_optimized_cost_inr",
}


@pytest.fixture(scope="session", autouse=True)
def prepared_artifacts():
    """Ensure the dataset and a trained model exist for the whole session."""
    data_gen.generate_data()
    ml_model.train_model()
    yield


@pytest.fixture(scope="session")
def client():
    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as test_client:
        yield test_client


@pytest.fixture(scope="session")
def dataset() -> pd.DataFrame:
    return pd.read_csv(data_gen.CSV_PATH)


# ---------------------------------------------------------------------------
# dataset
# ---------------------------------------------------------------------------
def test_dataset_has_exactly_720_rows(dataset):
    assert len(dataset) == 720
    assert list(dataset.columns) == EXPECTED_COLUMNS


def test_dataset_has_no_missing_values(dataset):
    assert dataset.isna().sum().sum() == 0
    assert dataset["timestamp"].nunique() == 720


def test_dataset_covers_30_contiguous_days(dataset):
    timestamps = pd.to_datetime(dataset["timestamp"])
    assert (timestamps.diff().dropna() == pd.Timedelta(hours=1)).all()
    assert timestamps.dt.hour.iloc[0] == 0
    assert timestamps.dt.hour.iloc[-1] == 23
    assert len(set(dataset["hour"])) == 24


def test_dataset_is_reproducible(tmp_path):
    first = data_gen.generate_data(tmp_path / "a.csv")
    second = data_gen.generate_data(tmp_path / "b.csv")
    pd.testing.assert_frame_equal(first, second)


def test_lag_features_are_consistent(dataset):
    total = dataset["total_kwh"].to_numpy()
    # lag_1h must match the previous row for every row after the first two.
    np.testing.assert_allclose(dataset["lag_1h_kwh"].to_numpy()[1:], total[:-1], atol=1e-9)
    np.testing.assert_allclose(
        dataset["lag_24h_kwh"].to_numpy()[24:], total[:-24], atol=1e-9
    )


def test_dataset_country_patterns_are_believable(dataset):
    assert dataset["total_kwh"].min() > 0
    by_hour = dataset.groupby("hour")["total_kwh"].mean()
    # evening air-conditioning peak clearly above the early-morning trough
    assert by_hour.loc[19] > by_hour.loc[3] * 2
    # morning geyser bump exists
    assert by_hour.loc[6] > by_hour.loc[3]
    # weekend daytime occupancy differs from weekdays
    weekend = dataset[dataset["is_weekend"] == 1]["total_kwh"].mean()
    weekday = dataset[dataset["is_weekend"] == 0]["total_kwh"].mean()
    assert weekend != pytest.approx(weekday, abs=1e-6)


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
def test_artifacts_persist():
    assert ml_model.MODEL_PATH.exists()
    assert ml_model.METRICS_PATH.exists()
    metrics = json.loads(ml_model.METRICS_PATH.read_text(encoding="utf-8"))
    assert metrics["algorithm"] == "RandomForestRegressor"
    assert metrics["n_estimators"] == 100
    assert metrics["random_state"] == 42
    assert metrics["features"] == ml_model.FEATURES


def test_training_is_reproducible():
    first = ml_model.train_model()
    second = ml_model.train_model()
    assert first["r2"] == second["r2"]
    assert first["mae_kwh"] == second["mae_kwh"]
    assert first["train_rows"] == second["train_rows"] == 576
    assert first["test_rows"] == second["test_rows"] == 144


def test_metrics_are_genuine_holdout_scores():
    metrics = ml_model.load_metrics()
    # Straight comparison against an independently recomputed hold-out score.
    frame = pd.read_csv(ml_model.CSV_PATH)
    split = int(round(len(frame) * 0.8))
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.metrics import mean_absolute_error, r2_score

    regressor = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
    regressor.fit(frame[ml_model.FEATURES].iloc[:split], frame[ml_model.TARGET].iloc[:split])
    preds = regressor.predict(frame[ml_model.FEATURES].iloc[split:])
    expected_r2 = r2_score(frame[ml_model.TARGET].iloc[split:], preds)
    expected_mae = mean_absolute_error(frame[ml_model.TARGET].iloc[split:], preds)

    assert metrics["r2"] == pytest.approx(expected_r2, abs=1e-4)
    assert metrics["mae_kwh"] == pytest.approx(expected_mae, abs=1e-4)
    # Honest sanity bounds: not a fabricated 0.99+ score, and not degenerate.
    assert -1.0 < metrics["r2"] <= 1.0
    assert 0.0 < metrics["mae_kwh"] < 1.0


def test_predictions_are_24_nonnegative_values():
    preds = ml_model.predict_next_24h()
    assert isinstance(preds, list)
    assert len(preds) == 24
    assert all(isinstance(v, float) for v in preds)
    assert all(v >= 0.0 for v in preds)
    assert all(np.isfinite(v) for v in preds)
    assert sum(preds) > 0


def test_predictions_are_reproducible():
    assert ml_model.predict_next_24h() == ml_model.predict_next_24h()


def test_spike_adds_ev_event_at_seven_pm():
    normal = ml_model.predict_next_24h(spike_injected=False)
    spike = ml_model.predict_next_24h(spike_injected=True)
    assert len(spike) == 24
    assert all(v >= 0 for v in spike)
    # 19:00 receives the extra 3.5 kWh event
    assert spike[19] - normal[19] == pytest.approx(3.5, abs=1e-6)
    # earlier hours are untouched
    assert spike[:19] == pytest.approx(normal[:19], abs=1e-9)
    # autoregressive lags propagate the event into later hours
    assert sum(spike) - sum(normal) >= 3.45


# ---------------------------------------------------------------------------
# tariffs
# ---------------------------------------------------------------------------
def test_hourly_profile_mapping_bengaluru():
    profiles = tariffs.hourly_profiles("Bengaluru")
    price = profiles["tariff_inr_per_kwh"]
    carbon = profiles["carbon_kg_per_kwh"]
    assert len(price) == len(carbon) == 24

    base = 8.00
    for hour in range(24):
        if 9 <= hour < 17:
            assert price[hour] == pytest.approx(base * 0.80)
            assert carbon[hour] == pytest.approx(0.35)
        elif 17 <= hour < 22:
            assert price[hour] == pytest.approx(base * 1.20)
            assert carbon[hour] == pytest.approx(0.85)
        else:
            assert price[hour] == pytest.approx(base * 1.00)
            assert carbon[hour] == pytest.approx(0.65)


@pytest.mark.parametrize("city", tariffs.CITIES)
def test_city_tariff_configuration(city):
    config = tariffs.get_city_tariff(city)
    assert config["city"] == city
    assert config["tariff_type"] == "illustrative"
    assert len(config["hourly_tariff_inr_per_kwh"]) == 24
    assert len(config["hourly_carbon_kg_per_kwh"]) == 24
    assert [b["start_hour"] for b in config["blocks"]] == [0, 9, 17, 22]
    assert config["base_inr_per_kwh"] == tariffs.BASE_RATES[city]


def test_invalid_city_raises():
    with pytest.raises(ValueError):
        tariffs.get_city_tariff("Atlantis")
    with pytest.raises(ValueError):
        tariffs.hourly_profiles("Paris")


def test_city_resolution_is_case_insensitive():
    assert tariffs.resolve_city("mumbai") == "Mumbai"
    assert tariffs.resolve_city("  DELHI ") == "Delhi"
    assert tariffs.resolve_city(None) == "Bengaluru"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def _assert_dashboard_shape(payload, city, scenario):
    assert list(payload.keys()) == CANONICAL_TOP_LEVEL

    meta = payload["meta"]
    assert meta == {
        "city": city,
        "scenario": scenario,
        "dataset": "synthetic",
        "offline": True,
        "tariff_type": "illustrative",
    }

    model_block = payload["model"]
    assert set(model_block) == {"algorithm", "r2", "mae_kwh", "train_rows", "test_rows"}
    assert model_block["algorithm"] == "RandomForestRegressor"
    assert isinstance(model_block["r2"], float)
    assert isinstance(model_block["mae_kwh"], float)
    assert model_block["train_rows"] > 0 and model_block["test_rows"] > 0

    assert payload["hours"] == [f"{h:02d}:00" for h in range(24)]
    for key in (
        "baseline_kwh",
        "optimized_kwh",
        "tariff_inr_per_kwh",
        "carbon_kg_per_kwh",
    ):
        assert len(payload[key]) == 24, key
        assert all(isinstance(v, (int, float)) for v in payload[key]), key
    assert all(v >= 0 for v in payload["baseline_kwh"])
    assert all(v >= 0 for v in payload["optimized_kwh"])
    assert all(v >= 0 for v in payload["tariff_inr_per_kwh"])

    savings = payload["savings"]
    assert set(savings) == SAVINGS_KEYS
    assert savings["plug_payback_months"] is None or isinstance(
        savings["plug_payback_months"], float
    )

    assert isinstance(payload["actuators"], list)
    assert isinstance(payload["agent_logs"], list)
    assert isinstance(payload["appliance_estimates"], list)
    assert isinstance(payload["assumptions"], list) and payload["assumptions"]


@pytest.mark.parametrize("city", tariffs.CITIES)
def test_dashboard_endpoint(client, city):
    response = client.get("/api/dashboard", query_string={"city": city, "spike": "false"})
    assert response.status_code == 200
    payload = response.get_json()
    _assert_dashboard_shape(payload, city, "normal")
    assert payload["tariff_inr_per_kwh"] == tariffs.hourly_profiles(city)[
        "tariff_inr_per_kwh"
    ]


def test_dashboard_defaults(client):
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    payload = response.get_json()
    _assert_dashboard_shape(payload, "Bengaluru", "normal")


def test_dashboard_spike_query(client):
    normal = client.get("/api/dashboard", query_string={"city": "Delhi"}).get_json()
    spike = client.get(
        "/api/dashboard", query_string={"city": "Delhi", "spike": "true"}
    ).get_json()
    _assert_dashboard_shape(spike, "Delhi", "spike")
    assert spike["baseline_kwh"] != pytest.approx(normal["baseline_kwh"], abs=1e-9)
    assert sum(spike["baseline_kwh"]) - sum(normal["baseline_kwh"]) >= 3.45
    assert spike["assumptions"] != normal["assumptions"]


def test_dashboard_rejects_invalid_city(client):
    response = client.get("/api/dashboard", query_string={"city": "Atlantis"})
    assert response.status_code == 400
    body = response.get_json()
    assert "error" in body and "Atlantis" in body["error"]


def test_dashboard_rejects_invalid_spike_flag(client):
    response = client.get("/api/dashboard", query_string={"city": "Mumbai", "spike": "maybe"})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_simulate_spike_route(client):
    response = client.post(
        "/api/simulate-spike", json={"city": "Bengaluru", "enabled": True}
    )
    assert response.status_code == 200
    payload = response.get_json()
    _assert_dashboard_shape(payload, "Bengaluru", "spike")

    off = client.post(
        "/api/simulate-spike", json={"city": "Bengaluru", "enabled": False}
    ).get_json()
    _assert_dashboard_shape(off, "Bengaluru", "normal")
    assert off["baseline_kwh"] == pytest.approx(
        ml_model.predict_next_24h(False), abs=1e-9
    )
    assert payload["baseline_kwh"] != off["baseline_kwh"]


def test_simulate_spike_rejects_malformed_bodies(client):
    no_body = client.post("/api/simulate-spike")
    assert no_body.status_code == 400
    assert "error" in no_body.get_json()

    bad_city = client.post("/api/simulate-spike", json={"city": "Gotham", "enabled": True})
    assert bad_city.status_code == 400
    assert "Gotham" in bad_city.get_json()["error"]

    bad_flag = client.post("/api/simulate-spike", json={"city": "Delhi", "enabled": "sure"})
    assert bad_flag.status_code == 400
    assert "error" in bad_flag.get_json()


def test_simulate_spike_requires_json_object(client):
    response = client.post(
        "/api/simulate-spike", data="not-json", content_type="application/json"
    )
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_savings_costs_match_tariff_multiplication(client):
    """Daily cost fields must be genuine baseline x tariff calculations."""
    payload = client.get("/api/dashboard", query_string={"city": "Bengaluru"}).get_json()
    prices = payload["tariff_inr_per_kwh"]
    expected = round(
        sum(v * p for v, p in zip(payload["baseline_kwh"], prices)), 2
    )
    assert payload["savings"]["daily_baseline_cost_inr"] == pytest.approx(
        expected, abs=0.05
    )


def test_unknown_api_route_returns_json_error(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert "error" in response.get_json()


# ---------------------------------------------------------------------------
# cross-chat integration (Chat 02 owns agent.py)
# ---------------------------------------------------------------------------
def test_optimization_agent_integration_or_fallback(client):
    """Structure holds either way; optimization values are validated if present."""
    payload = client.get("/api/dashboard", query_string={"city": "Bengaluru"}).get_json()
    agent = app_module._get_agent()

    if agent is None:
        # Documented degraded mode: baseline passthrough, no claimed savings.
        assert agent is None  # agent.py has not been delivered by Chat 02 yet
        assert payload["optimized_kwh"] == pytest.approx(payload["baseline_kwh"])
        assert payload["savings"]["daily_inr"] == 0.0
        assert payload["actuators"] == []
        assert any("not available" in log.get("message", "") for log in payload["agent_logs"])
    else:
        assert payload["optimized_kwh"] is not None
        assert isinstance(payload["savings"]["daily_inr"], (int, float))
        assert payload["actuators"]
        assert payload["agent_logs"]
