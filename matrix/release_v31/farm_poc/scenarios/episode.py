"""Run one scenario episode (or its fault-free twin) and record what every node saw.

Both runs use the same seed, configuration, weather, and benign events; only the
spec's ``faults`` differ. Rule-based nodes run in both, so peer messages exist
exactly as they would in deployment (stage 1 of training uses these messages).
Per tick and node the recorder keeps the CTM input the node would have had,
plus the rule brain's output (the baseline). The fault run also records a
compact plant-truth summary for labelling; none of it is part of any input.
"""
from __future__ import annotations

from copy import deepcopy

import numpy as np

from ..matrix.engine import MatrixEngine
from ..matrix.equipment_faults import (EquipmentFaultScenario, PowerOutageScenario, SensorChannelFaultScenario,
                                       WaterCutScenario, WeatherEventScenario)
from ..nodes import NODE_IDS, NodeRuntime
from ..nodes.contracts import ASSESSMENTS, COMMUNICATIONS, DANGER_ACTIONS, DIAGNOSES, MACHINE_TYPES
from .library import EpisodeSpec

# Severity scale for generic sensor faults, by channel family (bias in sensor units, drift in units/s).
_SENSOR_SCALE = {"co2_ppm": 60.0, "co_ppm": 6.0, "ammonia_ppm": 3.0, "rain_gauge": 3.0, "flood_gauge": 3.0}


def _sensor_scale(channel):
    return next((scale for key, scale in _SENSOR_SCALE.items() if channel.endswith(key)), 1.0)


def _apply_initial(engine, spec):
    for node, values in (spec.initial or {}).items():
        internal = engine.world.devices[node].internal
        for key, value in values.items():
            if isinstance(value, dict) and isinstance(internal.get(key), dict):
                internal[key].update(value)
            else:
                internal[key] = value


def _schedule(engine, tick, callback, name):
    if tick >= engine.clock.tick:
        engine.clock.schedule(int(tick), callback, name)


def _benign(engine, runtime, spec):
    has_weather = any(f["kind"] == "weather_event" for f in spec.faults)
    for event in spec.benign:
        kind, tick = event["kind"], int(event["tick"])
        if kind == "door_open":
            internal = engine.world.devices[event["device"]].internal
            _schedule(engine, tick, lambda i=internal: i.__setitem__("door_open", True), "benign_door_open")
            _schedule(engine, tick + int(event["duration"]), lambda i=internal: i.__setitem__("door_open", False),
                      "benign_door_close")
        elif kind == "cleaning":
            sources = engine.physical_world.state["benign_sources"]
            rate = float(event["ammonia_ppm_s"])
            _schedule(engine, tick, lambda: sources.__setitem__("ammonia_ppm_s", rate), "benign_cleaning")
            _schedule(engine, tick + int(event["duration"]), lambda: sources.__setitem__("ammonia_ppm_s", 0.0),
                      "benign_cleaning_end")
        elif kind == "rain_shower" and not has_weather:
            world = engine.physical_world
            rain = float(event["rain_mm_h"])
            _schedule(engine, tick, lambda: world.start_storm(pressure_drop_hpa=1.0, wind_m_s=1.0, gust_m_s=2.5,
                                                              rain_mm_h=rain, cooling_c=1.0, ramp_s=300.0),
                      "benign_rain")
            _schedule(engine, tick + int(event["duration"]), world.end_storm, "benign_rain_end")
        elif kind == "windows":
            controller = engine.controllers["animal-protection"]
            position = float(event["position_pct"])
            _schedule(engine, tick, lambda: setattr(controller, "base_windows", position), "benign_windows")
        elif kind == "owner_fan_setting":
            speed = int(event["speed_pct"])
            _schedule(engine, tick, lambda: engine.submit_command("hvac", "set_fan", {"speed_pct": speed}, source="owner"),
                      "benign_owner_fan")


