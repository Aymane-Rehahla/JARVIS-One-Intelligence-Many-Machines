from dataclasses import dataclass,field
from math import isfinite
@dataclass
class OvervoltageScenario:
    inject_tick:int=2
    voltage_v:float=263.0
    peer_modes:dict=field(default_factory=dict)
    action:str="none"
    action_fails:bool=False
    action_unverifiable:bool=False
    action_arguments:dict=field(default_factory=dict)
    recovery_tick:int|None=None
    recovery_voltage_v:float=230.0
    def apply(self,engine):
        device=engine.world.devices["incubator-1"]
        device.internal["ac_voltage_v"]=self.voltage_v
        # The optional Incubator 2 corroboration is a modeled shared-supply
        # disturbance: its own voltage sensor measures the same injected value.
        peer_mode=self.peer_modes.get("incubator-2","normal")
        if peer_mode=="elevated" or (isinstance(peer_mode,dict) and peer_mode.get("mode")=="elevated"):
            engine.world.devices["incubator-2"].internal["ac_voltage_v"]=self.voltage_v
        engine.hidden_truth={"scenario":"overvoltage","fault_label":"supply_overvoltage","injected_voltage_v":self.voltage_v,"root_cause":"unspecified_supply_disturbance"}
        engine.emit("scenario_injected","incubator-1",voltage_v=self.voltage_v)
    def recover(self,engine):
        device=engine.world.devices["incubator-1"]
        previous_voltage=float(device.internal["ac_voltage_v"])
        device.internal["ac_voltage_v"]=self.recovery_voltage_v
        engine.emit("scenario_recovered","incubator-1",previous_voltage_v=previous_voltage,voltage_v=self.recovery_voltage_v,
                    protective_latch_active=bool(device.internal["cutoff"]))


@dataclass
class SensorFaultScenario:
    """Incubator measurement-path fault; physical-world truth is untouched."""
    mode: str = "bias"
    inject_tick: int = 2
    end_tick: int = 8
    sensor_id: str = "room_temperature_1"
    offset: float = 3.0
    drift_per_tick: float = 0.1
    stuck_value: float | None = None
    device_id: str = "incubator-1"

    SENSOR_MAP = {
        "room_temperature_1": ("room_temperatures_c", 0, "temperature_1"),
        "room_temperature_2": ("room_temperatures_c", 1, "temperature_2"),
        "room_temperature_3": ("room_temperatures_c", 2, "temperature_3"),
    }

    def __post_init__(self):
        from math import isfinite
        if self.mode not in {"bias", "drift", "stuck_at", "dropout"}:
            raise ValueError("mode must be bias, drift, stuck_at, or dropout")
        if self.sensor_id not in self.SENSOR_MAP:
            raise ValueError("unsupported sensor_id")
        if self.inject_tick < 0 or self.end_tick <= self.inject_tick:
            raise ValueError("sensor fault end_tick must be after a nonnegative inject_tick")
        if self.end_tick - self.inject_tick > 10_000:
            raise ValueError("sensor fault duration cannot exceed 10000 ticks")
        if not all(isfinite(float(v)) for v in (self.offset, self.drift_per_tick)):
            raise ValueError("sensor fault parameters must be finite")
        if abs(float(self.offset)) > 10 or abs(float(self.drift_per_tick)) > 1:
            raise ValueError("sensor fault bias/drift exceeds configured bounds")
        if self.mode == "drift" and abs(float(self.drift_per_tick)) * (self.end_tick - self.inject_tick) > 20:
            raise ValueError("total configured drift cannot exceed 20 °C")
        if self.stuck_value is not None and (not isfinite(float(self.stuck_value)) or not -80 <= float(self.stuck_value) <= 100):
            raise ValueError("stuck_value must be finite and within [-80, 100]")

    def apply(self, engine):
        device = engine.world.devices[self.device_id]
        field, index, health = self.SENSOR_MAP[self.sensor_id]
        self._captured_value = device.internal[field][index]
        self._health_before = device.internal["sensor_health"].get(health, "good")
        engine.active_sensor_fault = self
        engine.hidden_truth = {"scenario": "sensor_fault", "fault_label": self.mode,
                               "sensor_id": self.sensor_id, "inject_tick": self.inject_tick,
                               "end_tick": self.end_tick}
        engine.emit("scenario_injected", self.device_id, sensor_id=self.sensor_id,
                    fault_mode=self.mode)

    def apply_measurement(self, engine):
        device = engine.world.devices[self.device_id]
        field, index, health = self.SENSOR_MAP[self.sensor_id]
        readings = device.internal[field]
        current = readings[index]
        if self.mode == "dropout":
            readings[index] = None
            device.internal["sensor_health"][health] = "failed"
        elif current is not None:
            if self.mode == "bias":
                value = float(current) + float(self.offset)
            elif self.mode == "drift":
                age = engine.clock.tick - self.inject_tick
                value = float(current) + float(self.drift_per_tick) * age
            else:
                value = self._captured_value if self.stuck_value is None else self.stuck_value
            readings[index] = round(float(value), 4)
            device.internal["sensor_health"][health] = self._health_before
        device._publish()

    def recover(self, engine):
        device = engine.world.devices[self.device_id]
        _, _, health = self.SENSOR_MAP[self.sensor_id]
        device.internal["sensor_health"][health] = self._health_before
        engine.active_sensor_fault = None
        engine.emit("scenario_recovered", self.device_id, sensor_id=self.sensor_id,
                    measurement_path_restored=True)


