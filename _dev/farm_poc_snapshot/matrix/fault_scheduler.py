"""Seeded, private periodic fault curriculum using existing Matrix mechanisms."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import random
from typing import Any

from .scenario import OvervoltageScenario, SensorFaultScenario, ThermalDisturbanceScenario


@dataclass(frozen=True)
class FaultPlan:
    onset_tick: int
    family: str
    duration_ticks: int
    severity: float
    peer_mode: str = "normal"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PeriodicFaultScheduler:
    """Jittered-period schedule; all schedule state stays on the evaluator side."""

    FAMILIES = ("supply_voltage", "sensor_bias", "sensor_drift", "sensor_stuck",
                "sensor_dropout", "incubator_heat_load", "hvac_supply_fan")
    SUPPORTED_FAMILIES = FAMILIES + ("fire_smoke",)

    def __init__(self, *, seed: int, total_ticks: int, mean_interval_ticks: int = 36_000,
                 interval_jitter_ticks: int = 9_000, first_onset_range: tuple[int, int] = (12_000, 24_000),
                 families: tuple[str, ...] | None = None,
                 peer_mode_weights: dict[str, float] | None = None):
        if total_ticks < 1 or mean_interval_ticks < 1 or interval_jitter_ticks < 0:
            raise ValueError("invalid fault scheduler horizon or interval")
        if interval_jitter_ticks >= mean_interval_ticks:
            raise ValueError("interval jitter must be smaller than mean interval")
        self.seed, self.total_ticks = int(seed), int(total_ticks)
        if len(first_onset_range) != 2 or first_onset_range[0] < 0 or first_onset_range[1] < first_onset_range[0]:
            raise ValueError("first_onset_range must be an ordered nonnegative pair")
        self.mean_interval_ticks, self.interval_jitter_ticks = mean_interval_ticks, interval_jitter_ticks
        self.first_onset_range = first_onset_range
        self.families = tuple(families or self.FAMILIES)
        if not self.families or len(set(self.families)) != len(self.families) or set(self.families) - set(self.SUPPORTED_FAMILIES):
            raise ValueError("fault families must be a nonempty unique subset of supported families")
        self.peer_mode_weights = dict(peer_mode_weights or {"elevated": .45, "normal": .25, "stale": .15, "missing": .15})
        if set(self.peer_mode_weights) != {"elevated", "normal", "stale", "missing"} or any(float(v) < 0 for v in self.peer_mode_weights.values()) or sum(self.peer_mode_weights.values()) <= 0:
            raise ValueError("peer mode weights must define elevated, normal, stale, and missing with nonnegative weights")
        self.schedule = self._build()
        self.next_index = 0
        self.active: FaultPlan | None = None
        self._active_until: int | None = None
        self._hvac_previous_fault = None

    def _build(self) -> tuple[FaultPlan, ...]:
        rng = random.Random(self.seed)
        families = list(self.families)
        rng.shuffle(families)
        onset = rng.randint(*self.first_onset_range)
        plans = []
        index = 0
        while onset < self.total_ticks:
            family = families[index % len(families)]
            if family == "supply_voltage":
                duration, severity = rng.randint(60, 240), round(rng.uniform(261.0, 268.0), 2)
                peer_mode = rng.choices(tuple(self.peer_mode_weights), tuple(self.peer_mode_weights.values()))[0]
            elif family.startswith("sensor_"):
                duration = rng.randint(120, 480)
                severity = round(rng.uniform(1.0, 4.0) * (-1 if rng.random() < 0.5 else 1), 2)
                peer_mode = "normal"
            elif family == "incubator_heat_load":
                # A stuck-on auxiliary heat source near the cabinet: 80-300 W for 10-40 min.
                duration, severity, peer_mode = rng.randint(600, 2_400), round(rng.uniform(80, 300), 1), "normal"
            elif family == "hvac_supply_fan":
                duration, severity, peer_mode = rng.randint(300, 1_200), 0.0, "normal"
            else:
                duration, severity, peer_mode = rng.randint(180, 900), round(rng.uniform(1_000, 4_000), 1), "normal"
            if onset + duration < self.total_ticks:
                plans.append(FaultPlan(onset, family, duration, severity, peer_mode))
            index += 1
            interval = self.mean_interval_ticks + rng.randint(-self.interval_jitter_ticks, self.interval_jitter_ticks)
            onset += max(interval, duration + 1)
        return tuple(plans)

    def before_tick(self, engine) -> list[dict[str, Any]]:
        """Apply scheduled start/end transitions at the current Matrix tick."""
        tick = engine.clock.tick
        records = []
        if self.active and self._active_until is not None and tick >= self._active_until:
            if self.active.family == "fire_smoke":
                # Staff or the fire service extinguish it; smoke takes time to clear.
                engine.physical_world.extinguish()
                engine.emit("scenario_recovered", "animal-protection", family="fire_smoke")
            elif self.active.family == "hvac_supply_fan":
                hvac = engine.world.devices["hvac"]
                hvac.config["supply_fan_fault"] = bool(self._hvac_previous_fault)
                if not self._hvac_previous_fault:
                    hvac.internal["faults"] = [x for x in hvac.internal["faults"] if x != "SUPPLY_FAN_FAILURE"]
                hvac._publish()
                engine.emit("scenario_recovered", "hvac", equipment="supply_fan", fault="repaired")
            records.append({"tick": tick, "transition": "recovered", "family": self.active.family,
                            "duration_ticks": self.active.duration_ticks})
            # The scheduled callback still runs during this Matrix step, but
            # clearing the scenario handle prevents MatrixEngine from issuing
            # peer queries on every later tick after the scenario has ended.
            engine.scenario = None
            engine.scenario_applied = False
            self.active = None; self._active_until = None
        if self.active is None and self.next_index < len(self.schedule):
            plan = self.schedule[self.next_index]
            if tick >= plan.onset_tick:
                self.next_index += 1
                self.active = plan
                self._active_until = plan.onset_tick + plan.duration_ticks
                if plan.family == "supply_voltage":
                    engine.start_scenario(OvervoltageScenario(
                        inject_tick=tick, voltage_v=plan.severity,
                        peer_modes={"incubator-2": plan.peer_mode}, recovery_tick=self._active_until))
                elif plan.family.startswith("sensor_"):
                    mode = {"sensor_bias": "bias", "sensor_drift": "drift",
                            "sensor_stuck": "stuck_at", "sensor_dropout": "dropout"}[plan.family]
                    engine.start_sensor_fault(SensorFaultScenario(mode=mode, inject_tick=tick,
                        end_tick=self._active_until, offset=plan.severity,
                        drift_per_tick=max(-0.1, min(0.1, plan.severity / max(plan.duration_ticks, 1)))))
                elif plan.family == "incubator_heat_load":
                    engine.start_thermal_scenario(ThermalDisturbanceScenario(
                        inject_tick=tick, heat_load_w=plan.severity, duration_ticks=plan.duration_ticks,
                        cycle_interval_ticks=plan.duration_ticks + 1))
                elif plan.family == "fire_smoke":
                    fire = engine.physical_world.ignite(**self.fire_parameters(plan))
                    engine.hidden_truth = {"scenario": "fire_smoke", "fault_label": f"{fire.zone}_fire",
                                           "fire": fire.public_summary(),
                                           "onset_tick": tick, "end_tick": self._active_until}
                    engine.scenario_applied = True
                    engine.peer_registry.query_all(engine)
                    engine.emit("scenario_started", "matrix", inject_tick=tick)
                    engine.emit("scenario_injected", "animal-protection", source="simulated_combustion")
                else:
                    hvac = engine.world.devices["hvac"]
                    self._hvac_previous_fault = bool(hvac.config.get("supply_fan_fault", False))
                    hvac.config["supply_fan_fault"] = True
                    hvac.internal["supply_fan_on"] = False
                    hvac.internal["supply_fan_rpm"] = 0
                    if "SUPPLY_FAN_FAILURE" not in hvac.internal["faults"]:
                        hvac.internal["faults"].append("SUPPLY_FAN_FAILURE")
                    hvac._publish()
                    engine.scenario_applied = True
                    engine.peer_registry.query_all(engine)
                    engine.emit("scenario_started", "matrix", inject_tick=tick)
                    engine.emit("scenario_injected", "hvac", equipment="supply_fan", fault="failure")
                records.append({"tick": tick, "transition": "injected", **plan.as_dict()})
        return records

    def fire_parameters(self, plan: FaultPlan) -> dict[str, Any]:
        """Seeded fire details drawn from their own stream so schedules stay stable."""
        rng = random.Random(f"{self.seed}:fire:{plan.onset_tick}")
        zone = rng.choices(("barn", "storage", "workspace"), weights=(4, 4, 2))[0]
        mode = rng.choices(("flaming", "smoldering"), weights=(6, 4))[0]
        length, width = {"barn": (12.5, 10.0), "storage": (7.5, 8.0), "workspace": (6.0, 5.5)}[zone]
        parameters = {"zone": zone, "mode": mode,
                      "growth": rng.choice(("slow", "medium", "fast")),
                      "peak_kw": round(plan.severity / 4.0, 1),
                      "fuel_kg": round(rng.uniform(30.0, 120.0), 1),
                      "position_m": (round(rng.uniform(1.0, length - 1.0), 2), round(rng.uniform(1.0, width - 1.0), 2))}
        if mode == "smoldering":
            parameters["transition_to_flaming_s"] = round(rng.uniform(600.0, 2_400.0), 1)
        return parameters

    def state_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "seed": self.seed, "total_ticks": self.total_ticks,
                "mean_interval_ticks": self.mean_interval_ticks,
                "interval_jitter_ticks": self.interval_jitter_ticks,
                "first_onset_range": list(self.first_onset_range), "families": list(self.families),
                "peer_mode_weights": self.peer_mode_weights,
                "schedule": [item.as_dict() for item in self.schedule], "next_index": self.next_index,
                "active": None if self.active is None else self.active.as_dict(),
                "active_until": self._active_until, "hvac_previous_fault": self._hvac_previous_fault}

    @classmethod
    def from_state_dict(cls, state: dict[str, Any]) -> "PeriodicFaultScheduler":
        if state.get("schema_version") != 1:
            raise ValueError("unsupported fault scheduler checkpoint")
        instance = cls(seed=state["seed"], total_ticks=state["total_ticks"],
                       mean_interval_ticks=state["mean_interval_ticks"],
                       interval_jitter_ticks=state["interval_jitter_ticks"],
                       first_onset_range=tuple(state["first_onset_range"]),
                       families=tuple(state["families"]), peer_mode_weights=state["peer_mode_weights"])
        expected = [item.as_dict() for item in instance.schedule]
        if expected != state.get("schedule"):
            raise ValueError("fault scheduler checkpoint schedule does not match seed/config")
        instance.next_index = int(state["next_index"])
        active = state.get("active")
        instance.active = FaultPlan(**active) if active else None
        instance._active_until = state.get("active_until")
        instance._hvac_previous_fault = state.get("hvac_previous_fault")
        return instance
