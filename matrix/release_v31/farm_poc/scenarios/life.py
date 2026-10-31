"""Farm life: a simulated year, one 24-hour day per calendar day (the long-life training data).

Each day starts at midnight with its month's weather: an Algiers-like climate table fed through the
``WeatherModel`` config keys the Matrix already reads (mean temperature, daily swing, humidity, sunrise and
sunset, peak sun). No Matrix code changes, so the simulation itself is unchanged.
- The owner's daily activity: door openings, cleaning, short showers, fan settings (``library._benign``,
  spread over the day's hours).
- About 15 % of days carry one realistic fault at a random hour, drawn from the library's own samplers
  (``library._make``) and shifted onto the day, so faults appear at natural rates inside long calm life.
  Families whose start state is tied to the fault time (turning motor, feeder, tank refill) are left out.
- The incubators stay at incubation day 14 + elapsed days: the Matrix fixes the cycle start (a change there
  would alter the simulation, which is off-limits).

Splits (``SPLITS``) cover every month in each set; none shares an episode with the scenario library's sets.
"""
from __future__ import annotations

import random

from .library import PLAN, WARMUP, EpisodeSpec, _benign, _make, _seed

DAY_TICKS = 86_400
FAULT_SHARE = 0.15
# Month: mean temperature °C, daily swing °C, relative humidity %, sunrise h, sunset h, peak sun W/m²
CLIMATE = {
    1: (11.5, 7.0, 72.0, 7.6, 17.6, 560.0), 2: (12.0, 7.5, 70.0, 7.3, 18.2, 650.0), 3: (14.0, 8.0, 68.0, 6.7, 18.7, 760.0),
    4: (16.0, 8.0, 67.0, 6.0, 19.2, 850.0), 5: (19.0, 8.5, 67.0, 5.5, 19.7, 920.0), 6: (23.0, 9.0, 63.0, 5.3, 20.1, 960.0),
    7: (26.0, 9.0, 61.0, 5.5, 20.0, 950.0), 8: (27.0, 8.5, 63.0, 5.9, 19.5, 900.0), 9: (24.0, 8.0, 66.0, 6.3, 18.8, 800.0),
    10: (20.0, 7.5, 69.0, 6.8, 18.1, 690.0), 11: (15.5, 7.0, 71.0, 7.2, 17.5, 590.0), 12: (12.5, 6.5, 73.0, 7.6, 17.3, 530.0),
}
MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
# Every month in every set: test = days ending in 5, judge = days ending in 0, train = the whole rest of the year.
SPLITS = {"train": [d for d in range(365) if d % 10 not in (0, 5)],
          "judge": [d for d in range(365) if d % 10 == 0], "test": [d for d in range(365) if d % 10 == 5]}
TICK_KEYS = ("inject_tick", "end_tick", "tick", "from_tick", "until_tick")
SKIP_FAMILIES = ("normal", "turning_motor_failure", "feeder_blocked", "tank_fill_valve_failure")
FIRST_FAULT_VARIANT = 200_000   # far beyond the library's variants: fresh fault draws


def month_of(day: int) -> int:
    day %= 365
    for month, length in enumerate(MONTH_DAYS, start=1):
        if day < length:
            return month
        day -= length
    return 12


def weather_for(day: int, rng: random.Random) -> dict:
    mean, swing, humidity, sunrise, sunset, sun = CLIMATE[month_of(day)]
    return {"enabled": True, "start_hour": 0.0, "mean_temperature_c": round(mean + rng.uniform(-2.0, 2.0), 2),
            "daily_amplitude_c": swing, "mean_relative_humidity_pct": humidity, "sunrise_h": sunrise,
            "sunset_h": sunset, "peak_solar_w_m2": sun, "mean_wind_m_s": round(rng.uniform(2.0, 5.0), 2)}


def _day_benign(rng: random.Random) -> list:
    events = []
    for hour in range(24):
        if rng.random() < 0.3 and 6 <= hour <= 21:      # the owner is around in the daytime
            for event in _benign(rng, 3600):
                shifted = dict(event)
                shifted["tick"] = int(event["tick"]) + hour * 3600
                if shifted["tick"] + int(event.get("duration", 0)) < DAY_TICKS - 60:
                    events.append(shifted)
    return events


def _fault_for(day: int, rng: random.Random):
    """One library fault, its timing moved to a random hour of the day (or None on a calm day)."""
    if rng.random() >= FAULT_SHARE:
        return None
    families = [f for f, n in PLAN.items() if f not in SKIP_FAMILIES for _ in range(n)]
    family = rng.choice(families)
    source = _make(family, FIRST_FAULT_VARIANT + day, False)
    start = rng.randint(3600, DAY_TICKS - source.duration_ticks - 600)
    offset = start - WARMUP
    faults = [{k: (v + offset if k in TICK_KEYS and v is not None else v) for k, v in f.items()} for f in source.faults]
    extra = {k: v for k, v in source.config.items() if k not in ("weather", "communications")}
    return family, faults, source.initial, extra


def life_day(day: int, split: str) -> EpisodeSpec:
    rng = random.Random(_seed("life", day))
    config = {"weather": weather_for(day, rng), "communications": {"owner_unreachable_probability": 0.1}}
    fault = _fault_for(day, rng)
    family, faults, initial = "life", [], {}
    if fault:
        name, faults, initial, extra = fault
        family = f"life+{name}"
        config.update(extra)
    month = month_of(day)
    return EpisodeSpec(f"life-{day:03d}-m{month:02d}-{family.replace('+', '-')}", family, day, _seed("life-run", day),
                       DAY_TICKS, config, faults, _day_benign(rng), initial,
                       {"day": day, "month": month, "fault": bool(fault)}, split)


def year(split: str | None = None) -> list[EpisodeSpec]:
    return [life_day(day, name) for name, days in SPLITS.items() if split in (None, name) for day in days]
