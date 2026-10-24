"""Label policy v1: what each node *should* conclude, tick by tick.

- **What is happening** comes from plant truth and the evaluator-only fault log.
- **When a node could know** comes from twin-run evidence onset (visibility.py).
  Before that moment the correct label is NORMAL: nothing is taught that the
  node's own inputs could not support.
- **What to do about it** follows the owner's rules:
  - a fault the machine handled -> HANDLED_BY_MACHINE (remember, do not intervene);
  - a fault that needs a person -> ESCALATE_TO_USER;
  - developing or unconfirmed evidence -> WATCHING;
  - a handled problem not recovered after 30 min, the 3rd repeat, or the same hazard
    on two nodes -> ESCALATE_TO_USER;
  - harm imminent -> DANGER, with the approved protective action for the node's own
    machine and the agreed calls (fire brigade by the incident lead; civil protection
    after the owner has had 5 minutes).

The rule brain is never consulted: labels can disagree with it, which is what
lets a trained CTM beat it (earlier detection, fewer false alarms).
"""
from __future__ import annotations

import numpy as np

from ..nodes.contracts import ASSESSMENTS, COMMUNICATIONS, DANGER_ACTIONS, DIAGNOSES, MACHINE_TYPES, NODE_IDS, SOURCES
from .visibility import group_visibility

RANK = {name: n for n, name in enumerate(ASSESSMENTS)}
KIND_PRIORITY = {"fire": 7, "flood": 6, "storm": 5, "gas": 4, "power": 3, "water": 2.5, "equipment": 2, "environment": 1,
                 "network": 0}
UNRECOVERED_S = 1800.0
HOLD_TICKS = {"WATCHING": 60, "ESCALATE_TO_USER": 120, "DANGER": 300}
CONFIRM_SILENT_S = 300.0
CONFIRM_REPAIR_S = 120.0

# injected fault -> (diagnosis, response, device codes that make it unambiguous)
FAULT_POLICY = {
    "fan1_failure": ("FAN_FAULT", "repair", ("FAN1_FAILURE",)),
    "fan2_failure": ("FAN_FAULT", "repair", ("FAN2_FAILURE",)),
    "heater_open_circuit": ("HEATER_FAULT", "repair", ("HEATER_OPEN_CIRCUIT",)),
    "heater_relay_stuck": ("HEATER_FAULT", "repair", ("HEATER_RELAY_STUCK",)),
    "turning_motor_failure": ("TURNING_FAULT", "repair", ("TURNING_FAULT",)),
    "incubator_water_leak": ("WATER_LEAK", "repair", ("WATER_LEAK",)),
    "humidifier_pump_failure": ("HUMIDIFICATION_FAULT", "repair", ("PUMP_FAULT",)),
    "humidifier_element_failure": ("HUMIDIFICATION_FAULT", "repair", ("HUMIDIFIER_FAULT",)),
    "door_switch_open_circuit": ("DOOR_PROBLEM", "repair", ("DOOR_SWITCH_FAULT",)),
    "door_open_undetected": ("DOOR_PROBLEM", "silent", ()),
    "dc_rail2_sag": ("INTERNAL_POWER_FAULT", "repair", ("INTERNAL_POWER_FAULT",)),
    "dc_rail3_failure": ("INTERNAL_POWER_FAULT", "repair", ("INTERNAL_POWER_FAULT",)),
    "controller_power_failure": ("INTERNAL_POWER_FAULT", "silent_device", ()),
    "high_limit_failed": (None, "invisible", ()),
    "supply_fan_failure": ("SUPPLY_FAN_FAULT", "repair", ("SUPPLY_FAN_FAILURE",)),
    "return_fan_failure": ("RETURN_FAN_FAULT", "repair", ("RETURN_FAN_FAILURE",)),
    "compressor_failure": ("COMPRESSOR_FAULT", "repair", ("COMPRESSOR_FAILURE",)),
    "damper_stuck": ("UNKNOWN_ANOMALY", "silent", ()),
    "condensate_drain_blocked": ("CONDENSATE_PROBLEM", "handled", ("CONDENSATE_OVERFLOW",)),
    "filter_clogging": ("FILTER_CLOGGED", "repair", ("FILTER_MAINTENANCE",)),
    "refrigerant_leak": ("REFRIGERANT_LEAK", "silent", ("LOW_REFRIGERANT_PRESSURE",)),
    "feeder_blocked": ("FEED_PROBLEM", "repair", ("FEEDER_BLOCKED",)),
    "drinker_pump_failure": ("WATER_PROBLEM", "repair", ("WATER_PUMP_FAULT",)),
    "drinker_line_leak": ("WATER_PROBLEM", "repair", ("WATER_LEAK",)),
    "tank_fill_valve_failure": ("WATER_PROBLEM", "repair", ("TANK_FILL_FAULT",)),
    "incubator_fill_valve_failure": ("WATER_INLET_FAULT", "repair", ("LOW_WATER",)),
    "site_water_cut": ("WATER_SUPPLY_LOSS", "farm_water", ("WATER_SUPPLY_LOST",)),
    "device_supply_outage": ("POWER_OUTAGE", "outage", ()),
    "site_power_outage": ("POWER_OUTAGE", "outage", ()),
    "sensor_stuck": ("SENSOR_FAULT", "silent", ()),
    "sensor_bias": ("SENSOR_FAULT", "silent", ()),
    "sensor_drift": ("SENSOR_FAULT", "silent", ()),
    "sensor_dropout": ("SENSOR_FAULT", "dropout", ()),
    "supply_overvoltage": ("SUPPLY_OVERVOLTAGE", "protection", ("OVERVOLTAGE", "OVERVOLTAGE_PROTECTION")),
}
_KIND = {"POWER_OUTAGE": "power", "SUPPLY_OVERVOLTAGE": "power", "WATER_SUPPLY_LOSS": "water"}
REASONS = ("normal", "barn_purging", "barrier_deployed", "barrier_failed_to_deploy", "battery_low", "device_code",
           "device_quiet", "device_silent", "early_evidence", "early_fire_evidence", "extreme_wind_on_site",
           "fire_out_or_responders_on_scene", "first_hand_smoke_or_co", "flood_warning", "floodwater_rising",
           "handled_not_recovered_30min", "machine_protected_itself", "no_flood_barrier_installed",
           "overheating_despite_cutoffs", "oxygen_low", "peer_reports_extreme_wind", "peer_reports_fire_evidence",
           "peer_reports_flood", "peer_reports_tornado_warning", "peer_silent", "persistent_evidence",
           "recovering_after_repair", "reservoir_low", "running_on_battery", "sensor_destroyed_by_fire", "sensor_missing",
           "severe_weather_warning", "shutters_failed_to_deploy", "smoke_detector_failed", "spread_to_second_node",
           "third_repeat", "tornado_on_site", "tornado_warning", "two_strong_independent_fire_signs",
           "water_entering_barn", "weather_watch_or_change", "farm_water_supply_lost", "peer_reports_water_supply_lost", "linked_to_incubator", "linked_to_hvac",
           "linked_to_animal_protection", "linked_to_outside", "own_change_cause_unknown")
