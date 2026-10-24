"""Deterministic evidence from what a node can actually see.

Shared by the rule-based node and the safety gate. Inputs are only a node's own
monitoring dataset, peer messages, and its own memory; never plant truth.
"""
from __future__ import annotations

from typing import Any


def _codes(monitoring: dict | None) -> set[str]:
    if not monitoring:
        return set()
    return {alarm["code"] for alarm in monitoring.get("alarms", []) if isinstance(alarm, dict)}


def key_readings(machine: str, monitoring: dict | None) -> dict[str, Any]:
    """The compact readings a node shares with its peers."""
    if not monitoring:
        return {"online": False}
    codes = sorted(_codes(monitoring))
    if machine == "incubator":
        temps = [t for t in monitoring["room_temperatures_c"] if t is not None]
        hums = [h for h in monitoring["room_humidities_pct"] if h is not None]
        return {"online": True, "temperature_mean_c": round(sum(temps) / len(temps), 3) if temps else None,
                "temperature_max_c": max(temps) if temps else None,
                "humidity_mean_pct": round(sum(hums) / len(hums), 2) if hums else None,
                "heater_power_w": monitoring["heating"]["power_w"],
                "power_source": monitoring["electricity"]["power_source"],
                "overtemperature_trip": monitoring["safety"]["overtemperature_trip"],
                "water_inlet_flow_ml_min": monitoring["water"].get("inlet_flow_ml_min"),
                "humidifier_pump_on": monitoring["humidification"]["pump_on"],
                "room_temperature_c": (monitoring.get("environment") or {}).get("ambient_temperature_c"), "codes": codes}
    if machine == "hvac":
        env = monitoring["environment"]
        zones = {name: zone["temperature_c"] for name, zone in env.items()}
        lost = sum(1 for zone in env.values() for value in zone["temperature_sensors_c"] if value is None)
        air = monitoring["air_handling"]
        heating = (monitoring.get("heating") or {}).get("output_pct") or 0.0
        return {"online": True, "zone_temperatures_c": zones, "zone_sensors_lost": lost, "heating_on": heating > 1.0,
                "barn_temperature_c": zones.get("barn"), "room_temperature_c": zones.get("workspace"),
                "storage_temperature_c": zones.get("storage"),
                "supply_temperature_c": air["supply_temperature_c"], "return_temperature_c": air["return_temperature_c"],
                "supply_airflow_m3_h": air["supply_airflow_m3_h"], "mode": monitoring["mode"], "codes": codes}
    air = monitoring["air_quality"]
    weather = monitoring["weather"]
    alerts = monitoring["disaster_alerts"]
    return {"online": True, "co_ppm": (air["co_ppm"] or {}).get("barn"), "co2_ppm": (air["co2_ppm"] or {}).get("barn"),
            "ammonia_ppm": (air["ammonia_ppm"] or {}).get("barn"), "o2_pct": (air["o2_pct"] or {}).get("barn"),
            "smoke_detected": air["smoke_detected"], "smoke_obscuration_pct_m": air["smoke_obscuration_pct_m"],
            "barn_temperature_c": monitoring["animal_environment"]["temperature_c"],
            "outside_temperature_c": monitoring["weather"].get("external_temperature_c"),
            "floodwater_level_cm": weather["floodwater_level_cm"], "water_intrusion": weather["water_intrusion"],
            "wind_speed_m_s": weather["wind_speed_m_s"], "risk_state": alerts["risk_state"],
            "tornado_warning": alerts["tornado_warning"], "flood_warning": alerts["flood_warning"],
            "severe_weather_warning": alerts["severe_weather_warning"],
            "water_supply_kpa": monitoring["water"].get("supply_pressure_kpa"), "codes": codes}


def _readings_by_machine(machine: str, monitoring: dict | None, peers: dict[str, Any]) -> dict[str, dict]:
    """Own readings plus the latest key readings each peer shared, by machine type."""
    from .contracts import MACHINE_TYPES
    result: dict[str, list[dict]] = {"incubator": [], "hvac": [], "animal_protection": []}
    if monitoring:
        result[machine].append(key_readings(machine, monitoring))
    for sender, message in peers.items():
        readings = message.key_readings if hasattr(message, "key_readings") else message.get("key_readings", {})
        if readings.get("online"):
            result[MACHINE_TYPES[sender]].append(readings)
    return result


