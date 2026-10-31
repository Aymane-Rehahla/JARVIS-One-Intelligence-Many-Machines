"""Capability-aware incubator state and telemetry derived from Matrix state."""
from dataclasses import dataclass
from ..fastcopy import json_copy
from math import exp
from datetime import date, timedelta

from .base_device import BaseDevice
from .telemetry_utils import capability_value, rate_per_minute


@dataclass
class Incubator(BaseDevice):
    DEFAULT_CAPABILITIES = {
        "ambient_temperature": True, "ambient_humidity": True, "water_flow": True,
        "water_leak": True, "air_pressure": True, "motorized_damper": True,
        "door_lamp_current": True, "door_switch": True, "egg_turning_feedback": True,
        "measurement_quality": True, "backup_power": True, "fan2": True,
        "egg_breed_tracking": False,
    }

    def __post_init__(self):
        self.capabilities = {**self.DEFAULT_CAPABILITIES, **self.config.get("capabilities", {})}
        self.internal = {
            "ac_voltage_v": 230.0, "frequency_hz": 50.0, "power_factor": .91, "energy_kwh": 0.0,
            "dc_rails": {"rail1_v": 5.0, "rail1_a": .3, "rail2_v": 12.0, "rail2_a": .4},
            # Default full-instrumentation profile includes the configured 24 V actuator rail.
            "additional_dc_rails": json_copy(self.config.get("additional_dc_rails", {
                "rail3": {"v": 24.0, "a": 0.08}
            })),
            "heater_power_w": 230.0, "heater_current_a": 1.0, "fan_command_pct": 60.0,
            "fan_rpm": [1200, 1180], "fan_nominal_rpm": [2000, 1967], "fan_faults": [False, False],
            "room_temperatures_c": [37.5, 37.4, 37.6], "heater_outlet_c": 39.0,
            "ambient_temperature_c": 22.0, "room_humidity_pct": [55.0, 55.2, 54.8],
            "ambient_humidity_pct": 40.0, "reservoir_pct": 95.0, "reservoir_capacity_l": 5.0,
            "fill_level_pct": 95.0, "fill_rate_l_min": 1.0, "water_inlet_flow_ml_min": 0.0,
            "water_consumption_rate_ml_h": 0.0, "water_flow_rate_ml_min": 0.0, "water_leak_detected": False,
            "airflow_m3_h": 20.0, "air_pressure_differential_pa": 12.0, "damper_command_pct": 50.0,
            "damper_actual_pct": 50.0, "door_open": False, "door_open_seconds": 0.0,
            "door_lamp_current_a": 0.0, "lamp_on": False, "door_switch_health": "good",
            "heater_on": True, "heater_control_mode": "automatic", "heater_output_pct": 100.0, "humidifier_on": False, "pump_on": False,
            "pump_fault": False, "humidifier_fault": False, "sensor_health": {
                "temperature_1": "good", "temperature_2": "good", "temperature_3": "good",
                "heater_outlet": "good", "ambient_temperature": "good", "humidity_1": "good",
                "humidity_2": "good", "humidity_3": "good", "ambient_humidity": "good",
                "door_switch": "good", "water_level": "good", "leak_sensor": "good",
            },
            "sensor_calibrated": True, "power_status": "normal", "cutoff": False,
            "backup_power_status": "standby", "backup_battery_charge_pct": 100.0,
            "emergency_stop_active": False,
            "egg_type": "chicken", "egg_breed": None, "batch": "BATCH-001",
            "incubation_day": 14, "incubation_duration_days": 21, "days_remaining": 7,
            "expected_hatch": "2026-10-19", "eggs": 24, "initial_egg_count": 24,
            "eggs_removed": 0, "turning": True, "turning_speed_rpm": 1.0,
            "turning_position_deg": 0.0, "turn_interval_s": 10800.0, "seconds_since_last_turn": 0.0,
            "last_turn_time_s": None, "turning_fault": False,
            "turns_per_day": 8, "turn_count": 0, "faults": [], "alarms": [],
            "uptime_s": 0.0, "last_successful_communication_s": 0.0,
            "temperature_outside_limits_s": 0.0, "humidity_outside_limits_s": 0.0,
            "low_water_threshold_pct": 15.0,
        }
        self.internal.update(json_copy(self.config.get("initial_internal", {})))
        i = self.internal
        # Physical actuator state behind the feedback fields above.
        i.setdefault("rated_airflow_m3_h", 33.61)
        i.setdefault("fan_actual_rpm", [None if (n == 1 and not self.capabilities["fan2"]) else float(rpm or 0.0)
                                        for n, rpm in enumerate(i["fan_rpm"])])
        i.setdefault("damper_position_pct", float(i["damper_actual_pct"] if i["damper_actual_pct"] is not None else i["damper_command_pct"]))
        i.setdefault("heater_rated_power_w", 230.0)
        i.setdefault("heater_rated_voltage_v", 230.0)
        i.setdefault("heater_delivered_w", float(i["heater_power_w"]) if i["heater_on"] else 0.0)
        i.setdefault("temperature_setpoint_c", float(self.config.get("temperature_setpoint_c", 37.5)))
        i.setdefault("humidity_setpoint_pct", 55.0)
        i.setdefault("high_limit_tripped", False)
        i.setdefault("measured_ac_voltage_v", float(i["ac_voltage_v"]))
        # Physical faults injected by Matrix scenarios. Private: the device only
        # learns about them through its own measurements (see _diagnose).
        i.setdefault("hidden_faults", {})
        i.setdefault("dc_rail_nominal_v", {"rail1": float(i["dc_rails"]["rail1_v"]), "rail2": float(i["dc_rails"]["rail2_v"]),
                                           **{name: float(rail.get("v", 0.0)) for name, rail in i["additional_dc_rails"].items()}})
        i.setdefault("backup_battery_capacity_wh", 120.0)
        i.setdefault("power_source", "mains")
        i.setdefault("heater_actual_pct", float(i["heater_output_pct"]))
        i.setdefault("door_open_reported", bool(i["door_open"]))
        i.setdefault("code_since_s", {})
        self._diagnostic_timers = {}
        if not i["heater_on"]:
            i["heater_output_pct"] = 0.0
        self.internal["eggs"] = max(0, int(self.internal.get("initial_egg_count", 24)) - int(self.internal.get("eggs_removed", 0)))
        self._previous_temperatures = list(self.internal["room_temperatures_c"])
        self._previous_humidities = list(self.internal["room_humidity_pct"])
        self._simulation_start_date = date.fromisoformat(self.internal["expected_hatch"]) - timedelta(
            days=int(self.internal["incubation_duration_days"]) - 1
        )
        self.update(0.0)

    def update(self, dt):
        i = self.internal
        hidden = i["hidden_faults"]
        dt = float(dt)
        i["uptime_s"] += dt
        self._power_supply(dt)
        mains = i["power_source"] == "mains"
        # The door lamp is wired through the door switch, so it follows what the switch reports.
        switch = hidden.get("door_switch")
        reported = (None if switch == "open_circuit" else False if switch == "stuck_closed"
                    else True if switch == "stuck_open" else bool(i["door_open"]))
        i["door_open_reported"] = reported
        if reported:
            i["door_open_seconds"] += dt
            i["lamp_on"] = self.online
        else:
            i["door_open_seconds"] = 0.0
            i["lamp_on"] = False
        i["door_lamp_current_a"] = .02 if i["lamp_on"] and self.capabilities["door_lamp_current"] else (0.0 if self.capabilities["door_lamp_current"] else None)

        pump_command = bool(i["humidifier_on"] and i["reservoir_pct"] > 0 and mains and self.online)
        pump_runs = pump_command and not hidden.get("pump")
        i["pump_on"] = pump_runs
        use = float(self.config.get("water_use_ml_h", 250.0))
        i["water_consumption_rate_ml_h"] = use if pump_runs else 0.0
        # A failed humidifier element moves water but no longer evaporates it.
        i["humidifier_evaporation_ml_h"] = use if pump_runs and not hidden.get("humidifier") else 0.0
        leak_ml_h = max(0.0, float(hidden.get("water_leak_ml_h", 0.0))) if i["reservoir_pct"] > 0 else 0.0
        consumed_l = (i["water_consumption_rate_ml_h"] + leak_ml_h) * dt / 3_600_000
        i["reservoir_pct"] = max(0.0, i["reservoir_pct"] - consumed_l / max(i["reservoir_capacity_l"], .001) * 100)
        # Hose-fed float valve (mechanical, needs no power): the farm water main tops the
        # reservoir back up to the float level, so inflow normally equals what was used.
        fill_l = 0.0
        if not hidden.get("mains_water_cut") and not hidden.get("fill_valve"):
            deficit_l = max(0.0, (float(i["fill_level_pct"]) - i["reservoir_pct"]) / 100.0 * i["reservoir_capacity_l"])
            fill_l = min(deficit_l, float(i["fill_rate_l_min"]) * dt / 60.0)
            i["reservoir_pct"] += fill_l / max(i["reservoir_capacity_l"], .001) * 100
        i["water_inlet_flow_ml_min"] = round(fill_l * 60_000.0 / dt, 3) if dt > 0 else 0.0
        i["water_flow_rate_ml_min"] = i["water_consumption_rate_ml_h"] / 60.0 if pump_runs else 0.0
        if self.capabilities["water_leak"]:
            i["water_leak_detected"] = leak_ml_h > 0.0

        heater_active = bool(i["heater_on"] and not i["cutoff"] and not i.get("high_limit_tripped")
                             and not i.get("emergency_stop_active") and self.online)
        if not heater_active:
            i["heater_output_pct"] = 0.0
        elif i.get("heater_control_mode", "automatic") != "automatic" or not self.config.get("thermostat_enabled", True):
            i["heater_output_pct"] = 100.0
        self._apply_heater_electrics()
        # The element's own thermal mass delays heat delivery to the air.
        i["heater_delivered_w"] = i["heater_power_w"] + (float(i.get("heater_delivered_w", 0.0)) - i["heater_power_w"]) * exp(-dt / 15.0)
        nominal_fans = i["fan_nominal_rpm"]
        for idx in range(len(i["fan_actual_rpm"])):
            if idx == 1 and not self.capabilities["fan2"]:
                i["fan_actual_rpm"][idx] = None
                i["fan_rpm"][idx] = None
                continue
            powered = self.online and i["dc_rails"]["rail2_v"] >= 0.9 * i["dc_rail_nominal_v"]["rail2"]
            target = 0.0 if (hidden.get(f"fan{idx + 1}") or not powered) else nominal_fans[idx] * i["fan_command_pct"] / 100.0
            actual = float(i["fan_actual_rpm"][idx] or 0.0)
            tau = 2.5 if target > actual else 6.0  # spin-up versus coast-down
            actual = target + (actual - target) * exp(-dt / tau)
            i["fan_actual_rpm"][idx] = 0.0 if target == 0.0 and actual < 5.0 else actual
        # Fan affinity law: shaft power scales with speed cubed.
        fan_power = sum(6.0 * (max(0.0, rpm) / max(nominal_fans[n], 1)) ** 3 for n, rpm in enumerate(i["fan_actual_rpm"]) if rpm is not None)
        i["fan_power_w"] = fan_power
        i["fan_current_a"] = round(fan_power / max(float(i["ac_voltage_v"]), 1.0), 5)
        controller_dc_w = sum(float(v.get("v", 0)) * float(v.get("a", 0)) for v in i["additional_dc_rails"].values())
        controller_dc_w += sum(float(i["dc_rails"][f"rail{n}_v"]) * float(i["dc_rails"][f"rail{n}_a"]) for n in (1, 2))
        i["controller_electronics_power_w"] = round(controller_dc_w / .85, 3)
        i["controller_current_a"] = round(i["controller_electronics_power_w"] / max(float(i["ac_voltage_v"]), 1.0), 5)
        i["water_system_current_a"] = round((0.10 if pump_runs else 0.0) + (0.20 if pump_runs and not hidden.get("humidifier") else 0.0), 3)
        i["controller_temperature_c"] = round(32.0 + i["controller_electronics_power_w"] * .04, 3)
        lamp_power = (float(i["door_lamp_current_a"] or 0) * float(i["ac_voltage_v"]))
        water_power = i["water_system_current_a"] * float(i["ac_voltage_v"])
        i["water_system_power_w"] = water_power
        real_power = i["heater_power_w"] + fan_power + i["controller_electronics_power_w"] + water_power + lamp_power
        self._battery(dt, fan_power + i["controller_electronics_power_w"])
        if not mains:
            real_power = 0.0  # the mains meter sees nothing during an outage
        i["real_power_w"] = round(real_power, 3)
        i["apparent_power_va"] = round(i["real_power_w"] / max(float(i["power_factor"]), .01), 3)
        i["ac_current_a"] = round(i["apparent_power_va"] / max(float(i["ac_voltage_v"]), 1.0), 5)
        i["energy_kwh"] += i["real_power_w"] * dt / 3_600_000

        valid_t = [v for v in i["room_temperatures_c"] if v is not None]
        if not self.config.get("external_physics", False):
            target = float(i.get("temperature_setpoint_c", 37.5)) if heater_active else float(i.get("ambient_temperature_c") or 22.0)
            for n, temperature in enumerate(i["room_temperatures_c"]):
                if temperature is not None:
                    i["room_temperatures_c"][n] = round(float(temperature) + (target - float(temperature)) * min(.03 * dt, 1), 4)
            i["heater_outlet_c"] = round(sum(valid_t) / len(valid_t) + (1.5 if heater_active else 0), 4) if valid_t else None
            if i["humidifier_on"] and i["reservoir_pct"] > 0:
                for n, humidity in enumerate(i["room_humidity_pct"]):
                    if humidity is not None: i["room_humidity_pct"][n] = min(100.0, round(float(humidity) + .0005 * dt, 4))
        i["airflow_m3_h"] = round(sum(rpm for rpm in i["fan_rpm"] if rpm is not None) / max(sum(nominal_fans[:len(i["fan_rpm"])]), 1) * float(i["rated_airflow_m3_h"]), 3)
        actuator_rail = i["dc_rails"].get("rail2_v", 0.0) if not i["additional_dc_rails"] else next(iter(i["additional_dc_rails"].values())).get("v", 0.0)
        actuator_nominal = i["dc_rail_nominal_v"]["rail2"] if not i["additional_dc_rails"] else next(iter(i["dc_rail_nominal_v"][k] for k in i["additional_dc_rails"]))
        actuators_powered = self.online and actuator_rail >= 0.9 * actuator_nominal
        # Damper servo travels at about 100 % per 30 s.
        if self.capabilities["motorized_damper"] and not self.config.get("damper_fault", False) and actuators_powered:
            position = float(i["damper_position_pct"])
            step = 100.0 / 30.0 * dt
            command = float(i["damper_command_pct"])
            i["damper_position_pct"] = command if abs(command - position) <= step else position + step * (1 if command > position else -1)
        if not self.capabilities["motorized_damper"]: i["damper_actual_pct"] = None
        elif not self.config.get("damper_feedback_enabled", True): i["damper_actual_pct"] = None
        else: i["damper_actual_pct"] = round(i["damper_position_pct"], 3)

        elapsed_days = int(i["uptime_s"] // 86400)
        i["incubation_day"] = min(int(i["incubation_duration_days"]), 14 + elapsed_days)
        simulated_date = self._simulation_start_date + timedelta(days=i["incubation_day"] - 1)
        hatch = self._simulation_start_date + timedelta(days=int(i["incubation_duration_days"]) - 1)
        i["expected_hatch"] = hatch.isoformat()
        i["days_remaining"] = max(0, (hatch - simulated_date).days)
        since_turn = i["seconds_since_last_turn"] + dt
        if i["turning"] and since_turn >= i["turn_interval_s"]:
            turns = int(since_turn // i["turn_interval_s"])
            if actuators_powered and not hidden.get("turning_motor"):
                i["turn_count"] += turns
                since_turn %= i["turn_interval_s"]
                if turns % 2:
                    i["turning_position_deg"] = 45.0 if i["turning_position_deg"] == 0 else 0.0
                i["last_turn_time_s"] = i["uptime_s"] - since_turn
                i["turn_missed"] = False
            else:
                # The controller commands a turn but the tray position never changes.
                i["turn_missed"] = True
        i["turning_speed_rpm"] = 0.0 if (hidden.get("turning_motor") or not actuators_powered) else float(self.config.get("turning_speed_rpm", 1.0))
        i["seconds_since_last_turn"] = since_turn
        i["turns_per_day"] = round(86400 / max(float(i["turn_interval_s"]), 1), 2)

        valid_h = [v for v in i["room_humidity_pct"] if v is not None]
        if valid_t and any(v < self.config.get("temperature_min_c", 35.0) or v > self.config.get("temperature_max_c", 39.0) for v in valid_t): i["temperature_outside_limits_s"] += dt
        if valid_h and any(v < self.config.get("humidity_min_pct", 40.0) or v > self.config.get("humidity_max_pct", 70.0) for v in valid_h): i["humidity_outside_limits_s"] += dt
        if i["reservoir_pct"] <= i["low_water_threshold_pct"] and "LOW_WATER" not in i["alarms"]: i["alarms"].append("LOW_WATER")
        self._diagnose(dt)
        self._publish(dt)

    def _power_supply(self, dt):
        """Mains, backup battery, and the internal DC rails that feed the electronics."""
        i = self.internal
        hidden = i["hidden_faults"]
        mains_ok = float(i["ac_voltage_v"]) >= 180.0
        on_battery = (not mains_ok and self.capabilities["backup_power"]
                      and float(i["backup_battery_charge_pct"]) > 0.0)
        i["power_source"] = "mains" if mains_ok else "battery" if on_battery else "none"
        for name, nominal in i["dc_rail_nominal_v"].items():
            factor = float(hidden.get(f"dc_{name}", 1.0))
            volts = 0.0 if i["power_source"] == "none" else round(nominal * factor, 3)
            if name in ("rail1", "rail2"):
                i["dc_rails"][f"{name}_v"] = volts
                i["dc_rails"][f"{name}_a"] = 0.0 if volts < 0.5 * nominal else {"rail1": .3, "rail2": .4}[name]
            elif name in i["additional_dc_rails"]:
                i["additional_dc_rails"][name]["v"] = volts
                if volts < 0.5 * nominal: i["additional_dc_rails"][name]["a"] = 0.0
        # The microcontroller browns out below 90 % of its 5 V logic rail.
        self.online = i["dc_rails"]["rail1_v"] >= 0.9 * i["dc_rail_nominal_v"]["rail1"]
        if self.capabilities["backup_power"]:
            charge = float(i["backup_battery_charge_pct"])
            i["backup_power_status"] = ("active" if on_battery else "depleted" if not mains_ok
                                        else "charging" if charge < 99.9 else "standby")

    def _battery(self, dt, dc_load_w):
        i = self.internal
        if not self.capabilities["backup_power"]:
            return
        capacity_wh = max(1.0, float(i["backup_battery_capacity_wh"]))
        charge = float(i["backup_battery_charge_pct"])
        if i["power_source"] == "battery":
            charge -= dc_load_w / 0.9 * dt / 3600.0 / capacity_wh * 100.0
        elif i["power_source"] == "mains" and charge < 100.0:
            charge += 20.0 * dt / 3600.0 / capacity_wh * 100.0  # 20 W charger
        i["backup_battery_charge_pct"] = round(max(0.0, min(100.0, charge)), 4)

    def _set_code(self, kind, code, active):
        i = self.internal
        codes = i[kind]
        if active and code not in codes:
            codes.append(code)
            i["code_since_s"][code] = i["uptime_s"]
        elif not active and code in codes:
            codes.remove(code)
            i["code_since_s"].pop(code, None)

    def _persist(self, name, condition, dt, after_s):
        """True once ``condition`` has held for ``after_s`` seconds."""
        timer = self._diagnostic_timers.get(name, 0.0) + dt if condition else 0.0
        self._diagnostic_timers[name] = timer
        return condition and timer >= after_s

    def _diagnose(self, dt):
        """What the incubator's own controller can detect from its own measurements."""
        i = self.internal
        if not self.online:
            return  # a dead controller detects nothing and reports nothing
        nominal = i["fan_nominal_rpm"]
        for idx, rpm in enumerate(i["fan_actual_rpm"]):
            if rpm is None:
                continue
            expected = nominal[idx] * float(i["fan_command_pct"]) / 100.0
            stalled = expected > 0.2 * nominal[idx] and float(rpm) < 0.1 * expected
            if self._persist(f"fan{idx}", stalled, dt, 10.0):
                i["fan_faults"][idx] = True
            elif not stalled:
                i["fan_faults"][idx] = False
            self._set_code("faults", f"FAN{idx + 1}_FAILURE", i["fan_faults"][idx])
        current = float(i["heater_current_a"])
        commanded = float(i["heater_output_pct"])
        self._set_code("faults", "HEATER_OPEN_CIRCUIT", self._persist(
            "heater_open", commanded > 5.0 and i["power_source"] == "mains" and current < 0.05, dt, 5.0))
        # A stuck relay stays latched until the controller sees the element actually de-energise.
        if self._persist("heater_stuck", commanded == 0.0 and current > 0.2, dt, 5.0):
            self._set_code("faults", "HEATER_RELAY_STUCK", True)
        elif self._persist("heater_released", commanded == 0.0 and current < 0.05 and not i.get("emergency_stop_active"), dt, 30.0):
            self._set_code("faults", "HEATER_RELAY_STUCK", False)
        flow_sensor = self.capabilities["water_flow"]
        pump_dry = i["humidifier_on"] and i["reservoir_pct"] > 0 and i["power_source"] == "mains" and not i["pump_on"]
        i["pump_fault"] = bool(flow_sensor and self._persist("pump", pump_dry, dt, 30.0))
        self._set_code("faults", "PUMP_FAULT", i["pump_fault"])
        element_dead = i["pump_on"] and float(i["water_system_current_a"]) < 0.15
        i["humidifier_fault"] = self._persist("humidifier", element_dead, dt, 30.0)
        self._set_code("faults", "HUMIDIFIER_FAULT", i["humidifier_fault"])
        self._set_code("alarms", "WATER_LEAK", bool(i["water_leak_detected"]))
        if self.capabilities["egg_turning_feedback"]:
            i["turning_fault"] = bool(i.get("turn_missed"))
            self._set_code("faults", "TURNING_FAULT", i["turning_fault"])
        switch_failed = i["door_open_reported"] is None
        i["door_switch_health"] = "failed" if switch_failed else "good"
        i["sensor_health"]["door_switch"] = "failed" if switch_failed else "good"
        self._set_code("faults", "DOOR_SWITCH_FAULT", switch_failed)
        rails_bad = any(abs(float(volts) - i["dc_rail_nominal_v"][name]) > 0.1 * i["dc_rail_nominal_v"][name]
                        for name, volts in [("rail1", i["dc_rails"]["rail1_v"]), ("rail2", i["dc_rails"]["rail2_v"])]
                        + [(k, v["v"]) for k, v in i["additional_dc_rails"].items()])
        self._set_code("faults", "INTERNAL_POWER_FAULT", self._persist("rails", rails_bad, dt, 2.0) if dt > 0 else rails_bad)
        self._set_code("alarms", "POWER_OUTAGE", i["power_source"] == "battery")
        self._set_code("alarms", "BACKUP_BATTERY_LOW", self.capabilities["backup_power"]
                       and i["power_source"] == "battery" and float(i["backup_battery_charge_pct"]) < 20.0)

    def _apply_heater_electrics(self):
        """Resistive element: P = V^2 / R at the commanded time-proportioned duty."""
        i = self.internal
        hidden = i.get("hidden_faults", {})
        resistance = float(i["heater_rated_voltage_v"]) ** 2 / max(float(i["heater_rated_power_w"]), 1e-6)
        voltage = max(0.0, float(i["ac_voltage_v"]))
        duty = float(i["heater_output_pct"])
        stuck = bool(hidden.get("heater_relay_stuck"))
        if stuck:
            duty = 100.0  # welded contacts: the element stays energised
        elif not self.online:
            duty = 0.0  # a dead controller drops its relay
        # The overvoltage contactor and the high-limit thermostat sit in series with the element.
        if (i.get("cutoff") or i.get("high_limit_tripped") or i.get("emergency_stop_active")
                or hidden.get("heater_open") or voltage < 50.0):
            duty = 0.0
        i["heater_actual_pct"] = duty
        i["heater_power_w"] = round(duty / 100.0 * voltage ** 2 / resistance, 4)
        i["heater_current_a"] = round(i["heater_power_w"] / max(voltage, 1.0), 4)

    def apply_heater_output(self, output_pct, dt):
        """Controller actuation within a tick: rebook this tick's power and energy."""
        i = self.internal
        previous_power = float(i["heater_power_w"])
        i["heater_output_pct"] = max(0.0, min(100.0, float(output_pct)))
        i["heater_on"] = i["heater_output_pct"] > 0.0
        self._apply_heater_electrics()
        delta = i["heater_power_w"] - previous_power
        if delta:
            voltage = max(float(i["ac_voltage_v"]), 1.0)
            i["real_power_w"] = round(i["real_power_w"] + delta, 3)
            i["energy_kwh"] += delta * float(dt) / 3_600_000
            i["apparent_power_va"] = round(i["real_power_w"] / max(float(i["power_factor"]), .01), 3)
            i["ac_current_a"] = round(i["apparent_power_va"] / voltage, 5)

    def _publish(self, dt=0.0):
        i = self.internal
        temps, hum = list(i["room_temperatures_c"]), list(i["room_humidity_pct"])
        good_t, good_h = [v for v in temps if v is not None], [v for v in hum if v is not None]
        mean_t = round(sum(good_t) / len(good_t), 3) if good_t else None
        mean_h = round(sum(good_h) / len(good_h), 3) if good_h else None
        availability = {}
        ambient_t = capability_value(self.capabilities, "ambient_temperature", i["ambient_temperature_c"], availability)
        ambient_h = capability_value(self.capabilities, "ambient_humidity", i["ambient_humidity_pct"], availability)
        sensor_health = json_copy(i["sensor_health"])
        sensor_channels = {
            **{f"room_temperature_sensor_{n+1}": (temps[n] if n < len(temps) else None, f"temperature_{n+1}") for n in range(3)},
            **{f"room_humidity_sensor_{n+1}": (hum[n] if n < len(hum) else None, f"humidity_{n+1}") for n in range(3)},
            "heater_outlet_temperature": (i["heater_outlet_c"], "heater_outlet"),
            "ambient_temperature": (ambient_t, "ambient_temperature"),
            "ambient_humidity": (ambient_h, "ambient_humidity"),
        }
        for field, (value, health_field) in sensor_channels.items():
            if value is None:
                status = str(sensor_health.get(health_field, "good")).lower()
                if status in {"good", "available", "normal"}:
                    status = "unavailable"
                    reason = "configured sensor has no reading"
                    availability_status = "unavailable"
                else:
                    reason = f"sensor health is {status} and no reading is available"
                    availability_status = "faulty" if status in {"failed", "fault", "faulty", "bad", "stuck"} else status
                sensor_health[health_field] = status
                availability[field] = {"status": availability_status, "reason": reason}
            else:
                availability.setdefault(field, {"status": "available", "reason": None})
        water_flow = capability_value(self.capabilities, "water_flow", i["water_flow_rate_ml_min"], availability)
        inlet_flow = capability_value(self.capabilities, "water_flow", i["water_inlet_flow_ml_min"], availability)
        leak = capability_value(self.capabilities, "water_leak", i["water_leak_detected"], availability)
        pressure = capability_value(self.capabilities, "air_pressure", i["air_pressure_differential_pa"], availability)
        damper = capability_value(self.capabilities, "motorized_damper", i["damper_actual_pct"], availability)
        lamp_current = capability_value(self.capabilities, "door_lamp_current", i["door_lamp_current_a"], availability)
        door_health = capability_value(self.capabilities, "door_switch", i["door_switch_health"], availability)
        backup = capability_value(self.capabilities, "backup_power", i["backup_power_status"], availability, "backup power is not installed")
        quality = {name: (1.0 if status == "good" else 0.0) for name, status in sensor_health.items()} if self.capabilities["measurement_quality"] else None
        availability["measurement_quality"] = {"status":"available","reason":None} if quality is not None else {"status":"unavailable","reason":"quality instrumentation is not configured"}
        if i["additional_dc_rails"]:
            availability["additional_dc_rails"]={"status":"available","reason":None}
        else:
            availability["additional_dc_rails"]={"status":"unavailable","reason":"no additional DC rails are configured"}
        availability["water_system_current"]={"status":"available","reason":None} if self.config.get("water_system_installed",True) else {"status":"unavailable","reason":"water pump/humidifier current sensor is not installed"}
        egg_breed=capability_value(self.capabilities,"egg_breed_tracking",i["egg_breed"],availability,"egg breed is not tracked by this profile")
        sensor_temp_spread = round(max(good_t) - min(good_t), 4) if good_t else None
        sensor_hum_spread = round(max(good_h) - min(good_h), 4) if good_h else None
        previous_mean_t = sum(v for v in self._previous_temperatures if v is not None) / max(len([v for v in self._previous_temperatures if v is not None]), 1)
        previous_mean_h = sum(v for v in self._previous_humidities if v is not None) / max(len([v for v in self._previous_humidities if v is not None]), 1)
        temp_rate = rate_per_minute(mean_t, previous_mean_t if self._previous_temperatures else None, dt)
        hum_rate = rate_per_minute(mean_h, previous_mean_h if self._previous_humidities else None, dt)
        i["temperature_change_rate_c_min"] = temp_rate
        i["humidity_change_rate_pct_min"] = hum_rate
        fan2_rpm=capability_value(self.capabilities,"fan2",i["fan_rpm"][1] if len(i["fan_rpm"])>1 else None,availability,"second fan is not installed")
        self.telemetry = {
            "device_id": self.device_id, "online": self.online,
            "health": "fault" if i["faults"] else "degraded" if i["alarms"] else "normal",
            "ac_voltage_v": i.get("measured_ac_voltage_v", i["ac_voltage_v"]), "frequency_hz": i["frequency_hz"], "ac_current_a": i["ac_current_a"],
            "real_power_w": i["real_power_w"], "apparent_power_va": i["apparent_power_va"], "power_factor": i["power_factor"], "energy_kwh": round(i["energy_kwh"], 8),
            "heater_current_a": i["heater_current_a"], "heater_power_w": i["heater_power_w"],
            "temperatures_c": temps, "temperature_mean_c": mean_t, "temperature_min_c": min(good_t) if good_t else None,
            "temperature_max_c": max(good_t) if good_t else None, "heater_outlet_c": i["heater_outlet_c"],
            "humidities_pct": hum, "humidity_mean_pct": mean_h, "humidity_min_pct": min(good_h) if good_h else None,
            "humidity_max_pct": max(good_h) if good_h else None, "reservoir_pct": i["reservoir_pct"],
            "fan_command_pct": i["fan_command_pct"], "fan_rpm": [rpm for n,rpm in enumerate(i["fan_rpm"]) if rpm is not None], "airflow_m3_h": i["airflow_m3_h"],
            "heater_on": i["heater_on"], "humidifier_on": i["humidifier_on"], "pump_on": i["pump_on"],
            "door_open": i["door_open_reported"], "door_open_seconds": i["door_open_seconds"], "lamp_on": i["lamp_on"],
            "egg_type": i["egg_type"], "batch": i["batch"], "incubation_day": i["incubation_day"],
            "days_remaining": i["days_remaining"], "expected_hatch": i["expected_hatch"], "eggs": i["eggs"],
            "turning": i["turning"], "sensor_health": sensor_health,
            "sensor_calibrated": i["sensor_calibrated"], "power_status": i["power_status"],
            "overtemperature_cutoff": bool(i["cutoff"] or i.get("high_limit_tripped")), "faults": list(i["faults"]), "alarms": list(i["alarms"]),
            "last_telemetry_tick": int(self.config.get("last_telemetry_tick",0)),
            # Complete specification values, retaining the legacy fields above.
            "total_ac_current_a": i["ac_current_a"], "total_real_power_w": i["real_power_w"],
            "total_apparent_power_va": i["apparent_power_va"], "cumulative_energy_kwh": round(i["energy_kwh"], 8),
            "dc_rail1_voltage_v": i["dc_rails"]["rail1_v"], "dc_rail1_current_a": i["dc_rails"]["rail1_a"],
            "dc_rail2_voltage_v": i["dc_rails"]["rail2_v"], "dc_rail2_current_a": i["dc_rails"]["rail2_a"],
            "additional_dc_rails": json_copy(i["additional_dc_rails"]),
            "heating_element_current_a": i["heater_current_a"], "heating_element_power_w": i["heater_power_w"],
            "fan_current_a": i["fan_current_a"], "controller_electronics_current_a": i["controller_current_a"],
            "controller_electronics_power_w": i["controller_electronics_power_w"],
            "water_system_current_a": i["water_system_current_a"] if self.config.get("water_system_installed", True) else None,
            "internal_controller_temperature_c": i["controller_temperature_c"],
            "room_temperature_sensor_1_c": temps[0] if len(temps)>0 else None,
            "room_temperature_sensor_2_c": temps[1] if len(temps)>1 else None,
            "room_temperature_sensor_3_c": temps[2] if len(temps)>2 else None,
            "heater_outlet_temperature_c": i["heater_outlet_c"], "ambient_temperature_c": ambient_t,
            "room_humidity_sensor_1_pct": hum[0] if len(hum)>0 else None,
            "room_humidity_sensor_2_pct": hum[1] if len(hum)>1 else None,
            "room_humidity_sensor_3_pct": hum[2] if len(hum)>2 else None,
            "ambient_relative_humidity_pct": ambient_h,
            "reservoir_level_pct": i["reservoir_pct"], "water_consumption_rate_ml_h": i["water_consumption_rate_ml_h"],
            "water_consumption_source": "estimated", "water_flow_rate_ml_min": water_flow,
            "water_inlet_flow_ml_min": inlet_flow,
            "water_leak_detected": leak, "low_water_alert": i["reservoir_pct"] <= i["low_water_threshold_pct"],
            "fan1_speed_rpm": i["fan_rpm"][0] if len(i["fan_rpm"])>0 else None,
            "fan2_speed_rpm": fan2_rpm,
            "actual_airflow_m3_h": i["airflow_m3_h"], "air_pressure_differential_pa": pressure,
            "damper_command_pct": i["damper_command_pct"], "damper_actual_pct": damper,
            "door_position": "unknown" if i["door_open_reported"] is None else "open" if i["door_open_reported"] else "closed", "door_open_duration_s": i["door_open_seconds"],
            "door_lamp_current_a": lamp_current, "door_lamp_on": i["lamp_on"], "door_switch_health": door_health,
            "egg_species": i["egg_type"], "egg_breed": egg_breed, "initial_egg_count": i["initial_egg_count"],
            "eggs_removed": i["eggs_removed"], "current_egg_count": i["eggs"],
            "turning_speed_rpm": i["turning_speed_rpm"] if self.capabilities["egg_turning_feedback"] else None,
            "turning_position_deg": i["turning_position_deg"] if self.capabilities["egg_turning_feedback"] else None,
            "seconds_since_last_turn": i["seconds_since_last_turn"], "turns_per_day": i["turns_per_day"],
            "turn_count": i["turn_count"], "last_egg_turn_time_s": i["last_turn_time_s"],
            "turning_fault": capability_value(self.capabilities,"egg_turning_feedback",i["turning_fault"],availability,"egg turning feedback is not installed"),
            "incubation_phase": "hatching" if i["days_remaining"]<=1 else "lockdown" if i["days_remaining"]<=3 else "incubation",
            "expected_hatch_date": i["expected_hatch"], "heater_output_pct": i["heater_output_pct"],
            "heater_actual_on": i["heater_actual_pct"] > 0.0 and i["heater_power_w"] > 0.0,
            "power_source": i["power_source"],
            "backup_battery_charge_pct": round(float(i["backup_battery_charge_pct"]), 3) if self.capabilities["backup_power"] else None,
            "sensor_calibration_status": "calibrated" if i["sensor_calibrated"] else "uncalibrated",
            "measurement_quality": quality, "backup_power_status": backup,
            "emergency_stop_active": i["emergency_stop_active"], "backup_power_alert": None if backup is None else i["power_source"] != "mains",
            "active_alarms": [{"code": code, "severity": "warning" if code in ("LOW_WATER", "WATER_LEAK") else "critical",
                               "detected_at_s": i["code_since_s"].get(code, i["uptime_s"])} for code in i["alarms"]],
            "last_successful_communication_s": i["last_successful_communication_s"],
            "last_telemetry_update_s": i["uptime_s"], "uptime_s": i["uptime_s"],
            "temperature_sensor_spread_c": round(sensor_temp_spread, 4) if sensor_temp_spread is not None else None,
            "humidity_sensor_spread_pct": round(sensor_hum_spread, 4) if sensor_hum_spread is not None else None,
            "heater_outlet_delta_c": round(i["heater_outlet_c"]-mean_t,4) if i["heater_outlet_c"] is not None and mean_t is not None else None,
            "temperature_change_rate_c_min": temp_rate, "humidity_change_rate_pct_min": hum_rate,
            "measured_subsystem_power_w": {"heater":i["heater_power_w"], "fans":round(i["fan_power_w"],3), "controller":i["controller_electronics_power_w"], "water_system":round(i["water_system_power_w"],3)},
            "time_outside_temperature_limits_s": i["temperature_outside_limits_s"],
            "time_outside_humidity_limits_s": i["humidity_outside_limits_s"],
            "measurement_availability": availability,
            "measurement_sources": {"water_consumption_rate_ml_h":"estimated from humidifier/pump state and configured use rate", "water_flow_rate_ml_min":"measured by configured simulated flow sensor", "energy_kwh":"calculated from real power and simulation timestep", "temperature_change_rate_c_min":"calculated from successive room sensor means", "humidity_change_rate_pct_min":"calculated from successive room sensor means"},
            "sensor_faults": [name for name, status in sensor_health.items() if status != "good"],
            "fan_faults": list(i["fan_faults"]), "humidifier_fault": i["humidifier_fault"], "pump_fault": i["pump_fault"],
        }
        if dt > 0:
            self._previous_temperatures = list(temps)
            self._previous_humidities = list(hum)

    def monitoring_output(self, copy=True):
        """Operational dataset sent to the monitoring PC (docs: "incub - OUT").

        Health and fault codes instead of raw internal electrical channels: if a DC
        rail fails, the incubator reports INTERNAL_POWER_FAULT rather than a voltage.
        """
        t = (self.public_telemetry() if copy else (self.telemetry or self.public_telemetry()))
        i = self.internal
        alarms = [{"code": code, "severity": "critical", "detected_at_s": i["code_since_s"].get(code, i["uptime_s"])}
                  for code in t["faults"]] + list(t["active_alarms"])
        return {
            "device_id": self.device_id, "observed_at_s": i["uptime_s"],
            "room_temperatures_c": t["temperatures_c"], "heater_outlet_temperature_c": t["heater_outlet_c"],
            "room_humidities_pct": t["humidities_pct"],
            # The room around the cabinet (the workspace air the HVAC also measures).
            "environment": {"ambient_temperature_c": t["ambient_temperature_c"],
                            "ambient_humidity_pct": t["ambient_relative_humidity_pct"]},
            "heating": {"state": "on" if t["heater_actual_on"] else "off", "output_pct": t["heater_output_pct"],
                        "power_w": t["heater_power_w"], "outlet_minus_room_c": t["heater_outlet_delta_c"]},
            "fans": {"command_pct": t["fan_command_pct"], "rpm": [t["fan1_speed_rpm"], t["fan2_speed_rpm"]],
                     "faults": t["fan_faults"]},
            "water": {"reservoir_pct": t["reservoir_level_pct"], "low_water_alert": t["low_water_alert"],
                      "leak_detected": t["water_leak_detected"], "inlet_flow_ml_min": t["water_inlet_flow_ml_min"]},
            "humidification": {"state": "on" if t["humidifier_on"] else "off", "pump_on": t["pump_on"],
                               "fault": bool(t["humidifier_fault"] or t["pump_fault"])},
            "door": {"position": t["door_position"], "open_duration_s": t["door_open_duration_s"]},
            "door_lamp": "on" if t["door_lamp_on"] else "off",
            "eggs": {"species": t["egg_species"], "batch": t["batch"], "count": t["current_egg_count"],
                     "incubation_day": t["incubation_day"], "days_remaining": t["days_remaining"],
                     "expected_hatch_date": t["expected_hatch_date"], "phase": t["incubation_phase"]},
            "egg_turning": {"enabled": t["turning"], "seconds_since_last_turn": t["seconds_since_last_turn"],
                            "last_turn_time_s": t["last_egg_turn_time_s"], "fault": t["turning_fault"]},
            "electricity": {"total_power_w": t["total_real_power_w"], "energy_kwh": t["cumulative_energy_kwh"],
                            "supply_status": t["power_status"], "power_source": t["power_source"]},
            "health": t["health"], "alarms": alarms,
            "connectivity": {"online": t["online"], "last_update_s": t["last_telemetry_update_s"]},
            "safety": {"overtemperature_trip": t["overtemperature_cutoff"], "emergency_stop": t["emergency_stop_active"],
                       "backup_power_alert": t["backup_power_alert"], "backup_battery_charge_pct": t["backup_battery_charge_pct"]},
        }

    def mark_communication(self, time_s):
        self.internal["last_successful_communication_s"] = float(time_s)
        self._publish()

    def public_telemetry(self):
        if not self.telemetry: self._publish()
        return json_copy(self.telemetry)
