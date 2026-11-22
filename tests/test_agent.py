"""Tests for the EnergySense autonomous agent.

Run with either::

    python -m pytest tests/test_agent.py -q
    python -m unittest discover -s tests -v

The suite deliberately depends only on ``agent.py``.  Tariff-dependent
assertions read the same profiles the agent used via ``agent.get_hourly_profiles``
so the tests pass both before and after the backend's ``tariffs.py`` lands.
"""

from __future__ import annotations

import json
import math
import random
import re
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import agent  # noqa: E402  (path setup must happen first)

CITY = "Bengaluru"
OTHER_CITIES = ("Mumbai", "Delhi")
PEAK_HOURS = set(range(17, 22))
# Safety windows asserted independently of the module's internal constants.
GEYSER_SAFE_HOURS = {5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 22, 23}
EV_SAFE_HOURS = {0, 1, 2, 3, 4, 5, 10, 11, 12, 13, 14, 15, 16, 22, 23}


def normal_profile() -> list[float]:
    """A plausible weekday home: morning geyser, evening cooling/cooking."""
    values = [0.35] * 24
    for hour in (6, 7):
        values[hour] += 1.6
    for hour in (18, 19, 20, 21):
        values[hour] += 1.1
    values[19] += 1.4
    return [round(value, 4) for value in values]


def spike_profile() -> list[float]:
    """Normal home plus the backend's simulated +3.5 kWh EV event at 19:00."""
    values = normal_profile()
    values[19] += 3.5
    return values


def flat_profile(level: float = 0.5) -> list[float]:
    return [level] * 24


def morning_profile() -> list[float]:
    values = [0.30] * 24
    values[6] += 2.0
    values[7] += 2.0
    return [round(value, 4) for value in values]


def total(profile) -> float:
    return math.fsum(profile)


def peak_energy(profile) -> float:
    return math.fsum(profile[hour] for hour in PEAK_HOURS)


def defer_logs(result) -> list[dict]:
    return [
        entry
        for entry in result["agent_logs"]
        if entry["payload"].get("action") == "DEFER"
    ]


def estimate_map(result) -> dict[str, float]:
    return {item["id"]: item["daily_kwh"] for item in result["appliance_estimates"]}


def fake_tariffs(price, carbon) -> types.ModuleType:
    module = types.ModuleType("tariffs")
    module.hourly_profiles = lambda city: {  # noqa: ARG005 - contract signature
        "tariff_inr_per_kwh": list(price),
        "carbon_kg_per_kwh": list(carbon),
    }
    return module


def flat_price() -> list[float]:
    return [8.0] * 24


def flat_carbon(value: float = 0.5) -> list[float]:
    return [value] * 24


