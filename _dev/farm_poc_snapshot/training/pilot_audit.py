"""Offline pilot validation and descriptive label/recording statistics."""
from __future__ import annotations

import json
import tracemalloc
from collections import Counter
from pathlib import Path

from .contract import validate_observation
from .recorder import load_training_episode, validate_episode_bundle


def _count_null(value):
    if isinstance(value, dict):
        return sum(_count_null(child) for child in value.values())
    if isinstance(value, list):
        return sum(_count_null(child) for child in value)
    return int(value is None)


def audit_pilot(root: str | Path) -> dict:
    root = Path(root)
    tracemalloc.start()
    labels = Counter()
    fresh_reports = stale_reports = no_report_ticks = 0
    null_values = tick_count = schema_failures = completed = incomplete = 0
    ambiguous = 0
    examples = {}
    for path in sorted((root / "manifests").glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("status") != "complete":
            incomplete += 1
            continue
        try:
            validate_episode_bundle(root, manifest["episode_id"])
            dataset = load_training_episode(root, manifest["episode_id"])
            for obs in dataset["observations"]:
                validate_observation(obs)
                null_values += _count_null(obs)
                reports = obs.get("scripted_peer_reports_to_jarvis", {})
                if not reports:
                    no_report_ticks += 1
                for report in reports.values():
                    age = int(obs["incubator_1_direct_observation"]["tick"]) - int(report.get("sample_tick", -10**9))
                    if 0 <= age <= 2:
                        fresh_reports += 1
                    else:
                        stale_reports += 1
            for row in dataset["targets"]:
                labels[row["decision"]] += 1
                ambiguous += int(row["decision"] == "DIAGNOSE" and row.get("diagnosis") == "UNKNOWN")
            if dataset["targets"]:
                examples.setdefault(manifest["scenario_id"], {
                    "episode_id": manifest["episode_id"],
                    "first_target": dataset["targets"][0],
                    "last_non_none": next((target for target in reversed(dataset["targets"])
                                            if target["decision"] != "NONE"), None),
                })
            tick_count += len(dataset["targets"])
            completed += 1
        except Exception:
            schema_failures += 1
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    data_bytes = sum(path.stat().st_size for folder in ("observations", "expert_targets", "expert_audit", "evaluator_private")
                     for path in (root / folder).glob("*.jsonl"))
    return {"episodes_completed": completed, "episodes_incomplete": incomplete,
            "ticks_recorded": tick_count, "observation_target_alignment_failures": schema_failures,
            "schema_validation_failures": schema_failures, "class_frequencies": dict(labels),
            "ambiguous_unknown_diagnoses": ambiguous, "explicit_null_values": null_values,
            "peer_report_observations_fresh": fresh_reports,
            "peer_report_observations_stale": stale_reports,
            "ticks_without_peer_reports": no_report_ticks,
            "data_bytes_including_private": data_bytes,
            "audit_memory_current_bytes": current, "audit_memory_peak_bytes": peak,
            "representative_examples": examples}