def _overvoltage(engine, fault):
    devices = ("incubator-1", "incubator-2") if fault.get("shared") else ("incubator-1",)
    voltage = float(fault["voltage_v"])

    def start():
        for device in devices:
            engine.world.devices[device].internal["ac_voltage_v"] = voltage
        engine.hidden_truth = {"scenario": "overvoltage", "fault_label": "supply_overvoltage", "devices": list(devices)}
        engine.fault_log.append({"tick": engine.clock.tick, "transition": "injected", "fault_label": "supply_overvoltage",
                                 "devices": list(devices), "voltage_v": voltage})

    def stop():
        for device in devices:
            engine.world.devices[device].internal["ac_voltage_v"] = 230.0
        engine.fault_log.append({"tick": engine.clock.tick, "transition": "recovered", "fault_label": "supply_overvoltage",
                                 "devices": list(devices)})
    _schedule(engine, fault["inject_tick"], start, "overvoltage_start")
    _schedule(engine, fault["end_tick"], stop, "overvoltage_end")


def _fire(engine, fault):
    params = dict(fault["params"])
    if "position_m" in params:
        params["position_m"] = tuple(params["position_m"])

    def start():
        fire = engine.physical_world.ignite(fault["zone"], **params)
        engine.hidden_truth = {"scenario": "fire", "fault_label": f"{fault['zone']}_fire", "fire": fire.public_summary()}
        engine.fault_log.append({"tick": engine.clock.tick, "transition": "injected", "fault_label": "fire",
                                 "zone": fault["zone"]})
    _schedule(engine, fault["tick"], start, "fire_ignition")


def _faults(engine, runtime, spec):
    for fault in spec.faults:
        kind = fault["kind"]
        if kind == "equipment_fault":
            engine.start_equipment_fault(EquipmentFaultScenario(fault["fault"], fault["device"], int(fault["inject_tick"]),
                                                                fault.get("end_tick"), fault.get("severity")))
        elif kind == "sensor_fault":
            scale = _sensor_scale(fault["channel"])
            severity = fault.get("severity") or 0.0
            engine.start_sensor_channel_fault(SensorChannelFaultScenario(
                fault["channel"], fault["mode"], int(fault["inject_tick"]), fault.get("end_tick"),
                offset=severity * scale if fault["mode"] == "bias" else 0.0,
                rate_per_s=severity * scale if fault["mode"] == "drift" else 0.0))
        elif kind == "overvoltage":
            _overvoltage(engine, fault)
        elif kind == "fire":
            _fire(engine, fault)
        elif kind == "weather_event":
            engine.start_weather_event(WeatherEventScenario(
                fault["event"], int(fault["inject_tick"]), int(fault["duration_ticks"]), int(fault["watch_lead_ticks"]),
                bool(fault["tornado_strike"]), bool(fault["issue_warning"]), float(fault["intensity"])))
        elif kind == "power_outage":
            engine.start_power_outage(PowerOutageScenario(int(fault["inject_tick"]), fault.get("end_tick"),
                                                          tuple(fault["devices"])))
        elif kind == "water_cut":
            engine.start_water_cut(WaterCutScenario(int(fault["inject_tick"]), fault.get("end_tick")))
        elif kind == "isolate":
            runtime.bus.isolate(fault["node"], int(fault["from_tick"]), int(fault["until_tick"]))
        else:
            raise ValueError(f"unknown fault kind {kind}")


def _active_faults(engine):
    """Injected faults active right now, per device (from the evaluator-only fault log)."""
    active: dict[str, set] = {node: set() for node in NODE_IDS}
    for entry in engine.fault_log:
        label = entry.get("fault_label")
        devices = entry.get("devices") or ([entry["device_id"]] if entry.get("device_id") else [])
        if not devices and entry["transition"] != "injected":
            devices = [node for node in NODE_IDS if label in active[node]]  # a recovery that names no device ends it everywhere
        for device in devices:
            if device not in active:
                continue
            if entry["transition"] == "injected":
                active[device].add(label)
            else:
                active[device].discard(label)
    return active


