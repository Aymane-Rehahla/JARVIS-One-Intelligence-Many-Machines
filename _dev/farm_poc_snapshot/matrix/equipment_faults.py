"""Physical equipment faults, site power outages, and severe-weather events.

Each scenario changes plant reality only (hidden fault flags, supply voltage,
weather, warning feed). Devices discover faults through their own
measurements; Jarvis sees only what the devices publish. Scenario identity
stays in ``engine.hidden_truth`` and the evaluator-private event log.
"""
from dataclasses import dataclass, field
from math import isfinite

INCUBATORS = ("incubator-1", "incubator-2")


def _hidden(engine, device_id):
    return engine.world.devices[device_id].internal["hidden_faults"]


def _flag(key, value=True):
    def apply(engine, device_id, severity):
        _hidden(engine, device_id)[key] = value if severity is None else severity
    def clear(engine, device_id):
        _hidden(engine, device_id).pop(key, None)
    return apply, clear


def _config(key):
    def apply(engine, device_id, severity):
        engine.world.devices[device_id].config[key] = True
    def clear(engine, device_id):
        engine.world.devices[device_id].config[key] = False
    return apply, clear


def _door_open_undetected():
    def apply(engine, device_id, severity):
        device = engine.world.devices[device_id].internal
        device["door_open"] = True
        device["hidden_faults"]["door_switch"] = "stuck_closed"
    def clear(engine, device_id):
        device = engine.world.devices[device_id].internal
        device["door_open"] = False
        device["hidden_faults"].pop("door_switch", None)
    return apply, clear


def _supply(engine, device_id, severity):
    engine.world.devices[device_id].internal["ac_voltage_v"] = 0.0 if severity is None else float(severity)


def _supply_restore(engine, device_id):
    engine.world.devices[device_id].internal["ac_voltage_v"] = 230.0


# name: (devices it applies to, apply(engine, device_id, severity), clear(engine, device_id), severity bounds)
EQUIPMENT_FAULTS = {
    "fan1_failure": (INCUBATORS, *_flag("fan1"), None),
    "fan2_failure": (INCUBATORS, *_flag("fan2"), None),
    "heater_open_circuit": (INCUBATORS, *_flag("heater_open"), None),
    "heater_relay_stuck": (INCUBATORS, *_flag("heater_relay_stuck"), None),
    "turning_motor_failure": (INCUBATORS, *_flag("turning_motor"), None),
    "high_limit_failed": (INCUBATORS, *_flag("high_limit_failed"), None),
    "incubator_water_leak": (INCUBATORS, *_flag("water_leak_ml_h", 400.0), (10.0, 5_000.0)),
    "humidifier_pump_failure": (INCUBATORS, *_flag("pump"), None),
    "humidifier_element_failure": (INCUBATORS, *_flag("humidifier"), None),
    "door_switch_open_circuit": (INCUBATORS, *_flag("door_switch", "open_circuit"), None),
    "door_open_undetected": (INCUBATORS, *_door_open_undetected(), None),
    "dc_rail2_sag": (INCUBATORS, *_flag("dc_rail2", 0.8), (0.0, 1.2)),
    "dc_rail3_failure": (INCUBATORS, *_flag("dc_rail3", 0.0), (0.0, 1.2)),
    "controller_power_failure": (INCUBATORS, *_flag("dc_rail1", 0.0), (0.0, 1.2)),
    "supply_fan_failure": (("hvac",), *_config("supply_fan_fault"), None),
    "return_fan_failure": (("hvac",), *_config("return_fan_fault"), None),
    "compressor_failure": (("hvac",), *_config("compressor_fault"), None),
    "damper_stuck": (("hvac",), *_config("damper_fault"), None),
    "condensate_drain_blocked": (("hvac",), *_flag("condensate_drain_blocked"), None),
    "filter_clogging": (("hvac",), *_flag("filter_clog_per_h", 0.05), (0.0, 5.0)),
    "refrigerant_leak": (("hvac",), *_flag("refrigerant_charge_pct", 35.0), (0.0, 100.0)),
    "feeder_blocked": (("animal-protection",), *_flag("feeder_blocked"), None),
    "drinker_pump_failure": (("animal-protection",), *_flag("water_pump"), None),
    "drinker_line_leak": (("animal-protection",), *_flag("water_leak_l_h", 60.0), (1.0, 2_000.0)),
    # The farm water main itself is cut by WaterCutScenario (every consumer at once); these are local.
    "incubator_fill_valve_failure": (INCUBATORS, *_flag("fill_valve"), None),
    "tank_fill_valve_failure": (("animal-protection",), *_flag("tank_fill_valve"), None),
    "device_supply_outage": (INCUBATORS + ("hvac", "animal-protection"), _supply, _supply_restore, (0.0, 400.0)),
}


