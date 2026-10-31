"""Scenario library v1: families, seeded variants, and the train/validation/test split.

Every episode is described by a JSON-serialisable ``EpisodeSpec``. ``faults``
are what the twin run removes; ``benign`` events (doors, cleaning, showers,
owner adjustments) happen in both twins, so a model learns they are normal.

Splits are by whole episode: 70 % train, 15 % validation, 15 % test inside each
family, plus a separate *novel* test set drawn with variations the training
sampler never produces (extreme severities, unseen weather, unseen combinations).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import random

WARMUP = 300
INCUBATORS = ("incubator-1", "incubator-2")

WEATHER_PROFILES = {
    "mild": {"mean_temperature_c": 21.0, "daily_amplitude_c": 4.0, "mean_relative_humidity_pct": 55.0},
    "hot": {"mean_temperature_c": 31.0, "daily_amplitude_c": 6.0, "mean_relative_humidity_pct": 50.0},
    "humid_hot": {"mean_temperature_c": 30.0, "daily_amplitude_c": 4.0, "mean_relative_humidity_pct": 75.0},
    "cold": {"mean_temperature_c": 4.0, "daily_amplitude_c": 5.0, "mean_relative_humidity_pct": 70.0},
    "windy": {"mean_temperature_c": 16.0, "daily_amplitude_c": 4.0, "mean_wind_m_s": 7.0},
    # Only in the novel test set.
    "heatwave": {"mean_temperature_c": 37.0, "daily_amplitude_c": 6.0, "mean_relative_humidity_pct": 35.0},
    "freezing": {"mean_temperature_c": -6.0, "daily_amplitude_c": 4.0, "mean_relative_humidity_pct": 80.0},
}
TRAIN_WEATHER = ("mild", "hot", "humid_hot", "cold", "windy")
NOVEL_WEATHER = ("heatwave", "freezing")

SENSOR_CHANNELS = {
    "incubator": ("{node}.temperature_1", "{node}.temperature_2", "{node}.temperature_3", "{node}.humidity_1",
                  "{node}.heater_outlet"),
    "hvac": ("hvac.barn.temperature_1", "hvac.storage.temperature_1", "hvac.workspace.temperature_1",
             "hvac.barn.humidity_1", "hvac.supply_temperature", "hvac.outdoor_temperature"),
    "animal-protection": ("animal-protection.co2_ppm", "animal-protection.co_ppm", "animal-protection.ammonia_ppm",
                          "animal-protection.rain_gauge", "animal-protection.flood_gauge"),
}

EQUIPMENT_FAULTS = {
    # fault: (devices, severity range or None, permanent probability, weather needs)
    "fan1_failure": (INCUBATORS, None, 0.5, None),
    "fan2_failure": (INCUBATORS, None, 0.5, None),
    "heater_open_circuit": (INCUBATORS, None, 0.5, None),
    "heater_relay_stuck": (INCUBATORS, None, 0.5, None),
    "turning_motor_failure": (INCUBATORS, None, 0.6, None),
    "incubator_water_leak": (INCUBATORS, (100.0, 2000.0), 0.5, None),
    "humidifier_pump_failure": (INCUBATORS, None, 0.5, None),
    "humidifier_element_failure": (INCUBATORS, None, 0.5, None),
    "door_switch_open_circuit": (INCUBATORS, None, 0.5, None),
    "door_open_undetected": (INCUBATORS, None, 0.0, None),
    "dc_rail2_sag": (INCUBATORS, (0.5, 0.85), 0.5, None),
    "dc_rail3_failure": (INCUBATORS, (0.0, 0.3), 0.5, None),
    "controller_power_failure": (INCUBATORS, (0.0, 0.5), 0.3, None),
    "supply_fan_failure": (("hvac",), None, 0.5, None),
    "return_fan_failure": (("hvac",), None, 0.5, None),
    "compressor_failure": (("hvac",), None, 0.5, "hot"),
    "damper_stuck": (("hvac",), None, 0.6, "hot"),
    "condensate_drain_blocked": (("hvac",), None, 0.8, "humid_hot"),
    "filter_clogging": (("hvac",), (1.0, 3.0), 1.0, None),
    "refrigerant_leak": (("hvac",), (15.0, 70.0), 1.0, "hot"),
    "feeder_blocked": (("animal-protection",), None, 0.7, None),
    "drinker_pump_failure": (("animal-protection",), None, 0.5, None),
    "drinker_line_leak": (("animal-protection",), (20.0, 300.0), 0.5, None),
    "tank_fill_valve_failure": (("animal-protection",), None, 0.8, None),
    "incubator_fill_valve_failure": (INCUBATORS, None, 0.7, None),
    "device_supply_outage": (INCUBATORS + ("hvac", "animal-protection"), None, 0.2, None),
    "high_limit_failed": (INCUBATORS, None, 1.0, None),   # invisible on its own: must stay NORMAL
}


@dataclass
class EpisodeSpec:
    episode_id: str
    family: str
    variant: int
    seed: int
    duration_ticks: int
    config: dict
    faults: list = field(default_factory=list)
    benign: list = field(default_factory=list)
    initial: dict = field(default_factory=dict)
    tags: dict = field(default_factory=dict)
    split: str = "train"

    def as_dict(self):
        return asdict(self)


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def _base_config(rng, profile):
    weather = {"enabled": True, "start_hour": round(rng.uniform(0.0, 24.0), 2) % 24.0, **WEATHER_PROFILES[profile]}
    return {"weather": weather, "communications": {"owner_unreachable_probability": 0.1}}


def _benign(rng, duration):
    """Owner and farm activity that both twins share."""
    events = []
    for _ in range(rng.randint(0, 3)):
        start = rng.randint(WARMUP, max(WARMUP + 1, duration - 400))
        events.append({"kind": "door_open", "device": rng.choice(INCUBATORS), "tick": start,
                       "duration": rng.randint(20, 180)})
    if rng.random() < 0.3:
        start = rng.randint(WARMUP, max(WARMUP + 1, duration - 1200))
        events.append({"kind": "cleaning", "tick": start, "duration": rng.randint(600, 1800), "ammonia_ppm_s": 0.00003})
    if rng.random() < 0.3:
        start = rng.randint(WARMUP, max(WARMUP + 1, duration - 1200))
        events.append({"kind": "rain_shower", "tick": start, "duration": rng.randint(600, 1800),
                       "rain_mm_h": round(rng.uniform(2.0, 12.0), 1)})
    if rng.random() < 0.15:
        events.append({"kind": "owner_fan_setting", "tick": rng.randint(WARMUP, max(WARMUP + 1, duration - 200)),
                       "speed_pct": rng.choice((50, 70, 90))})
    return events


def _severity(rng, bounds, novel):
    if bounds is None:
        return None
    low, high = bounds
    span = high - low
    if novel:  # beyond the training sampler's inner 80 % range
        return round(rng.choice((rng.uniform(low, low + 0.1 * span), rng.uniform(high - 0.1 * span, high))), 3)
    return round(rng.uniform(low + 0.1 * span, high - 0.1 * span), 3)


def _equipment(family, variant, novel):
    rng = random.Random(_seed("equipment", family, variant, novel))
    devices, bounds, permanent, weather = EQUIPMENT_FAULTS[family]
    profile = weather or rng.choice(NOVEL_WEATHER if novel else TRAIN_WEATHER)
    duration = rng.randint(1800, 3000)
    device = rng.choice(devices)
    inject = WARMUP + rng.randint(0, 600)
    end = None if rng.random() < permanent else min(duration - 60, inject + rng.randint(300, 1200))
    faults = [{"kind": "equipment_fault", "fault": family, "device": device, "inject_tick": inject,
               "end_tick": end, "severity": _severity(rng, bounds, novel)}]
    initial = {}
    if family == "turning_motor_failure":
        initial[device] = {"seconds_since_last_turn": 10800.0 - (inject - WARMUP) - rng.randint(60, 600) - WARMUP}
    if family == "feeder_blocked":
        initial["animal-protection"] = {"feed_hopper_kg": {"barn-feeder-1": round(rng.uniform(1.6, 2.2), 2)}}
    if family == "tank_fill_valve_failure":  # the tank must be near its refill level for the valve to matter
        initial["animal-protection"] = {"water_level_pct": round(rng.uniform(58.0, 61.0), 2)}
    return EpisodeSpec("", family, variant, _seed(family, variant, novel), duration, _base_config(rng, profile),
                       faults, _benign(rng, duration), initial, {"device": device, "profile": profile, "novel": novel})


def _sensor_fault(variant, novel):
    rng = random.Random(_seed("sensor", variant, novel))
    owner = rng.choice(("incubator", "hvac", "animal-protection"))
    node = rng.choice(INCUBATORS) if owner == "incubator" else owner
    channel = rng.choice(SENSOR_CHANNELS[owner]).format(node=node)
    mode = rng.choice(("stuck", "bias", "drift", "dropout"))
    duration = rng.randint(1800, 2700)
    inject = WARMUP + rng.randint(0, 600)
    severity = {"stuck": None, "bias": round(rng.choice((-1, 1)) * rng.uniform(1.0, 4.0) * (2.0 if novel else 1.0), 2),
                "drift": round(rng.choice((-1, 1)) * rng.uniform(0.001, 0.005), 5), "dropout": None}[mode]
    benign = _benign(rng, duration)
    if channel.endswith("humidity_1") and owner == "hvac" and rng.random() < 0.7:
        # Consistency trap: rain should raise barn humidity; a stuck sensor will not.
        benign.append({"kind": "rain_shower", "tick": inject + 60, "duration": 1500, "rain_mm_h": 8.0})
        benign.append({"kind": "windows", "tick": inject, "position_pct": 60})
    profile = rng.choice(NOVEL_WEATHER if novel else TRAIN_WEATHER)
    faults = [{"kind": "sensor_fault", "channel": channel, "device": node, "mode": mode, "inject_tick": inject,
               "end_tick": None if rng.random() < 0.6 else inject + rng.randint(300, 1200), "severity": severity}]
    return EpisodeSpec("", "sensor_fault", variant, _seed("sensor_fault", variant, novel), duration,
                       _base_config(rng, profile), faults, benign, {}, {"device": node, "channel": channel,
                                                                         "mode": mode, "novel": novel})


def _overvoltage(variant, novel):
    rng = random.Random(_seed("overvoltage", variant, novel))
    pattern = rng.choice(("single", "shared", "recurring"))
    duration = 3600 if pattern == "recurring" else 1800
    faults, tick = [], WARMUP + rng.randint(0, 300)
    for _ in range(3 if pattern == "recurring" else 1):
        length = rng.randint(60, 240)
        voltage = round(rng.uniform(262.0, 275.0 if novel else 268.0), 1)
        faults.append({"kind": "overvoltage", "inject_tick": tick, "end_tick": tick + length, "voltage_v": voltage,
                       "shared": pattern == "shared"})
        tick += length + rng.randint(300, 600)
    return EpisodeSpec("", "overvoltage", variant, _seed("overvoltage", variant, novel), duration,
                       _base_config(rng, rng.choice(TRAIN_WEATHER)), faults, _benign(rng, duration), {},
                       {"pattern": pattern, "novel": novel})


def _fire(variant, novel):
    rng = random.Random(_seed("fire", variant, novel))
    zone = rng.choice(("barn", "storage", "workspace"))
    mode = rng.choice(("flaming", "smoldering"))
    growth = rng.choice(("slow", "medium", "fast", "ultrafast") if novel else ("slow", "medium", "fast"))
    length, width = {"barn": (12.5, 10.0), "storage": (7.5, 8.0), "workspace": (6.0, 5.5)}[zone]
    params = {"mode": mode, "growth": growth, "peak_kw": round(rng.uniform(80.0, 600.0), 1),
              "fuel_kg": round(rng.uniform(20.0, 120.0), 1),
              "position_m": [round(rng.uniform(1.0, length - 1.0), 2), round(rng.uniform(1.0, width - 1.0), 2)]}
    if mode == "smoldering":
        params["transition_to_flaming_s"] = round(rng.uniform(300.0, 1500.0), 1) if rng.random() < 0.6 else None
    duration = rng.randint(2100, 2700)
    unreachable = 1.0 if rng.random() < 0.2 else 0.0
    config = _base_config(rng, rng.choice(NOVEL_WEATHER if novel else TRAIN_WEATHER))
    config["communications"]["owner_unreachable_probability"] = unreachable
    faults = [{"kind": "fire", "tick": WARMUP + rng.randint(0, 600), "zone": zone, "params": params}]
    return EpisodeSpec("", "fire", variant, _seed("fire", variant, novel), duration, config, faults,
                       _benign(rng, duration), {}, {"zone": zone, "mode": mode, "growth": growth,
                                                    "owner_unreachable": bool(unreachable), "novel": novel})


def _weather(family, variant, novel):
    rng = random.Random(_seed(family, variant, novel))
    warned = rng.random() < 0.7
    strike = family == "tornado" and rng.random() < 0.5
    intensity = round(rng.uniform(0.6, 1.4) if not novel else rng.uniform(1.4, 2.0), 2)
    if family == "flood":
        duration = rng.randint(3 * 3600, 5 * 3600)
        event_ticks = duration - 3600
    else:
        duration = rng.randint(3000, 4200)
        event_ticks = rng.randint(1200, 2400)
    config = _base_config(rng, rng.choice(("mild", "cold", "windy")))
    config["communications"]["owner_unreachable_probability"] = 1.0 if rng.random() < 0.3 else 0.0
    if family == "flood" and rng.random() < 0.4:
        config.setdefault("animal_protection", {})["capabilities"] = {"flood_barrier": False}
    faults = [{"kind": "weather_event", "event": family, "inject_tick": WARMUP + rng.randint(300, 900),
               "duration_ticks": event_ticks, "watch_lead_ticks": rng.randint(300, 900), "issue_warning": warned,
               "tornado_strike": strike, "intensity": intensity}]
    return EpisodeSpec("", family, variant, _seed(family, variant, novel), duration, config, faults,
                       _benign(rng, duration), {}, {"warned": warned, "strike": strike, "intensity": intensity,
                                                    "barrier": not bool(config.get("animal_protection")),
                                                    "novel": novel})


def _outage(variant, novel):
    rng = random.Random(_seed("outage", variant, novel))
    long_outage = rng.random() < 0.4
    duration = 7200 if long_outage else 2400
    inject = WARMUP + rng.randint(0, 300)
    end = inject + (rng.randint(1800, 6000) if long_outage else rng.randint(120, 900))
    devices = list(INCUBATORS + ("hvac", "animal-protection")) if rng.random() < 0.7 else \
        rng.sample(list(INCUBATORS + ("hvac", "animal-protection")), 2)
    initial = {node: {"backup_battery_charge_pct": round(rng.uniform(5.0, 40.0), 1)} for node in devices
               if rng.random() < 0.4 and node != "hvac"}
    faults = [{"kind": "power_outage", "inject_tick": inject, "end_tick": min(end, duration - 60), "devices": devices}]
    return EpisodeSpec("", "power_outage", variant, _seed("power_outage", variant, novel), duration,
                       _base_config(rng, rng.choice(NOVEL_WEATHER if novel else TRAIN_WEATHER)), faults,
                       _benign(rng, duration), initial, {"long": long_outage, "devices": devices, "novel": novel})


def _water_cut(variant, novel):
    """The farm water main stops: both incubators and the drinker tank lose their supply at once."""
    rng = random.Random(_seed("water_cut", variant, novel))
    duration = rng.randint(3600, 7200) if novel else rng.randint(1800, 3600)
    inject = WARMUP + rng.randint(0, 600)
    end = None if rng.random() < 0.5 else min(duration - 60, inject + rng.randint(600, 1800))
    # Sometimes the drinker tank is near its refill level, so the cut also stops a refill.
    initial = {"animal-protection": {"water_level_pct": round(rng.uniform(58.0, 80.0), 2)}}
    faults = [{"kind": "water_cut", "inject_tick": inject, "end_tick": end}]
    return EpisodeSpec("", "water_cut", variant, _seed("water_cut", variant, novel), duration,
                       _base_config(rng, rng.choice(NOVEL_WEATHER if novel else TRAIN_WEATHER)), faults,
                       _benign(rng, duration), initial, {"restored": end is not None, "novel": novel})


def _double(variant, novel):
    rng = random.Random(_seed("double", variant, novel))
    combos = ("relay_and_high_limit", "hvac_fan_heat", "fire_and_isolation", "flood_and_outage")
    combo = rng.choice(combos[2:] if novel else combos[:2] + ("relay_and_high_limit",))
    duration = 2700
    faults, config = [], _base_config(rng, "hot" if combo == "hvac_fan_heat" else rng.choice(TRAIN_WEATHER))
    if combo == "relay_and_high_limit":
        device = rng.choice(INCUBATORS)
        faults += [{"kind": "equipment_fault", "fault": "high_limit_failed", "device": device, "inject_tick": 30},
                   {"kind": "equipment_fault", "fault": "heater_relay_stuck", "device": device,
                    "inject_tick": WARMUP + rng.randint(0, 300)}]
        if rng.random() < 0.5:
            config[{"incubator-1": "incubator1", "incubator-2": "incubator2"}[device]] = {"fan_control_enabled": False}
    elif combo == "hvac_fan_heat":
        faults.append({"kind": "equipment_fault", "fault": "supply_fan_failure", "device": "hvac",
                       "inject_tick": WARMUP + rng.randint(0, 300)})
        config["weather"].update(WEATHER_PROFILES["heatwave"] if novel else WEATHER_PROFILES["hot"])
    elif combo == "fire_and_isolation":
        tick = WARMUP + rng.randint(0, 300)
        faults += [{"kind": "fire", "tick": tick, "zone": rng.choice(("barn", "storage")),
                    "params": {"mode": "flaming", "growth": "medium", "peak_kw": 300.0, "fuel_kg": 60.0}},
                   {"kind": "isolate", "node": rng.choice(("hvac", "animal-protection")), "from_tick": tick - 60,
                    "until_tick": tick + 900}]
    else:
        duration = 4 * 3600
        faults += [{"kind": "weather_event", "event": "flood", "inject_tick": WARMUP + 300, "duration_ticks": 3 * 3600,
                    "watch_lead_ticks": 600, "issue_warning": True, "tornado_strike": False, "intensity": 1.5},
                   {"kind": "power_outage", "inject_tick": 3600, "end_tick": 7200,
                    "devices": list(INCUBATORS + ("hvac", "animal-protection"))}]
    return EpisodeSpec("", "double_fault", variant, _seed("double_fault", variant, novel), duration, config, faults,
                       _benign(rng, duration), {}, {"combo": combo, "novel": novel})


def _comm_loss(variant, novel):
    rng = random.Random(_seed("comm_loss", variant, novel))
    node = rng.choice(INCUBATORS + ("hvac", "animal-protection"))
    start = WARMUP + rng.randint(0, 600)
    duration = 2400
    faults = [{"kind": "isolate", "node": node, "from_tick": start, "until_tick": start + rng.randint(120, 1500)}]
    return EpisodeSpec("", "comm_loss", variant, _seed("comm_loss", variant, novel), duration,
                       _base_config(rng, rng.choice(TRAIN_WEATHER)), faults, _benign(rng, duration), {},
                       {"node": node, "novel": novel})


def _normal(variant, novel, hours=None):
    rng = random.Random(_seed("normal", variant, novel, hours))
    duration = int(hours * 3600) if hours else rng.randint(1800, 3600)
    profile = rng.choice(NOVEL_WEATHER if novel else TRAIN_WEATHER)
    return EpisodeSpec("", "normal", variant, _seed("normal", variant, novel, hours), duration,
                       _base_config(rng, profile), [], _benign(rng, duration), {}, {"profile": profile, "novel": novel,
                                                                                   "hours": hours})


PLAN = {
    # family: number of in-distribution variants
    **{name: 18 for name in EQUIPMENT_FAULTS},
    "sensor_fault": 48, "overvoltage": 30, "fire": 72, "thunderstorm": 16, "tornado": 20, "flood": 12,
    "power_outage": 30, "water_cut": 18, "double_fault": 36, "comm_loss": 20, "normal": 80,
}
NOVEL_PLAN = {"equipment": 54, "sensor_fault": 12, "overvoltage": 8, "fire": 18, "tornado": 6, "flood": 3,
              "power_outage": 8, "water_cut": 4, "double_fault": 12, "normal": 10}


def _make(family, variant, novel):
    if family in EQUIPMENT_FAULTS:
        return _equipment(family, variant, novel)
    return {"sensor_fault": _sensor_fault, "overvoltage": _overvoltage, "fire": _fire, "power_outage": _outage,
            "water_cut": _water_cut, "double_fault": _double, "comm_loss": _comm_loss,
            "normal": _normal}.get(family, None)(variant, novel) \
        if family not in ("thunderstorm", "tornado", "flood") else _weather(family, variant, novel)


def _splits(family, count):
    """Stratified 70/15/15 split, so every family is present in validation and test."""
    order = sorted(range(count), key=lambda v: _seed("split", family, v))
    held_out = max(2, round(count * 0.15)) if count >= 6 else 0
    split = {v: "train" for v in range(count)}
    split.update({v: "val" for v in order[:held_out]})
    split.update({v: "test" for v in order[held_out:2 * held_out]})
    return split


def build_library(plan=None, novel_plan=None) -> list[EpisodeSpec]:
    specs = []
    for family, count in (plan or PLAN).items():
        splits = _splits(family, count)
        for variant in range(count):
            spec = _make(family, variant, False)
            spec.split = splits[variant]
            specs.append(spec)
    equipment_names = sorted(EQUIPMENT_FAULTS)
    for family, count in (novel_plan or NOVEL_PLAN).items():
        for variant in range(count):
            name = equipment_names[variant % len(equipment_names)] if family == "equipment" else family
            spec = _make(name, 1000 + variant, True)
            spec.split = "novel_test"
            specs.append(spec)
    for n in range(4):  # 24-hour normal days for the false-alarm rate
        spec = _normal(5000 + n, n >= 2, hours=24)
        spec.family, spec.split = "normal_day_24h", "normal_day_test"
        specs.append(spec)
    for spec in specs:
        spec.episode_id = f"{spec.family}-{spec.split}-{spec.variant:04d}-{spec.seed % 100000:05d}"
    return specs


def library_hash(specs) -> str:
    return hashlib.sha256(json.dumps([s.as_dict() for s in specs], sort_keys=True).encode()).hexdigest()
