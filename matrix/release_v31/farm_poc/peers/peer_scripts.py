"""Deterministic response scripts; Matrix supplies all measurement content."""
from ..fastcopy import json_copy


class PeerResponseScript:
    """Selects an allowed canned response mode and echoes supplied telemetry."""

    def __init__(self, mode="normal", delay_ticks=0):
        self.mode = mode
        self.delay_ticks = max(0, int(delay_ticks))

    def respond(self, device_id, telemetry, tick, cached_telemetry, cached_tick, sample_tick=None):
        if self.mode in ("missing", "unavailable"):
            return None
        if self.mode == "stale":
            sample, sample_tick = cached_telemetry, cached_tick
        else:
            # Label the sample with the tick it was measured, not the reply tick.
            sample, sample_tick = telemetry, (tick if sample_tick is None else sample_tick)
        summary = self._summary(device_id, sample)
        return {
            "device_id": device_id,
            "summary": summary,
            "sample_tick": sample_tick,
            "observed_tick": tick,
            "delay_ticks": self.delay_ticks,
        }

    @staticmethod
    def _summary(device_id, telemetry):
        fields = {
            "incubator-2": ("ac_voltage_v", "power_status", "health", "temperatures_c",
                            "humidities_pct", "faults", "alarms"),
            "hvac": ("zones", "supply_air_temperature_c", "supply_air_humidity_pct",
                     "supply_fan_actual_rpm", "return_fan_actual_rpm", "supply_airflow_m3_h",
                     "ventilation_state", "power_status", "faults", "alarms", "emergency_mode"),
            "animal-protection": ("co2_ppm_by_zone", "o2_pct_by_zone", "ammonia_ppm_by_zone", "co_ppm_by_zone",
                                  "feed_supply_status", "water_availability_status",
                                  "environment", "weather_warning", "flood",
                                  "window_actual_positions_pct", "vent_actual_positions_pct",
                                  "overall_protection_state", "alarms", "faults"),
        }
        return {key: json_copy(telemetry[key]) for key in fields[device_id] if key in telemetry}
