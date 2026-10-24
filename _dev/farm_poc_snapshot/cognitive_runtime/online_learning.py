"""Online one-step world prediction and per-observation Jarvis decisions."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any

import torch

from .agent import CTMRuntimeAgent
from .contracts import CandidateDecisionV1
from .world_learning import NextObservationHead, target_validity_mask


@dataclass(frozen=True)
class OnlineLearningConfig:
    schema_version: int = 1
    seed: int = 20261016
    learning_rate: float = 1e-3
    gradient_clip_norm: float = 1.0
    checkpoint_interval_updates: int = 5_000


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class OnlineWorldLearner:
    """Train next-observation prediction while processing every Jarvis input.

    Each transition's source representation is recomputed with gradients from
    the recurrent state and CMM context that existed before that observation.
    The recurrent state is carried sequentially and detached after each update
    (one-transition truncated BPTT).
    """

    CHECKPOINT_VERSION = 2

    def __init__(self, output_dir: str | Path, config: OnlineLearningConfig = OnlineLearningConfig(),
                 *, agent: CTMRuntimeAgent | None = None,
                 supervised_training_metadata: dict[str, Any] | None = None):
        if config.checkpoint_interval_updates < 1:
            raise ValueError("checkpoint_interval_updates must be positive")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.config = config
        torch.manual_seed(config.seed)
        torch.use_deterministic_algorithms(True, warn_only=True)
        self.agent = agent or CTMRuntimeAgent()
        self.supervised_training_metadata = dict(supervised_training_metadata or {})
        self.agent._materialize()
        self.head = NextObservationHead(self.agent.ctm.synch_representation_size_out,
                                        self.agent.encoder.dimension).to(self.agent.config.device)
        self.optimizer = torch.optim.Adam((*self.agent.ctm.parameters(), *self.head.parameters()), lr=config.learning_rate)
        self._state_before_source = None
        self._source_context: tuple[float, ...] | None = None
        self._previous_observation: dict[str, Any] | None = None
        self._episode_id: str | None = None
        self._last_candidate: CandidateDecisionV1 | None = None
        self._last_deliberation: dict[str, Any] | None = None
        self.errors: list[dict[str, str]] = []
        self.observations_processed = 0
        self.optimizer_updates = 0
        self.valid_target_features = 0
        self.masked_target_features = 0
        self.cmm_observation_writes = 0
        self.loss_sum = 0.0
        self.loss_count = 0
        self.recent_losses: deque[float] = deque(maxlen=1000)
        self.started = perf_counter()
        self.initial_weight_hash = self._weight_hash()
        self._observations_stream = None
        self._decisions_stream = None
        self._history_stream = None
        self._evaluator_stream = None
        self._open_streams()

    def _open_streams(self) -> None:
        self._observations_stream = gzip.open(self.output_dir / "observations.jsonl.gz", "at", encoding="utf-8")
        self._decisions_stream = gzip.open(self.output_dir / "decisions.jsonl.gz", "at", encoding="utf-8")
        self._history_stream = gzip.open(self.output_dir / "online-training-history.jsonl.gz", "at", encoding="utf-8")
        private_dir = self.output_dir / "evaluator-private"
        private_dir.mkdir(parents=True, exist_ok=True)
        self._evaluator_stream = gzip.open(private_dir / "trial-records.jsonl.gz", "at", encoding="utf-8")

    def _write(self, stream, row: dict[str, Any]) -> None:
        stream.write(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")

    def _weight_hash(self) -> str:
        digest = hashlib.sha256()
        for module in (self.agent.ctm, self.agent.decision_head, self.agent.diagnosis_head, self.head):
            for name, tensor in sorted(module.state_dict().items()):
                digest.update(name.encode("utf-8")); digest.update(tensor.detach().cpu().contiguous().numpy().tobytes())
        return digest.hexdigest()

    @staticmethod
    def _tick(observation: dict[str, Any]) -> int:
        return int(observation["incubator_1_direct_observation"]["tick"])

    def _receive_observation(self, observation: dict[str, Any], *, episode_id: str,
                             evaluator_private: dict[str, Any] | None = None) -> None:
        """Run the trained/inference heads, save features, and update governed CMM."""
        encoded = self.agent.encode(observation)
        state_before = self.agent._state
        context_before = tuple(self.agent.cmm.context_vector())
        candidate, deliberation = self.agent.process(observation)
        tick = self._tick(observation)
        self._state_before_source = tuple(value.detach().clone() for value in state_before)
        self._source_context = context_before
        self._previous_observation = observation
        self._last_candidate = candidate
        self._last_deliberation = {
            "ctm_iterations": deliberation.ctm_iterations_executed, "inference_time_ns": deliberation.inference_time_ns,
            "cmm_retrieval_count": deliberation.cmm_retrieval_count,
            "cmm_write_ids": list(deliberation.cmm_write_operations),
        }
        self.observations_processed += 1
        self._write(self._observations_stream, {"episode_id": episode_id, "observation_index": self.observations_processed - 1,
                                                "observation": observation})
        self._write(self._decisions_stream, {"episode_id": episode_id, "observation_index": self.observations_processed - 1,
                                             "simulation_tick": tick, "candidate": asdict(candidate),
                                             "evidence_trace": {"valid_encoder_features": [name for name, valid in zip(encoded.feature_names, encoded.validity_mask) if valid],
                                                                 "peer_sources": sorted(observation["scripted_peer_reports_to_jarvis"]),
                                                                 "peer_sample_ticks": {key: value.get("sample_tick") for key, value in observation["scripted_peer_reports_to_jarvis"].items()},
                                                                 "available_operational_fields": list(encoded.feature_names)},
                                             "deliberation": self._last_deliberation,
                                             "cmm_policy": "governed working observation and hypothesis writes; no permanent promotion"})
        if evaluator_private is not None:
            self._write(self._evaluator_stream, {"episode_id": episode_id, "simulation_tick": tick,
                                                 **evaluator_private})
        self.cmm_observation_writes += 1

    def begin_episode(self, episode_id: str, observation: dict[str, Any], *,
                      evaluator_private: dict[str, Any] | None = None) -> None:
        if self._episode_id is not None:
            self.finalize_episode()
        self.agent.begin_episode(episode_id)
        self._episode_id = episode_id
        self._receive_observation(observation, episode_id=episode_id, evaluator_private=evaluator_private)

    def observe(self, observation: dict[str, Any], *, episode_id: str,
                evaluator_private: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Train on the prior public input predicting this one; then process it."""
        if self._episode_id != episode_id or self._previous_observation is None:
            self.begin_episode(episode_id, observation, evaluator_private=evaluator_private)
            return None
        source = self.agent.encode(self._previous_observation)
        target = self.agent.encode(observation)
        state = self._state_before_source
        self.agent.train(); self.head.train(); self.optimizer.zero_grad(set_to_none=True)
        representation, _ = self.agent.differentiable_representation(source, state, self._source_context)
        prediction = self.head(representation)
        mask = torch.tensor(target_validity_mask(target), dtype=torch.float32,
                            device=self.agent.config.device).unsqueeze(0)
        values = torch.tensor(target.values, dtype=torch.float32, device=self.agent.config.device).unsqueeze(0)
        valid_count = int(mask.sum().detach())
        if valid_count <= 0:
            raise ValueError("no valid next-observation prediction targets")
        loss = ((prediction - values).square() * mask).sum() / valid_count
        if not torch.isfinite(loss):
            raise FloatingPointError("non-finite online prediction loss")
        mse_before_update = float(loss.detach().cpu())
        loss.backward()
        params = [p for p in (*self.agent.ctm.parameters(), *self.head.parameters()) if p.grad is not None]
        if not params or any(not torch.isfinite(p.grad).all() for p in params):
            raise FloatingPointError("missing or non-finite online prediction gradients")
        torch.nn.utils.clip_grad_norm_(params, self.config.gradient_clip_norm)
        self.optimizer.step()
        source_tick = self._tick(self._previous_observation)
        row = {"update": self.optimizer_updates + 1, "episode_id": episode_id,
               "source_observation_index": self.observations_processed - 1,
               "source_simulation_tick": source_tick, "target_simulation_tick": self._tick(observation),
               "mse_before_update": mse_before_update, "valid_target_features": valid_count,
               "masked_target_features": len(target.values) - valid_count,
               "cmm_operation": "working_observed_fact_write",
               "objective": "masked next OperationalEncoderV1 feature MSE"}
        self.optimizer_updates += 1; self.valid_target_features += valid_count
        self.masked_target_features += len(target.values) - valid_count
        self.loss_sum += mse_before_update; self.loss_count += 1; self.recent_losses.append(mse_before_update)
        self._write(self._history_stream, row)
        # Retain observer's state from processing source, detach at each update,
        # then process target exactly once under the same episode's live context.
        self.agent._state = tuple(v.detach() for v in self.agent._state)
        self.agent.eval(); self.head.eval()
        self._receive_observation(observation, episode_id=episode_id, evaluator_private=evaluator_private)
        if self.optimizer_updates % self.config.checkpoint_interval_updates == 0:
            self.persist(status="running", finalize=False)
        return row

    def finalize_episode(self) -> None:
        if self._previous_observation is None:
            return
        self._write(self._evaluator_stream, {"episode_id": self._episode_id, "episode_boundary_tick": self._tick(self._previous_observation)})
        self._previous_observation = None
        self._state_before_source = None
        self._source_context = None

    def _report(self, status: str) -> dict[str, Any]:
        memory = self.agent.cmm.diagnostics()
        return {"schema_version": 2, "status": status,
                "objective": "online masked next OperationalEncoderV1 observation MSE",
                "decision_and_diagnosis_head_training": "pretrained from the linked provisional supervised checkpoint; not updated online",
                "learning_scope": "online prediction metrics are not held-out generalization evidence",
                "data_boundary": "allowlisted Matrix public observations only",
                "model": {"upstream_core": "ContinuousThoughtMachineRL unchanged",
                          "encoder_schema_hash": self.agent.encoder.schema_hash,
                          "decision_diagnosis_head_checkpoint_linked": bool(self.supervised_training_metadata),
                          "supervised_training": self.supervised_training_metadata,
                          "initial_trainable_weights_sha256": self.initial_weight_hash,
                          "current_trainable_weights_sha256": self._weight_hash(),
                          "trainable_weights_changed": self.initial_weight_hash != self._weight_hash()},
                "training": {"optimizer": "Adam", "learning_rate": self.config.learning_rate,
                             "optimizer_updates": self.optimizer_updates,
                             "observations_processed": self.observations_processed,
                             "valid_target_features": self.valid_target_features,
                             "masked_target_features": self.masked_target_features,
                             "prediction_error_count": self.loss_count,
                             "prediction_error_mean": self.loss_sum / self.loss_count if self.loss_count else None,
                             "prediction_error_recent_1000_mean": sum(self.recent_losses) / len(self.recent_losses) if self.recent_losses else None,
                             "prediction_errors": [{"mse_before_update": value,
                                                    "cmm_operation": "working_observed_fact_write"}
                                                   for value in self.recent_losses]},
                "memory": {"policy": "working observation/hypothesis writes; no permanent promotion",
                           "observation_writes": self.cmm_observation_writes,
                           "working_entries": memory["working_count"], "episodic_entries": memory["episodic_count"],
                           "semantic_keys": memory["semantic_keys"], "procedural_keys": memory["procedural_keys"],
                           "mandatory_rule_keys": memory["mandatory_rule_keys"]},
                "elapsed_seconds": perf_counter() - self.started, "errors": list(self.errors)}

    def persist(self, *, status: str, finalize: bool = True) -> dict[str, Any]:
        if finalize:
            self.finalize_episode()
        path = self.output_dir / "online-world-learning-checkpoint.pt"
        temporary = path.with_name(path.name + ".tmp")
        torch.save({"checkpoint_version": self.CHECKPOINT_VERSION,
                    "runtime_config": asdict(self.agent.config), "online_config": asdict(self.config),
                    "encoder_manifest": self.agent.encoder.schema_manifest(),
                    "ctm_state_dict": self.agent.ctm.state_dict(),
                    "decision_head_state_dict": self.agent.decision_head.state_dict(),
                    "diagnosis_head_state_dict": self.agent.diagnosis_head.state_dict(),
                    "prediction_head_state_dict": self.head.state_dict(),
                    "optimizer_state_dict": self.optimizer.state_dict(), "recurrent_state": self.agent._state,
                    "cmm_state": self.agent.cmm.state_dict(), "episode_id": self._episode_id,
                    "previous_observation": self._previous_observation,
                    "state_before_source": self._state_before_source, "source_context": self._source_context,
                    "observations_processed": self.observations_processed, "optimizer_updates": self.optimizer_updates,
                    "valid_target_features": self.valid_target_features, "masked_target_features": self.masked_target_features,
                    "cmm_observation_writes": self.cmm_observation_writes, "loss_sum": self.loss_sum,
                    "loss_count": self.loss_count, "recent_losses": list(self.recent_losses),
                    "initial_weight_hash": self.initial_weight_hash,
                    "supervised_training_metadata": self.supervised_training_metadata}, temporary)
        os.replace(temporary, path)
        self._seal_streams()
        report = self._report(status)
        report["artifacts"] = {name: {"path": str(self.output_dir / filename),
                                     "sha256": _sha256(self.output_dir / filename) if (self.output_dir / filename).exists() else None}
                               for name, filename in (("checkpoint", path.name), ("observations", "observations.jsonl.gz"),
                                                      ("decisions", "decisions.jsonl.gz"),
                                                      ("history", "online-training-history.jsonl.gz"),
                                                      ("evaluator_private", "evaluator-private/trial-records.jsonl.gz"))}
        _atomic_json(self.output_dir / "online-learning-report.json", report)
        if not finalize:
            self._open_streams()
        return report

    def _seal_streams(self) -> None:
        streams = (self._observations_stream, self._decisions_stream, self._history_stream, self._evaluator_stream)
        for stream in streams:
            if stream is not None:
                stream.flush(); stream.close()
        self._observations_stream = self._decisions_stream = self._history_stream = self._evaluator_stream = None

    def close(self) -> None:
        self._seal_streams()

    @classmethod
    def restore(cls, output_dir: str | Path, checkpoint: str | Path) -> "OnlineWorldLearner":
        saved = torch.load(checkpoint, map_location="cpu", weights_only=False)
        if saved.get("checkpoint_version") != cls.CHECKPOINT_VERSION:
            raise ValueError("unsupported online learning checkpoint")
        config = OnlineLearningConfig(**saved["online_config"])
        agent = CTMRuntimeAgent()
        if saved["encoder_manifest"] != agent.encoder.schema_manifest():
            raise ValueError("checkpoint encoder schema mismatch")
        agent._materialize()
        agent.ctm.load_state_dict(saved["ctm_state_dict"])
        agent.decision_head.load_state_dict(saved["decision_head_state_dict"])
        agent.diagnosis_head.load_state_dict(saved["diagnosis_head_state_dict"])
        agent._state = saved["recurrent_state"]
        agent.cmm.load_state_dict(saved["cmm_state"])
        agent._episode_active = saved["episode_id"] is not None
        learner = cls(output_dir, config, agent=agent)
        learner.head.load_state_dict(saved["prediction_head_state_dict"])
        learner.optimizer.load_state_dict(saved["optimizer_state_dict"])
        learner._episode_id = saved["episode_id"]
        learner._previous_observation = saved["previous_observation"]
        learner._state_before_source = saved["state_before_source"]
        learner._source_context = tuple(saved["source_context"]) if saved["source_context"] is not None else None
        for key in ("observations_processed", "optimizer_updates", "valid_target_features", "masked_target_features",
                    "cmm_observation_writes", "loss_sum", "loss_count"):
            setattr(learner, key, saved[key])
        learner.recent_losses = deque(saved["recent_losses"], maxlen=1000)
        learner.initial_weight_hash = saved["initial_weight_hash"]
        learner.supervised_training_metadata = dict(saved.get("supervised_training_metadata", {}))
        return learner
