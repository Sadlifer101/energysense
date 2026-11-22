"""EnergySense autonomous appliance-estimation and load-shifting agent.

This module owns *only* the heuristic sub-metering and the deterministic
scheduling agent.  The Flask backend calls::

    optimize_schedule(baseline_kwh, city)

with 24 nonnegative hourly kWh values (index 0 == 00:00) and one of the three
supported cities.  The returned dictionary is the shared integration contract.

Design notes / assumptions
--------------------------
Appliance estimation is **heuristic disaggregation**, not trained or validated
non-intrusive load monitoring (NILM).  The application never measures an
individual appliance; it attributes aggregate load using time-of-day
signatures and documented assumptions:

* ``fridge``  -> overnight floor load (the minimum of 01:00-05:00) treated as
  always-on fridge/base consumption.  It is protected and never moved.
* ``geyser``  -> high morning excess (05:00-09:00) plus, only when no EV block
  is detected, 30 % of the evening excess (17:00-22:00).  The morning
  component is capped at 6 kWh/day and the evening component at 3 kWh/day.
  Evening water heating is shiftable only inside safe windows.
* ``ev``      -> a sharp evening spike (>= 2.0 kWh above the local evening
  baseline) attributed to an EV charging block.  The EV connection is a
  *scenario assumption*, never a measured fact; the block is capped at 7 kWh.
  The category also covers washing-machine energy, but a running wash cycle is
  never modelled as interruptible.
* ``ac``      -> weighted cooling-signature share of the remaining excess,
  capped at 12 kWh/day.  There is no validated thermal model, so AC is
  recommendation-only and is never rescheduled automatically.

Optimization rules
------------------
* Only flexible energy already attributed to the 17:00-22:00 peak band moves.
* Destinations must be outside the peak band, inside the appliance's safe
  window, no more expensive and no dirtier than the source, and below the
  hourly destination capacity (``max(4.0, peak * 1.15)`` kWh).
* Transfers are scheduling decisions for *future* eligible loads.  The agent
  does not interrupt a running appliance, does not control hardware and does
  not fake device acknowledgements; MQTT/OCPP payloads are dry-run records.
* Energy is conserved exactly: moving ``x`` kWh subtracts ``x`` from the source
  hour and adds ``x`` to the destination hour.  Nothing is deleted.
* If no feasible destination exists, the load is preserved and the constraint
  is logged instead of inventing an action.
* The agent produces a day-ahead schedule for *future* eligible blocks.  It has
  no clock and no device state, so it never claims to interrupt a running
  appliance or acknowledge a command.

Tariffs and carbon intensities come from ``tariffs.hourly_profiles(city)`` when
that sibling module is importable.  A built-in fallback mirrors the documented
illustrative bands so this module and its tests remain runnable on their own.
All tariff, carbon and plug-cost figures are demonstrative, not verified
utility values.

The plug-payback figure assumes an illustrative smart-plug acquisition cost of
``PLUG_COST_INR`` (INR 1,499).  If projected monthly savings are zero or
negative, payback is reported as ``None`` rather than a fabricated number.
"""

from __future__ import annotations

import importlib
import math
import numbers
import sys
from collections.abc import Mapping
from typing import Any

__all__ = [
    "CITIES",
    "PEAK_HOURS",
    "PLUG_COST_INR",
    "estimate_appliances",
    "get_hourly_profiles",
    "optimize_schedule",
]

HOURS_PER_DAY = 24
CITIES = ("Bengaluru", "Mumbai", "Delhi")

#: 17:00-22:00 peak band (hour indices 17..21 inclusive).
PEAK_HOURS = (17, 18, 19, 20, 21)
#: 09:00-17:00 solar band (hour indices 9..16 inclusive).
SOLAR_HOURS = tuple(range(9, 17))
#: 00:00-09:00 and 22:00-24:00 normal band.
NORMAL_HOURS = tuple(range(0, 9)) + (22, 23)

DAYS_PER_MONTH = 30
#: Illustrative smart-plug acquisition cost in INR (documented assumption).
PLUG_COST_INR = 1499.0

_EPS = 1e-9

_APPLIANCE_NAMES: dict[str, str] = {
    "fridge": "Fridge & base load",
    "geyser": "Water heater",
    "ev": "Washing machine / EV charging",
    "ac": "Air conditioner",
}

_APPLIANCE_ORDER = ("fridge", "geyser", "ev", "ac")

_CONTROL_TYPES: dict[str, str] = {
    "fridge": "Protected circuit (always on)",
    "geyser": "Simulated 16A Smart Plug",
    "ev": "Simulated OCPP 1.6J charge point",
    "ac": "No actuation (recommendation only)",
}