_REASON_INDEX = {text: n for n, text in enumerate(REASONS)}


def _reason(text):
    return _REASON_INDEX[text]  # a new reason must be added to REASONS (indices are stored in the dataset)


class _Node:
    """Access to one node's recorded inputs (normalised values as the CTM saw them)."""

    def __init__(self, node_id, record, twin):
        self.node_id, self.machine = node_id, MACHINE_TYPES[node_id]
        self.names = record["feature_names"][node_id]
        self.index = {name: i for i, name in enumerate(self.names)}
        self.values = record["values"][node_id].astype(np.float32)
        self.valid = record["validity"][node_id].astype(bool)
        self.twin_values = twin["values"][node_id].astype(np.float32) if twin is not None else self.values
        self.twin_valid = twin["validity"][node_id].astype(bool) if twin is not None else self.valid
        if twin is not None:
            self.visible, self.onset, _ = group_visibility(
                node_id, self.names, record["feature_kinds"][node_id], record["values"][node_id],
                record["validity"][node_id], twin["values"][node_id], twin["validity"][node_id])
        else:
            zeros = np.zeros(self.values.shape[0], dtype=bool)
            self.visible = {g: zeros for g in ("own", "heat_own", "smoke_own", "flood_own", "storm_own", "water_own", "link")}
            self.onset = {g: None for g in self.visible}

    def peer_label(self, peer):
        return "other_incubator" if self.machine == "incubator" and MACHINE_TYPES[peer] == "incubator" else peer

    def peer_view(self, nodes, peer, own_group):
        """What ``peer`` had visibly seen in ``own_group`` as of the message this node holds from it."""
        sample = self.peer_sample_ticks(self.peer_label(peer))
        peer_visible = nodes[peer].visible[own_group]
        held = (sample >= 0) & (sample < len(peer_visible))
        return np.where(held, peer_visible[np.clip(sample, 0, len(peer_visible) - 1)], False)

    def peer_sample_ticks(self, peer_label, step=1.0):
        """Per tick: the sample tick of the latest message this node holds from that peer (-1: none)."""
        i = self.index.get(f"peer.{peer_label}.age_s")
        ticks = np.arange(self.values.shape[0])
        if i is None:
            return np.full(ticks.shape, -1)
        age = self.values[:, i].astype(np.float64) * 300.0
        sample = np.floor(ticks - age / step + 0.5).astype(int)
        return np.where(self.valid[:, i], sample, -1)

    def combine(self, nodes):
        """Evidence families as this node can know them: its own, plus what peers had seen when they last reported."""
        sources = {"heat": ("heat_own", "hvac"), "smoke": ("smoke_own", "animal-protection"),
                   "flood": ("flood_own", "animal-protection"), "storm": ("storm_own", "animal-protection")}
        for group, (own_group, peer) in sources.items():
            combined = self.visible[own_group].copy()
            if peer != self.node_id:
                combined |= self.peer_view(nodes, peer, own_group)
            self.visible[group] = combined
            self.onset[group] = int(np.argmax(combined)) if combined.any() else None
        # Water: the main's pressure is measured by Animal Protection; each incubator only sees its own hose.
        zeros = np.zeros(self.values.shape[0], dtype=bool)
        others = [p for p in ("incubator-1", "incubator-2") if p != self.node_id]
        self.visible["water_apu"] = zeros if self.node_id == "animal-protection" else \
            self.peer_view(nodes, "animal-protection", "water_own")
        self.visible["water_other"] = self.peer_view(nodes, others[0], "water_own") \
            if self.machine == "incubator" else zeros
        self.visible["water"] = self.visible["water_own"] | self.visible["water_apu"] | self.visible["water_other"]
        # What each peer had visibly noticed about its own machine, as of the message this node holds.
        for peer in NODE_IDS:
            if peer != self.node_id:
                self.visible[f"own@{peer}"] = self.peer_view(nodes, peer, "own")
        for group in ("water_apu", "water_other", "water"):
            self.onset[group] = int(np.argmax(self.visible[group])) if self.visible[group].any() else None

    def hold_evidence(self):
        """Evidence stays in mind for a minute after it was last seen, so labels do not flap.
        Runs after every node has combined, so peers are always read before any holding."""
        for group in list(self.visible):
            self.visible[group] = _hold(self.visible[group], EVIDENCE_HOLD_TICKS)

    def has(self, name):
        return name in self.index

    def flag(self, name, t):
        i = self.index.get(name)
        return i is not None and self.valid[t, i] and self.values[t, i] >= 0.5

    def value(self, name, t, low=0.0, high=1.0):
        i = self.index.get(name)
        if i is None or not self.valid[t, i]:
            return None
        return low + float(self.values[t, i]) * (high - low)

    def code(self, code, t):
        return self.flag(f"own.code.{code}", t)

    def vis(self, group, t):
        return bool(self.visible[group][t])

    def rise(self, name, low, high, window=10):
        """Per tick: how much higher this reading is than in the twin (10-tick means, raw units)."""
        i = self.index.get(name)
        if i is None:
            return np.zeros(self.values.shape[0], dtype=np.float32)
        main = np.where(self.valid[:, i], self.values[:, i], np.nan)
        twin = np.where(self.twin_valid[:, i], self.twin_values[:, i], np.nan)
        kernel = np.ones(window) / window
        def smooth(x):
            filled = np.nan_to_num(x, nan=0.0)
            counts = np.convolve(~np.isnan(x), kernel, mode="full")[: len(x)]
            return np.convolve(filled, kernel, mode="full")[: len(x)] / np.maximum(counts, 1e-9)
        return (smooth(main) - smooth(twin)) * (high - low)

    def lost(self, name):
        """Per tick: reading missing here but present in the twin."""
        i = self.index.get(name)
        if i is None:
            return np.zeros(self.values.shape[0], dtype=bool)
        return (~self.valid[:, i]) & self.twin_valid[:, i]


