"""Deterministic, validated whole-episode training/evaluation assignments."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .dataset import TrainingDataset

SPLIT_SCHEMA_VERSION = 1
PARTITIONS = ("train", "evaluation")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def dataset_identity(dataset: TrainingDataset) -> str:
    """Hash episode IDs and immutable recorder-manifest identities."""
    rows = sorted((episode.episode_id, episode.manifest_hash) for episode in dataset.episodes)
    if len({episode_id for episode_id, _ in rows}) != len(rows):
        raise ValueError("dataset contains duplicate episode IDs")
    return _digest(rows)


def _labels(episode) -> frozenset[str]:
    decisions = {target["decision"] for target in episode.targets}
    diagnoses = {"diagnosis:" + target["diagnosis"] for target in episode.targets
                 if target["decision"] == "DIAGNOSE"}
    return frozenset({"decision:" + label for label in decisions} | diagnoses)


def create_episode_split(dataset: TrainingDataset, *, seed: int, evaluation_fraction: float = 0.2) -> dict[str, Any]:
    """Create seeded whole-episode assignments, stratifying by episode label signature.

    For each repeated signature, at least one episode is reserved in each
    partition. Rare signatures remain in train when there is only one episode.
    """
    if not 0.0 < evaluation_fraction < 1.0:
        raise ValueError("evaluation_fraction must be between 0 and 1")
    identity = dataset_identity(dataset)
    episodes = list(dataset.episodes)
    if not episodes:
        raise ValueError("cannot split an empty dataset")

    groups: dict[tuple[str, ...], list] = defaultdict(list)
    for episode in episodes:
        groups[tuple(sorted(_labels(episode)))].append(episode)

    def rank(episode) -> str:
        return hashlib.sha256(f"{seed}\0{episode.episode_id}\0{episode.manifest_hash}".encode()).hexdigest()

    train_ids: set[str] = set()
    evaluation_ids: set[str] = set()
    ordered_groups = sorted(groups.items(), key=lambda pair: (len(pair[1]), pair[0]))
    for _, group in ordered_groups:
        ordered = sorted(group, key=rank)
        if len(ordered) == 1:
            train_ids.add(ordered[0].episode_id)
        else:
            evaluation_ids.add(ordered[0].episode_id)
            train_ids.update(episode.episode_id for episode in ordered[1:])

    target_eval = max(1, round(len(episodes) * evaluation_fraction)) if len(episodes) > 1 else 0
    # Top up evaluation deterministically without removing the train member of
    # any repeated signature.
    candidates = sorted((episode for episode in episodes if episode.episode_id in train_ids), key=rank)
    while len(evaluation_ids) < target_eval and candidates:
        chosen = candidates.pop(0)
        group = groups[tuple(sorted(_labels(chosen)))]
        if sum(item.episode_id in train_ids for item in group) > 1:
            train_ids.remove(chosen.episode_id)
            evaluation_ids.add(chosen.episode_id)
    if len(episodes) > 1 and not evaluation_ids:
        # Unique label signatures cannot be stratified, but a held-out
        # partition is still useful and must not silently disappear.
        chosen = min((episode for episode in episodes if episode.episode_id in train_ids), key=rank)
        train_ids.remove(chosen.episode_id)
        evaluation_ids.add(chosen.episode_id)

    assignments = [{"episode_id": episode.episode_id,
                    "partition": "evaluation" if episode.episode_id in evaluation_ids else "train"}
                   for episode in sorted(episodes, key=lambda item: item.episode_id)]
    manifest = {"schema_version": SPLIT_SCHEMA_VERSION, "seed": int(seed),
                "evaluation_fraction": float(evaluation_fraction),
                "dataset_identity": identity, "assignments": assignments}
    manifest["manifest_hash"] = _digest(manifest)
    validate_episode_split(dataset, manifest)
    return manifest


def validate_episode_split(dataset: TrainingDataset, manifest: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """Validate schema, hash, exact dataset coverage, uniqueness and partitions."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SPLIT_SCHEMA_VERSION:
        raise ValueError("unsupported episode split manifest")
    expected_hash = manifest.get("manifest_hash")
    unhashed = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    if not isinstance(expected_hash, str) or _digest(unhashed) != expected_hash:
        raise ValueError("episode split manifest hash mismatch")
    if manifest.get("dataset_identity") != dataset_identity(dataset):
        raise ValueError("episode split manifest belongs to a different dataset")
    assignments = manifest.get("assignments")
    if not isinstance(assignments, list):
        raise ValueError("episode split assignments must be a list")
    actual: dict[str, list[str]] = {partition: [] for partition in PARTITIONS}
    seen: set[str] = set()
    known = {episode.episode_id for episode in dataset.episodes}
    for row in assignments:
        if not isinstance(row, dict) or set(row) != {"episode_id", "partition"}:
            raise ValueError("invalid episode split assignment row")
        episode_id, partition = row["episode_id"], row["partition"]
        if episode_id in seen:
            raise ValueError(f"duplicate episode ID in split manifest: {episode_id}")
        if episode_id not in known:
            raise ValueError(f"unknown episode ID in split manifest: {episode_id}")
        if partition not in PARTITIONS:
            raise ValueError(f"invalid episode partition: {partition}")
        seen.add(episode_id)
        actual[partition].append(episode_id)
    missing = known - seen
    if missing:
        raise ValueError("split manifest is missing episode IDs: " + ", ".join(sorted(missing)))
    return {partition: tuple(sorted(ids)) for partition, ids in actual.items()}


def save_episode_split(path: str | Path, manifest: dict[str, Any]) -> None:
    """Write a complete manifest atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_episode_split(path: str | Path, dataset: TrainingDataset) -> dict[str, Any]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_episode_split(dataset, manifest)
    return manifest


def partition_episodes(dataset: TrainingDataset, manifest: dict[str, Any], partition: str):
    if partition not in PARTITIONS:
        raise ValueError(f"partition must be one of {PARTITIONS}")
    assignments = validate_episode_split(dataset, manifest)
    ids = set(assignments[partition])
    return tuple(episode for episode in dataset.episodes if episode.episode_id in ids)