#: Hours into which a flexible appliance may be scheduled.  Peak hours are
#: deliberately absent: flexible energy only ever leaves the peak band.
_DESTINATION_WINDOWS: dict[str, frozenset[int]] = {
    # Morning/afternoon water heating and a late-evening fallback; never
    # overnight 23:00-05:00 and never the 17:00-22:00 peak.
    "geyser": frozenset({5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 22, 23}),
    # Solar daytime charging plus late-night/early-morning off-peak charging.
    "ev": frozenset({0, 1, 2, 3, 4, 5, 10, 11, 12, 13, 14, 15, 16, 22, 23}),
    "fridge": frozenset(),
    "ac": frozenset(),
}

#: Cooling-signature weight per hour.  Used only to keep the AC estimate
#: conservative; there is no thermal model behind it.
_AC_HOUR_WEIGHT: tuple[float, ...] = (
    0.55, 0.55, 0.55, 0.55, 0.55, 0.55,  # 00-05 overnight cooling
    0.15, 0.15, 0.15,                     # 06-08 morning
    0.30, 0.30, 0.30,                     # 09-11 daytime
    0.75, 0.75, 0.75, 0.75, 0.75,         # 12-16 hot afternoon
    0.55, 0.55, 0.55, 0.55, 0.55,         # 17-21 evening
    0.60, 0.60,                           # 22-23
)

_AC_DAILY_CAP = 12.0
_GEYSER_MORNING_CAP = 6.0
_GEYSER_EVENING_CAP = 3.0
_EV_DAILY_CAP = 7.0
_GEYSER_MORNING_SHARE = 0.85
_GEYSER_EVENING_SHARE = 0.30
_EV_SPIKE_THRESHOLD = 2.0       # kWh above local evening baseline
_EV_MIN_HOUR_EXCESS = 2.5       # kWh above base load in the spike hour
_DEST_CAP_ABSOLUTE = 4.0        # kWh / hour
_DEST_CAP_FACTOR = 1.15         # relative to the day's peak hour
#: Guard against absurd magnitudes that would overflow cost arithmetic and
#: emit non-JSON values.  Realistic household days are well under 1,000 kWh.
_MAX_DAILY_KWH = 1.0e9

# Fallback illustrative presets.  Only used when ``tariffs.py`` is absent or
# malformed.  They intentionally match the backend's illustrative bands
# (base tariff per city, 20 % solar discount, 20 % peak surcharge, and the
# backend's carbon factors) so standalone and integrated runs agree.
_FALLBACK_PRESETS: dict[str, dict[str, float]] = {
    "Bengaluru": {
        "base": 8.00,
        "solar_factor": 0.80,
        "peak_factor": 1.20,
        "carbon_normal": 0.65,
        "carbon_solar": 0.35,
        "carbon_peak": 0.85,
    },
    "Mumbai": {
        "base": 9.20,
        "solar_factor": 0.80,
        "peak_factor": 1.20,
        "carbon_normal": 0.65,
        "carbon_solar": 0.35,
        "carbon_peak": 0.85,
    },
    "Delhi": {
        "base": 7.50,
        "solar_factor": 0.80,
        "peak_factor": 1.20,
        "carbon_normal": 0.65,
        "carbon_solar": 0.35,
        "carbon_peak": 0.85,
    },
}


# ---------------------------------------------------------------------------
# Validation and tariff resolution
# ---------------------------------------------------------------------------


def _normalize_city(city: str) -> str:
    """Return the canonical city name or raise ``ValueError``."""
    if not isinstance(city, str):
        raise ValueError(
            "city must be one of " + ", ".join(CITIES) + f", got {city!r}"
        )
    key = city.strip().casefold()
    for name in CITIES:
        if name.casefold() == key:
            return name
    raise ValueError(
        "unsupported city " + repr(city) + "; expected one of " + ", ".join(CITIES)
    )


def _validate_baseline(baseline_kwh: Any) -> list[float]:
    """Validate the 24-hour baseline and return it as a list of floats."""
    if isinstance(baseline_kwh, (str, bytes, bytearray, Mapping, set, frozenset)):
        raise ValueError("baseline_kwh must be a sequence of 24 hourly kWh values")
    try:
        raw = list(baseline_kwh)
    except TypeError as exc:
        raise ValueError(
            "baseline_kwh must be a sequence of 24 hourly kWh values"
        ) from exc

    if len(raw) != HOURS_PER_DAY:
        raise ValueError(
            f"baseline_kwh must contain exactly {HOURS_PER_DAY} hourly values, "
            f"got {len(raw)}"
        )

    values: list[float] = []
    for index, item in enumerate(raw):
        if isinstance(item, bool) or not isinstance(item, numbers.Real):
            raise ValueError(
                f"baseline_kwh[{index}] must be a real number, got {item!r}"
            )
        value = float(item)
        if not math.isfinite(value):
            raise ValueError(
                f"baseline_kwh[{index}] must be finite, got {item!r}"
            )
        if value < 0.0:
            raise ValueError(
                f"baseline_kwh[{index}] must be nonnegative, got {value!r}"
            )
        values.append(value)
    return values