def _first_true_since(array, start):
    hits = np.nonzero(array[start:])[0]
    return int(start + hits[0]) if hits.size else None


def _fault_intervals(truth, node_id):
    """{fault label: [(start, end_or_None), ...]} for one device."""
    intervals, open_at = {}, {}
    for t, row in enumerate(truth):
        current = set(row["faults"][node_id])
        for label in current - set(open_at):
            open_at[label] = t
        for label in set(open_at) - current:
            intervals.setdefault(label, []).append((open_at.pop(label), t))
    for label, start in open_at.items():
        intervals.setdefault(label, []).append((start, None))
    return intervals


SITE_FAULTS = {"site_power_outage", "site_water_cut", "supply_overvoltage"}   # the cause is the site, not this machine
FIRE_ZONE_MACHINE = {"storage": "hvac", "workspace": "hvac", "barn": "animal_protection"}


def _norm(candidate, source):
    """Every candidate as (assessment, diagnosis, kind, reason, action, source)."""
    action = candidate[4] if len(candidate) > 4 else "none"
    return tuple(candidate[:4]) + (action, candidate[5] if len(candidate) > 5 else source)


def _role(n: _Node, machine: str) -> str:
    return "self" if machine == n.machine else machine


def _equipment_candidates(n: _Node, truth, t, intervals, shared_overvoltage, repeat_index):
    out = []
    for label, spans in intervals.items():
        before = len(out)
        _equipment_label(n, t, label, spans, shared_overvoltage, repeat_index, out)
        out[before:] = [_norm(c, "site" if label in SITE_FAULTS else "self") for c in out[before:]]
    return out