@dataclass
class EquipmentFaultScenario:
    """A physical fault on one device between ``inject_tick`` and ``end_tick``."""
    fault: str
    device_id: str
    inject_tick: int = 2
    end_tick: int | None = None
    severity: float | None = None

    def __post_init__(self):
        if self.fault not in EQUIPMENT_FAULTS:
            raise ValueError(f"unknown equipment fault: {self.fault}")
        devices, _, _, bounds = EQUIPMENT_FAULTS[self.fault]
        if self.device_id not in devices:
            raise ValueError(f"{self.fault} does not apply to {self.device_id}")
        if self.inject_tick < 0 or (self.end_tick is not None and self.end_tick <= self.inject_tick):
            raise ValueError("equipment fault end_tick must follow a nonnegative inject_tick")
        if self.severity is not None:
            if bounds is None or not isfinite(float(self.severity)) or not bounds[0] <= float(self.severity) <= bounds[1]:
                raise ValueError(f"severity for {self.fault} is outside its bounds")

    def apply(self, engine):
        EQUIPMENT_FAULTS[self.fault][1](engine, self.device_id, self.severity)
        engine.hidden_truth = {"scenario": "equipment_fault", "fault_label": self.fault,
                               "device_id": self.device_id, "inject_tick": self.inject_tick,
                               "end_tick": self.end_tick, "severity": self.severity}
        engine.fault_log.append({"tick": engine.clock.tick, "transition": "injected", **engine.hidden_truth})
        engine.emit("scenario_injected", self.device_id, fault=self.fault)

    def recover(self, engine):
        EQUIPMENT_FAULTS[self.fault][2](engine, self.device_id)
        engine.fault_log.append({"tick": engine.clock.tick, "transition": "recovered",
                                 "fault_label": self.fault, "device_id": self.device_id})
        engine.emit("scenario_recovered", self.device_id, fault=self.fault, repaired=True)


@dataclass
class SensorChannelFaultScenario:
    """Measurement-chain fault on any sensor channel; plant truth is untouched."""
    channel: str
    mode: str = "stuck"
    inject_tick: int = 2
    end_tick: int | None = None
    offset: float = 0.0
    rate_per_s: float = 0.0

    def __post_init__(self):
        if self.mode not in ("stuck", "bias", "drift", "dropout"):
            raise ValueError("sensor fault mode must be stuck, bias, drift, or dropout")
        if self.inject_tick < 0 or (self.end_tick is not None and self.end_tick <= self.inject_tick):
            raise ValueError("sensor fault end_tick must follow a nonnegative inject_tick")
        if abs(float(self.offset)) > 1000 or abs(float(self.rate_per_s)) > 1.0:
            raise ValueError("sensor fault offset or drift rate outside bounds")

    @property
    def device_id(self):
        return self.channel.split(".", 1)[0]

    def apply(self, engine):
        engine.physical_world.sensors.inject(self.channel, self.mode, offset=self.offset, rate_per_s=self.rate_per_s)
        engine.hidden_truth = {"scenario": "sensor_fault", "fault_label": f"sensor_{self.mode}",
                               "channel": self.channel, "device_id": self.device_id,
                               "inject_tick": self.inject_tick, "end_tick": self.end_tick}
        engine.fault_log.append({"tick": engine.clock.tick, "transition": "injected", **engine.hidden_truth})
        engine.emit("scenario_injected", self.device_id, sensor_channel=self.channel)

    def recover(self, engine):
        engine.physical_world.sensors.clear(self.channel)
        engine.fault_log.append({"tick": engine.clock.tick, "transition": "recovered",
                                 "fault_label": f"sensor_{self.mode}", "device_id": self.device_id,
                                 "channel": self.channel})
        engine.emit("scenario_recovered", self.device_id, sensor_channel=self.channel)


