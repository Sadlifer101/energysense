"""Illustrative Indian household tariffs and carbon intensity profiles.

These numbers are ideathon scenario assumptions. They are NOT verified
official domestic tariffs and they do NOT represent measured grid carbon
intensity. All values are documented here so the assumption is explicit.
"""

from __future__ import annotations

from typing import Any

CITIES: tuple[str, ...] = ("Bengaluru", "Mumbai", "Delhi")

# Illustrative base energy charges (INR per kWh) for the demonstration.
BASE_RATES: dict[str, float] = {
    "Bengaluru": 8.00,
    "Mumbai": 9.20,
    "Delhi": 7.50,
}

# One-line rationale for each city variation (shown in the dashboard).
CITY_NOTES: dict[str, str] = {
    "Bengaluru": "Base illustrative rate of INR 8.00/kWh.",
    "Mumbai": "Illustrative +15% over Bengaluru to reflect a higher-cost urban tariff slab.",
    "Delhi": "Illustrative -6.25% versus Bengaluru to reflect a lower-cost urban tariff slab.",
}

# Tariff blocks: [start_hour, end_hour) with multiplier and carbon intensity.
# 00:00-09:00 and 22:00-24:00 normal | 09:00-17:00 solar | 17:00-22:00 peak
CARBON_NORMAL = 0.65   # kg CO2 per kWh (illustrative)
CARBON_SOLAR = 0.35    # kg CO2 per kWh (illustrative)
CARBON_PEAK = 0.85     # kg CO2 per kWh (illustrative)

SOLAR_DISCOUNT = 0.20
PEAK_SURCHARGE = 0.20

BLOCKS: list[dict[str, Any]] = [
    {
        "name": "normal_morning",
        "start_hour": 0,
        "end_hour": 9,
        "label": "00:00-09:00",
        "kind": "normal",
        "multiplier": 1.0,
        "carbon_kg_per_kwh": CARBON_NORMAL,
    },
    {
        "name": "solar",
        "start_hour": 9,
        "end_hour": 17,
        "label": "09:00-17:00",
        "kind": "solar",
        "multiplier": 1.0 - SOLAR_DISCOUNT,
        "carbon_kg_per_kwh": CARBON_SOLAR,
    },
    {
        "name": "peak",
        "start_hour": 17,
        "end_hour": 22,
        "label": "17:00-22:00",
        "kind": "peak",
        "multiplier": 1.0 + PEAK_SURCHARGE,
        "carbon_kg_per_kwh": CARBON_PEAK,
    },
    {
        "name": "normal_night",
        "start_hour": 22,
        "end_hour": 24,
        "label": "22:00-24:00",
        "kind": "normal",
        "multiplier": 1.0,
        "carbon_kg_per_kwh": CARBON_NORMAL,
    },
]


def resolve_city(city: str | None, default: str = "Bengaluru") -> str:
    """Normalise and validate a city name.

    Matches case-insensitively and ignores surrounding whitespace.
    Raises ValueError with a meaningful message for unsupported cities.
    """
    if city is None or (isinstance(city, str) and not city.strip()):
        return default
    if not isinstance(city, str):
        raise ValueError("Field 'city' must be a string.")
    wanted = city.strip().lower()
    for known in CITIES:
        if known.lower() == wanted:
            return known
    raise ValueError(
        f"Unsupported city '{city}'. Supported cities: {', '.join(CITIES)}."
    )


def block_for_hour(hour: int) -> dict[str, Any]:
    """Return the tariff block covering the given 0-23 hour."""
    hour = int(hour)
    if hour < 0 or hour > 23:
        raise ValueError(f"Hour out of range: {hour}")
    for block in BLOCKS:
        if block["start_hour"] <= hour < block["end_hour"]:
            return block
    raise ValueError(f"No tariff block defined for hour {hour}")


def _build_profiles(city: str) -> tuple[list[float], list[float]]:
    base = BASE_RATES[city]
    prices: list[float] = []
    carbon: list[float] = []
    for hour in range(24):
        block = block_for_hour(hour)
        prices.append(round(base * block["multiplier"], 4))
        carbon.append(block["carbon_kg_per_kwh"])
    return prices, carbon


def hourly_profiles(city: str) -> dict[str, list[float]]:
    """Return the 24-value tariff and carbon profiles for a city."""
    canonical = resolve_city(city)
    prices, carbon = _build_profiles(canonical)
    return {
        "tariff_inr_per_kwh": prices,
        "carbon_kg_per_kwh": carbon,
    }


def get_city_tariff(city: str) -> dict[str, Any]:
    """Return the full illustrative tariff configuration for a city."""
    canonical = resolve_city(city)
    prices, carbon = _build_profiles(canonical)
    base = BASE_RATES[canonical]

    blocks = []
    for block in BLOCKS:
        blocks.append(
            {
                "name": block["name"],
                "label": block["label"],
                "start_hour": block["start_hour"],
                "end_hour": block["end_hour"],
                "kind": block["kind"],
                "multiplier": block["multiplier"],
                "tariff_inr_per_kwh": round(base * block["multiplier"], 4),
                "carbon_kg_per_kwh": block["carbon_kg_per_kwh"],
            }
        )

    return {
        "city": canonical,
        "currency": "INR",
        "tariff_type": "illustrative",
        "base_inr_per_kwh": base,
        "solar_discount_pct": SOLAR_DISCOUNT * 100.0,
        "peak_surcharge_pct": PEAK_SURCHARGE * 100.0,
        "hourly_tariff_inr_per_kwh": prices,
        "hourly_carbon_kg_per_kwh": carbon,
        "blocks": blocks,
        "city_note": CITY_NOTES[canonical],
        "disclaimer": (
            "Illustrative ideathon assumptions only - not verified official "
            "domestic tariffs, and carbon values are not measured grid data."
        ),
    }