def _equipment_label(n: _Node, t, label, spans, shared_overvoltage, repeat_index, out):
        policy = FAULT_POLICY.get(label)
        if policy is None or policy[0] is None:
            return
        diagnosis, response, codes = policy
        kind = _KIND.get(diagnosis, "equipment")
        for occurrence, (start, end) in enumerate(spans):
            if t < start or (end is not None and t >= end + 300):
                continue
            onset = _first_true_since(n.visible["own"], start)
            coded = any(n.code(c, t) for c in codes)
            if end is not None and t >= end:
                if n.vis("own", t) and onset is not None and onset <= t:
                    out.append(("HANDLED_BY_MACHINE", diagnosis, kind, "recovering_after_repair"))
                continue
            if response == "silent_device":
                age = n.value("observation_age_s", t, 0.0, 300.0) or 0.0
                if age >= 30.0:
                    out.append(("ESCALATE_TO_USER", diagnosis, kind, "device_silent"))
                elif age >= 5.0:
                    out.append(("WATCHING", diagnosis, kind, "device_quiet"))
                continue
            if response == "outage":
                out.extend(_outage(n, t, start, diagnosis))
                continue
            if response == "farm_water":
                out.extend(_farm_water(n, t, coded))
                continue
            if onset is None or t < onset:
                continue
            seen = t - onset
            if response == "repair":
                out.append(("ESCALATE_TO_USER" if coded or seen >= CONFIRM_REPAIR_S else "WATCHING", diagnosis, kind,
                            "device_code" if coded else ("persistent_evidence" if seen >= CONFIRM_REPAIR_S else "early_evidence")))
            elif response == "silent":
                if coded:
                    out.append(("HANDLED_BY_MACHINE", diagnosis, kind, "machine_protected_itself"))
                    if t - start >= UNRECOVERED_S:
                        out.append(("ESCALATE_TO_USER", diagnosis, kind, "handled_not_recovered_30min"))
                out.append(("ESCALATE_TO_USER" if seen >= CONFIRM_SILENT_S else "WATCHING", diagnosis, kind,
                            "persistent_evidence" if seen >= CONFIRM_SILENT_S else "early_evidence"))
            elif response == "dropout":
                out.append(("ESCALATE_TO_USER" if seen >= 60 else "WATCHING", diagnosis, kind, "sensor_missing"))
            elif response == "handled":
                if coded:
                    out.append(("ESCALATE_TO_USER" if t - start >= UNRECOVERED_S else "HANDLED_BY_MACHINE", diagnosis, kind,
                                "handled_not_recovered_30min" if t - start >= UNRECOVERED_S else "machine_protected_itself"))
                else:
                    out.append(("WATCHING", diagnosis, kind, "early_evidence"))
            elif response == "protection":
                if coded or n.vis("own", t):
                    escalate = shared_overvoltage or repeat_index.get((label, start), 0) >= 2
                    out.append(("ESCALATE_TO_USER" if escalate else "HANDLED_BY_MACHINE", diagnosis, kind,
                                "spread_to_second_node" if shared_overvoltage else
                                ("third_repeat" if escalate else "machine_protected_itself")))


def _active_fault(intervals, t, after=300):
    """Does this device have an injected fault right now (or recovering from one)?"""
    return any(start <= t and (end is None or t < end + after) for spans in intervals.values() for start, end in spans)


def _world_context(truth):
    """Per tick, computed once: was there a fire in the last 10 minutes (and where), and is the weather acting?"""
    fire = np.array([row["fire"] for row in truth], dtype=bool)
    recent = _hold(fire, 600)
    zones = [row["fire_zone"] for row in truth]
    weather = np.array([row["storm"] or row["flood_cm"] > 0.5 for row in truth], dtype=bool)
    return {"fire_recent": recent, "fire_zone": zones, "weather": weather}


def _external_cause(nodes, n: _Node, t, world, intervals):
    """The cause of a change this node sees but did not cause itself: (role, node id or None), or None."""
    if world["fire_recent"][t]:
        machine = FIRE_ZONE_MACHINE.get(world["fire_zone"][t], "hvac")
        return _role(n, machine), next((m for m in NODE_IDS if MACHINE_TYPES[m] == machine), None)
    if world["weather"][t]:
        return "outside", None
    for other in ("hvac", "animal-protection", "incubator-1", "incubator-2"):
        if other != n.node_id and _active_fault(intervals[other], t):
            return MACHINE_TYPES[other], other
    return None


def _linked_candidates(nodes, n: _Node, t, world, intervals):
    """My own readings moved, nothing is wrong with me: say so, then name the machine once its evidence reaches me."""
    if not n.vis("own", t) or _active_fault(intervals[n.node_id], t):
        return []
    cause = _external_cause(nodes, n, t, world, intervals)
    if cause is None:
        return []
    role, source_node = cause
    if source_node is None:
        known = n.vis("storm", t) or n.vis("flood", t)
    else:
        known = role == "self" or n.vis(f"own@{source_node}", t)
    if known and role != "self":
        return [("WATCHING", "EXTERNAL_HAZARD", "environment", f"linked_to_{role}", "none", role)]
    return [("WATCHING", "UNKNOWN_ANOMALY", "environment", "own_change_cause_unknown", "none", "none")]