class InputValidationTests(unittest.TestCase):
    def test_accepts_24_hour_sequences(self):
        for profile in (flat_profile(), tuple(flat_profile()), list(range(24))):
            result = agent.optimize_schedule(profile, CITY)
            self.assertEqual(len(result["optimized_kwh"]), 24)

    def test_accepts_city_case_insensitively(self):
        for city in ("bengaluru", "BENGALURU", " Bengaluru "):
            result = agent.optimize_schedule(flat_profile(), city)
            self.assertEqual(len(result["optimized_kwh"]), 24)

    def test_rejects_wrong_length(self):
        for length in (0, 1, 12, 23, 25, 48):
            with self.assertRaises(ValueError):
                agent.optimize_schedule([0.1] * length, CITY)

    def test_rejects_negative_hourly_values(self):
        profile = flat_profile()
        profile[9] = -0.01
        with self.assertRaises(ValueError):
            agent.optimize_schedule(profile, CITY)

    def test_rejects_non_numeric_values(self):
        for bad in ("0.4", None, {}, [], True, False):
            profile = flat_profile()
            profile[3] = bad
            with self.assertRaises(ValueError):
                agent.optimize_schedule(profile, CITY)

    def test_rejects_non_finite_values(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            profile = flat_profile()
            profile[5] = bad
            with self.assertRaises(ValueError):
                agent.optimize_schedule(profile, CITY)

    def test_rejects_non_sequence_baseline(self):
        for bad in (None, 42, 3.14, "0.5" * 24, b"0" * 24, object()):
            with self.assertRaises(ValueError):
                agent.optimize_schedule(bad, CITY)

    def test_rejects_mapping_and_unordered_inputs(self):
        with self.assertRaises(ValueError):
            agent.optimize_schedule(
                {hour: 0.5 for hour in range(24)}, CITY
            )
        with self.assertRaises(ValueError):
            agent.optimize_schedule(set(range(24)), CITY)

    def test_rejects_absurd_total_energy(self):
        # Guards against float overflow producing NaN/Inf in JSON.
        for profile in ([1e9] * 24, [1e307] * 24, [1e308] * 24):
            with self.assertRaises(ValueError):
                agent.optimize_schedule(profile, CITY)

    def test_rejects_unknown_city(self):
        for city in ("Chennai", "", "bengaluruu", None, 17):
            with self.assertRaises(ValueError):
                agent.optimize_schedule(flat_profile(), city)

    def test_public_estimate_helper_validates(self):
        with self.assertRaises(ValueError):
            agent.estimate_appliances([0.2] * 23)


class ApplianceEstimationTests(unittest.TestCase):
    def test_contract_shape_and_stable_ids(self):
        result = agent.optimize_schedule(normal_profile(), CITY)
        estimates = result["appliance_estimates"]
        self.assertEqual(
            [item["id"] for item in estimates], ["fridge", "geyser", "ev", "ac"]
        )
        for item in estimates:
            self.assertEqual(
                set(item), {"id", "name", "daily_kwh", "confidence"}
            )
            self.assertTrue(item["name"])
            self.assertEqual(item["confidence"], "heuristic")
            self.assertIsInstance(item["daily_kwh"], float)
            self.assertGreaterEqual(item["daily_kwh"], 0.0)

    def test_combined_estimates_never_exceed_total_energy(self):
        rng = random.Random(20240607)
        profiles = [
            normal_profile(),
            spike_profile(),
            flat_profile(),
            flat_profile(0.0),
            morning_profile(),
        ]
        for _ in range(15):
            profiles.append(
                [round(rng.uniform(0.0, 3.0), 3) for _ in range(24)]
            )
        for profile in profiles:
            result = agent.optimize_schedule(profile, CITY)
            allocated = math.fsum(
                item["daily_kwh"] for item in result["appliance_estimates"]
            )
            self.assertLessEqual(allocated, total(profile) + 1e-9)

    def test_flat_profile_is_attributed_to_protected_base(self):
        result = agent.optimize_schedule(flat_profile(0.5), CITY)
        estimates = estimate_map(result)
        self.assertAlmostEqual(estimates["fridge"], 12.0, places=6)
        self.assertEqual(estimates["geyser"], 0.0)
        self.assertEqual(estimates["ev"], 0.0)
        self.assertEqual(estimates["ac"], 0.0)

    def test_public_helper_matches_optimizer_output(self):
        profile = normal_profile()
        direct = agent.estimate_appliances(profile)
        via_agent = agent.optimize_schedule(profile, CITY)["appliance_estimates"]
        self.assertEqual(direct, via_agent)

    def test_morning_geyser_signature(self):
        result = agent.optimize_schedule(morning_profile(), CITY)
        estimates = estimate_map(result)
        self.assertGreater(estimates["geyser"], 1.0)
        self.assertEqual(estimates["ev"], 0.0)

    def test_evening_spike_is_attributed_to_ev_only_as_assumption(self):
        result = agent.optimize_schedule(spike_profile(), CITY)
        estimates = estimate_map(result)
        self.assertGreater(estimates["ev"], 2.0)
        self.assertTrue(
            any(
                entry["payload"].get("device") == "ev"
                and entry["payload"].get("assumption") is True
                for entry in result["agent_logs"]
            )
        )

    def test_no_spike_means_no_ev_for_normal_home(self):
        result = agent.optimize_schedule(normal_profile(), CITY)
        self.assertEqual(estimate_map(result)["ev"], 0.0)

    def test_estimates_are_city_independent(self):
        profile = spike_profile()
        first = estimate_map(agent.optimize_schedule(profile, CITY))
        for city in OTHER_CITIES:
            self.assertEqual(
                estimate_map(agent.optimize_schedule(profile, city)), first
            )


class ConservationTests(unittest.TestCase):
    def _profiles(self):
        rng = random.Random(7)
        profiles = [
            normal_profile(),
            spike_profile(),
            flat_profile(),
            flat_profile(0.0),
            morning_profile(),
            [0.0] * 23 + [5.0],
            [8.0] + [0.0] * 23,
        ]
        for _ in range(20):
            profile = [round(rng.uniform(0.0, 4.0), 3) for _ in range(24)]
            profile[rng.randrange(24)] += rng.uniform(0.5, 6.0)
            profiles.append(profile)
        return profiles

    def test_energy_is_moved_not_deleted(self):
        for profile in self._profiles():
            result = agent.optimize_schedule(profile, CITY)
            optimized = result["optimized_kwh"]
            self.assertEqual(len(optimized), 24)
            self.assertTrue(all(value >= 0.0 for value in optimized))
            self.assertAlmostEqual(total(optimized), total(profile), places=9)

    def test_flat_profile_is_preserved_exactly(self):
        profile = flat_profile(0.5)
        result = agent.optimize_schedule(profile, CITY)
        self.assertEqual(result["optimized_kwh"], profile)

    def test_all_zero_profile_stays_zero(self):
        profile = [0.0] * 24
        result = agent.optimize_schedule(profile, CITY)
        self.assertEqual(result["optimized_kwh"], [0.0] * 24)
        self.assertEqual(result["savings"]["daily_inr"], 0.0)
        self.assertIsNone(result["savings"]["plug_payback_months"])

    def test_destination_load_limit_is_respected(self):
        profile = spike_profile()
        result = agent.optimize_schedule(profile, CITY)
        cap = max(4.0, max(profile) * 1.15)
        for hour, value in enumerate(result["optimized_kwh"]):
            self.assertLessEqual(value, cap + 1e-9, msg=f"hour {hour}")


class SchedulingTests(unittest.TestCase):
    def test_spike_scenario_shifts_ev_out_of_peak(self):
        profile = spike_profile()
        result = agent.optimize_schedule(profile, CITY)
        optimized = result["optimized_kwh"]

        self.assertLess(peak_energy(optimized), peak_energy(profile) - 1.0)
        self.assertGreater(result["savings"]["peak_shift_pct"], 0.0)
        self.assertGreater(result["savings"]["daily_inr"], 0.0)

        ev_logs = [
            entry for entry in defer_logs(result)
            if entry["payload"]["device"] == "ev"
        ]
        self.assertTrue(ev_logs)
        self.assertTrue(all(entry["protocol"] == "SIMULATED_OCPP" for entry in ev_logs))
        ev_actuator = next(
            item for item in result["actuators"] if item["id"] == "ev"
        )
        self.assertEqual(ev_actuator["status"], "Deferred")
        self.assertTrue(ev_actuator["shiftable"])

    def test_spike_scenario_differs_from_normal_scenario(self):
        normal_result = agent.optimize_schedule(normal_profile(), CITY)
        spike_result = agent.optimize_schedule(spike_profile(), CITY)
        self.assertNotEqual(
            normal_result["optimized_kwh"], spike_result["optimized_kwh"]
        )
        self.assertGreater(
            estimate_map(spike_result)["ev"], estimate_map(normal_result)["ev"]
        )
        self.assertGreater(
            spike_result["savings"]["peak_shift_pct"],
            normal_result["savings"]["peak_shift_pct"],
        )

    def test_normal_scenario_shifts_water_heating(self):
        result = agent.optimize_schedule(normal_profile(), CITY)
        geyser_logs = [
            entry for entry in defer_logs(result)
            if entry["payload"]["device"] == "geyser"
        ]
        self.assertTrue(geyser_logs)
        self.assertTrue(
            all(entry["protocol"] == "SIMULATED_MQTT" for entry in geyser_logs)
        )
        geyser = next(
            item for item in result["actuators"] if item["id"] == "geyser"
        )
        self.assertEqual(geyser["status"], "Deferred")

    def test_transfers_only_leave_the_peak_band(self):
        for profile in (normal_profile(), spike_profile()):
            result = agent.optimize_schedule(profile, CITY)
            for entry in defer_logs(result):
                payload = entry["payload"]
                self.assertIn(payload["from_hour"], PEAK_HOURS)
                self.assertNotIn(payload["resume_hour"], PEAK_HOURS)
                self.assertGreater(payload["kwh"], 0.0)
                self.assertTrue(payload["dry_run"])

    def test_destinations_are_safer_and_cheaper_or_cleaner(self):
        for profile in (normal_profile(), spike_profile()):
            result = agent.optimize_schedule(profile, CITY)
            profiles = agent.get_hourly_profiles(CITY)
            price = profiles["tariff_inr_per_kwh"]
            carbon = profiles["carbon_kg_per_kwh"]
            for entry in defer_logs(result):
                payload = entry["payload"]
                device = payload["device"]
                destination = payload["resume_hour"]
                source = payload["from_hour"]
                safe = GEYSER_SAFE_HOURS if device == "geyser" else EV_SAFE_HOURS
                self.assertIn(destination, safe)
                self.assertLessEqual(price[destination], price[source] + 1e-9)
                if abs(price[destination] - price[source]) <= 1e-9:
                    self.assertLess(carbon[destination], carbon[source])
                self.assertLessEqual(carbon[destination], carbon[source] + 1e-9)

    def test_only_flexible_appliances_are_scheduled(self):
        result = agent.optimize_schedule(spike_profile(), CITY)
        self.assertTrue(
            all(
                entry["payload"]["device"] in {"geyser", "ev"}
                for entry in defer_logs(result)
            )
        )
        actuators = {item["id"]: item for item in result["actuators"]}
        self.assertEqual(actuators["fridge"]["status"], "Locked ON")
        self.assertFalse(actuators["fridge"]["shiftable"])
        self.assertFalse(actuators["ac"]["shiftable"])
        self.assertEqual(actuators["geyser"]["control_type"], "Simulated 16A Smart Plug")
        self.assertEqual(actuators["ac"]["status"], "Recommendation Only")

    def test_protected_base_floor_is_never_breached(self):
        for profile in (normal_profile(), spike_profile(), morning_profile()):
            result = agent.optimize_schedule(profile, CITY)
            floor = min(min(profile[1:6]), 0.75)
            for hour, value in enumerate(result["optimized_kwh"]):
                self.assertGreaterEqual(
                    value, floor - 1e-9, msg=f"hour {hour} fell below base load"
                )

    def test_unusual_profile_never_drops_below_its_protected_load(self):
        # A profile can legitimately have daytime hours below the overnight
        # floor.  The guarantee is that no hour is reduced below the smaller
        # of its own load and the protected floor, and that non-peak hours are
        # never reduced at all.
        rng = random.Random(99)
        profile = [round(rng.uniform(0.0, 1.0), 3) for _ in range(24)]
        profile[3] += 5.5
        profile[16] += 5.0
        profile[18] += 2.4
        floor = min(min(profile[1:6]), 0.75)
        result = agent.optimize_schedule(profile, CITY)
        for hour, value in enumerate(result["optimized_kwh"]):
            self.assertGreaterEqual(value, min(profile[hour], floor) - 1e-9)
            if hour not in PEAK_HOURS:
                self.assertGreaterEqual(value, profile[hour] - 1e-9)

    def test_zero_shift_scenario_is_stable(self):
        profile = flat_profile(0.5)
        result = agent.optimize_schedule(profile, CITY)
        savings = result["savings"]
        self.assertEqual(result["optimized_kwh"], profile)
        self.assertEqual(savings["daily_inr"], 0.0)
        self.assertEqual(savings["monthly_inr"], 0.0)
        self.assertEqual(savings["monthly_co2_kg"], 0.0)
        self.assertEqual(savings["peak_shift_pct"], 0.0)
        self.assertIsNone(savings["plug_payback_months"])
        self.assertEqual(defer_logs(result), [])
        self.assertTrue(
            any(
                entry["payload"].get("reason") == "no_eligible_flex"
                for entry in result["agent_logs"]
            )
        )

    def test_infeasible_destination_preserves_load_and_logs_constraint(self):
        # Every hour costs the same and has the same carbon intensity, so no
        # destination is cheaper or cleaner: the agent must refuse to move.
        module = fake_tariffs(flat_price(), flat_carbon())
        with mock.patch.dict(sys.modules, {"tariffs": module}):
            profile = normal_profile()
            result = agent.optimize_schedule(profile, CITY)
        self.assertEqual(result["optimized_kwh"], profile)
        self.assertEqual(result["savings"]["daily_inr"], 0.0)
        self.assertIsNone(result["savings"]["plug_payback_months"])
        self.assertEqual(defer_logs(result), [])
        self.assertTrue(
            any(
                entry["level"] == "WARN"
                and entry["payload"].get("reason") == "no_destination"
                for entry in result["agent_logs"]
            )
        )

    def test_equal_price_cleaner_destination_shifts_carbon_not_cost(self):
        price = flat_price()
        carbon = [0.7] * 24
        for hour in range(9, 17):
            carbon[hour] = 0.2
        module = fake_tariffs(price, carbon)
        with mock.patch.dict(sys.modules, {"tariffs": module}):
            profile = normal_profile()
            result = agent.optimize_schedule(profile, CITY)

        self.assertTrue(defer_logs(result))
        self.assertEqual(result["savings"]["daily_inr"], 0.0)
        self.assertEqual(result["savings"]["monthly_inr"], 0.0)
        self.assertGreater(result["savings"]["monthly_co2_kg"], 0.0)
        self.assertIsNone(result["savings"]["plug_payback_months"])
        self.assertLess(peak_energy(result["optimized_kwh"]), peak_energy(profile))

    def test_cheaper_but_dirtier_destination_is_rejected(self):
        # Hour 13 is cheaper but much dirtier than the peak.  Shifting there
        # would improve cost while worsening carbon, so the agent must refuse.
        price = flat_price()
        price[13] = 5.0
        carbon = flat_carbon(0.5)
        carbon[13] = 0.95
        module = fake_tariffs(price, carbon)
        with mock.patch.dict(sys.modules, {"tariffs": module}):
            profile = normal_profile()
            result = agent.optimize_schedule(profile, CITY)

        self.assertEqual(result["optimized_kwh"], profile)
        self.assertEqual(result["savings"]["daily_inr"], 0.0)
        self.assertEqual(result["savings"]["monthly_co2_kg"], 0.0)
        self.assertEqual(defer_logs(result), [])
        self.assertTrue(
            any(
                entry["level"] == "WARN"
                and entry["payload"].get("reason") == "no_destination"
                for entry in result["agent_logs"]
            )
        )

    def test_ev_detection_does_not_claim_cycle_interruption(self):
        result = agent.optimize_schedule(spike_profile(), CITY)
        detection = next(
            entry
            for entry in result["agent_logs"]
            if entry["payload"].get("device") == "ev"
            and entry["payload"].get("action") == "DETECT"
        )
        self.assertTrue(detection["payload"]["schedules_future_block"])
        self.assertFalse(detection["payload"]["interrupts_running_cycle"])
        self.assertEqual(
            detection["payload"]["planning_horizon"], "day-ahead schedule"
        )

    def test_late_night_spike_is_not_shifted_out_of_peak(self):
        # A 22:00 spike is outside the expensive window; the agent must not
        # pretend it is a schedulable peak-hour block.
        profile = flat_profile(0.4)
        profile[22] += 4.0
        result = agent.optimize_schedule(profile, CITY)
        self.assertEqual(estimate_map(result)["ev"], 0.0)
        self.assertEqual(defer_logs(result), [])
        self.assertEqual(result["optimized_kwh"], profile)

    def test_shifted_kwh_equals_peak_reduction(self):
        for profile in (normal_profile(), spike_profile()):
            result = agent.optimize_schedule(profile, CITY)
            shifted = math.fsum(entry["payload"]["kwh"] for entry in defer_logs(result))
            reduction = peak_energy(profile) - peak_energy(result["optimized_kwh"])
            self.assertAlmostEqual(shifted, reduction, places=6)


class FinancialTests(unittest.TestCase):
    def test_cost_savings_arithmetic(self):
        profile = normal_profile()
        result = agent.optimize_schedule(profile, CITY)
        price = agent.get_hourly_profiles(CITY)["tariff_inr_per_kwh"]
        baseline_cost = math.fsum(
            profile[hour] * price[hour] for hour in range(24)
        )
        optimized_cost = math.fsum(
            result["optimized_kwh"][hour] * price[hour] for hour in range(24)
        )
        savings = result["savings"]

        self.assertAlmostEqual(
            savings["daily_baseline_cost_inr"], baseline_cost, places=9
        )
        self.assertAlmostEqual(
            savings["daily_optimized_cost_inr"], optimized_cost, places=9
        )
        self.assertAlmostEqual(
            savings["daily_inr"], baseline_cost - optimized_cost, places=9
        )
        self.assertAlmostEqual(
            savings["monthly_inr"], savings["daily_inr"] * 30, places=9
        )
        self.assertGreaterEqual(savings["daily_inr"], 0.0)

    def test_payback_arithmetic_when_savings_exist(self):
        result = agent.optimize_schedule(spike_profile(), CITY)
        monthly = result["savings"]["monthly_inr"]
        self.assertGreater(monthly, 0.0)
        self.assertAlmostEqual(
            result["savings"]["plug_payback_months"],
            agent.PLUG_COST_INR / monthly,
            places=9,
        )

    def test_payback_is_null_without_savings(self):
        for profile in (flat_profile(0.5), [0.0] * 24):
            result = agent.optimize_schedule(profile, CITY)
            self.assertEqual(result["savings"]["monthly_inr"], 0.0)
            self.assertIsNone(result["savings"]["plug_payback_months"])

    def test_carbon_arithmetic(self):
        profile = spike_profile()
        result = agent.optimize_schedule(profile, CITY)
        carbon = agent.get_hourly_profiles(CITY)["carbon_kg_per_kwh"]
        baseline_carbon = math.fsum(
            profile[hour] * carbon[hour] for hour in range(24)
        )
        optimized_carbon = math.fsum(
            result["optimized_kwh"][hour] * carbon[hour] for hour in range(24)
        )
        self.assertAlmostEqual(
            result["savings"]["monthly_co2_kg"],
            (baseline_carbon - optimized_carbon) * 30,
            places=6,
        )
        self.assertGreaterEqual(result["savings"]["monthly_co2_kg"], 0.0)

    def test_peak_shift_percentage_arithmetic(self):
        profile = spike_profile()
        result = agent.optimize_schedule(profile, CITY)
        expected = (
            (peak_energy(profile) - peak_energy(result["optimized_kwh"]))
            / peak_energy(profile)
            * 100.0
        )
        self.assertAlmostEqual(
            result["savings"]["peak_shift_pct"], expected, places=9
        )

    def test_savings_keys_are_exact(self):
        result = agent.optimize_schedule(normal_profile(), CITY)
        self.assertEqual(
            set(result["savings"]),
            {
                "daily_inr",
                "monthly_inr",
                "monthly_co2_kg",
                "peak_shift_pct",
                "plug_payback_months",
                "daily_baseline_cost_inr",
                "daily_optimized_cost_inr",
            },
        )

    def test_no_energy_savings_are_claimed(self):
        result = agent.optimize_schedule(spike_profile(), CITY)
        summary = next(
            entry
            for entry in result["agent_logs"]
            if entry["payload"].get("action") == "SUMMARY"
        )
        self.assertIn("shifted", summary["message"].lower())
        self.assertNotIn("kwh saved", summary["message"].lower())
        self.assertIn("shifted, not reduced", summary["payload"]["note"])
        self.assertAlmostEqual(
            total(result["optimized_kwh"]), total(spike_profile()), places=9
        )


class AgentLogTests(unittest.TestCase):
    HOUR_PATTERN = re.compile(r"^\d{2}:00$")

    def test_logs_are_structured_and_json_safe(self):
        for profile in (normal_profile(), spike_profile(), flat_profile()):
            result = agent.optimize_schedule(profile, CITY)
            self.assertTrue(result["agent_logs"])
            json.dumps(result, allow_nan=False)
            for entry in result["agent_logs"]:
                self.assertEqual(
                    set(entry), {"hour", "level", "protocol", "message", "payload"}
                )
                self.assertRegex(entry["hour"], self.HOUR_PATTERN)
                self.assertIn(entry["level"], {"INFO", "WARN", "DEBUG"})
                self.assertIn(
                    entry["protocol"],
                    {"AGENT", "SIMULATED_MQTT", "SIMULATED_OCPP"},
                )
                self.assertIsInstance(entry["message"], str)
                self.assertTrue(entry["message"])
                self.assertIsInstance(entry["payload"], dict)

    def test_protocols_match_the_device(self):
        result = agent.optimize_schedule(spike_profile(), CITY)
        for entry in defer_logs(result):
            expected = (
                "SIMULATED_OCPP"
                if entry["payload"]["device"] == "ev"
                else "SIMULATED_MQTT"
            )
            self.assertEqual(entry["protocol"], expected)

    def test_actions_are_dry_run_records(self):
        result = agent.optimize_schedule(spike_profile(), CITY)
        for entry in defer_logs(result):
            self.assertTrue(entry["payload"]["dry_run"])
            self.assertNotIn("acknowledged", entry["payload"])
            self.assertNotIn("device_response", entry["payload"])

    def test_summary_reports_conservation(self):
        result = agent.optimize_schedule(spike_profile(), CITY)
        summary = next(
            entry
            for entry in result["agent_logs"]
            if entry["payload"].get("action") == "SUMMARY"
        )
        self.assertTrue(summary["payload"]["energy_conserved"])
        self.assertGreater(summary["payload"]["shifted_kwh"], 0.0)

    def test_spike_log_documents_the_simulation_assumption(self):
        result = agent.optimize_schedule(spike_profile(), CITY)
        detection = next(
            entry
            for entry in result["agent_logs"]
            if entry["payload"].get("device") == "ev"
            and entry["payload"].get("action") == "DETECT"
        )
        self.assertIn("simulation assumption", detection["message"].lower())
        self.assertTrue(detection["payload"]["assumption"])

    def test_summary_documents_the_plug_cost_assumption(self):
        result = agent.optimize_schedule(spike_profile(), CITY)
        summary = next(
            entry
            for entry in result["agent_logs"]
            if entry["payload"].get("action") == "SUMMARY"
        )
        self.assertEqual(
            summary["payload"]["plug_cost_inr"], agent.PLUG_COST_INR
        )
        self.assertIn("illustrative", summary["payload"]["payback_basis"])

    def test_logs_are_deterministic(self):
        profile = spike_profile()
        first = json.dumps(
            agent.optimize_schedule(profile, CITY)["agent_logs"], sort_keys=True
        )
        for _ in range(3):
            again = json.dumps(
                agent.optimize_schedule(profile, CITY)["agent_logs"],
                sort_keys=True,
            )
            self.assertEqual(first, again)


class ContractTests(unittest.TestCase):
    def test_top_level_contract(self):
        result = agent.optimize_schedule(normal_profile(), CITY)
        self.assertEqual(
            set(result),
            {
                "optimized_kwh",
                "savings",
                "actuators",
                "agent_logs",
                "appliance_estimates",
            },
        )

    def test_actuator_schema(self):
        result = agent.optimize_schedule(spike_profile(), CITY)
        self.assertEqual(
            [item["id"] for item in result["actuators"]],
            ["fridge", "geyser", "ev", "ac"],
        )
        allowed_status = {
            "Locked ON",
            "Deferred",
            "Active",
            "Recommendation Only",
        }
        for item in result["actuators"]:
            self.assertEqual(
                set(item),
                {
                    "id",
                    "name",
                    "control_type",
                    "status",
                    "scheduled_window",
                    "shiftable",
                },
            )
            self.assertIn(item["status"], allowed_status)
            self.assertIsInstance(item["shiftable"], bool)

    def test_no_load_actuators_use_documented_statuses(self):
        result = agent.optimize_schedule(flat_profile(0.5), CITY)
        for item in result["actuators"]:
            self.assertIn(
                item["status"],
                {"Locked ON", "Deferred", "Active", "Recommendation Only"},
            )
        self.assertEqual(result["actuators"][0]["status"], "Locked ON")

    def test_json_serializable_full_result(self):
        payload = agent.optimize_schedule(spike_profile(), CITY)
        encoded = json.dumps(payload, allow_nan=False)
        self.assertIn("optimized_kwh", encoded)

    def test_repeated_execution_is_identical(self):
        profile = spike_profile()
        first = agent.optimize_schedule(profile, CITY)
        for _ in range(4):
            self.assertEqual(agent.optimize_schedule(profile, CITY), first)

    def test_all_cities_run_and_expose_valid_profiles(self):
        for city in (CITY,) + OTHER_CITIES:
            profiles = agent.get_hourly_profiles(city)
            self.assertEqual(len(profiles["tariff_inr_per_kwh"]), 24)
            self.assertEqual(len(profiles["carbon_kg_per_kwh"]), 24)
            self.assertTrue(all(value >= 0 for value in profiles["tariff_inr_per_kwh"]))
            self.assertTrue(all(value >= 0 for value in profiles["carbon_kg_per_kwh"]))
            result = agent.optimize_schedule(normal_profile(), city)
            self.assertAlmostEqual(
                total(result["optimized_kwh"]),
                total(normal_profile()),
                places=9,
            )

    def test_external_tariffs_module_is_used(self):
        module = fake_tariffs([3.0] * 24, [0.1] * 24)
        with mock.patch.dict(sys.modules, {"tariffs": module}):
            result = agent.optimize_schedule(normal_profile(), CITY)
        self.assertAlmostEqual(
            result["savings"]["daily_baseline_cost_inr"],
            total(normal_profile()) * 3.0,
            places=9,
        )
        self.assertEqual(result["savings"]["daily_inr"], 0.0)

    def test_builtin_fallback_uses_documented_bands(self):
        broken = types.ModuleType("tariffs")  # no hourly_profiles provider
        with mock.patch.dict(sys.modules, {"tariffs": broken}):
            for city, base in (
                ("Bengaluru", 8.0),
                ("Mumbai", 9.2),
                ("Delhi", 7.5),
            ):
                profiles = agent.get_hourly_profiles(city)
                price = profiles["tariff_inr_per_kwh"]
                carbon = profiles["carbon_kg_per_kwh"]
                self.assertAlmostEqual(price[0], base, places=6)
                self.assertAlmostEqual(price[9], base * 0.80, places=6)
                self.assertAlmostEqual(price[17], base * 1.20, places=6)
                self.assertLess(carbon[9], carbon[0])
                self.assertLess(carbon[0], carbon[17])

    def test_malformed_external_profile_falls_back_safely(self):
        module = types.ModuleType("tariffs")
        module.hourly_profiles = lambda city: {  # noqa: ARG005
            "tariff_inr_per_kwh": [1.0] * 23,
            "carbon_kg_per_kwh": [0.1] * 24,
        }
        with mock.patch.dict(sys.modules, {"tariffs": module}):
            profiles = agent.get_hourly_profiles(CITY)
            result = agent.optimize_schedule(flat_profile(), CITY)
        self.assertEqual(len(profiles["tariff_inr_per_kwh"]), 24)
        self.assertEqual(result["optimized_kwh"], flat_profile())


if __name__ == "__main__":
    unittest.main(verbosity=2)