def _truth(engine, runtime):
    world = engine.physical_world
    state = world.state
    fire = world.fire
    outside = state["outside"]
    storm = world.storm
    apu = engine.world.devices["animal-protection"]
    weather = apu.weather_provider
    devices = engine.world.devices
    return {
        "fire": bool(fire is not None and fire.state != "out"),
        "fire_zone": fire.zone if fire is not None else None,
        "fire_hrr_kw": float(fire.hrr_kw) if fire is not None else 0.0,
        "brigade_on_scene": any(c["service"] == "fire_brigade" and c["status"] == "on_scene" for c in engine.comms.calls),
        "flood_cm": float(outside["floodwater_level_cm"]),
        "floor_water_cm": float(state.get("barn_floor_water_cm", 0.0)),
        "water_entering": bool(getattr(world, "_water_entering_barn", False)),
        "storm": storm is not None,
        "strike": bool(storm is not None and storm.get("tornado_strike") and outside["wind_speed_m_s"] >= 40.0),
        "tornado_warning": bool(weather.get("tornado_warning")), "severe_warning": bool(weather.get("severe_weather_warning")),
        "flood_warning": bool(weather.get("flood_warning")), "watch": weather.get("weather_watch"),
        "faults": {node: sorted(labels) for node, labels in _active_faults(engine).items()},
        "supply_out": {node: float(devices[node].internal["ac_voltage_v"]) < 50.0 for node in NODE_IDS},
        "online": {node: bool(devices[node].online) for node in NODE_IDS},
        "incubator_air_c": {node: float(state["zones"][node]["temperature_c"]) for node in ("incubator-1", "incubator-2")},
        "heater_power_w": {node: float(devices[node].internal["heater_power_w"]) for node in ("incubator-1", "incubator-2")},
        "failed_sensors": sorted(world.sensors.failed),
        "isolated": sorted(node for node, (start, end) in runtime.bus.isolated.items()
                           if start <= engine.clock.tick - 1 < end),
    }


def build(spec: EpisodeSpec, *, with_faults: bool, brain_factory=None):
    """``brain_factory(node_id, spec)`` returns a fresh trained brain per node (default: rule-based nodes)."""
    config = deepcopy(spec.config)
    engine = MatrixEngine(seed=spec.seed, config=config)
    _apply_initial(engine, spec)
    brains = {node: brain_factory(node, spec) for node in NODE_IDS} if brain_factory else None
    runtime = NodeRuntime(engine, modes={node: "real" for node in brains} if brains else None, brains=brains)
    _benign(engine, runtime, spec)
    if with_faults:
        _faults(engine, runtime, spec)
    return engine, runtime


def run(spec: EpisodeSpec, *, with_faults: bool, record_truth: bool = True, brain_factory=None):
    """Returns per-node inputs, the acting brains' outputs (``rule``), and (for the fault run) the truth timeline."""
    engine, runtime = build(spec, with_faults=with_faults, brain_factory=brain_factory)
    ticks = int(spec.duration_ticks)
    encoders = {node: runtime.nodes[node].encoder for node in NODE_IDS}
    width = {node: len(encoders[node].feature_names) for node in NODE_IDS}
    values = {node: np.zeros((ticks, width[node]), dtype=np.float16) for node in NODE_IDS}
    validity = {node: np.zeros((ticks, width[node]), dtype=np.uint8) for node in NODE_IDS}
    rule = {node: np.zeros((ticks, 4), dtype=np.int8) for node in NODE_IDS}
    truth = []
    # Inputs are captured inside the step, before each node decides: a brain never sees its own
    # decision for this tick in its memory features.
    runtime.recorded_inputs = {}
    for t in range(ticks):
        results = runtime.step()
        for node_id in NODE_IDS:
            encoded = runtime.recorded_inputs[node_id]
            values[node_id][t] = encoded.values
            validity[node_id][t] = encoded.validity
            output = results[node_id]["output"]
            machine = MACHINE_TYPES[node_id]
            rule[node_id][t] = (ASSESSMENTS.index(output.assessment), DIAGNOSES[machine].index(output.diagnosis),
                                DANGER_ACTIONS[machine].index(output.action), COMMUNICATIONS.index(output.communication))
        if record_truth:
            truth.append(_truth(engine, runtime))
    outcome = {"calls": [{k: c[k] for k in ("service", "sender", "called_s", "status", "outcome")} for c in engine.comms.calls],
               "notifications": len(engine.comms.notifications),
               "welfare_index_pct": float(engine.world.physical_state["animals"]["welfare_index_pct"]),
               "fire_fuel_burned_kg": float(engine.physical_world.fire.fuel_consumed_kg) if engine.physical_world.fire else None,
               "gate_actions": [d for d in runtime.gate.decisions if d.get("action") and d.get("allowed")],
               "fault_log": list(engine.fault_log)}
    return {"values": values, "validity": validity, "rule": rule, "truth": truth, "outcome": outcome,
            "feature_names": {node: encoders[node].feature_names for node in NODE_IDS},
            "feature_kinds": {node: encoders[node].feature_kinds for node in NODE_IDS},
            "schema_hash": {node: encoders[node].schema_hash for node in NODE_IDS}}
