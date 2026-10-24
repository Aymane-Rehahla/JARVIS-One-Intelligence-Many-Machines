"""Validated, episode-ordered behavior-cloning dataset reader.

This loader reads only public observations, expert targets, and optional expert
audit traces. It intentionally never opens ``evaluator_private`` files.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from farm_poc.cognitive_runtime.contracts import DECISIONS, DIAGNOSES
from farm_poc.cognitive_runtime.encoder import OperationalEncoderV1
from farm_poc.training.recorder import _sha256, load_training_episode


@dataclass(frozen=True)
class SequentialEpisode:
    episode_id: str
    manifest_hash: str
    manifest: dict[str, Any]
    observations: tuple[dict, ...]
    targets: tuple[dict, ...]
    audit_traces: tuple[dict | None, ...]

    @property
    def tick_count(self) -> int:
        return len(self.observations)


@dataclass
class TrainingDataset:
    episodes: tuple[SequentialEpisode, ...]
    discarded: list[dict[str, str]] = field(default_factory=list)
    legacy_encoder_episodes: tuple[str, ...] = ()
    legacy_audit_episodes: tuple[str, ...] = ()

    def class_counts(self) -> dict[str, int]:
        counts = Counter(target["decision"] for episode in self.episodes for target in episode.targets)
        return {decision: counts[decision] for decision in DECISIONS}


@dataclass(frozen=True)
class SplitAssessment:
    valid: bool
    reason: str | None
    decision_episode_counts: dict[str, int]
    diagnosis_episode_counts: dict[str, int]


def _manifest_hash(manifest: dict[str, Any]) -> str:
    # The manifest file checksum is stable identity; it also detects edits to
    # scenario metadata without treating that metadata as an input feature.
    value = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    import hashlib
    return hashlib.sha256(value).hexdigest()


def _validate_target(target: dict) -> None:
    if target.get("schema_version") != 1:
        raise ValueError("unsupported expert target schema")
    decision, diagnosis = target.get("decision"), target.get("diagnosis")
    if decision not in DECISIONS or diagnosis not in DIAGNOSES:
        raise ValueError("unknown decision or diagnosis target")
    if decision != "DIAGNOSE" and diagnosis != "UNKNOWN":
        raise ValueError("non-diagnostic targets must use UNKNOWN diagnosis")
    if not isinstance(target.get("operational_commands", []), list):
        raise ValueError("operational_commands must be a list")


def _load_one(root: Path, episode_id: str, encoder: OperationalEncoderV1,
              *, allow_legacy_encoder: bool) -> tuple[SequentialEpisode, bool, bool]:
    path = root / "manifests" / f"{episode_id}.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise ValueError("episode is incomplete")
    expected_hash = manifest.get("encoder_schema_hash")
    legacy_encoder = expected_hash is None
    if legacy_encoder and not allow_legacy_encoder:
        raise ValueError("dataset has no frozen encoder schema hash; explicit legacy opt-in required")
    if not legacy_encoder and expected_hash != encoder.schema_hash:
        raise ValueError("dataset encoder schema hash is incompatible")
    if manifest.get("observation_schema_version") != encoder.schema_manifest()["schema_version"]:
        raise ValueError("dataset observation schema version is incompatible")
    if manifest.get("target_schema_version") != 1:
        raise ValueError("dataset target schema version is incompatible")
    # Public loader: load_training_episode does not read evaluator_private.
    records = load_training_episode(root, episode_id)
    observations, targets = records["observations"], records["targets"]
    if len(observations) != len(targets) or len(observations) != int(manifest["tick_count"]):
        raise ValueError("observation/target tick alignment failure")
    for observation, target in zip(observations, targets, strict=True):
        encoder.encode(observation)  # validates public allowlist and finite values.
        _validate_target(target)
    audits = records.get("expert_audit")
    legacy_audit = audits is None
    if audits is None:
        audits = [None] * len(observations)
    if len(audits) != len(observations):
        raise ValueError("expert-audit tick alignment failure")
    return SequentialEpisode(episode_id, _manifest_hash(manifest), manifest,
                             tuple(observations), tuple(targets), tuple(audits)), legacy_encoder, legacy_audit


def load_training_dataset(root: str | Path, *, encoder: OperationalEncoderV1 | None = None,
                          allow_legacy_encoder: bool = False, strict: bool = True) -> TrainingDataset:
    root = Path(root)
    encoder = encoder or OperationalEncoderV1()
    episodes: list[SequentialEpisode] = []
    discarded: list[dict[str, str]] = []
    legacy_encoder: list[str] = []
    legacy_audit: list[str] = []
    for manifest_path in sorted((root / "manifests").glob("*.json")):
        try:
            episode, old_encoder, old_audit = _load_one(root, manifest_path.stem, encoder,
                                                         allow_legacy_encoder=allow_legacy_encoder)
            episodes.append(episode)
            if old_encoder: legacy_encoder.append(episode.episode_id)
            if old_audit: legacy_audit.append(episode.episode_id)
        except Exception as error:
            discarded.append({"episode_id": manifest_path.stem, "reason": f"{type(error).__name__}: {error}"})
            if strict:
                raise
    if not episodes:
        raise ValueError("no valid complete episodes")
    return TrainingDataset(tuple(episodes), discarded, tuple(legacy_encoder), tuple(legacy_audit))


def assess_episode_splits(dataset: TrainingDataset, *, required_splits: int = 3) -> SplitAssessment:
    """Say whether whole-episode train/validation/test coverage is defensible."""
    decision_episodes = {label: 0 for label in DECISIONS}
    diagnosis_episodes = {label: 0 for label in DIAGNOSES}
    for episode in dataset.episodes:
        for label in {target["decision"] for target in episode.targets}:
            decision_episodes[label] += 1
        for label in {target["diagnosis"] for target in episode.targets if target["decision"] == "DIAGNOSE"}:
            diagnosis_episodes[label] += 1
    insufficient = [f"decision {key}" for key, count in decision_episodes.items() if count and count < required_splits]
    insufficient += [f"diagnosis {key}" for key, count in diagnosis_episodes.items() if count and count < required_splits]
    return SplitAssessment(not insufficient,
                           None if not insufficient else "insufficient independent episodes for " + ", ".join(insufficient),
                           decision_episodes, diagnosis_episodes)
