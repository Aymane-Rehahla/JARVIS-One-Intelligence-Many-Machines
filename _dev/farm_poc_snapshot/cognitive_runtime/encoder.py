"""Deterministic, allowlist-validated numeric encoder for ObservationV1."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from math import isfinite
from typing import Any

import torch

from farm_poc.training.contract import OBSERVATION_SCHEMA_VERSION, validate_observation


ENCODER_VERSION = 1


@dataclass(frozen=True)
class EncodedInputV1:
    schema_version: int
    encoder_version: int
    feature_names: tuple[str, ...]
    values: tuple[float, ...]
    validity_mask: tuple[float, ...]

    def tensor(self) -> torch.Tensor:
        return torch.tensor(self.values, dtype=torch.float32)


def _clip_norm(value: Any, low: float, high: float) -> tuple[float, float]:
    """Return normalized value and an explicit valid bit; zero stays valid."""
    if value is None or isinstance(value, bool):
        return 0.0, 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0, 0.0
    if not isfinite(number) or number < low or number > high:
        return 0.0, 0.0
    return (number - low) / (high - low), 1.0


def _count(values: Any, cap: int = 16) -> float:
    return min(len(values or ()), cap) / cap


def _code(value: Any, mapping: dict[str, float]) -> float:
    return mapping.get(str(value), 0.0)


class OperationalEncoderV1:
    """Selected operational fields from the audited contract, not a raw-state dump."""

    # Slot order preserves source identity without relying on arrival order.
    PEER_ORDER = ("incubator-2", "hvac", "animal-protection")
    CONTEXT_DIMENSION = 8
    CONTEXT_FEATURE_NAMES = (
        "cmm.working_fill", "cmm.episodic_fill", "cmm.semantic_fill",
        "cmm.procedural_fill", "cmm.mandatory_rule_fill",
        "cmm.pending_verification", "cmm.working_hypothesis_fill",
        "cmm.confirmed_outcome_fill",
    )
    _FEATURE_NAMES: tuple[str, ...] = (
        "direct.tick", "direct.power_status", "direct.protection_active",
        "direct.heater_enabled", "direct.online", "direct.health",
        "direct.ac_voltage_v", "direct.ac_voltage_v.valid",
        "direct.frequency_hz", "direct.frequency_hz.valid",
        "direct.ac_current_a", "direct.ac_current_a.valid",
        "direct.real_power_w", "direct.real_power_w.valid",
        "direct.apparent_power_va", "direct.apparent_power_va.valid",
        "direct.power_factor", "direct.power_factor.valid",
        "direct.energy_kwh", "direct.energy_kwh.valid",
        "direct.heater_current_a", "direct.heater_current_a.valid",
        "direct.heater_power_w", "direct.heater_power_w.valid",
        "direct.temperature_1_c", "direct.temperature_1_c.valid",
        "direct.temperature_2_c", "direct.temperature_2_c.valid",
        "direct.temperature_3_c", "direct.temperature_3_c.valid",
        "direct.humidity_1_pct", "direct.humidity_1_pct.valid",
        "direct.humidity_2_pct", "direct.humidity_2_pct.valid",
        "direct.humidity_3_pct", "direct.humidity_3_pct.valid",
        "direct.reservoir_pct", "direct.reservoir_pct.valid",
        "direct.fan_command_pct", "direct.fan_command_pct.valid",
        "direct.airflow_m3_h", "direct.airflow_m3_h.valid",
        "direct.fault_count", "direct.alarm_count",
        "peer.incubator-2.present", "peer.incubator-2.sample_age", "peer.incubator-2.observed_age",
        "peer.incubator-2.ac_voltage_v", "peer.incubator-2.ac_voltage_v.valid",
        "peer.incubator-2.health", "peer.incubator-2.fault_count", "peer.incubator-2.alarm_count",
        "peer.hvac.present", "peer.hvac.sample_age", "peer.hvac.observed_age",
        "peer.hvac.barn_temperature_c", "peer.hvac.barn_temperature_c.valid",
        "peer.hvac.barn_humidity_pct", "peer.hvac.barn_humidity_pct.valid",
        "peer.hvac.power_status", "peer.hvac.fault_count", "peer.hvac.alarm_count",
        "peer.animal-protection.present", "peer.animal-protection.sample_age", "peer.animal-protection.observed_age",
        "peer.animal-protection.co2_ppm", "peer.animal-protection.co2_ppm.valid",
        "peer.animal-protection.o2_pct", "peer.animal-protection.o2_pct.valid",
        "peer.animal-protection.ammonia_ppm", "peer.animal-protection.ammonia_ppm.valid",
        "peer.animal-protection.weather_warning", "peer.animal-protection.flood",
        "peer.animal-protection.protection_state", "peer.animal-protection.alarm_count",
        "peer.animal-protection.fault_count",
    )

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self._FEATURE_NAMES

    @property
    def dimension(self) -> int:
        return len(self._FEATURE_NAMES)

    def schema_manifest(self) -> dict[str, Any]:
        """Inspectable contract; feature order is the sequence in this manifest."""
        manifest = {"schema_version": OBSERVATION_SCHEMA_VERSION, "encoder_version": ENCODER_VERSION,
                "dimension": self.dimension, "feature_order": list(self.feature_names),
                "context_dimension": self.CONTEXT_DIMENSION,
                "context_feature_order": list(self.CONTEXT_FEATURE_NAMES),
                "normalization": {"temperature_c": [-40, 100], "humidity_pct": [0, 100],
                                  "ac_voltage_v": [0, 300], "frequency_hz": [0, 100],
                                  "current_a": [0, 100], "power_w_or_va": [0, 20000],
                                  "energy_kwh": [0, 1000000], "airflow_m3_h": [0, 5000],
                                  "co2_ppm": [0, 100000], "ammonia_ppm": [0, 1000],
                                  "age_ticks": [0, 100], "categorical": "documented fixed code; 0 is unknown"},
                "missing": "numeric value 0 plus paired .valid feature 0; valid zero has .valid feature 1",
                "peer_order": list(self.PEER_ORDER)}
        manifest["schema_hash"] = self.manifest_hash(manifest)
        return manifest

    @staticmethod
    def manifest_hash(manifest: dict[str, Any]) -> str:
        """Hash canonical schema content, excluding its self-referential hash field."""
        canonical = {key: value for key, value in manifest.items() if key != "schema_hash"}
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def schema_hash(self) -> str:
        return str(self.schema_manifest()["schema_hash"])

    def verify_schema_manifest(self, manifest: dict[str, Any]) -> None:
        """Reject a dataset/checkpoint manifest that does not exactly describe this encoder."""
        expected = self.schema_manifest()
        if manifest.get("schema_hash") != self.manifest_hash(manifest):
            raise ValueError("encoder manifest hash is invalid")
        if manifest != expected:
            raise ValueError("encoder manifest is incompatible with OperationalEncoderV1")

    def write_schema_manifest(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(self.schema_manifest(), stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")

    def encode(self, observation: dict[str, Any]) -> EncodedInputV1:
        validate_observation(observation)
        direct = observation["incubator_1_direct_observation"]
        if direct.get("contract_version") != OBSERVATION_SCHEMA_VERSION:
            raise ValueError("unsupported direct-observation schema version")
        telemetry = direct.get("telemetry", {})
        values: list[float] = []
        validity: list[float] = []

        def scalar(value: Any, low: float, high: float):
            number, valid = _clip_norm(value, low, high)
            values.extend((number, valid)); validity.extend((valid, valid))

        values.extend((min(max(float(direct.get("tick", 0)), 0.0), 10000.0) / 10000.0,
                       _code(direct.get("local_controller", {}).get("power_status"),
                             {"normal": 0.25, "overvoltage": 0.5, "fault": 0.75}),
                       float(bool(direct.get("local_controller", {}).get("protection_active"))),
                       float(bool(direct.get("local_controller", {}).get("heater_enabled"))),
                       float(bool(telemetry.get("online"))),
                       _code(telemetry.get("health"), {"healthy": 0.25, "warning": 0.5, "fault": 0.75})))
        validity.extend((1.0,) * 6)
        for field, low, high in (("ac_voltage_v", 0, 300), ("frequency_hz", 0, 100),
                                 ("ac_current_a", 0, 100), ("real_power_w", 0, 20000),
                                 ("apparent_power_va", 0, 20000), ("power_factor", -1, 1),
                                 ("energy_kwh", 0, 1_000_000), ("heater_current_a", 0, 100),
                                 ("heater_power_w", 0, 20000)):
            scalar(telemetry.get(field), low, high)
        for value in (telemetry.get("temperatures_c") or [])[:3]: scalar(value, -40, 100)
        for _ in range(3 - len((telemetry.get("temperatures_c") or [])[:3])): scalar(None, -40, 100)
        for value in (telemetry.get("humidities_pct") or [])[:3]: scalar(value, 0, 100)
        for _ in range(3 - len((telemetry.get("humidities_pct") or [])[:3])): scalar(None, 0, 100)
        scalar(telemetry.get("reservoir_pct"), 0, 100)
        scalar(telemetry.get("fan_command_pct"), 0, 100)
        scalar(telemetry.get("airflow_m3_h"), 0, 5000)
        values.extend((_count(telemetry.get("faults")), _count(telemetry.get("alarms"))))
        validity.extend((1.0, 1.0))

        current_tick = int(direct.get("tick", 0))
        reports = observation["scripted_peer_reports_to_jarvis"]
        self._peer_incubator(values, validity, reports.get("incubator-2"), current_tick)
        self._peer_hvac(values, validity, reports.get("hvac"), current_tick)
        self._peer_animal(values, validity, reports.get("animal-protection"), current_tick)
        if len(values) != self.dimension:
            raise RuntimeError("EncodedInputV1 implementation and declared schema diverged")
        if not all(isfinite(item) for item in values):
            raise ValueError("non-finite encoded feature")
        return EncodedInputV1(OBSERVATION_SCHEMA_VERSION, ENCODER_VERSION,
                              self.feature_names, tuple(values), tuple(validity))

    @staticmethod
    def _ages(report: dict | None, tick: int) -> tuple[float, float, float]:
        if not report: return 0.0, 0.0, 0.0
        sample = max(0, min(100, tick - int(report.get("sample_tick", tick)))) / 100.0
        observed = max(0, min(100, tick - int(report.get("observed_tick", tick)))) / 100.0
        return 1.0, sample, observed

    def _peer_incubator(self, values, validity, report, tick):
        present, sample, observed = self._ages(report, tick); values.extend((present, sample, observed)); validity.extend((present, present, present))
        summary = (report or {}).get("summary", {}); voltage, valid = _clip_norm(summary.get("ac_voltage_v"), 0, 300)
        values.extend((voltage, valid, _code(summary.get("health"), {"healthy": .25, "warning": .5, "fault": .75}), _count(summary.get("faults")), _count(summary.get("alarms"))))
        validity.extend((valid, valid, present, present, present))

    def _peer_hvac(self, values, validity, report, tick):
        present, sample, observed = self._ages(report, tick); values.extend((present, sample, observed)); validity.extend((present, present, present))
        summary = (report or {}).get("summary", {}); zone = summary.get("zones", {}).get("barn", {})
        temp, tv = _clip_norm(zone.get("temperature_c"), -40, 100); hum, hv = _clip_norm(zone.get("humidity_pct"), 0, 100)
        values.extend((temp, tv, hum, hv, _code(summary.get("power_status"), {"normal": .25, "fault": .75}), _count(summary.get("faults")), _count(summary.get("alarms"))))
        validity.extend((tv, tv, hv, hv, present, present, present))

    def _peer_animal(self, values, validity, report, tick):
        present, sample, observed = self._ages(report, tick); values.extend((present, sample, observed)); validity.extend((present, present, present))
        summary = (report or {}).get("summary", {}); co2, cv = _clip_norm(summary.get("co2_ppm_by_zone", {}).get("barn"), 0, 100000); o2, ov = _clip_norm(summary.get("o2_pct_by_zone", {}).get("barn"), 0, 100); ammonia, av = _clip_norm(summary.get("ammonia_ppm_by_zone", {}).get("barn"), 0, 1000)
        values.extend((co2, cv, o2, ov, ammonia, av, float(bool(summary.get("weather_warning"))), float(bool(summary.get("flood"))), _code(summary.get("overall_protection_state"), {"normal": .25, "alert": .5, "emergency": .75}), _count(summary.get("alarms")), _count(summary.get("faults"))))
        validity.extend((cv, cv, ov, ov, av, av, present, present, present, present, present))