def _outage(n: _Node, t, start, diagnosis):
    if n.machine == "hvac":  # no battery: an outage shows as the device going quiet
        age = n.value("observation_age_s", t, 0.0, 300.0) or 0.0
        if age < 5.0:
            return []
        return [("ESCALATE_TO_USER" if age >= 30 else "WATCHING", diagnosis, "power", "device_silent" if age >= 30 else "device_quiet")]
    on_battery = n.flag("own.power_source=battery", t) if n.machine == "incubator" else not n.flag("own.mains_normal", t)
    silent = (n.value("observation_age_s", t, 0.0, 300.0) or 0.0) >= 30
    battery = n.value("own.battery_pct", t, 0.0, 100.0)
    if silent:
        return [("ESCALATE_TO_USER", diagnosis, "power", "device_silent")]
    if not on_battery:
        return []
    if (battery is not None and battery < 20.0) or t - start >= UNRECOVERED_S:
        return [("ESCALATE_TO_USER", diagnosis, "power", "battery_low" if battery is not None and battery < 20 else "handled_not_recovered_30min")]
    return [("HANDLED_BY_MACHINE", diagnosis, "power", "running_on_battery")]


def _farm_water(n: _Node, t, coded):
    """The farm main is down. Animal Protection measures it and tells the owner; an incubator only
    knows it is farm-wide once Animal Protection (or the other incubator) shows the same."""
    if n.machine == "animal_protection":
        if coded:
            return [("ESCALATE_TO_USER", "WATER_SUPPLY_LOSS", "water", "farm_water_supply_lost")]
        return [("WATCHING", "WATER_SUPPLY_LOSS", "water", "early_evidence")] if n.vis("water_own", t) else []
    if n.machine != "incubator":
        return []
    farm_wide = n.vis("water_apu", t) or (n.vis("water_own", t) and n.vis("water_other", t))
    if farm_wide:
        if n.code("LOW_WATER", t):
            return [("ESCALATE_TO_USER", "WATER_SUPPLY_LOSS", "water", "reservoir_low")]
        return [("WATCHING", "WATER_SUPPLY_LOSS", "water", "peer_reports_water_supply_lost")]
    if n.vis("water_own", t):  # its own hose looks dry, scope not yet known
        return [("WATCHING", "WATER_INLET_FAULT", "equipment", "early_evidence")]
    return []


def _incubator_danger(n: _Node, t):
    temps = [n.value(f"own.room_temperature_{k}", t, 20.0, 45.0) for k in (1, 2, 3)]
    temps = [v for v in temps if v is not None]
    outlet = n.value("own.heater_outlet", t, 20.0, 90.0)
    power = n.value("own.heater_power_w", t, 0.0, 400.0) or 0.0
    hot = (max(temps) if temps else 0.0) >= 40.0 or (outlet or 0.0) >= 70.0
    stuck = n.code("HEATER_RELAY_STUCK", t) or n.flag("own.overtemperature_trip", t)
    if hot and power > 5.0 and stuck:
        action = "none" if n.flag("own.emergency_stop", t) else "emergency_stop_heater"
        return [("DANGER", "OVERHEATING", "equipment", "overheating_despite_cutoffs", action)]
    return []


EVIDENCE_HOLD_TICKS = 60
VISIBLE_GROUPS = ("own", "heat", "smoke", "flood", "storm", "water", "link")  # columns of the stored ``visible`` array


def _hold(mask, ticks):
    """True for ``ticks`` steps after any True (onset unchanged)."""
    if not mask.any():
        return mask
    last = np.where(mask, np.arange(mask.size), -10**9)
    return (np.arange(mask.size) - np.maximum.accumulate(last)) < ticks


STRONG_TEMPERATURE_K = 2.0
STRONG_OBSCURATION = 2.0
STRONG_CO_PPM = 10.0


def _strong_fire_families(n: _Node):
    """Per tick, which independent fire signs this node sees strongly (vs. the twin)."""
    room = np.zeros(n.values.shape[0], dtype=bool)
    duct = np.zeros_like(room)
    smoke = np.zeros_like(room)
    if n.machine == "hvac":
        for zone in ("barn", "storage", "workspace"):
            room |= n.rise(f"own.{zone}_temperature", 0.0, 60.0) >= STRONG_TEMPERATURE_K
            room |= n.lost(f"own.{zone}_temperature")
        duct |= (n.rise("own.supply_temperature", 0.0, 60.0) >= STRONG_TEMPERATURE_K) |                 (n.rise("own.return_temperature", 0.0, 60.0) >= STRONG_TEMPERATURE_K)
    else:
        if n.machine == "animal_protection":
            room |= n.rise("own.barn_temperature", 0.0, 60.0) >= STRONG_TEMPERATURE_K
        room |= n.rise("peer.hvac.zone_sensors_lost", 0.0, 3.0) >= 0.5
        duct |= (n.rise("peer.hvac.supply_temperature_c", 0.0, 60.0) >= STRONG_TEMPERATURE_K) |                 (n.rise("peer.hvac.return_temperature_c", 0.0, 60.0) >= STRONG_TEMPERATURE_K)
    if n.machine == "animal_protection":
        smoke |= (n.rise("own.smoke_obscuration", 0.0, 40.0) >= STRONG_OBSCURATION) |                  (n.rise("own.co_ppm", 0.0, 200.0) >= STRONG_CO_PPM) | (n.rise("own.smoke_detected", 0.0, 1.0) >= 0.5)
    else:
        smoke |= (n.rise("peer.animal-protection.smoke_obscuration_pct_m", 0.0, 40.0) >= STRONG_OBSCURATION) |                  (n.rise("peer.animal-protection.co_ppm", 0.0, 200.0) >= STRONG_CO_PPM) |                  (n.rise("peer.animal-protection.smoke_detected", 0.0, 1.0) >= 0.5)
    return room.astype(int) + duct.astype(int) + smoke.astype(int)