WATER_CONSUMERS = INCUBATORS + ("animal-protection",)


@dataclass
class WaterCutScenario:
    """The farm water main stops: every consumer on it loses its supply at once."""
    inject_tick: int = 2
    end_tick: int | None = None
    devices: tuple = WATER_CONSUMERS

    def __post_init__(self):
        if self.inject_tick < 0 or (self.end_tick is not None and self.end_tick <= self.inject_tick):
            raise ValueError("water cut end_tick must follow a nonnegative inject_tick")
        if not set(self.devices) <= set(WATER_CONSUMERS):
            raise ValueError("only the incubators and animal protection draw water")

    def apply(self, engine):
        for device_id in self.devices:
            _hidden(engine, device_id)["mains_water_cut"] = True
        engine.fault_log.append({"tick": engine.clock.tick, "transition": "injected", "fault_label": "site_water_cut",
                                 "devices": list(self.devices)})
        engine.emit("scenario_injected", "matrix", water_cut=True)

    def recover(self, engine):
        for device_id in self.devices:
            _hidden(engine, device_id).pop("mains_water_cut", None)
        engine.fault_log.append({"tick": engine.clock.tick, "transition": "recovered", "fault_label": "site_water_cut",
                                 "devices": list(self.devices)})
        engine.emit("scenario_recovered", "matrix", water_cut=False)


@dataclass
class PowerOutageScenario:
    """Site mains failure: every listed device loses its supply at once."""
    inject_tick: int = 2
    end_tick: int | None = None
    devices: tuple = INCUBATORS + ("hvac", "animal-protection")

    def __post_init__(self):
        if self.inject_tick < 0 or (self.end_tick is not None and self.end_tick <= self.inject_tick):
            raise ValueError("outage end_tick must follow a nonnegative inject_tick")

    def apply(self, engine):
        for device_id in self.devices:
            _supply(engine, device_id, 0.0)
        engine.hidden_truth = {"scenario": "power_outage", "fault_label": "site_power_outage",
                               "devices": list(self.devices), "inject_tick": self.inject_tick, "end_tick": self.end_tick}
        engine.fault_log.append({"tick": engine.clock.tick, "transition": "injected", **engine.hidden_truth})
        engine.emit("scenario_injected", "matrix", outage=True)

    def recover(self, engine):
        for device_id in self.devices:
            _supply_restore(engine, device_id)
        engine.fault_log.append({"tick": engine.clock.tick, "transition": "recovered", "fault_label": "site_power_outage",
                                 "devices": list(self.devices)})
        engine.emit("scenario_recovered", "matrix", outage=False)