@dataclass
class ThermalDisturbanceScenario:
    """Bounded physical heat-load scenario for the existing incubator model.

    The load is authoritative plant input, not a synthetic temperature reading.
    The thermostat can switch the regular heater off; heat dissipates through
    the normal envelope and ventilation equation once the load ends.
    """
    inject_tick: int = 2
    heat_load_w: float = 300.0
    duration_ticks: int = 3
    cycles: int = 1
    cycle_interval_ticks: int = 28
    device_id: str = "incubator-1"
    peer_modes: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.device_id != "incubator-1":
            raise ValueError("thermal curriculum currently supports incubator-1 only")
        if self.inject_tick < 0 or self.duration_ticks < 1 or self.cycles < 1:
            raise ValueError("thermal timing values must be positive and injection nonnegative")
        if self.cycle_interval_ticks <= self.duration_ticks:
            raise ValueError("thermal cycles must be separated by at least one recovery tick")
        if self.cycles > 4 or self.cycle_interval_ticks > 1_000:
            raise ValueError("thermal scenario exceeds bounded curriculum limits")
        if not isfinite(float(self.heat_load_w)) or not 100.0 <= float(self.heat_load_w) <= 10_000.0:
            raise ValueError("thermal heat load must be finite and within [100, 10000] W")

    def cycle_start(self, cycle_index: int) -> int:
        return self.inject_tick + cycle_index * self.cycle_interval_ticks

    def apply(self, engine, cycle_index: int = 0):
        engine.physical_world.set_incubator_external_heat_load(self.device_id, self.heat_load_w)
        if cycle_index == 0:
            engine.hidden_truth = {
                "scenario": "thermal_disturbance",
                "fault_label": "intermittent_external_heat_load" if self.cycles > 1 else "temporary_external_heat_load",
                "heat_load_w": self.heat_load_w,
                "cycles": self.cycles,
            }
        engine.emit("scenario_injected", self.device_id, cycle_index=cycle_index)

    def recover(self, engine, cycle_index: int = 0):
        engine.physical_world.set_incubator_external_heat_load(self.device_id, 0.0)
        engine.emit("scenario_recovered", self.device_id, cycle_index=cycle_index,
                    external_heat_load_removed=True)