def _fire_candidates(nodes, n: _Node, truth, t, fire_start, confirmed_at, ended_at):
    row = truth[t]
    if fire_start is None or t < fire_start:
        return []
    active = row["fire"] and not row["brigade_on_scene"]
    if n.machine == "incubator":
        if (active or (ended_at is not None and t < ended_at + 600)) and (n.vis("heat", t) or n.vis("smoke", t)):
            return [("WATCHING", "EXTERNAL_HAZARD", "environment", "peer_reports_fire_evidence")]
        return []
    out = []
    if active and confirmed_at is not None and t >= confirmed_at:
        if n.machine == "hvac":
            action = "none" if n.flag("own.mode=fire_shutdown", t) else "fire_shutdown"
        else:
            action = "none" if n.flag("own.alarm_system_active", t) else "activate_alarm"
        out.append(("DANGER", "FIRE_SUSPECTED", "fire", "two_strong_independent_fire_signs", action))
    elif active and (n.vis("heat", t) or n.vis("smoke", t)):
        first_hand = n.machine == "animal_protection" and n.vis("smoke_own", t)
        out.append(("ESCALATE_TO_USER" if first_hand else "WATCHING", "FIRE_SUSPECTED", "fire",
                    "first_hand_smoke_or_co" if first_hand else "early_fire_evidence"))
    elif not active and ended_at is not None and t < ended_at + 600 and (n.vis("heat", t) or n.vis("smoke", t)):
        out.append(("HANDLED_BY_MACHINE", "FIRE_SUSPECTED", "fire", "fire_out_or_responders_on_scene"))
    prefix = "hvac." if n.machine == "hvac" else "animal-protection."
    if not active and any(sensor.startswith(prefix) for sensor in row["failed_sensors"]):
        out.append(("ESCALATE_TO_USER", "SENSOR_FAULT", "equipment", "sensor_destroyed_by_fire"))
    return out


FLOOD_RELEASE_S = 1800  # as the Animal Protection controller: flood mode ends 30 min after the water has gone


def _flood_mode(apu: _Node):
    """Per tick: flood mode as the controller latches it from the same readings, and when it began (-1: off)."""
    ticks = apu.values.shape[0]
    since = np.full(ticks, -1)
    start = clear_since = None
    for t in range(ticks):
        level = apu.value("own.floodwater_cm", t, 0.0, 60.0) or 0.0
        if apu.flag("own.flood_warning", t) or level >= 5.0 or apu.flag("own.water_intrusion", t):
            start = t if start is None else start
            clear_since = None
        elif start is not None:
            if level >= 2.0:
                clear_since = None
            elif clear_since is None:
                clear_since = t
            elif t - clear_since >= FLOOD_RELEASE_S:
                start = clear_since = None
        since[t] = -1 if start is None else start
    return since


def _weather_candidates(n: _Node, truth, t, flood_ever, flood_since):
    row = truth[t]
    out = []
    if n.machine != "animal_protection":
        if flood_ever and n.vis("flood", t):
            out.append(("WATCHING", "EXTERNAL_HAZARD", "environment", "peer_reports_flood"))
        if n.flag("peer.animal-protection.tornado_warning", t):
            out.append(("WATCHING", "EXTERNAL_HAZARD", "environment", "peer_reports_tornado_warning"))
        if (n.value("peer.animal-protection.wind_speed_m_s", t, 0.0, 50.0) or 0.0) >= 33.0:
            out.append(("WATCHING", "EXTERNAL_HAZARD", "environment", "peer_reports_extreme_wind"))
        return out
    warning = n.flag("own.flood_warning", t)
    if n.flag("own.water_intrusion", t) and row["floor_water_cm"] > 0.0:
        out.append(("DANGER", "FLOOD", "flood", "water_entering_barn", "none"))
    elif flood_since[t] >= 0:
        installed = n.has("own.flood_barrier=ready") and n.valid[t, n.index["own.flood_barrier=ready"]]
        deployed = n.flag("own.flood_barrier=deployed", t) or n.flag("own.flood_barrier=deploying", t)
        waited = t - flood_since[t]
        if not installed:
            out.append(("ESCALATE_TO_USER", "FLOOD", "flood", "no_flood_barrier_installed"))
        elif not deployed and waited >= 180:
            out.append(("DANGER", "FLOOD", "flood", "barrier_failed_to_deploy", "deploy_flood_barrier"))
        elif warning:
            out.append(("ESCALATE_TO_USER", "FLOOD", "flood", "flood_warning"))
        elif waited >= UNRECOVERED_S:
            out.append(("ESCALATE_TO_USER", "FLOOD", "flood", "handled_not_recovered_30min"))
        elif deployed:
            out.append(("HANDLED_BY_MACHINE", "FLOOD", "flood", "barrier_deployed"))
        else:
            out.append(("WATCHING", "FLOOD", "flood", "floodwater_rising"))
    elif flood_ever and n.vis("flood", t):
        out.append(("WATCHING", "FLOOD", "flood", "floodwater_rising"))
    tornado = n.flag("own.tornado_warning", t)
    wind = n.value("own.wind_m_s", t, 0.0, 50.0) or 0.0
    if wind >= 33.0:
        # Hurricane-force wind on site is dangerous whatever the feed says; the controller only
        # closes the shutters on the feed, so Jarvis does it.
        open_shutters = n.flag("own.shutters=standby", t) or n.flag("own.shutters=retracting", t)
        out.append(("DANGER", "SEVERE_WEATHER", "storm", "tornado_on_site" if tornado else "extreme_wind_on_site",
                    "deploy_shutters" if open_shutters else "none"))
    elif tornado:
        shutters = n.flag("own.shutters=deployed", t) or n.flag("own.shutters=deploying", t)
        standby = n.flag("own.shutters=standby", t)
        if standby and not shutters and t >= 120 and all(n.flag("own.tornado_warning", k) for k in range(max(0, t - 120), t)):
            out.append(("DANGER", "SEVERE_WEATHER", "storm", "shutters_failed_to_deploy", "deploy_shutters"))
        else:
            out.append(("ESCALATE_TO_USER", "SEVERE_WEATHER", "storm", "tornado_warning"))
    elif n.flag("own.severe_weather_warning", t):
        out.append(("ESCALATE_TO_USER", "SEVERE_WEATHER", "storm", "severe_weather_warning"))
    elif (n.flag("own.watch_active", t) or n.flag("own.risk=watch", t) or n.vis("storm", t)) and not out:
        # Generic weather change; a specific flood conclusion, when there is one, says more.
        out.append(("WATCHING", "SEVERE_WEATHER", "storm", "weather_watch_or_change"))
    return out