WEATHER_EVENTS = {
    # kind: storm offsets, warning-feed products
    "thunderstorm": ({"pressure_drop_hpa": 8.0, "wind_m_s": 12.0, "gust_m_s": 22.0, "rain_mm_h": 35.0, "cooling_c": 5.0},
                     "severe_thunderstorm_watch", ("severe_weather_warning",)),
    "tornado": ({"pressure_drop_hpa": 14.0, "wind_m_s": 16.0, "gust_m_s": 28.0, "rain_mm_h": 45.0, "cooling_c": 6.0},
                "tornado_watch", ("severe_weather_warning", "tornado_warning")),
    "flood": ({"pressure_drop_hpa": 6.0, "wind_m_s": 6.0, "gust_m_s": 11.0, "rain_mm_h": 45.0, "cooling_c": 3.0},
              "flood_watch", ("flood_warning",)),
}


@dataclass
class WeatherEventScenario:
    """Severe weather with an authorised warning feed: watch, then warning, then expiry.

    ``tornado_strike`` decides whether a vortex actually crosses the site; a
    warning without a strike is a realistic false alarm. ``issue_warning`` can be
    False to model weather that arrives without any warning.
    """
    kind: str = "thunderstorm"
    inject_tick: int = 1800
    duration_ticks: int = 3600
    watch_lead_ticks: int = 1800
    tornado_strike: bool = False
    issue_warning: bool = True
    intensity: float = 1.0
    source: str = "simulated_national_warning_service"
    overrides: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in WEATHER_EVENTS:
            raise ValueError("weather event kind must be thunderstorm, tornado, or flood")
        if self.inject_tick < 0 or self.duration_ticks < 1 or self.watch_lead_ticks < 0:
            raise ValueError("weather event timing must be nonnegative with a positive duration")
        if not 0.1 <= float(self.intensity) <= 3.0:
            raise ValueError("weather event intensity must be within [0.1, 3]")

    def schedule(self, engine):
        watch_tick = max(engine.clock.tick, self.inject_tick - self.watch_lead_ticks)
        if self.issue_warning:
            engine.clock.schedule(watch_tick, self.issue_watch, "weather_watch")
        engine.clock.schedule(self.inject_tick, lambda: self.apply(engine), "weather_event_start")
        engine.clock.schedule(self.inject_tick + self.duration_ticks, lambda: self.recover(engine), "weather_event_end")
        self._engine = engine

    def _provider(self, engine):
        return engine.world.devices["animal-protection"].weather_provider

    def issue_watch(self):
        engine = self._engine
        w = self._provider(engine)
        now = engine.clock.time
        w.update(weather_watch=WEATHER_EVENTS[self.kind][1], watch_issue_time_s=now,
                 watch_valid_until_s=now + (self.watch_lead_ticks + self.duration_ticks + 1800) * engine.clock.step_seconds,
                 warning_source=self.source)

    def apply(self, engine):
        offsets, _, warnings = WEATHER_EVENTS[self.kind]
        storm = {key: value * float(self.intensity) for key, value in offsets.items()}
        storm.update(self.overrides)
        ramp = min(1800.0, 0.25 * self.duration_ticks * engine.clock.step_seconds)
        engine.physical_world.start_storm(ramp_s=ramp, tornado_strike=bool(self.tornado_strike), **storm)
        if self.issue_warning:
            w = self._provider(engine)
            now = engine.clock.time
            w.update({name: True for name in warnings})
            w.update(warning_issue_time_s=now, warning_source=self.source,
                     warning_valid_until_s=now + (self.duration_ticks + 1800) * engine.clock.step_seconds)
        engine.hidden_truth = {"scenario": "weather_event", "fault_label": self.kind, "inject_tick": self.inject_tick,
                               "duration_ticks": self.duration_ticks, "tornado_strike": self.tornado_strike,
                               "warned": self.issue_warning}
        engine.fault_log.append({"tick": engine.clock.tick, "transition": "injected", **engine.hidden_truth})
        engine.emit("scenario_injected", "animal-protection", weather=self.kind)

    def recover(self, engine):
        engine.physical_world.end_storm()
        engine.fault_log.append({"tick": engine.clock.tick, "transition": "recovered", "fault_label": self.kind})
        engine.emit("scenario_recovered", "animal-protection", weather=self.kind)
