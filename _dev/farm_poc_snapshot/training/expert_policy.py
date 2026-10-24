"""Auditable v1 behavior-cloning heuristic using public observations only."""
from __future__ import annotations

from copy import deepcopy

from .contract import validate_observation


class ExpertPolicy:
    """Simple rules, not a validated diagnostic system or real Jarvis."""

    VERSION = "farm-expert-target-v1"
    TARGET_SCHEMA_VERSION = 1

    def __init__(self, *, verification_interval_ticks: int = 5,
                 overtemperature_c: float = 39.0, recovery_c: float = 38.5):
        if verification_interval_ticks < 1 or recovery_c >= overtemperature_c:
            raise ValueError("invalid policy thresholds")
        self.verification_interval_ticks = verification_interval_ticks
        self.overtemperature_c = overtemperature_c
        self.recovery_c = recovery_c
        self.reset()

    def reset(self):
        self.cycle_count = 0
        self._active_cycle = False
        self._last_simulation_tick = None
        self._next_verify_tick = None
        self._last_decision = "NONE"
        self._history: list[dict] = []
        self._completed_cycle_ticks: list[int] = []
        self._verification_origin_tick: int | None = None
        self._supply_diagnosed_active = False
        self._local_voltage_investigated_active = False
        self._recurrence_reported_cycle_count = 0

    def decide(self, observation: dict) -> dict:
        target, _ = self.decide_with_audit(observation)
        return target

    def decide_with_audit(self, observation: dict) -> tuple[dict, dict]:
        """Emit a target and a replayable trace built only from public evidence."""
        validate_observation(observation)
        obs = deepcopy(observation)
        simulation_tick = int(obs["incubator_1_direct_observation"].get("tick", len(self._history)))
        if self._last_simulation_tick is not None and simulation_tick < self._last_simulation_tick:
            raise ValueError("expert observations cannot move backwards in Matrix time")
        # The Matrix reset observation and first completed transition can share
        # the engine's legacy tick label. Policy timers use decision sequence.
        tick = len(self._history)
        direct = obs["incubator_1_direct_observation"]
        telemetry = direct.get("telemetry", {})
        temps = telemetry.get("temperatures_c") or []
        available_temps = [float(value) for value in temps if value is not None]
        if len(available_temps) < 2:
            # A gap cannot prove recovery or complete a cycle.
            self._active_cycle = False
        else:
            overtemp = max(available_temps) > self.overtemperature_c
            if overtemp and not self._active_cycle:
                self._active_cycle = True
            elif not overtemp and self._active_cycle and max(available_temps) <= self.recovery_c:
                self.cycle_count += 1
                self._completed_cycle_ticks.append(tick)
                self._active_cycle = False

        decision, diagnosis, rationale = "NONE", "UNKNOWN", "benign_or_unresolved_single_cycle"
        voltage = telemetry.get("ac_voltage_v")
        try:
            elevated_local_voltage = voltage is not None and float(voltage) > 260.0
        except (TypeError, ValueError):
            elevated_local_voltage = False
        peer_voltage = None
        peer = obs.get("scripted_peer_reports_to_jarvis", {}).get("incubator-2")
        if peer:
            age = tick - int(peer.get("sample_tick", -10**9))
            if 0 <= age <= 2:
                peer_voltage = peer.get("summary", {}).get("ac_voltage_v")
        corroborated_supply = elevated_local_voltage and peer_voltage is not None and float(peer_voltage) > 260.0
        # A temporarily absent/stale peer report cannot erase a diagnosis
        # already formed from fresh public corroboration. The latch clears
        # only when the observed local voltage has recovered.
        if not elevated_local_voltage:
            self._supply_diagnosed_active = False
        if not elevated_local_voltage:
            self._local_voltage_investigated_active = False

        if self._next_verify_tick is not None and tick >= self._next_verify_tick:
            decision, rationale = "VERIFY", "scheduled_from_prior_public_decision"
            # One verification is scheduled for each unresolved public trigger.
            # A later anomaly can schedule a new one; idle conditions do not
            # generate an endless series of verification labels.
            self._next_verify_tick = None
        elif corroborated_supply and not self._supply_diagnosed_active:
            decision, diagnosis, rationale = "DIAGNOSE", "WIDER_SUPPLY_ANOMALY", "fresh_peer_and_local_voltage_elevated"
            self._schedule_verification(tick)
            self._supply_diagnosed_active = True
            self._local_voltage_investigated_active = True
        elif self.cycle_count >= 2 and self.cycle_count > self._recurrence_reported_cycle_count:
            decision, rationale = "INVESTIGATE", "two_completed_observable_overtemperature_cycles"
            self._schedule_verification(tick)
            self._recurrence_reported_cycle_count = self.cycle_count
        elif elevated_local_voltage and not self._local_voltage_investigated_active:
            decision, rationale = "INVESTIGATE", "local_voltage_elevated_without_peer_root_cause_proof"
            self._schedule_verification(tick)
            self._local_voltage_investigated_active = True
        target = {"schema_version": self.TARGET_SCHEMA_VERSION,
                  "policy_version": self.VERSION, "decision": decision,
                  "diagnosis": diagnosis, "rationale_code": rationale,
                  "operational_commands": []}
        trace = self._trace(target, tick=tick, simulation_tick=simulation_tick,
                            temperatures=available_temps, voltage=voltage,
                            peer=peer, peer_voltage=peer_voltage)
        self._history.append({"tick": tick, "simulation_tick": simulation_tick,
                              "observation": obs, "target": deepcopy(target)})
        self._last_simulation_tick = simulation_tick
        self._last_decision = decision
        return target, trace

    def _schedule_verification(self, tick: int):
        if self._next_verify_tick is None:
            self._next_verify_tick = tick + self.verification_interval_ticks
            self._verification_origin_tick = tick

    def _trace(self, target: dict, *, tick: int, simulation_tick: int,
               temperatures: list[float], voltage, peer: dict | None, peer_voltage) -> dict:
        """Training metadata. It deliberately contains no scenario/evaluator state."""
        fields = ["incubator_1_direct_observation.telemetry.temperatures_c",
                  "incubator_1_direct_observation.telemetry.ac_voltage_v"]
        evidence = {"temperatures_c": list(temperatures), "ac_voltage_v": voltage}
        history_ticks: list[int] = []
        uncertainty = None
        rationale = target["rationale_code"]
        if rationale == "fresh_peer_and_local_voltage_elevated":
            fields.append("scripted_peer_reports_to_jarvis.incubator-2.summary.ac_voltage_v")
            evidence["peer_incubator_2_ac_voltage_v"] = peer_voltage
            evidence["peer_sample_tick"] = (peer or {}).get("sample_tick")
        elif rationale == "two_completed_observable_overtemperature_cycles":
            history_ticks = self._completed_cycle_ticks[-2:]
            evidence["completed_cycle_count"] = self.cycle_count
        elif rationale == "scheduled_from_prior_public_decision":
            history_ticks = ([] if self._verification_origin_tick is None else [self._verification_origin_tick])
            evidence["verification_interval_ticks"] = self.verification_interval_ticks
        elif rationale == "local_voltage_elevated_without_peer_root_cause_proof":
            fields.append("scripted_peer_reports_to_jarvis.incubator-2")
            if peer is None:
                uncertainty = "peer_report_missing"
            else:
                age = tick - int(peer.get("sample_tick", tick))
                uncertainty = "peer_report_stale" if age > 2 else "peer_not_elevated"
        elif len(temperatures) < 2:
            uncertainty = "insufficient_temperature_measurements"
        return {"schema_version": 1, "policy_version": self.VERSION,
                "local_tick": tick, "simulation_tick": simulation_tick,
                "rule_id": rationale, "decision": target["decision"],
                "diagnosis": target["diagnosis"], "consulted_fields": fields,
                "history_tick_indices": history_ticks, "evidence": evidence,
                "uncertainty": uncertainty}