def _daily_total(values: list[float]) -> float:
    """Return a finite total in a supported magnitude range."""
    try:
        total = math.fsum(values)
    except OverflowError as exc:
        raise ValueError("total daily energy is too large to schedule") from exc
    if not math.isfinite(total):
        raise ValueError("total daily energy must be finite")
    if total > _MAX_DAILY_KWH:
        raise ValueError(
            f"total daily energy {total!r} kWh exceeds the supported range"
        )
    return total


def _coerce_vector(raw: Any) -> list[float] | None:
    """Coerce a 24-value tariff/carbon vector, returning ``None`` if invalid."""
    try:
        items = list(raw)
    except TypeError:
        return None
    if len(items) != HOURS_PER_DAY:
        return None
    out: list[float] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, numbers.Real):
            return None
        value = float(item)
        if not math.isfinite(value) or value < 0.0:
            return None
        out.append(value)
    return out


def _coerce_profile(data: Any) -> dict[str, list[float]] | None:
    """Validate a tariff profile returned by the shared backend module."""
    if not isinstance(data, dict):
        return None
    price = _coerce_vector(data.get("tariff_inr_per_kwh"))
    carbon = _coerce_vector(data.get("carbon_kg_per_kwh"))
    if price is None or carbon is None:
        return None
    return {"tariff_inr_per_kwh": price, "carbon_kg_per_kwh": carbon}


def _external_profiles(city: str) -> dict[str, list[float]] | None:
    """Try the sibling ``tariffs.hourly_profiles`` contract; ``None`` if unusable."""
    module = sys.modules.get("tariffs")
    if module is None:
        try:
            module = importlib.import_module("tariffs")
        except Exception:
            return None
    provider = getattr(module, "hourly_profiles", None)
    if not callable(provider):
        return None
    try:
        data = provider(city)
    except Exception:
        return None
    return _coerce_profile(data)


def _fallback_profile(city: str) -> dict[str, list[float]]:
    """Build the documented illustrative time-band profile for ``city``."""
    preset = _FALLBACK_PRESETS[city]
    base = preset["base"]
    price: list[float] = []
    carbon: list[float] = []
    for hour in range(HOURS_PER_DAY):
        if hour in PEAK_HOURS:
            price.append(round(base * preset["peak_factor"], 4))
            carbon.append(preset["carbon_peak"])
        elif hour in SOLAR_HOURS:
            price.append(round(base * preset["solar_factor"], 4))
            carbon.append(preset["carbon_solar"])
        else:
            price.append(round(base, 4))
            carbon.append(preset["carbon_normal"])
    return {"tariff_inr_per_kwh": price, "carbon_kg_per_kwh": carbon}


def _load_profiles(city: str) -> tuple[dict[str, list[float]], str]:
    """Return ``(profiles, source)`` using the shared module when available."""
    external = _external_profiles(city)
    if external is not None:
        return external, "tariffs.hourly_profiles"
    return _fallback_profile(city), "built-in illustrative fallback"


def get_hourly_profiles(city: str) -> dict[str, list[float]]:
    """Return 24 hourly tariffs (INR/kWh) and carbon factors (kg/kWh).

    Delegates to ``tariffs.hourly_profiles(city)`` when that module is
    importable and returns a valid 24-hour profile.  Otherwise uses the
    built-in illustrative profile.  The returned lists are fresh copies.
    """
    city_name = _normalize_city(city)
    profiles, _source = _load_profiles(city_name)
    return {
        "tariff_inr_per_kwh": list(profiles["tariff_inr_per_kwh"]),
        "carbon_kg_per_kwh": list(profiles["carbon_kg_per_kwh"]),
    }


# ---------------------------------------------------------------------------
# Heuristic appliance estimation
# ---------------------------------------------------------------------------


def _base_load_hourly(values: list[float]) -> float:
    """Overnight floor load treated as fridge/base consumption (kWh/hour)."""
    night_floor = min(values[1:6])
    return min(max(night_floor, 0.0), 0.75)


def _spread_energy(
    amount: float, hours: list[int], excess: list[float]
) -> dict[int, float]:
    """Split ``amount`` across ``hours`` proportionally to their excess load."""
    if amount <= 0.0 or not hours:
        return {}
    weights = [max(0.0, excess[h]) for h in hours]
    total_weight = sum(weights)
    if total_weight <= _EPS:
        return {}
    return {h: amount * weight / total_weight for h, weight in zip(hours, weights)}


