"""Per-machine node encoders v1: monitoring dataset + peers + memory -> fixed vector.

Every feature is always present with a validity flag, so missing or failed
measurements are explicit rather than silently zero. The feature list (and so
the schema hash) is fixed per machine type.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from math import cos, radians, sin
from typing import Any

from .contracts import ASSESSMENTS, MACHINE_TYPES, NODE_IDS
from .memory import NodeMemory

INCUBATOR_CODES = ("OVERVOLTAGE", "OVERVOLTAGE_PROTECTION", "OVERTEMPERATURE", "OVERTEMPERATURE_PROTECTION",
                   "FAN1_FAILURE", "FAN2_FAILURE", "HEATER_OPEN_CIRCUIT", "HEATER_RELAY_STUCK", "PUMP_FAULT",
                   "HUMIDIFIER_FAULT", "TURNING_FAULT", "DOOR_SWITCH_FAULT", "INTERNAL_POWER_FAULT", "LOW_WATER",
                   "WATER_LEAK", "POWER_OUTAGE", "BACKUP_BATTERY_LOW")
HVAC_CODES = ("SUPPLY_FAN_FAILURE", "RETURN_FAN_FAILURE", "COMPRESSOR_FAILURE", "CONDENSATE_OVERFLOW",
              "LOW_REFRIGERANT_PRESSURE", "FILTER_MAINTENANCE", "CONDENSATE_LEAK")
APU_CODES = ("SMOKE_DETECTED", "CO_HIGH", "AMMONIA_HIGH", "CO2_HIGH", "O2_LOW", "HEAT_STRESS", "FEEDER_BLOCKED",
             "FEED_EMPTY", "WATER_PUMP_FAULT", "WATER_LEAK", "MAINS_POWER_FAILURE", "BACKUP_BATTERY_LOW",
             "WATER_INTRUSION", "SMOKE_DETECTOR_FAILED", "WATER_SUPPLY_LOST", "TANK_FILL_FAULT")


@dataclass(frozen=True)
class EncodedNodeInput:
    machine: str
    feature_names: tuple[str, ...]
    values: tuple[float, ...]
    validity: tuple[float, ...]
    own_names: tuple[str, ...]      # the own-machine numeric features a world model predicts
    schema_hash: str

    @property
    def vector(self) -> list[float]:
        return [*self.values, *self.validity]


class _Builder:
    def __init__(self):
        self.names, self.values, self.validity, self.kinds = [], [], [], []

    def num(self, name, value, low, high):
        ok = value is not None and isinstance(value, (int, float)) and not isinstance(value, bool)
        self.names.append(name)
        self.kinds.append("number")
        self.values.append(max(-1.0, min(2.0, (float(value) - low) / (high - low))) if ok else 0.0)
        self.validity.append(1.0 if ok else 0.0)

    def flag(self, name, value):
        self.names.append(name)
        self.kinds.append("flag")
        self.values.append(1.0 if value else 0.0)
        self.validity.append(0.0 if value is None else 1.0)

    def onehot(self, name, value, options):
        for option in options:
            self.flag(f"{name}={option}", None if value is None else value == option)

    def codes(self, prefix, present, catalogue, known=True):
        for code in catalogue:
            self.flag(f"{prefix}.{code}", (code in present) if known else None)


def _codes(monitoring):
    return {alarm["code"] for alarm in (monitoring or {}).get("alarms", []) if isinstance(alarm, dict)}


def _incubator(b: _Builder, m: dict | None):
    m = m or {}
    temps = m.get("room_temperatures_c") or [None] * 3
    hums = m.get("room_humidities_pct") or [None] * 3
    for n in range(3): b.num(f"room_temperature_{n + 1}", temps[n] if n < len(temps) else None, 20.0, 45.0)
    b.num("heater_outlet", m.get("heater_outlet_temperature_c"), 20.0, 90.0)
    for n in range(3): b.num(f"room_humidity_{n + 1}", hums[n] if n < len(hums) else None, 0.0, 100.0)
    surroundings = m.get("environment") or {}
    b.num("ambient_temperature", surroundings.get("ambient_temperature_c"), 0.0, 60.0)
    b.num("ambient_humidity", surroundings.get("ambient_humidity_pct"), 0.0, 100.0)
    heating, fans = m.get("heating") or {}, m.get("fans") or {}
    b.flag("heater_on", None if not heating else heating.get("state") == "on")
    b.num("heater_output_pct", heating.get("output_pct"), 0.0, 100.0)
    b.num("heater_power_w", heating.get("power_w"), 0.0, 400.0)
    b.num("heater_outlet_minus_room", heating.get("outlet_minus_room_c"), -5.0, 40.0)
    b.num("fan_command_pct", fans.get("command_pct"), 0.0, 100.0)
    rpm = fans.get("rpm") or [None, None]
    for n in range(2): b.num(f"fan{n + 1}_rpm", rpm[n] if n < len(rpm) else None, 0.0, 2500.0)
    water, hum = m.get("water") or {}, m.get("humidification") or {}
    b.num("reservoir_pct", water.get("reservoir_pct"), 0.0, 100.0)
    b.num("water_inlet_ml_min", water.get("inlet_flow_ml_min"), 0.0, 20.0)
    b.flag("humidifier_on", None if not hum else hum.get("state") == "on")
    b.flag("pump_on", None if not hum else hum.get("pump_on"))
    door = (m.get("door") or {}).get("position")
    b.onehot("door", door, ("open", "closed", "unknown"))
    b.num("door_open_s", (m.get("door") or {}).get("open_duration_s"), 0.0, 3600.0)
    eggs = m.get("eggs") or {}
    b.num("incubation_day", eggs.get("incubation_day"), 0.0, 21.0)
    b.onehot("phase", eggs.get("phase"), ("incubation", "lockdown", "hatching"))
    turning = m.get("egg_turning") or {}
    b.num("since_last_turn_h", None if turning.get("seconds_since_last_turn") is None else turning["seconds_since_last_turn"] / 3600.0, 0.0, 6.0)
    electricity = m.get("electricity") or {}
    b.num("total_power_w", electricity.get("total_power_w"), 0.0, 500.0)
    b.onehot("supply", electricity.get("supply_status"), ("normal", "undervoltage", "overvoltage", "outage"))
    b.onehot("power_source", electricity.get("power_source"), ("mains", "battery", "none"))
    safety = m.get("safety") or {}
    b.flag("overtemperature_trip", safety.get("overtemperature_trip"))
    b.flag("emergency_stop", safety.get("emergency_stop"))
    b.num("battery_pct", safety.get("backup_battery_charge_pct"), 0.0, 100.0)
    b.onehot("health", m.get("health"), ("normal", "degraded", "fault"))
    b.codes("code", _codes(m), INCUBATOR_CODES, bool(m))


def _hvac(b: _Builder, m: dict | None):
    m = m or {}
    env = m.get("environment") or {}
    for zone in ("barn", "storage", "workspace"):
        z = env.get(zone) or {}
        b.num(f"{zone}_temperature", z.get("temperature_c"), 0.0, 60.0)
        b.num(f"{zone}_humidity", z.get("humidity_pct"), 0.0, 100.0)
        b.num(f"{zone}_sensors_lost", None if not z else sum(v is None for v in z.get("temperature_sensors_c", [])), 0.0, 3.0)
    outdoor = m.get("outdoor") or {}
    b.num("outdoor_temperature", outdoor.get("temperature_c"), -20.0, 45.0)
    b.num("outdoor_humidity", outdoor.get("humidity_pct"), 0.0, 100.0)
    air = m.get("air_handling") or {}
    b.num("supply_temperature", air.get("supply_temperature_c"), 0.0, 60.0)
    b.num("return_temperature", air.get("return_temperature_c"), 0.0, 60.0)
    b.num("supply_humidity", air.get("supply_humidity_pct"), 0.0, 100.0)
    b.num("supply_airflow", air.get("supply_airflow_m3_h"), 0.0, 2000.0)
    b.num("return_airflow", air.get("return_airflow_m3_h"), 0.0, 2000.0)
    b.num("duct_static_pa", air.get("duct_static_pressure_pa"), 0.0, 150.0)
    b.num("filter_dp_pa", air.get("filter_differential_pressure_pa"), 0.0, 150.0)
    b.num("outdoor_air_pct", air.get("outdoor_air_fraction_pct"), 0.0, 100.0)
    cooling, heating, fans = m.get("cooling") or {}, m.get("heating") or {}, m.get("fans") or {}
    b.flag("compressor_on", None if not cooling else cooling.get("state") == "on")
    b.num("cooling_output_pct", cooling.get("output_pct"), 0.0, 100.0)
    b.num("cooling_delivered_w", cooling.get("delivered_w"), 0.0, 12000.0)
    b.num("refrigerant_kpa", cooling.get("refrigerant_pressure_kpa"), 400.0, 3500.0)
    b.num("heating_output_pct", heating.get("output_pct"), 0.0, 100.0)
    b.num("supply_fan_rpm", fans.get("supply_rpm"), 0.0, 2500.0)
    b.num("return_fan_rpm", fans.get("return_rpm"), 0.0, 2000.0)
    b.num("fan_command_pct", fans.get("command_pct"), 0.0, 100.0)
    b.onehot("mode", m.get("mode"), ("automatic", "manual", "cooling", "ventilation", "emergency_ventilation",
                                     "fire_shutdown", "fault"))
    deviations = (m.get("deviations") or {}).get("temperature_c") or {}
    for zone in ("barn", "storage", "workspace"): b.num(f"{zone}_deviation", deviations.get(zone), -10.0, 20.0)
    b.onehot("supply", (m.get("electricity") or {}).get("supply_status"), ("normal", "undervoltage", "overvoltage", "outage"))
    b.num("total_power_w", (m.get("electricity") or {}).get("total_power_w"), 0.0, 12000.0)
    b.onehot("filter", (m.get("maintenance") or {}).get("filter_status"), ("normal", "maintenance_due", "clogged"))
    b.onehot("health", m.get("health"), ("normal", "degraded", "fault"))
    b.codes("code", _codes(m), HVAC_CODES, bool(m))


def _apu(b: _Builder, m: dict | None):
    m = m or {}
    food, water, air = m.get("food") or {}, m.get("water") or {}, m.get("air_quality") or {}
    b.num("feed_storage_pct", food.get("storage_level_pct"), 0.0, 100.0)
    hoppers = list((food.get("hopper_levels_pct") or {}).values())
    b.num("feed_hopper_pct", sum(hoppers) / len(hoppers) if hoppers else None, 0.0, 100.0)
    b.num("feed_consumption_kg_h", food.get("consumption_kg_h"), 0.0, 1.0)
    b.onehot("dispensing", food.get("dispensing_state"), ("on", "off", "fault"))
    b.num("water_tank_pct", water.get("tank_level_pct"), 0.0, 100.0)
    b.num("water_flow_l_min", water.get("flow_l_min"), 0.0, 5.0)
    b.num("water_pressure_kpa", water.get("pressure_kpa"), 0.0, 300.0)
    b.num("water_consumption_l_h", water.get("consumption_l_h"), 0.0, 2.0)
    b.num("water_supply_kpa", water.get("supply_pressure_kpa"), 0.0, 400.0)
    b.num("water_supply_flow_l_min", water.get("supply_flow_l_min"), 0.0, 5.0)
    b.flag("water_refill_on", water.get("refill_on"))
    for key, low, high in (("co2_ppm", 300.0, 6000.0), ("o2_pct", 15.0, 21.5), ("ammonia_ppm", 0.0, 50.0), ("co_ppm", 0.0, 200.0)):
        b.num(key, (air.get(key) or {}).get("barn"), low, high)
    b.flag("smoke_detected", air.get("smoke_detected"))
    b.num("smoke_obscuration", air.get("smoke_obscuration_pct_m"), 0.0, 40.0)
    b.onehot("air_safety", air.get("safety_state"), ("normal", "warning", "critical", "unknown"))
    env = m.get("animal_environment") or {}
    b.num("barn_temperature", env.get("temperature_c"), 0.0, 60.0)
    b.num("barn_humidity", env.get("humidity_pct"), 0.0, 100.0)
    weather = m.get("weather") or {}
    b.num("outside_temperature", weather.get("external_temperature_c"), -20.0, 45.0)
    b.num("wind_m_s", weather.get("wind_speed_m_s"), 0.0, 50.0)
    direction = weather.get("wind_direction_deg")
    b.num("wind_dir_sin", None if direction is None else sin(radians(direction)), -1.0, 1.0)
    b.num("wind_dir_cos", None if direction is None else cos(radians(direction)), -1.0, 1.0)
    b.num("rain_mm_h", weather.get("rainfall_mm_h"), 0.0, 80.0)
    b.num("pressure_hpa", weather.get("pressure_hpa"), 960.0, 1050.0)
    b.num("pressure_tendency", weather.get("pressure_tendency_hpa_3h"), -15.0, 15.0)
    b.num("floodwater_cm", weather.get("floodwater_level_cm"), 0.0, 60.0)
    b.flag("water_intrusion", weather.get("water_intrusion"))
    alerts = m.get("disaster_alerts") or {}
    b.onehot("risk", alerts.get("risk_state"), ("normal", "watch", "warning", "emergency", "unknown"))
    b.flag("watch_active", None if not alerts else bool(alerts.get("watch")))
    for key in ("severe_weather_warning", "tornado_warning", "flood_warning"): b.flag(key, alerts.get(key))
    building = m.get("building_protection") or {}
    windows = list((building.get("windows_pct") or {}).values()); vents = list((building.get("vents_pct") or {}).values())
    b.num("windows_pct", sum(windows) / len(windows) if windows else None, 0.0, 100.0)
    b.num("vents_pct", sum(vents) / len(vents) if vents else None, 0.0, 100.0)
    gates = list((building.get("gates") or {}).values())
    b.num("gates_closed_fraction", sum(g == "closed" for g in gates) / len(gates) if gates else None, 0.0, 1.0)
    b.onehot("flood_barrier", building.get("flood_barrier"), ("ready", "deploying", "deployed", "retracting"))
    b.onehot("shutters", building.get("storm_shutters"), ("standby", "deploying", "deployed", "retracting"))
    readiness = m.get("emergency_readiness") or {}
    b.num("battery_pct", readiness.get("backup_battery_charge_pct"), 0.0, 100.0)
    b.flag("mains_normal", None if not readiness else readiness.get("mains_power_status") == "normal")
    b.flag("alarm_system_active", None if not readiness else readiness.get("alarm_system") == "active")
    welfare = m.get("animal_welfare") or {}
    b.num("activity_pct", (welfare.get("activity") or {}).get("activity_index_pct") if isinstance(welfare.get("activity"), dict) else None, 0.0, 100.0)
    b.num("welfare_pct", welfare.get("welfare_index_pct"), 0.0, 100.0)
    b.num("safety_conflicts", None if not m else len(m.get("safety_conflicts") or []), 0.0, 3.0)
    b.codes("code", _codes(m), APU_CODES, bool(m))


_MACHINE_ENCODERS = {"incubator": _incubator, "hvac": _hvac, "animal_protection": _apu}
_PEER_KEYS = ("temperature_mean_c", "humidity_mean_pct", "heater_power_w", "supply_temperature_c", "return_temperature_c",
              "supply_airflow_m3_h", "zone_sensors_lost", "co_ppm", "co2_ppm", "smoke_obscuration_pct_m",
              "floodwater_level_cm", "wind_speed_m_s", "water_inlet_flow_ml_min", "water_supply_kpa",
              # temperatures each machine sees around it: what lets a node link its own warming to another's
              "room_temperature_c", "barn_temperature_c", "storage_temperature_c", "outside_temperature_c")
_PEER_RANGES = {"temperature_mean_c": (20, 45), "humidity_mean_pct": (0, 100), "heater_power_w": (0, 400),
                "supply_temperature_c": (0, 60), "return_temperature_c": (0, 60), "supply_airflow_m3_h": (0, 2000),
                "zone_sensors_lost": (0, 3), "co_ppm": (0, 200), "co2_ppm": (300, 6000),
                "smoke_obscuration_pct_m": (0, 40), "floodwater_level_cm": (0, 60), "wind_speed_m_s": (0, 50),
                "water_inlet_flow_ml_min": (0, 20), "water_supply_kpa": (0, 400), "room_temperature_c": (0, 60),
                "barn_temperature_c": (0, 60), "storage_temperature_c": (0, 60), "outside_temperature_c": (-20, 45)}


class NodeEncoderV1:
    VERSION = 1

    def __init__(self, node_id: str):
        self.node_id = node_id
        self.machine = MACHINE_TYPES[node_id]
        others = [n for n in NODE_IDS if n != node_id]
        # Fixed role order: the other incubator first for incubators, then by node id.
        self.peer_slots = tuple(sorted(others, key=lambda n: (MACHINE_TYPES[n] != self.machine, n)))
        probe = self._build(None, 0.0, {}, NodeMemory(node_id), None, 0.0)
        self.feature_names = tuple(probe.names)
        self.feature_kinds = tuple(probe.kinds)
        self.own_names = tuple(name for name in self.feature_names if name.startswith("own."))
        self.schema_hash = hashlib.sha256(json.dumps({"version": self.VERSION, "machine": self.machine,
                                                      "features": self.feature_names}).encode()).hexdigest()

    @property
    def dimension(self) -> int:
        return 2 * len(self.feature_names)

    def _build(self, monitoring, observation_age_s, peers, memory, comms_status, time_s, step_seconds=1.0):
        own = _Builder()
        _MACHINE_ENCODERS[self.machine](own, monitoring)
        b = _Builder()
        for name, value, valid, kind in zip(own.names, own.values, own.validity, own.kinds):
            b.names.append(f"own.{name}"); b.values.append(value); b.validity.append(valid); b.kinds.append(kind)
        b.num("observation_age_s", observation_age_s, 0.0, 300.0)
        for slot in self.peer_slots:
            message = peers.get(slot)
            # Role names, so both incubators share one input schema (and one trained brain).
            label = "other_incubator" if self.machine == "incubator" and MACHINE_TYPES[slot] == "incubator" else slot
            prefix = f"peer.{label}"
            b.flag(f"{prefix}.present", message is not None)
            b.num(f"{prefix}.age_s", None if message is None else max(0.0, time_s - message.sample_tick * step_seconds), 0.0, 300.0)
            # Data age above can grow because the peer's machine is down; this is the link itself.
            b.num(f"{prefix}.silent_s", None if message is None else max(0.0, time_s - message.sent_tick * step_seconds), 0.0, 300.0)
            b.onehot(f"{prefix}.assessment", None if message is None else message.assessment, ASSESSMENTS)
            b.num(f"{prefix}.confidence", None if message is None else message.confidence, 0.0, 1.0)
            b.flag(f"{prefix}.incident", None if message is None else message.incident_id is not None)
            readings = {} if message is None else message.key_readings
            for key in _PEER_KEYS:
                low, high = _PEER_RANGES[key]
                b.num(f"{prefix}.{key}", readings.get(key), low, high)
            b.flag(f"{prefix}.smoke_detected", readings.get("smoke_detected") if readings else None)
            b.flag(f"{prefix}.water_intrusion", readings.get("water_intrusion") if readings else None)
            b.flag(f"{prefix}.tornado_warning", readings.get("tornado_warning") if readings else None)
            b.flag(f"{prefix}.humidifier_pump_on", readings.get("humidifier_pump_on") if readings else None)
        for name, value in memory.context(time_s, comms_status).items():
            b.num(f"memory.{name}", value, 0.0, {"incident_age_h": 24.0, "recurrence_24h": 10.0,
                                                 "peers_same_incident": 3.0, "minutes_since_owner_notified": 60.0,
                                                 "owner_notified_priority": 4.0, "water_dry_pumping_min": 60.0}.get(name, 1.0))
        return b

    def encode(self, monitoring: dict | None, *, observation_age_s: float, peers: dict[str, Any],
               memory: NodeMemory, comms_status: dict | None, time_s: float, step_seconds: float = 1.0) -> EncodedNodeInput:
        b = self._build(monitoring, observation_age_s, peers, memory, comms_status, time_s, step_seconds)
        if tuple(b.names) != self.feature_names:
            raise RuntimeError("node encoder feature order changed")
        return EncodedNodeInput(self.machine, self.feature_names, tuple(b.values), tuple(b.validity),
                                self.own_names, self.schema_hash)