def fire_indicators(machine: str, monitoring: dict | None, peers: dict[str, Any], memory=None) -> list[str]:
    """Independent families of fire evidence visible to this node."""
    seen = _readings_by_machine(machine, monitoring, peers)
    families = []
    apu = seen["animal_protection"]
    if any(r.get("smoke_detected") for r in apu):
        families.append("smoke_detector")
    if any((r.get("co_ppm") or 0) >= 35 for r in apu):
        families.append("carbon_monoxide")
    hvac = seen["hvac"]
    hot_zone = any((t or 0) >= 45.0 for r in hvac for t in (r.get("zone_temperatures_c") or {}).values())
    lost_hot = bool(memory is not None and memory.sensor_lost_while_hot())
    if hot_zone or lost_hot or any((r.get("zone_sensors_lost") or 0) > 0 and lost_hot for r in hvac):
        families.append("room_heat")
    # Hot supply air is expected while the air handler heats; only return air (or supply air
    # with the heater off) counts as fire evidence from the ducts.
    if any((r.get("return_temperature_c") or 0) >= 40.0 or
           ((r.get("supply_temperature_c") or 0) >= 35.0 and (r.get("supply_airflow_m3_h") or 0) > 100
            and not r.get("heating_on")) for r in hvac):
        families.append("duct_heat")
    return families


def gas_hazard(machine: str, monitoring: dict | None, peers: dict[str, Any]) -> list[str]:
    seen = _readings_by_machine(machine, monitoring, peers)["animal_protection"]
    signs = []
    if any((r.get("co2_ppm") or 0) >= 3000 for r in seen): signs.append("co2_high")
    if any((r.get("ammonia_ppm") or 0) >= 25 for r in seen): signs.append("ammonia_high")
    if any(r.get("o2_pct") is not None and r["o2_pct"] < 19.5 for r in seen): signs.append("oxygen_low")
    if any((r.get("co_ppm") or 0) >= 100 for r in seen): signs.append("carbon_monoxide_dangerous")
    return signs


def flood_state(monitoring: dict | None, peers: dict[str, Any], machine: str) -> dict[str, Any]:
    seen = _readings_by_machine(machine, monitoring, peers)["animal_protection"]
    level = max([(r.get("floodwater_level_cm") or 0.0) for r in seen] or [0.0])
    return {"level_cm": level, "intrusion": any(r.get("water_intrusion") for r in seen),
            "warning": any(r.get("flood_warning") for r in seen)}


def tornado_state(monitoring: dict | None, peers: dict[str, Any], machine: str) -> dict[str, Any]:
    seen = _readings_by_machine(machine, monitoring, peers)["animal_protection"]
    warning = any(r.get("tornado_warning") for r in seen)
    wind = max([(r.get("wind_speed_m_s") or 0.0) for r in seen] or [0.0])
    # Only the warning feed establishes a tornado; local wind then confirms a strike.
    return {"warning": warning, "strike": warning and wind >= 33.0, "extreme_wind": wind >= 33.0, "wind_m_s": wind}


def farm_water_cut(machine: str, monitoring: dict | None, peers: dict[str, Any], memory=None) -> bool:
    """The farm water main is down: Animal Protection sees no pressure on it, or two incubators run dry at once."""
    seen = _readings_by_machine(machine, monitoring, peers)
    if any(r.get("water_supply_kpa") is not None and r["water_supply_kpa"] < 50.0 or "WATER_SUPPLY_LOST" in (r.get("codes") or ())
           for r in seen["animal_protection"]):
        return True
    if machine != "incubator" or memory is None or memory.dry_pumping_s < 120.0:
        return False
    return any(r.get("humidifier_pump_on") and (r.get("water_inlet_flow_ml_min") or 0.0) < 0.5
               for sender, r in ((s, getattr(m, "key_readings", {})) for s, m in peers.items())
               if r.get("online") and sender.startswith("incubator"))


def incubator_overheating_despite_cutoffs(monitoring: dict | None) -> bool:
    if not monitoring:
        return False
    temps = [t for t in monitoring["room_temperatures_c"] if t is not None]
    hot = (max(temps) if temps else 0.0) >= 40.0 or (monitoring["heater_outlet_temperature_c"] or 0.0) >= 70.0
    still_heating = monitoring["heating"]["power_w"] > 5.0
    codes = _codes(monitoring)
    return hot and still_heating and ("HEATER_RELAY_STUCK" in codes or monitoring["safety"]["overtemperature_trip"])
