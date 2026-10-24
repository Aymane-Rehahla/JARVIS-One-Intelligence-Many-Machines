"""Small deterministic sequential CTM-RL behavior-cloning smoke trainer."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn

from farm_poc.cognitive_runtime.agent import CTMRuntimeAgent
from farm_poc.cognitive_runtime.contracts import DECISIONS, DIAGNOSES
from .dataset import SequentialEpisode, TrainingDataset
from .splits import partition_episodes


@dataclass(frozen=True)
class TrainingConfig:
    schema_version: int = 1
    seed: int = 7
    learning_rate: float = 1e-3
    epochs: int = 1
    gradient_clip_norm: float | None = 1.0
    diagnosis_loss_weight: float = 1.0
    decision_class_weights: tuple[float, ...] | None = None
    diagnosis_class_weights: tuple[float, ...] | None = None


def set_training_seed(seed: int) -> None:
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


class BehaviorCloningTrainer:
    """Full-episode BPTT; no targets, audit trace, or private state enter CTM input."""

    def __init__(self, agent: CTMRuntimeAgent, config: TrainingConfig = TrainingConfig()):
        self.agent, self.config = agent, config
        set_training_seed(config.seed)
        self.agent.train(); self.agent._materialize()
        self.optimizer = torch.optim.Adam(self.agent.parameters(), lr=config.learning_rate)

    def _losses(self, decision_logits: torch.Tensor, diagnosis_logits: torch.Tensor, target: dict) -> tuple[torch.Tensor, torch.Tensor]:
        decision = target["decision"]
        diagnosis = target["diagnosis"]
        if decision not in DECISIONS or diagnosis not in DIAGNOSES:
            raise ValueError("invalid target class")
        if decision != "DIAGNOSE" and diagnosis != "UNKNOWN":
            raise ValueError("non-diagnostic target has a diagnosis label")
        decision_weights = (None if self.config.decision_class_weights is None else
                            torch.tensor(self.config.decision_class_weights, dtype=torch.float32,
                                         device=decision_logits.device))
        diagnosis_weights = (None if self.config.diagnosis_class_weights is None else
                             torch.tensor(self.config.diagnosis_class_weights, dtype=torch.float32,
                                          device=diagnosis_logits.device))
        decision_loss = nn.functional.cross_entropy(decision_logits,
            torch.tensor([DECISIONS.index(decision)], device=decision_logits.device), reduction="none")
        if decision_weights is not None:
            decision_loss = decision_loss * decision_weights[DECISIONS.index(decision)]
        decision_loss = decision_loss.mean()
        diagnosis_loss = torch.zeros((), device=decision_logits.device)
        if decision == "DIAGNOSE":
            diagnosis_loss = nn.functional.cross_entropy(diagnosis_logits,
                torch.tensor([DIAGNOSES.index(diagnosis)], device=diagnosis_logits.device), reduction="none")
            if diagnosis_weights is not None:
                diagnosis_loss = diagnosis_loss * diagnosis_weights[DIAGNOSES.index(diagnosis)]
            diagnosis_loss = diagnosis_loss.mean()
        return decision_loss, diagnosis_loss

    def train_episode(self, episode: SequentialEpisode) -> dict[str, float | int]:
        self.agent.begin_episode(episode.episode_id)
        state = self.agent._state
        self.optimizer.zero_grad(set_to_none=True)
        total = torch.zeros((), device=self.agent.config.device)
        decision_total = torch.zeros((), device=self.agent.config.device)
        diagnosis_total = torch.zeros((), device=self.agent.config.device)
        for index, (observation, target) in enumerate(zip(episode.observations, episode.targets, strict=True)):
            encoded = self.agent.encode(observation)
            decision_logits, diagnosis_logits, state = self.agent.differentiable_forward(encoded, state)
            decision_loss, diagnosis_loss = self._losses(decision_logits, diagnosis_logits, target)
            total = total + decision_loss + self.config.diagnosis_loss_weight * diagnosis_loss
            decision_total = decision_total + decision_loss; diagnosis_total = diagnosis_total + diagnosis_loss
            # Memory advances only from public observation and the model's own prediction.
            predicted_decision = DECISIONS[int(decision_logits.detach().argmax(dim=-1)[0])]
            predicted_diagnosis = (DIAGNOSES[int(diagnosis_logits.detach().argmax(dim=-1)[0])]
                                   if predicted_decision == "DIAGNOSE" else "UNKNOWN")
            sim_tick = int(observation["incubator_1_direct_observation"]["tick"])
            self.agent.record_training_observation(encoded, simulation_tick=sim_tick,
                                                   decision=predicted_decision, diagnosis=predicted_diagnosis)
        mean = total / episode.tick_count
        if not torch.isfinite(mean):
            raise FloatingPointError("non-finite behavior-cloning loss")
        mean.backward()
        gradients = [parameter.grad for parameter in self.agent.parameters() if parameter.grad is not None]
        if not gradients or not all(torch.isfinite(gradient).all() for gradient in gradients):
            raise FloatingPointError("non-finite or missing CTM gradient")
        if self.config.gradient_clip_norm is not None:
            torch.nn.utils.clip_grad_norm_(self.agent.parameters(), self.config.gradient_clip_norm)
        self.optimizer.step()
        return {"ticks": episode.tick_count, "loss": float(mean.detach()),
                "decision_loss": float((decision_total / episode.tick_count).detach()),
                "diagnosis_loss": float((diagnosis_total / episode.tick_count).detach())}

    def fit(self, dataset: TrainingDataset, split_manifest: dict[str, Any], *,
            partition: str = "train") -> list[dict[str, float | int]]:
        """Fit only episodes assigned to the validated training partition."""
        if partition != "train":
            raise ValueError("fit only accepts the train partition")
        episodes = partition_episodes(dataset, split_manifest, partition)
        if not episodes:
            raise ValueError("split manifest assigns no episodes to training")
        history = []
        for epoch in range(self.config.epochs):
            for episode in episodes:
                metrics = self.train_episode(episode)
                history.append({"epoch": epoch, "episode_id": episode.episode_id, **metrics})
        return history


def evaluate_sequences(agent: CTMRuntimeAgent, dataset: TrainingDataset,
                       split_manifest: dict[str, Any]) -> dict[str, Any]:
    """Evaluate only the validated, disjoint evaluation partition."""
    episodes = partition_episodes(dataset, split_manifest, "evaluation")
    if not episodes:
        return {"available": False, "reason": "no held-out episodes"}
    agent.eval()
    matrix = {actual: {predicted: 0 for predicted in DECISIONS} for actual in DECISIONS}
    diagnosis_total = diagnosis_correct = 0
    sequence_outcomes = []
    with torch.no_grad():
        for episode in episodes:
            agent.begin_episode(episode.episode_id)
            correct = 0
            for observation, target in zip(episode.observations, episode.targets, strict=True):
                candidate, _ = agent.process(observation)
                matrix[target["decision"]][candidate.decision] += 1
                correct += int(candidate.decision == target["decision"])
                if target["decision"] == "DIAGNOSE":
                    diagnosis_total += 1
                    diagnosis_correct += int(candidate.diagnosis == target["diagnosis"])
            sequence_outcomes.append({"episode_id": episode.episode_id, "ticks": episode.tick_count,
                                      "decision_accuracy": correct / episode.tick_count})
    support = {label: sum(matrix[label].values()) for label in DECISIONS}
    total = sum(support.values())
    per_class = {}
    for label in DECISIONS:
        true_positive = matrix[label][label]
        predicted = sum(matrix[actual][label] for actual in DECISIONS)
        per_class[label] = {"support": support[label],
                            "precision": None if not predicted else true_positive / predicted,
                            "recall": None if not support[label] else true_positive / support[label]}
    majority = max(support, key=support.get)
    return {"available": True, "decision_accuracy": sum(matrix[label][label] for label in DECISIONS) / total,
            "confusion_matrix": matrix, "per_class": per_class,
            "diagnosis_accuracy": None if not diagnosis_total else diagnosis_correct / diagnosis_total,
            "diagnosis_support": diagnosis_total, "majority_class": majority,
            "majority_baseline_accuracy": support[majority] / total,
            "sequence_outcomes": sequence_outcomes}


def checkpoint_metadata(agent: CTMRuntimeAgent, config: TrainingConfig, dataset: TrainingDataset) -> dict[str, Any]:
    return {"training_schema_version": 1, "encoder_manifest": agent.encoder.schema_manifest(),
            "cmm_config": {"working_limit": agent.cmm.working_limit, "episodic_limit": agent.cmm.episodic_limit},
            "decision_labels": list(DECISIONS), "diagnosis_labels": list(DIAGNOSES),
            "dataset_manifest_hashes": {episode.episode_id: episode.manifest_hash for episode in dataset.episodes},
            "training_config": asdict(config), "torch_version": str(torch.__version__)}


def save_training_checkpoint(path: str | Path, *, trainer: BehaviorCloningTrainer,
                             dataset: TrainingDataset, history: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"checkpoint_version": 1, "runtime_config": asdict(trainer.agent.config),
                "state_dict": trainer.agent.state_dict(), "optimizer_state": trainer.optimizer.state_dict(),
                "metadata": checkpoint_metadata(trainer.agent, trainer.config, dataset), "history": history}, path)
    path.with_suffix(path.suffix + ".history.json").write_text(json.dumps(history, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_training_checkpoint(path: str | Path, agent: CTMRuntimeAgent) -> dict[str, Any]:
    checkpoint = torch.load(Path(path), map_location=agent.config.device, weights_only=True)
    if checkpoint.get("checkpoint_version") != 1:
        raise ValueError("unsupported behavior-cloning checkpoint")
    manifest = checkpoint.get("metadata", {}).get("encoder_manifest")
    if not isinstance(manifest, dict):
        raise ValueError("checkpoint lacks encoder manifest")
    agent.encoder.verify_schema_manifest(manifest)
    if checkpoint.get("metadata", {}).get("decision_labels") != list(DECISIONS):
        raise ValueError("checkpoint decision label mapping is incompatible")
    if checkpoint.get("metadata", {}).get("diagnosis_labels") != list(DIAGNOSES):
        raise ValueError("checkpoint diagnosis label mapping is incompatible")
    agent._materialize(); agent.load_state_dict(checkpoint["state_dict"])
    return checkpoint["metadata"]