def _own_state_candidates(n: _Node, t, held_since):
    """Conditions the node's own data shows directly, whatever caused them."""
    out = []
    if n.machine == "animal_protection":
        if n.code("HEAT_STRESS", t):
            long = held_since.get("HEAT_STRESS") is not None and t - held_since["HEAT_STRESS"] >= UNRECOVERED_S
            out.append(("ESCALATE_TO_USER" if long else "HANDLED_BY_MACHINE", "HEAT_STRESS", "environment",
                        "handled_not_recovered_30min" if long else "machine_protected_itself"))
        if n.code("CO2_HIGH", t) or n.code("AMMONIA_HIGH", t):
            out.append(("HANDLED_BY_MACHINE", "GAS_HAZARD", "gas", "barn_purging"))
        if n.code("O2_LOW", t):
            out.append(("DANGER", "GAS_HAZARD", "gas", "oxygen_low", "none"))
        if n.code("SMOKE_DETECTOR_FAILED", t):
            out.append(("ESCALATE_TO_USER", "SENSOR_FAULT", "equipment", "smoke_detector_failed"))
    if n.machine == "incubator" and n.code("LOW_WATER", t):  # the hose-fed reservoir is not being refilled
        out.append(("ESCALATE_TO_USER", "WATER_INLET_FAULT", "equipment", "reservoir_low"))
    # Link silence, not data age: a peer whose machine is down still sends messages.
    gaps = [n.value(f"peer.{slot}.silent_s", t, 0.0, 300.0) for slot in _peer_labels(n)]
    silent = [g for g in gaps if g is not None and g >= 60.0] + \
             [1e9 for slot in _peer_labels(n) if not n.flag(f"peer.{slot}.present", t) and t >= 60]
    if silent:
        slots = _peer_labels(n)
        worst = max(range(len(slots)), key=lambda k: 1e9 if not n.flag(f"peer.{slots[k]}.present", t) else (gaps[k] or 0.0))
        quiet = slots[worst]
        role = "incubator" if "incubator" in quiet else ("hvac" if quiet == "hvac" else "animal_protection")
        out.append(("ESCALATE_TO_USER" if max(silent) >= 600 else "WATCHING", "COMMUNICATION_LOSS", "network",
                    "peer_silent", "none", role))
    return out


def _peer_labels(n: _Node):
    return sorted({name.split(".")[1] for name in n.names if name.startswith("peer.") and name.endswith(".present")})


def _communication(n: _Node, t, assessment, kind, lead, row):
    if assessment == "DANGER":
        if kind == "fire" and lead and not n.flag("memory.fire_brigade_called", t) and not row["brigade_on_scene"]:
            return "call_fire_brigade"
        wind = n.value("own.wind_m_s", t, 0.0, 50.0) or 0.0
        emergency = (kind == "flood" and row["floor_water_cm"] > 0.0) or (kind == "storm" and wind >= 33.0)
        minutes = n.value("memory.minutes_since_owner_notified", t, 0.0, 60.0) or 0.0
        if emergency and minutes >= 5.0 and not n.flag("memory.owner_acknowledged", t) \
                and not n.flag("memory.civil_protection_called", t) and n.node_id == "animal-protection":
            return "call_civil_protection"
        priority = n.value("memory.owner_notified_priority", t, 0.0, 4.0) or 0.0
        return "notify_owner" if priority < 3.5 else "none"
    if assessment == "ESCALATE_TO_USER":
        priority = n.value("memory.owner_notified_priority", t, 0.0, 4.0) or 0.0
        return "notify_owner" if priority < 2.5 else "none"
    return "none"