def _detect_ev_block(
    excess: list[float], base_hourly: float
) -> tuple[dict[int, float], float]:
    """Detect a sharp evening charging spike and return (hour->kWh, surplus).

    The local reference is the mean excess of the adjacent evening hours.  A
    block needs at least 2.0 kWh above that reference and at least 2.5 kWh
    above base load.  Only blocks containing a 17:00-22:00 peak hour are
    returned, because only those are schedulable.  This is a simulation
    assumption, not EV detection.
    """
    candidates: list[tuple[float, int]] = []
    for hour in range(17, 23):
        neighbours = [excess[x] for x in (hour - 1, hour + 1) if 15 <= x <= 23]
        local = max(
            base_hourly * 1.2,
            (sum(neighbours) / len(neighbours)) if neighbours else 0.0,
            0.20,
        )
        surplus = excess[hour] - local
        if surplus > 0.0:
            candidates.append((surplus, hour))

    if not candidates:
        return {}, 0.0

    best_surplus, best_hour = max(candidates)
    if best_surplus < _EV_SPIKE_THRESHOLD or excess[best_hour] < _EV_MIN_HOUR_EXCESS:
        return {}, best_surplus

    anchor_energy = excess[best_hour]
    block: dict[int, float] = {}
    for hour in (best_hour - 1, best_hour, best_hour + 1):
        if hour not in range(17, 23):
            continue
        neighbours = [excess[x] for x in (hour - 1, hour + 1) if 15 <= x <= 23]
        local = max(
            base_hourly * 1.2,
            (sum(neighbours) / len(neighbours)) if neighbours else 0.0,
            0.20,
        )
        surplus = max(0.0, excess[hour] - local)
        if hour != best_hour and surplus < max(1.5, anchor_energy * 0.35):
            continue
        if surplus >= 0.5:
            block[hour] = surplus

    # Only peak-band charging blocks are schedulable; a late-night spike is
    # already outside the expensive window and is left to the AC/base estimate.
    block = {hour: value for hour, value in block.items() if hour in PEAK_HOURS}
    if not block:
        return {}, max(0.0, best_surplus)

    total = sum(block.values())
    if total > _EV_DAILY_CAP:
        factor = _EV_DAILY_CAP / total
        block = {hour: value * factor for hour, value in block.items()}
    return block, best_surplus


def _estimate_loads(values: list[float]) -> dict[str, Any]:
    """Run heuristic sub-metering and return component estimates + signals.

    The result is internal: daily kWh per appliance, the hourly attribution
    used for scheduling, and the signal details needed for explanatory logs.
    """
    total = _daily_total(values)
    base_hourly = _base_load_hourly(values)
    fridge_daily = min(base_hourly * HOURS_PER_DAY, total)
    excess = [max(0.0, value - base_hourly) for value in values]

    # 1. Morning water-heating signature (05:00-09:00).
    morning_hours = [hour for hour in range(5, 9) if excess[hour] >= 0.10]
    morning_energy = sum(excess[hour] for hour in morning_hours)
    geyser_morning = (
        min(morning_energy * _GEYSER_MORNING_SHARE, _GEYSER_MORNING_CAP)
        if morning_energy >= 0.35
        else 0.0
    )
    geyser_attr = _spread_energy(geyser_morning, morning_hours, excess)

    # 2. EV / charging spike (always marked as a scenario assumption).
    ev_attr, spike_surplus = _detect_ev_block(excess, base_hourly)
    ev_daily = sum(ev_attr.values())

    # 3. Evening water heating, only when no EV block already explains the
    #    evening excess (avoids attributing the same kWh twice).
    evening_geyser_kwh = 0.0
    if not ev_attr:
        evening_hours = [hour for hour in PEAK_HOURS if excess[hour] >= 0.35]
        evening_energy = sum(excess[hour] for hour in evening_hours)
        if evening_energy >= 1.0:
            evening_geyser_kwh = min(
                evening_energy * _GEYSER_EVENING_SHARE, _GEYSER_EVENING_CAP
            )
            evening_attr = _spread_energy(
                evening_geyser_kwh, evening_hours, excess
            )
            geyser_attr.update(evening_attr)

    geyser_daily = sum(geyser_attr.values())

    # 4. Remaining cooling-signature share -> AC (never auto-scheduled).
    #    Subtract already-attributed flexible energy hour by hour so the same
    #    kWh is not counted as both water heating/EV and cooling.
    ac_daily = 0.0
    for hour in range(HOURS_PER_DAY):
        residual = max(
            0.0,
            excess[hour]
            - geyser_attr.get(hour, 0.0)
            - ev_attr.get(hour, 0.0),
        )
        ac_daily += residual * _AC_HOUR_WEIGHT[hour]
    ac_daily = min(ac_daily, _AC_DAILY_CAP)

    # 5. Combined allocations may never exceed measured/forecast energy.
    available = max(0.0, total - fridge_daily)
    others = geyser_daily + ev_daily + ac_daily
    attr_scale = 1.0
    if others > available + _EPS:
        attr_scale = (available / others) if others > 0.0 else 0.0
        geyser_daily *= attr_scale
        ev_daily *= attr_scale
        ac_daily *= attr_scale
        geyser_attr = {h: v * attr_scale for h, v in geyser_attr.items()}
        ev_attr = {h: v * attr_scale for h, v in ev_attr.items()}

    # 6. Round for display, then clamp so the rounded sum never exceeds total.
    final = {
        "fridge": max(0.0, round(fridge_daily, 2)),
        "geyser": max(0.0, round(geyser_daily, 2)),
        "ev": max(0.0, round(ev_daily, 2)),
        "ac": max(0.0, round(ac_daily, 2)),
    }
    over = sum(final.values()) - total
    if over > 0.0:
        for key in ("ac", "ev", "geyser", "fridge"):
            if over <= _EPS:
                break
            reduction = min(final[key], over)
            final[key] -= reduction
            over -= reduction

    # Keep the hourly attribution consistent with the reported daily totals.
    def _rescale(attr: dict[int, float], target: float) -> dict[int, float]:
        current = sum(attr.values())
        if target <= _EPS or current <= _EPS:
            return {}
        factor = target / current
        return {hour: value * factor for hour, value in attr.items()}

    geyser_attr = _rescale(geyser_attr, final["geyser"])
    ev_attr = _rescale(ev_attr, final["ev"])

    return {
        "values": final,
        "total": total,
        "base_hourly": base_hourly,
        "attribution": {"geyser": geyser_attr, "ev": ev_attr},
        "signals": {
            "morning_hours": morning_hours,
            "evening_geyser_kwh": evening_geyser_kwh * attr_scale,
            "ev_hours": dict(ev_attr),
            "spike_surplus_kwh": spike_surplus,
            "ev_detected": bool(ev_attr),
        },
    }


