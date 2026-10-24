"""Auditable whole-episode preparation for the bounded Jarvis farm trial."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import random
from typing import Any

import torch

from farm_poc.behavior_cloning.dataset import SequentialEpisode, TrainingDataset
from farm_poc.behavior_cloning.trainer import BehaviorCloningTrainer, TrainingConfig
from farm_poc.cognitive_runtime.agent import CTMRuntimeAgent
from farm_poc.cognitive_runtime.contracts import DECISIONS, DIAGNOSES
from farm_poc.cognitive_runtime.encoder import OperationalEncoderV1


PARTITIONS = ("train", "validation", "test")


def _episode_labels(episode: SequentialEpisode) -> set[str]:
    labels = {"decision:" + target["decision"] for target in episode.targets}
    labels.update("diagnosis:" + target["diagnosis"] for target in episode.targets
                  if target["decision"] == "DIAGNOSE")
    return labels


def create_trial_split(dataset: TrainingDataset, *, seed: int) -> dict[str, Any]:
    """Deterministic stratified episode assignment with no row-level splitting."""
    if len(dataset.episodes) < 3:
        raise ValueError("three-way trial split requires at least three complete episodes")
    encoder = OperationalEncoderV1()
    trajectory_hashes = {}
    for episode in dataset.episodes:
        encoded = [(encoder.encode(obs).values, encoder.encode(obs).validity_mask)
                   for obs in episode.observations]
        digest = hashlib.sha256(json.dumps(encoded, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        if digest in trajectory_hashes.values():
            raise ValueError("duplicate operational trajectory across episodes")
        trajectory_hashes[episode.episode_id] = digest
    label_sets = {ep.episode_id: _episode_labels(ep) for ep in dataset.episodes}
    support = Counter(label for labels in label_sets.values() for label in labels)
    labels = sorted((label for label, count in support.items() if count >= len(PARTITIONS)),
                    key=lambda label: (support[label], label))
    assignments: dict[str, str] = {}
    sizes = Counter()
    fractions = {"train": 0.65, "validation": 0.20, "test": 0.15}
    targets = {part: fraction * len(dataset.episodes) for part, fraction in fractions.items()}

    def rank(episode_id: str) -> str:
        return hashlib.sha256(f"{seed}:{episode_id}:{dataset_identity(dataset)}".encode()).hexdigest()

    for label in labels:
        for partition in PARTITIONS:
            if any(label in label_sets[eid] and assigned == partition
                   for eid, assigned in assignments.items()):
                continue
            candidates = [ep.episode_id for ep in dataset.episodes
                          if ep.episode_id not in assignments and label in label_sets[ep.episode_id]]
            if not candidates:
                raise ValueError(f"insufficient distinct episodes to cover {label} in all partitions")
            chosen = min(candidates, key=lambda eid: (
                -(sum(other in label_sets[eid] and not any(
                    other in label_sets[known] and part == partition
                    for known, part in assignments.items()) for other in labels)),
                sizes[partition] / targets[partition], rank(eid)))
            assignments[chosen] = partition; sizes[partition] += 1

    for ep in sorted(dataset.episodes, key=lambda item: rank(item.episode_id)):
        if ep.episode_id in assignments:
            continue
        partition = min(PARTITIONS, key=lambda part: (
            sizes[part] / targets[part],
            -sum(label in label_sets[ep.episode_id] and not any(
                label in label_sets[eid] and assigned == part
                for eid, assigned in assignments.items()) for label in labels), part))
        assignments[ep.episode_id] = partition; sizes[partition] += 1

    for label in labels:
        if any(not any(label in label_sets[eid] and part == partition
                       for eid, part in assignments.items()) for partition in PARTITIONS):
            raise ValueError(f"class coverage impossible for {label}")
    rows = [{"episode_id": episode.episode_id, "partition": assignments[episode.episode_id],
             "manifest_hash": episode.manifest_hash, "trajectory_sha256": trajectory_hashes[episode.episode_id]}
            for episode in sorted(dataset.episodes, key=lambda item: item.episode_id)]
    manifest = {"schema_version": 1, "seed": int(seed), "partition_fractions": fractions,
                "dataset_identity": _dataset_identity(dataset), "assignments": rows}
    manifest["manifest_sha256"] = hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    validate_trial_split(dataset, manifest)
    return manifest


def _dataset_identity(dataset: TrainingDataset) -> str:
    data = sorted((ep.episode_id, ep.manifest_hash) for ep in dataset.episodes)
    return hashlib.sha256(json.dumps(data, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def dataset_identity(dataset: TrainingDataset) -> str:
    return _dataset_identity(dataset)


def validate_trial_split(dataset: TrainingDataset, manifest: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    unhashed = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    digest = hashlib.sha256(json.dumps(unhashed, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if manifest.get("schema_version") != 1 or manifest.get("manifest_sha256") != digest:
        raise ValueError("invalid three-way split manifest hash or version")
    if manifest.get("dataset_identity") != _dataset_identity(dataset):
        raise ValueError("three-way split manifest dataset mismatch")
    rows = manifest.get("assignments")
    if not isinstance(rows, list):
        raise ValueError("split assignments must be a list")
    expected = {ep.episode_id for ep in dataset.episodes}
    found: dict[str, list[str]] = {partition: [] for partition in PARTITIONS}
    for row in rows:
        if set(row) != {"episode_id", "partition", "manifest_hash", "trajectory_sha256"}:
            raise ValueError("invalid split assignment row")
        if row["partition"] not in PARTITIONS or row["episode_id"] not in expected:
            raise ValueError("unknown episode or partition in split manifest")
        episode = next(ep for ep in dataset.episodes if ep.episode_id == row["episode_id"])
        if episode.manifest_hash != row["manifest_hash"]:
            raise ValueError("episode manifest changed after split creation")
        found[row["partition"]].append(row["episode_id"])
    all_ids = [episode_id for values in found.values() for episode_id in values]
    if len(all_ids) != len(set(all_ids)) or set(all_ids) != expected:
        raise ValueError("split manifest contains duplicate or missing episodes")
    return {key: tuple(sorted(value)) for key, value in found.items()}


def subset(dataset: TrainingDataset, ids: tuple[str, ...]) -> TrainingDataset:
    wanted = set(ids)
    return TrainingDataset(tuple(ep for ep in dataset.episodes if ep.episode_id in wanted))


def evaluate_labeled_episodes(agent: CTMRuntimeAgent, episodes: tuple[SequentialEpisode, ...]) -> dict[str, Any]:
    decision_confusion = {actual: {predicted: 0 for predicted in DECISIONS} for actual in DECISIONS}
    diagnosis_confusion = {actual: {predicted: 0 for predicted in DIAGNOSES} for actual in DIAGNOSES}
    end_to_end_diagnosis_correct = 0
    per_episode = []
    agent.eval()
    with torch.no_grad():
        for episode in episodes:
            agent.begin_episode(episode.episode_id)
            state = agent._state
            correct = 0
            for observation, target in zip(episode.observations, episode.targets, strict=True):
                encoded = agent.encode(observation)
                decision_logits, diagnosis_logits, state = agent.differentiable_forward(encoded, state)
                predicted_decision = DECISIONS[int(decision_logits.argmax(dim=-1)[0])]
                predicted_diagnosis = DIAGNOSES[int(diagnosis_logits.argmax(dim=-1)[0])]
                decision_confusion[target["decision"]][predicted_decision] += 1
                correct += int(predicted_decision == target["decision"])
                if target["decision"] == "DIAGNOSE":
                    # Score the diagnosis head on every labelled diagnostic
                    # case, separately from whether the decision head chose
                    # DIAGNOSE. The latter is reported as end-to-end accuracy.
                    diagnosis_confusion[target["diagnosis"]][predicted_diagnosis] += 1
                    if predicted_decision == "DIAGNOSE" and predicted_diagnosis == target["diagnosis"]:
                        end_to_end_diagnosis_correct += 1
                agent.record_training_observation(encoded,
                    simulation_tick=int(observation["incubator_1_direct_observation"]["tick"]),
                    decision=predicted_decision,
                    diagnosis=predicted_diagnosis if predicted_decision == "DIAGNOSE" else "UNKNOWN")
            per_episode.append({"episode_id": episode.episode_id,
                                "scenario_family": episode.manifest.get("scenario_id"),
                                "ticks": episode.tick_count, "decision_accuracy": correct / episode.tick_count})
    total = sum(sum(row.values()) for row in decision_confusion.values())
    correct = sum(decision_confusion[label][label] for label in DECISIONS)
    per_decision = {}
    for label in DECISIONS:
        tp = decision_confusion[label][label]
        support = sum(decision_confusion[label].values())
        predicted = sum(decision_confusion[actual][label] for actual in DECISIONS)
        per_decision[label] = {"support": support,
                               "precision": tp / predicted if predicted else None,
                               "recall": tp / support if support else None}
    diagnosis_total = sum(sum(row.values()) for row in diagnosis_confusion.values())
    diagnosis_correct = sum(diagnosis_confusion[label][label] for label in DIAGNOSES)
    return {"episodes": len(episodes), "ticks": total, "decision_accuracy": correct / total if total else None,
            "decision_confusion_matrix": decision_confusion, "decision_per_class": per_decision,
            "diagnosis_confusion_matrix": diagnosis_confusion,
            "diagnosis_accuracy": diagnosis_correct / diagnosis_total if diagnosis_total else None,
            "diagnosis_support": diagnosis_total,
            "end_to_end_diagnosis_accuracy": end_to_end_diagnosis_correct / diagnosis_total if diagnosis_total else None,
            "per_episode": per_episode}


def train_decision_diagnosis_heads(dataset: TrainingDataset, split_manifest: dict[str, Any], *,
                                   output_dir: str | Path, seed: int, epochs: int = 8) -> dict[str, Any]:
    partitions = validate_trial_split(dataset, split_manifest)
    train_eps = tuple(ep for ep in dataset.episodes if ep.episode_id in set(partitions["train"]))
    val_eps = tuple(ep for ep in dataset.episodes if ep.episode_id in set(partitions["validation"]))
    test_eps = tuple(ep for ep in dataset.episodes if ep.episode_id in set(partitions["test"]))
    torch.manual_seed(seed)
    agent = CTMRuntimeAgent(); agent._materialize()
    before = evaluate_labeled_episodes(agent, test_eps)
    decision_counts = Counter(target["decision"] for episode in train_eps for target in episode.targets)
    diagnosis_counts = Counter(target["diagnosis"] for episode in train_eps
                               for target in episode.targets if target["decision"] == "DIAGNOSE")
    # These weights are derived exclusively from the train partition. They
    # counter the current corpus's overwhelming NONE majority; validation/test
    # labels are never consulted.
    decision_weights = tuple(sum(decision_counts.values()) / max(1, decision_counts[label])
                             for label in DECISIONS)
    present_diagnoses = [label for label in DIAGNOSES if diagnosis_counts[label] > 0]
    diagnosis_weights = tuple(
        (sum(diagnosis_counts.values()) / (len(present_diagnoses) * diagnosis_counts[label]))
        if diagnosis_counts[label] else 0.0 for label in DIAGNOSES)
    trainer = BehaviorCloningTrainer(agent, TrainingConfig(seed=seed, epochs=epochs,
        decision_class_weights=decision_weights, diagnosis_class_weights=diagnosis_weights,
        diagnosis_loss_weight=25.0))
    initial_heads = {key: value.detach().clone() for key, value in agent.state_dict().items()
                     if key.startswith(("decision_head.", "diagnosis_head."))}
    history = []
    for epoch in range(epochs):
        for episode in train_eps:
            history.append({"epoch": epoch, "episode_id": episode.episode_id, **trainer.train_episode(episode)})
    updated_heads = agent.state_dict()
    heads_changed = {name: any(not torch.equal(value, updated_heads[key])
                               for key, value in initial_heads.items() if key.startswith(name + "."))
                     for name in ("decision_head", "diagnosis_head")}
    validation = evaluate_labeled_episodes(agent, val_eps)
    after = evaluate_labeled_episodes(agent, test_eps)
    training_metrics = evaluate_labeled_episodes(agent, train_eps)
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    split_path = output / "episode-split-3way.json"
    tmp = split_path.with_name(split_path.name + ".tmp")
    tmp.write_text(json.dumps(split_manifest, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(split_path)
    from farm_poc.behavior_cloning.trainer import save_training_checkpoint
    save_training_checkpoint(output / "jarvis-decision-checkpoint.pt", trainer=trainer,
                             dataset=dataset, history=history)
    summary = {
        "schema_version": 1, "label_provenance": "provisional observable rule-generated labels from audited ExpertPolicy; not human-reviewed",
        "dataset": {"episodes": len(dataset.episodes), "ticks": sum(ep.tick_count for ep in dataset.episodes),
                    "scenario_counts": dict(Counter(ep.manifest.get("scenario_id") for ep in dataset.episodes)),
                    "decision_counts": dict(Counter(t["decision"] for ep in dataset.episodes for t in ep.targets)),
                    "diagnosis_counts": dict(Counter(t["diagnosis"] for ep in dataset.episodes for t in ep.targets if t["decision"] == "DIAGNOSE"))},
        "split": {part: {"episode_ids": list(partitions[part]), "episodes": len(partitions[part]),
                          "decision_counts": dict(Counter(t["decision"] for ep in dataset.episodes if ep.episode_id in set(partitions[part]) for t in ep.targets)),
                          "diagnosis_counts": dict(Counter(t["diagnosis"] for ep in dataset.episodes if ep.episode_id in set(partitions[part]) for t in ep.targets if t["decision"] == "DIAGNOSE"))}
                  for part in PARTITIONS},
        "split_manifest": str(split_path), "split_manifest_sha256": split_manifest["manifest_sha256"],
        "initial_test_metrics": before, "training_metrics": training_metrics,
        "validation_metrics": validation, "final_test_metrics": after,
        "validation_test_episodes_used_for_training": [], "training": {"epochs": epochs, "episode_updates": len(history),
              "initial_loss": history[0]["loss"] if history else None,
              "final_loss": history[-1]["loss"] if history else None,
              "decision_class_weights": list(decision_weights), "diagnosis_class_weights": list(diagnosis_weights),
              "diagnosis_loss_weight": 25.0,
              "decision_head_changed": heads_changed["decision_head"],
              "diagnosis_head_changed": heads_changed["diagnosis_head"],
              "per_episode_history": history},
        "model": {"checkpoint": str(output / "jarvis-decision-checkpoint.pt"),
                  "encoder_schema_hash": agent.encoder.schema_hash,
                  "decision_labels": list(DECISIONS), "diagnosis_labels": list(DIAGNOSES)},
        "limitations": ["No human-reviewed targets were present; all labels are provisional scripted-policy supervision.",
                        "The diagnosis target corpus contains only the existing WIDER_SUPPLY_ANOMALY diagnostic class.",
                        "Test metrics use whole held-out episodes and must be interpreted with small support counts."]}
    report_path = output / "supervised-training-report.json"
    tmp = report_path.with_name(report_path.name + ".tmp")
    tmp.write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(report_path)
    return summary
