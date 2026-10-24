"""Versioned observation projection and validation against the audited inventory."""
from __future__ import annotations

import json
from math import isfinite
from functools import lru_cache
from copy import deepcopy
from pathlib import Path
from typing import Any

INVENTORY_PATH = Path(__file__).resolve().parents[2] / "docs" / "jarvis-input-inventory.json"
OBSERVATION_SCHEMA_VERSION = 1


def _paths(value: Any, prefix: str = "") -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.add(path)
            result.update(_paths(child, path))
    elif isinstance(value, list):
        path = f"{prefix}[*]"
        result.add(path)
        for child in value:
            result.update(_paths(child, path))
    return result


def _validate_paths(value: Any, allowed: set[str], label: str) -> None:
    observed = _paths(value)
    valid = _expanded_allowed_paths(tuple(sorted(allowed)))
    unknown = sorted(observed - valid)
    if unknown:
        raise ValueError(f"{label} contains fields outside the audited input inventory: {unknown}")


@lru_cache(maxsize=8)
def _expanded_allowed_paths(allowed: tuple[str, ...]) -> frozenset[str]:
    """Cache the schema closure; inventories are immutable during a run."""
    valid = set(allowed)
    for candidate in allowed:
        parts = candidate.split(".")
        variants = {candidate}
        for index, part in enumerate(parts):
            if "[*]" in part:
                altered = list(parts)
                altered[index] = part.replace("[*]", "")
                variants.add(".".join(altered))
        for variant in variants:
            valid.add(variant)
            components = variant.split(".")
            valid.update(".".join(components[:index]) for index in range(1, len(components)))
    return frozenset(valid)


def _finite(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _finite(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _finite(item, f"{path}[{index}]")
    elif isinstance(value, float) and not isfinite(value):
        raise ValueError(f"non-finite numeric value at {path}")


@lru_cache(maxsize=1)
def inventory() -> dict:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def validate_observation(observation: dict) -> None:
    """Reject unknown public fields; metadata is deliberately outside this object."""
    expected_keys = {"incubator_1_direct_observation", "scripted_peer_reports_to_jarvis"}
    if set(observation) != expected_keys:
        raise ValueError("observation root must match the audited interface names")
    inv = inventory()["interfaces"]
    direct_schema = inv["incubator_1_direct_observation"]["field_paths"]
    direct_allowed = {path for paths in direct_schema.values() for path in paths}
    _validate_paths(observation["incubator_1_direct_observation"], direct_allowed,
                    "incubator_1_direct_observation")
    peer_schemas = inv["scripted_peer_reports_to_jarvis"]
    if set(observation["scripted_peer_reports_to_jarvis"]) - set(peer_schemas):
        raise ValueError("unknown peer report source")
    for device_id, report in observation["scripted_peer_reports_to_jarvis"].items():
        _validate_paths(report, set(peer_schemas[device_id]["field_paths"]), device_id)
    _finite(observation)


def public_observation(engine, *, validate: bool = True) -> dict:
    # Use the most recently published package, rather than recomputing one
    # after the engine has advanced its clock to the next tick.
    direct = (engine.device_computer.latest_observation or
              engine.device_computer.observation())
    direct = deepcopy(direct)
    # Episode identity is recorder alignment metadata, not a model feature.
    for event in direct.get("recent_history", []):
        event.pop("episode_id", None)
    observation = {
        "incubator_1_direct_observation": direct,
        "scripted_peer_reports_to_jarvis": {key: value for key, value in sorted(engine.peer_reports.items())},
    }
    if validate:
        validate_observation(observation)
    return observation