def estimate_appliances(baseline_kwh: Any) -> list[dict[str, Any]]:
    """Return the four heuristic appliance estimates for a 24-hour baseline."""
    values = _validate_baseline(baseline_kwh)
    estimates = _estimate_loads(values)["values"]
    return [
        {
            "id": appliance,
            "name": _APPLIANCE_NAMES[appliance],
            "daily_kwh": float(estimates[appliance]),
            "confidence": "heuristic",
        }
        for appliance in _APPLIANCE_ORDER
    ]


# ---------------------------------------------------------------------------
# Scheduling agent
# ---------------------------------------------------------------------------


def _destination_capacity(values: list[float]) -> list[float]:
    """Maximum aggregate load accepted at each destination hour.

    This is an illustrative household hourly ceiling (``1.15 x`` the day's
    peak, at least 4 kWh), not a per-appliance power rating.  It prevents the
    optimizer from concentrating every shifted load into one hour.
    """
    day_peak = max(values) if values else 0.0
    cap = max(_DEST_CAP_ABSOLUTE, day_peak * _DEST_CAP_FACTOR)
    return [max(cap, values[hour]) for hour in range(HOURS_PER_DAY)]


def _rank_destinations(
    appliance: str,
    source_hour: int,
    price: list[float],
    carbon: list[float],
    optimized: list[float],
    capacity: list[float],
) -> list[int]:
    """Rank feasible destination hours for one appliance and source hour."""
    window = _DESTINATION_WINDOWS.get(appliance, frozenset())
    ranked: list[tuple[float, float, int, int]] = []
    for hour in sorted(window):
        if hour == source_hour or hour in PEAK_HOURS:
            continue
        if capacity[hour] - optimized[hour] <= _EPS:
            continue
        # Never trade a dirtier hour for a cheaper one: a destination must be
        # at least as clean as the source and no more expensive, so shifting
        # can improve cost, carbon, or both, but never worsen either.
        cheaper = (
            price[hour] < price[source_hour] - _EPS
            and carbon[hour] <= carbon[source_hour] + _EPS
        )
        cleaner = (
            price[hour] <= price[source_hour] + _EPS
            and carbon[hour] < carbon[source_hour] - _EPS
        )
        if not (cheaper or cleaner):
            continue
        # Prefer the cheapest/cleanest hour; break ties toward midday solar.
        ranked.append((price[hour], carbon[hour], abs(hour - 13), hour))
    ranked.sort()
    return [row[3] for row in ranked]


def _format_window(hours: list[int] | set[int]) -> str:
    """Format destination hours as ``13:00-14:00`` style ranges."""
    unique = sorted({int(hour) for hour in hours})
    if not unique:
        return ""
    runs: list[tuple[int, int]] = []
    start = previous = unique[0]
    for hour in unique[1:]:
        if hour == previous + 1:
            previous = hour
            continue
        runs.append((start, previous))
        start = previous = hour
    runs.append((start, previous))
    return ", ".join(f"{a:02d}:00\u2013{b + 1:02d}:00" for a, b in runs)


