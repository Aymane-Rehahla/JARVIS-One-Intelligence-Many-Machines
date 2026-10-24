"""Evidence onset: when could a node first have noticed something, from what it actually saw?

The fault run and its twin share every random draw (sensor noise, weather,
benign events), so their inputs are identical until the fault's effects reach a
node. A feature counts as *visibly different* when:

- number: the 10-tick mean differs from the twin by more than
  max(4 * noise / sqrt(10), 0.4 % of the feature's range) for 5 ticks in a row,
  where noise is the twin's own tick-to-tick variability;
- flag / category: the 6-tick mean differs by at least 0.5 for 3 ticks;
- device alarm code: it differs for 2 ticks in a row (a code is an explicit message);
- validity: a reading is missing in one run but present in the other for 5 ticks.

Features are grouped into evidence families (own machine, room heat, smoke/CO,
flood, storm, water supply, peer link). A family is visible when any of its features is.
Conclusions (assessments, memory) are never evidence.
"""
from __future__ import annotations

import numpy as np

from ..nodes.contracts import MACHINE_TYPES

NUMBER_WINDOW, NUMBER_PERSIST = 10, 5
FLAG_WINDOW, FLAG_PERSIST = 6, 3
CODE_PERSIST = 2
FLOOR = 0.004


def _groups(node_id, names):
    machine = MACHINE_TYPES[node_id]
    index = {name: i for i, name in enumerate(names)}

    def pick(candidates):
        return sorted(index[name] for name in candidates if name in index)

    own = [i for name, i in index.items() if name.startswith("own.") and not name.endswith("observation_age_s")]
    heat_own = [f"own.{z}_{k}" for z in ("barn", "storage", "workspace") for k in ("temperature", "sensors_lost")] + \
               ["own.supply_temperature", "own.return_temperature"] if machine == "hvac" else \
               (["own.barn_temperature"] if machine == "animal_protection" else ["own.ambient_temperature"])
    heat_peer = [f"peer.hvac.{k}" for k in ("supply_temperature_c", "return_temperature_c", "zone_sensors_lost")]
    smoke_own = ["own.smoke_detected", "own.smoke_obscuration", "own.co_ppm", "own.code.SMOKE_DETECTED",
                 "own.code.CO_HIGH"] if machine == "animal_protection" else []
    smoke_peer = [f"peer.animal-protection.{k}" for k in ("co_ppm", "smoke_obscuration_pct_m", "smoke_detected")]
    flood_own = ["own.floodwater_cm", "own.water_intrusion", "own.code.WATER_INTRUSION"] if machine == "animal_protection" else []
    flood_peer = [f"peer.animal-protection.{k}" for k in ("floodwater_level_cm", "water_intrusion")]
    storm_own = ["own.wind_m_s", "own.pressure_tendency", "own.risk=watch", "own.risk=warning", "own.risk=emergency",
                 "own.watch_active", "own.severe_weather_warning", "own.tornado_warning", "own.flood_warning"] \
        if machine == "animal_protection" else []
    storm_peer = ["peer.animal-protection.wind_speed_m_s", "peer.animal-protection.tornado_warning"]
    water_own = ["own.water_inlet_ml_min", "own.reservoir_pct", "own.code.LOW_WATER"] if machine == "incubator" else \
        (["own.water_supply_kpa", "own.water_supply_flow_l_min", "own.water_tank_pct", "own.code.WATER_SUPPLY_LOST",
          "own.code.TANK_FILL_FAULT"] if machine == "animal_protection" else [])
    # Peer readings are deliberately absent: a node's copy of a peer's readings depends on
    # when that peer last sent a message, so comparing them across twins would turn
    # message timing into fake evidence. labels.py derives peer evidence from the peer's
    # own onset at the sample time the node actually holds.
    return {
        "own": own, "heat_own": pick(heat_own), "smoke_own": pick(smoke_own),
        "flood_own": pick(flood_own), "storm_own": pick(storm_own), "water_own": pick(water_own),
        "link": pick([name for name in names if name.startswith("peer.") and name.endswith(".present")]),
    }


def _rolling_mean(x, window):
    cumulative = np.cumsum(np.vstack([np.zeros((1, x.shape[1])), x.astype(np.float64)]), axis=0)
    index = np.arange(x.shape[0])
    start = np.maximum(0, index + 1 - window)
    return ((cumulative[index + 1] - cumulative[start]) / (index + 1 - start)[:, None]).astype(np.float32)


def _persist(mask, ticks):
    """True where ``mask`` has held for ``ticks`` consecutive steps (inclusive)."""
    if ticks <= 1:
        return mask.copy()
    counts = np.cumsum(np.vstack([np.zeros((1, mask.shape[1]), dtype=np.int32), mask.astype(np.int32)]), axis=0)
    index = np.arange(mask.shape[0])
    start = np.maximum(0, index + 1 - ticks)
    full = (index + 1 - start) == ticks
    return ((counts[index + 1] - counts[start]) == ticks) & full[:, None]


def feature_visibility(main_values, main_valid, twin_values, twin_valid, kinds, codes=None):
    """Boolean matrix [T, F]: feature f is visibly different at tick t. ``codes``: mask of alarm-code columns."""
    a = main_values.astype(np.float32)
    b = twin_values.astype(np.float32)
    va = main_valid.astype(bool)
    vb = twin_valid.astype(bool)
    numbers = np.array([kind == "number" for kind in kinds])
    visible = np.zeros(a.shape, dtype=bool)
    if numbers.any():
        cols = np.where(numbers)[0]
        a_n = np.where(va[:, cols], a[:, cols], b[:, cols])
        b_n = np.where(vb[:, cols], b[:, cols], a_n)
        diff = np.abs(_rolling_mean(a_n, NUMBER_WINDOW) - _rolling_mean(b_n, NUMBER_WINDOW))
        steps = np.abs(np.diff(b_n, axis=0))
        noise = 1.4826 * np.median(steps, axis=0) / np.sqrt(2.0) if steps.shape[0] else np.zeros(len(cols))
        threshold = np.maximum(4.0 * noise / np.sqrt(NUMBER_WINDOW), FLOOR)
        visible[:, cols] = _persist(diff > threshold, NUMBER_PERSIST)
    codes = np.zeros(len(kinds), dtype=bool) if codes is None else np.asarray(codes, dtype=bool)
    flags = ~numbers & ~codes
    if flags.any():
        cols = np.where(flags)[0]
        diff = np.abs(_rolling_mean(a[:, cols], FLAG_WINDOW) - _rolling_mean(b[:, cols], FLAG_WINDOW))
        visible[:, cols] = _persist(diff >= 0.5, FLAG_PERSIST)
    if codes.any():
        cols = np.where(codes)[0]
        visible[:, cols] = _persist(np.abs(a[:, cols] - b[:, cols]) >= 0.5, CODE_PERSIST)
    validity_changed = _persist(va != vb, NUMBER_PERSIST)
    return visible | validity_changed


def group_visibility(node_id, names, kinds, main_values, main_valid, twin_values, twin_valid):
    """Returns {group: bool[T]} and {group: first visible tick or None}."""
    codes = [name.startswith("own.code.") for name in names]
    per_feature = feature_visibility(main_values, main_valid, twin_values, twin_valid, kinds, codes)
    groups = _groups(node_id, names)
    visible = {g: (per_feature[:, cols].any(axis=1) if cols else np.zeros(per_feature.shape[0], dtype=bool))
               for g, cols in groups.items()}
    onset = {g: (int(np.argmax(v)) if v.any() else None) for g, v in visible.items()}
    return visible, onset, per_feature