def label_episode(record, twin):
    """Per node: assessment/diagnosis/action/communication indices and a reason code per tick."""
    truth = record["truth"]
    ticks = len(truth)
    nodes = {node_id: _Node(node_id, record, twin) for node_id in NODE_IDS}
    for n in nodes.values():
        n.combine(nodes)
    for n in nodes.values():
        n.hold_evidence()
    fire_start = next((t for t, row in enumerate(truth) if row["fire"]), None)
    flood_ever = any(row["flood_cm"] > 0.5 for row in truth)
    # Fire is confirmed when HVAC or Animal Protection sees two strong independent signs at once.
    confirmed_at = ended_at = None
    if fire_start is not None:
        strong = {x: _strong_fire_families(nodes[x]) for x in ("hvac", "animal-protection")}
        confirmed_at = next((t for t in range(fire_start, ticks) if truth[t]["fire"] and
                             max(strong[x][t] for x in strong) >= 2), None)
        ended_at = next((t for t in range(fire_start, ticks) if not truth[t]["fire"] or truth[t]["brigade_on_scene"]), None)
    apu = nodes["animal-protection"]
    flood_since = _flood_mode(apu)
    shared = {}
    intervals = {node_id: _fault_intervals(truth, node_id) for node_id in NODE_IDS}
    over = {node_id: intervals[node_id].get("supply_overvoltage", []) for node_id in ("incubator-1", "incubator-2")}
    shared_overvoltage = bool(over["incubator-1"]) and bool(over["incubator-2"])
    repeats = {node_id: {("supply_overvoltage", start): k for k, (start, _) in enumerate(over.get(node_id, []))}
               for node_id in NODE_IDS}
    world = _world_context(truth)
    out = {}
    for node_id, n in nodes.items():
        machine = n.machine
        arrays = {key: np.zeros(ticks, dtype=np.int8) for key in ("assessment", "diagnosis", "action", "communication",
                                                                  "reason", "source")}
        held_since = {}
        held = None
        for t in range(ticks):
            if n.code("HEAT_STRESS", t):
                held_since.setdefault("HEAT_STRESS", t)
            else:
                held_since.pop("HEAT_STRESS", None)
            fire_source = _role(n, FIRE_ZONE_MACHINE.get(world["fire_zone"][t], "hvac"))
            candidates = _equipment_candidates(n, truth, t, intervals[node_id], shared_overvoltage and node_id in over,
                                               repeats[node_id])
            if machine == "incubator":
                candidates += [_norm(c, "self") for c in _incubator_danger(n, t)]
            candidates += [_norm(c, fire_source) for c in _fire_candidates(nodes, n, truth, t, fire_start, confirmed_at, ended_at)]
            candidates += [_norm(c, "outside") for c in _weather_candidates(n, truth, t, flood_ever, flood_since)]
            candidates += [_norm(c, "self") for c in _own_state_candidates(n, t, held_since)]
            candidates += [_norm(c, "none") for c in _linked_candidates(nodes, n, t, world, intervals)]
            if candidates:
                best = max(candidates, key=lambda c: (RANK[c[0]], KIND_PRIORITY.get(c[2], 0)))
            else:
                best = ("NORMAL", "NONE", "none", "normal", "none", "none")
            assessment, diagnosis, kind, reason, proposed, source = best
            # A conclusion is not dropped the moment its evidence flickers: hold it for a while,
            # but a held conclusion is not news: it acts and tells no one.
            holding = held is not None and t < held[4] and RANK[assessment] < RANK[held[0]]
            if holding:
                assessment, diagnosis, kind, reason, source = held[0], held[1], held[2], held[3], held[5]
                action = "none"
            else:
                action = proposed if assessment == "DANGER" else "none"
                if RANK[assessment] >= RANK["WATCHING"]:
                    held = (assessment, diagnosis, kind, reason, t + HOLD_TICKS[assessment], source)
            if assessment == "NORMAL":
                arrays["reason"][t] = _reason("normal")
                continue
            arrays["source"][t] = SOURCES.index(source)
            lead = (node_id == "animal-protection" and apu.vis("smoke_own", t)) or \
                   (node_id == "hvac" and not apu.vis("smoke_own", t))
            communication = "none" if holding else _communication(n, t, assessment, kind, lead, truth[t])
            arrays["assessment"][t] = ASSESSMENTS.index(assessment)
            arrays["diagnosis"][t] = DIAGNOSES[machine].index(diagnosis)
            arrays["action"][t] = DANGER_ACTIONS[machine].index(action)
            arrays["communication"][t] = COMMUNICATIONS.index(communication)
            arrays["reason"][t] = _reason(reason)
        out[node_id] = arrays
        out[node_id]["onset"] = {g: v for g, v in n.onset.items()}
        out[node_id]["visible"] = np.stack([n.visible[g] for g in VISIBLE_GROUPS], axis=1)
    return out