def _make_log(
    hour: int, level: str, protocol: str, message: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Build one JSON-compatible agent log entry."""
    return {
        "hour": f"{int(hour) % 24:02d}:00",
        "level": level,
        "protocol": protocol,
        "message": message,
        "payload": payload,
    }


def _build_logs(
    *,
    city: str,
    tariff_source: str,
    values: list[float],
    price: list[float],
    carbon: list[float],
    estimate: dict[str, Any],
    transfers: list[dict[str, Any]],
    preserved: dict[str, float],
    moved: dict[str, float],
    savings: dict[str, Any],
    peak_shift_pct: float,
    optimized: list[float],
) -> list[dict[str, Any]]:
    """Produce the deterministic autonomous-agent terminal narration."""
    logs: list[dict[str, Any]] = []
    estimates = estimate["values"]
    signals = estimate["signals"]
    total = sum(values)

    logs.append(
        _make_log(
            17,
            "INFO",
            "AGENT",
            "24-hour forecast scanned; heuristic appliance estimate complete",
            {
                "city": city,
                "baseline_kwh": round(total, 3),
                "method": "time-of-day load signatures (heuristic, not metered)",
                "tariff_source": tariff_source,
                "estimated_kwh": {
                    appliance: round(estimates[appliance], 2)
                    for appliance in _APPLIANCE_ORDER
                },
            },
        )
    )

    logs.append(
        _make_log(
            17,
            "INFO",
            "AGENT",
            "Peak band 17:00\u201322:00 identified as the highest-cost window",
            {
                "peak_inr_per_kwh": round(max(price), 2),
                "cheapest_inr_per_kwh": round(min(price), 2),
                "peak_hours": list(PEAK_HOURS),
                "peak_kwh": round(sum(values[h] for h in PEAK_HOURS), 3),
            },
        )
    )

    logs.append(
        _make_log(
            17,
            "INFO",
            "AGENT",
            f"Protected base load locked: {estimates['fridge']:.2f} kWh/day is never interrupted",
            {
                "device": "fridge",
                "action": "LOCKED_ON",
                "kwh_per_day": round(estimates["fridge"], 2),
            },
        )
    )

    if signals["ev_detected"]:
        spike_hour = min(signals["ev_hours"])
        logs.append(
            _make_log(
                spike_hour,
                "INFO",
                "AGENT",
                "Evening load spike detected; attributed to EV charging "
                "(simulation assumption)",
                {
                    "device": "ev",
                    "action": "DETECT",
                    "detected_kwh": round(estimates["ev"], 2),
                    "surplus_above_local_baseline_kwh": round(
                        signals["spike_surplus_kwh"], 3
                    ),
                    "assumption": True,
                    "scenario": "simulated EV plug-in, not a measured connection",
                    "planning_horizon": "day-ahead schedule",
                    "schedules_future_block": True,
                    "interrupts_running_cycle": False,
                },
            )
        )
    elif signals["evening_geyser_kwh"] > 0.01:
        evening_hours = [
            hour
            for hour in PEAK_HOURS
            if hour in estimate["attribution"]["geyser"]
        ]
        logs.append(
            _make_log(
                min(evening_hours) if evening_hours else 18,
                "INFO",
                "AGENT",
                "Evening water-heating signature detected in the peak band",
                {
                    "device": "geyser",
                    "action": "DETECT",
                    "detected_kwh": round(signals["evening_geyser_kwh"], 2),
                    "assumption": "heuristic time-of-day signature",
                },
            )
        )

    # Simulated command records, one per accepted transfer.
    for transfer in transfers:
        appliance = transfer["device"]
        if appliance == "geyser":
            protocol = "SIMULATED_MQTT"
            message = "Peak-hour geyser operation rescheduled"
            action = "DEFER"
        else:
            protocol = "SIMULATED_OCPP"
            message = "EV charging block deferred to a cheaper, cleaner hour"
            action = "DEFER"
        logs.append(
            _make_log(
                transfer["from_hour"],
                "INFO",
                protocol,
                message,
                {
                    "device": appliance,
                    "action": action,
                    "from_hour": transfer["from_hour"],
                    "resume_hour": transfer["to_hour"],
                    "kwh": round(transfer["kwh"], 3),
                    "tariff_before_inr_per_kwh": round(transfer["price_from"], 2),
                    "tariff_after_inr_per_kwh": round(transfer["price_to"], 2),
                    "carbon_before_kg_per_kwh": round(
                        transfer["carbon_from"], 3
                    ),
                    "carbon_after_kg_per_kwh": round(transfer["carbon_to"], 3),
                    "dry_run": True,
                    "planning_horizon": "day-ahead schedule",
                },
            )
        )

    # Constraints / refusals: preserve the load and explain why.
    for appliance in ("geyser", "ev"):
        if preserved.get(appliance, 0.0) <= 0.01:
            continue
        source_hours = sorted(
            hour
            for hour, kwh in estimate["attribution"][appliance].items()
            if hour in PEAK_HOURS and kwh > 0.01
        )
        logs.append(
            _make_log(
                source_hours[0] if source_hours else 18,
                "WARN",
                "AGENT",
                f"Flexible {appliance} load preserved: no feasible cheaper/cleaner "
                "destination inside the safe window",
                {
                    "device": appliance,
                    "action": "HOLD",
                    "preserved_kwh": round(preserved[appliance], 3),
                    "reason": "no_destination",
                },
            )
        )

    if estimates["ac"] > 0.01:
        logs.append(
            _make_log(
                20,
                "INFO",
                "AGENT",
                "Air-conditioner load left in place: no validated thermal model "
                "for safe rescheduling",
                {
                    "device": "ac",
                    "action": "HOLD",
                    "kwh_per_day": round(estimates["ac"], 2),
                    "status": "Recommendation Only",
                },
            )
        )

    flexible = sum(moved.values()) + sum(preserved.values())
    if flexible <= 0.01:
        logs.append(
            _make_log(
                17,
                "INFO",
                "AGENT",
                "No flexible appliance load detected in the peak band; "
                "baseline schedule preserved",
                {"action": "NOOP", "reason": "no_eligible_flex"},
            )
        )

    shifted = sum(moved.values())
    logs.append(
        _make_log(
            22,
            "INFO",
            "AGENT",
            f"Autonomous schedule committed: {shifted:.2f} kWh shifted, "
            f"INR {savings['daily_inr']:.2f}/day saved",
            {
                "action": "SUMMARY",
                "shifted_kwh": round(shifted, 3),
                "energy_conserved": abs(sum(optimized) - total) < 1e-6,
                "daily_inr": round(savings["daily_inr"], 2),
                "monthly_inr": round(savings["monthly_inr"], 2),
                "monthly_co2_kg": round(savings["monthly_co2_kg"], 3),
                "peak_shift_pct": round(peak_shift_pct, 2),
                "plug_payback_months": (
                    round(savings["plug_payback_months"], 2)
                    if savings["plug_payback_months"] is not None
                    else None
                ),
                "plug_cost_inr": PLUG_COST_INR,
                "payback_basis": "illustrative smart-plug cost / projected monthly savings",
                "note": "Consumption is shifted, not reduced; no kWh savings claimed",
            },
        )
    )
    return logs


def _build_actuators(
    *,
    estimates: dict[str, float],
    transfers: list[dict[str, Any]],
    preserved: dict[str, float],
) -> list[dict[str, Any]]:
    """Build the structured actuator state list."""
    destination_hours: dict[str, list[int]] = {"geyser": [], "ev": []}
    moved_total: dict[str, float] = {"geyser": 0.0, "ev": 0.0}
    for transfer in transfers:
        destination_hours[transfer["device"]].append(transfer["to_hour"])
        moved_total[transfer["device"]] += transfer["kwh"]

    def _flexible_status(appliance: str) -> tuple[str, str]:
        if moved_total[appliance] > 0.01:
            return "Deferred", _format_window(destination_hours[appliance])
        if preserved.get(appliance, 0.0) > 0.01:
            return "Recommendation Only", "Manual action suggested"
        if estimates[appliance] > 0.01:
            return "Active", "Current schedule"
        return "Recommendation Only", "Not scheduled"

    geyser_status, geyser_window = _flexible_status("geyser")
    ev_status, ev_window = _flexible_status("ev")

    return [
        {
            "id": "fridge",
            "name": _APPLIANCE_NAMES["fridge"],
            "control_type": _CONTROL_TYPES["fridge"],
            "status": "Locked ON",
            "scheduled_window": "00:00\u201324:00",
            "shiftable": False,
        },
        {
            "id": "geyser",
            "name": _APPLIANCE_NAMES["geyser"],
            "control_type": _CONTROL_TYPES["geyser"],
            "status": geyser_status,
            "scheduled_window": geyser_window,
            "shiftable": True,
        },
        {
            "id": "ev",
            "name": _APPLIANCE_NAMES["ev"],
            "control_type": _CONTROL_TYPES["ev"],
            "status": ev_status,
            "scheduled_window": ev_window,
            "shiftable": True,
        },
        {
            "id": "ac",
            "name": _APPLIANCE_NAMES["ac"],
            "control_type": _CONTROL_TYPES["ac"],
            "status": "Recommendation Only",
            "scheduled_window": "Manual override only",
            "shiftable": False,
        },
    ]


def optimize_schedule(baseline_kwh: Any, city: str) -> dict[str, Any]:
    """Estimate appliances and reschedule eligible flexible load.

    Parameters
    ----------
    baseline_kwh:
        Exactly 24 nonnegative hourly kWh values, index 0 == 00:00.
    city:
        ``"Bengaluru"``, ``"Mumbai"`` or ``"Delhi"`` (case-insensitive).

    Returns
    -------
    dict
        The shared contract: ``optimized_kwh``, ``savings``, ``actuators``,
        ``agent_logs`` and ``appliance_estimates``.

    Raises
    ------
    ValueError
        If the baseline or city is invalid.
    """
    values = _validate_baseline(baseline_kwh)
    city_name = _normalize_city(city)
    profiles, tariff_source = _load_profiles(city_name)
    price = profiles["tariff_inr_per_kwh"]
    carbon = profiles["carbon_kg_per_kwh"]

    estimate = _estimate_loads(values)
    total = estimate["total"]
    estimates = estimate["values"]
    base_hourly = estimate["base_hourly"]

    optimized = list(values)
    capacity = _destination_capacity(values)
    transfers: list[dict[str, Any]] = []
    moved: dict[str, float] = {"geyser": 0.0, "ev": 0.0}
    preserved: dict[str, float] = {"geyser": 0.0, "ev": 0.0}

    # Only peak-hour flexible energy moves; each source hour is processed once.
    for appliance in ("geyser", "ev"):
        attribution = estimate["attribution"][appliance]
        for source_hour in sorted(attribution):
            if source_hour not in PEAK_HOURS:
                continue
            amount = attribution[source_hour]
            if amount <= 0.01:
                continue

            # Never drop below the protected overnight floor.
            remaining = min(amount, max(0.0, optimized[source_hour] - base_hourly))
            if remaining <= _EPS:
                preserved[appliance] += amount
                continue

            destinations = _rank_destinations(
                appliance, source_hour, price, carbon, optimized, capacity
            )
            for destination in destinations:
                if remaining <= _EPS:
                    break
                room = capacity[destination] - optimized[destination]
                if room <= _EPS:
                    continue
                chunk = min(remaining, room, optimized[source_hour] - base_hourly)
                if chunk <= _EPS:
                    break
                optimized[source_hour] -= chunk
                optimized[destination] += chunk
                remaining -= chunk
                moved[appliance] += chunk
                transfers.append(
                    {
                        "device": appliance,
                        "from_hour": source_hour,
                        "to_hour": destination,
                        "kwh": chunk,
                        "price_from": price[source_hour],
                        "price_to": price[destination],
                        "carbon_from": carbon[source_hour],
                        "carbon_to": carbon[destination],
                    }
                )
            preserved[appliance] += max(0.0, remaining)

    # Numerical hygiene + exact energy conservation (move, never delete).
    optimized = [max(0.0, value) for value in optimized]
    residual = total - sum(optimized)
    if residual != 0.0:
        index = max(range(HOURS_PER_DAY), key=lambda h: optimized[h])
        optimized[index] = max(0.0, optimized[index] + residual)

    baseline_cost = sum(values[h] * price[h] for h in range(HOURS_PER_DAY))
    optimized_cost = sum(optimized[h] * price[h] for h in range(HOURS_PER_DAY))
    baseline_carbon = sum(values[h] * carbon[h] for h in range(HOURS_PER_DAY))
    optimized_carbon = sum(optimized[h] * carbon[h] for h in range(HOURS_PER_DAY))

    daily_inr = baseline_cost - optimized_cost
    # Destinations are never more expensive, so savings are nonnegative by
    # construction.  Clamp float residue so a same-price shift reports zero
    # rather than a fictitious fraction of a rupee.
    if daily_inr <= 1e-9:
        daily_inr = 0.0
    daily_co2 = baseline_carbon - optimized_carbon
    if daily_co2 <= 1e-9:
        daily_co2 = 0.0

    monthly_inr = daily_inr * DAYS_PER_MONTH
    monthly_co2_kg = daily_co2 * DAYS_PER_MONTH

    peak_baseline = sum(values[h] for h in PEAK_HOURS)
    peak_optimized = sum(optimized[h] for h in PEAK_HOURS)
    if peak_baseline > 0.0:
        peak_shift_pct = (peak_baseline - peak_optimized) / peak_baseline * 100.0
        if abs(peak_shift_pct) <= 1e-9:
            peak_shift_pct = 0.0
    else:
        peak_shift_pct = 0.0

    plug_payback_months = (
        PLUG_COST_INR / monthly_inr if monthly_inr > 0.0 else None
    )

    savings: dict[str, Any] = {
        "daily_inr": daily_inr,
        "monthly_inr": monthly_inr,
        "monthly_co2_kg": monthly_co2_kg,
        "peak_shift_pct": peak_shift_pct,
        "plug_payback_months": plug_payback_months,
        "daily_baseline_cost_inr": baseline_cost,
        "daily_optimized_cost_inr": optimized_cost,
    }

    appliance_estimates = [
        {
            "id": appliance,
            "name": _APPLIANCE_NAMES[appliance],
            "daily_kwh": float(estimates[appliance]),
            "confidence": "heuristic",
        }
        for appliance in _APPLIANCE_ORDER
    ]

    actuators = _build_actuators(
        estimates=estimates, transfers=transfers, preserved=preserved
    )
    agent_logs = _build_logs(
        city=city_name,
        tariff_source=tariff_source,
        values=values,
        price=price,
        carbon=carbon,
        estimate=estimate,
        transfers=transfers,
        preserved=preserved,
        moved=moved,
        savings=savings,
        peak_shift_pct=peak_shift_pct,
        optimized=optimized,
    )

    return {
        "optimized_kwh": [float(value) for value in optimized],
        "savings": savings,
        "actuators": actuators,
        "agent_logs": agent_logs,
        "appliance_estimates": appliance_estimates,
    }


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import json

    demo = [0.35] * 24
    for hour in (6, 7):
        demo[hour] += 1.6
    for hour in (18, 19, 20, 21):
        demo[hour] += 1.1
    demo[19] += 3.5
    print(json.dumps(optimize_schedule(demo, "Bengaluru"), indent=2))
