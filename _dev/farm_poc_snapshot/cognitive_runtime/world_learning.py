"""Bounded self-supervised next-observation prediction on ordinary Matrix runs."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

import torch
from torch import nn

from farm_poc.behavior_cloning.dataset import SequentialEpisode, TrainingDataset
from farm_poc.behavior_cloning.splits import (create_episode_split, partition_episodes,
                                               save_episode_split, validate_episode_split)
from farm_poc.cognitive_runtime.agent import CTMRuntimeAgent
from farm_poc.cognitive_runtime.encoder import EncodedInputV1, OperationalEncoderV1
from farm_poc.training.environment import MatrixEnvironment
from farm_poc.training.runner import episode_seed

NORMAL_ENVIRONMENT_PROFILES = (
    {"outside_temperature_c": 15.0, "outside_relative_humidity_pct": 38.0},
    {"outside_temperature_c": 22.0, "outside_relative_humidity_pct": 50.0},
    {"outside_temperature_c": 29.0, "outside_relative_humidity_pct": 64.0},
    {"outside_temperature_c": 18.0, "outside_relative_humidity_pct": 72.0},
    {"outside_temperature_c": 26.0, "outside_relative_humidity_pct": 42.0},
    {"outside_temperature_c": 12.0, "outside_relative_humidity_pct": 58.0},
    {"outside_temperature_c": 31.0, "outside_relative_humidity_pct": 48.0},
    {"outside_temperature_c": 20.0, "outside_relative_humidity_pct": 66.0},
)


@dataclass
class WorldPredictionConfig:
    schema_version: int = 1
    seed: int = 20261015
    learning_rate: float = 1e-3
    gradient_clip_norm: float = 1.0
    episodes: int = 3
    ticks_per_episode: int = 24
    evaluation_fraction: float = 0.34
    step_seconds: float = 1.0


class NextObservationHead(nn.Module):
    """Maps a CTM synchronization representation to 76 encoder features."""
    def __init__(self, input_dimension: int, output_dimension: int):
        super().__init__()
        self.projection = nn.Linear(input_dimension, output_dimension)

    def forward(self, representation: torch.Tensor) -> torch.Tensor:
        return torch.sigmoid(self.projection(representation))


def target_validity_mask(encoded: EncodedInputV1) -> tuple[float, ...]:
    """Mask absent measurements while still learning explicit presence/valid bits."""
    if len(encoded.feature_names) != len(encoded.validity_mask):
        raise ValueError("encoder feature and validity dimensions differ")
    return tuple(1.0 if name.endswith(".valid") or name.endswith(".present") else float(valid)
                 for name, valid in zip(encoded.feature_names, encoded.validity_mask, strict=True))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _module_hash(module: nn.Module) -> str:
    digest = hashlib.sha256()
    for name, tensor in sorted(module.state_dict().items()):
        digest.update(name.encode())
        digest.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def _state_hash(agent: CTMRuntimeAgent, head: NextObservationHead) -> str:
    return _digest(f"{_module_hash(agent.ctm)}:{_module_hash(head)}".encode())


def _generate_benign_episodes(config: WorldPredictionConfig):
    """Run finite normal Matrix episodes and retain only public observations."""
    output = []
    hashes = {}
    feature_hashes = {}
    profiles = {}
    observation_rows = []
    for index in range(config.episodes):
        seed = episode_seed(config.seed, index)
        profile = NORMAL_ENVIRONMENT_PROFILES[index]
        environment = MatrixEnvironment(seed=seed, config={"step_seconds": config.step_seconds,
            "environment": profile},
                                        max_ticks=config.ticks_per_episode)
        observation = environment.reset("benign", seed=seed)
        observations = [observation]
        for _ in range(config.ticks_per_episode):
            observation, done, _ = environment.step()
            observations.append(observation)
            if done and len(observations) != config.ticks_per_episode + 1:
                raise RuntimeError("Matrix ended a benign episode before its configured limit")
        episode_id = f"normal-{index:03d}-{seed}"
        rows = [{"episode_id": episode_id, "observation_index": i,
                 "simulation_tick": int(obs["incubator_1_direct_observation"]["tick"]),
                 "observation": obs} for i, obs in enumerate(observations)]
        stream = "".join(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
                         for row in rows).encode("utf-8")
        hashes[episode_id] = _digest(stream)
        encoder = OperationalEncoderV1()
        feature_sequence = [[list(encoder.encode(obs).values),
                             list(encoder.encode(obs).validity_mask)] for obs in observations]
        feature_hashes[episode_id] = _digest(json.dumps(feature_sequence, separators=(",", ":"),
                                                        allow_nan=False).encode())
        profiles[episode_id] = dict(profile)
        observation_rows.extend(rows)
        # targets is intentionally empty: the splitter receives only IDs and
        # observation-derived identities, never expert labels.
        output.append(SequentialEpisode(episode_id, feature_hashes[episode_id],
            {"source": "simulated_matrix_public_observations"}, tuple(observations), (), ()))
    return tuple(output), hashes, feature_hashes, profiles, observation_rows


def _advance_cmm(agent: CTMRuntimeAgent, encoded: EncodedInputV1, observation: dict) -> None:
    agent.cmm.record_observation(
        {"encoded_feature_count": len(encoded.values),
         "valid_feature_count": int(sum(target_validity_mask(encoded)))},
        simulation_tick=int(observation["incubator_1_direct_observation"]["tick"]),
        tags=("world_prediction",))


def _run_episode_loss(agent: CTMRuntimeAgent, head: NextObservationHead,
                      episode: SequentialEpisode, *, train: bool,
                      optimizer: torch.optim.Optimizer | None = None,
                      gradient_clip_norm: float = 1.0) -> dict[str, Any]:
    agent.begin_episode(episode.episode_id)
    state = agent._state
    numerator = torch.zeros((), device=agent.config.device)
    valid_count = 0.0
    transition_losses = []
    context_writes = 0
    if train and optimizer is None:
        raise ValueError("training pass requires an optimizer")
    if train:
        optimizer.zero_grad(set_to_none=True)

    manager = torch.enable_grad() if train else torch.no_grad()
    with manager:
        for index in range(len(episode.observations) - 1):
            current = agent.encode(episode.observations[index])
            future = agent.encode(episode.observations[index + 1])
            representation, state = agent.differentiable_representation(current, state)
            prediction = head(representation)
            target_mask = torch.tensor(target_validity_mask(future), dtype=torch.float32,
                                       device=agent.config.device).unsqueeze(0)
            target_values = torch.tensor(future.values, dtype=torch.float32,
                                         device=agent.config.device).unsqueeze(0)
            squared = (prediction - target_values).square()
            count = float(target_mask.sum().detach())
            if count <= 0:
                raise ValueError(f"no valid next-observation targets at {episode.episode_id}:{index}")
            tick_loss = (squared * target_mask).sum()
            numerator = numerator + tick_loss
            valid_count += count
            transition_losses.append(float((tick_loss.detach() / count).cpu()))
            _advance_cmm(agent, current, episode.observations[index])
            context_writes += 1

        # Consume the terminal observation with the same parameter version as
        # the rest of this episode, even though it has no next-step target.
        final_observation = episode.observations[-1]
        final_encoded = agent.encode(final_observation)
        with torch.no_grad():
            _, state = agent.differentiable_representation(final_encoded, state)
        _advance_cmm(agent, final_encoded, final_observation)
        context_writes += 1

        loss = numerator / valid_count
        if not torch.isfinite(loss):
            raise FloatingPointError("non-finite next-observation loss")
        if train:
            loss.backward()
            parameters = [parameter for parameter in (*agent.ctm.parameters(), *head.parameters())
                          if parameter.grad is not None]
            if not parameters or any(not torch.isfinite(parameter.grad).all() for parameter in parameters):
                raise FloatingPointError("missing or non-finite prediction gradients")
            torch.nn.utils.clip_grad_norm_(parameters, gradient_clip_norm)
            optimizer.step()
    return {"episode_id": episode.episode_id, "transitions": len(transition_losses),
            "observations_processed": len(episode.observations),
            "valid_target_features": int(valid_count), "masked_target_features":
            len(transition_losses) * len(agent.encoder.feature_names) - int(valid_count),
            "mse": float(loss.detach().cpu()), "cmm_observation_writes": context_writes}


def _write_atomic(path: Path, payload: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def run_world_learning_experiment(*, output_dir: str | Path,
                                  config: WorldPredictionConfig = WorldPredictionConfig()) -> dict[str, Any]:
    if config.episodes < 2 or config.episodes > 8:
        raise ValueError("world-learning pilot requires 2 to 8 complete episodes")
    if config.ticks_per_episode < 1 or config.ticks_per_episode > 48:
        raise ValueError("ticks_per_episode must be between 1 and 48")
    if not 0.0 < config.evaluation_fraction < 1.0:
        raise ValueError("evaluation_fraction must be between 0 and 1")

    started = time.perf_counter()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    episodes, stream_hashes, feature_hashes, profiles, observation_rows = _generate_benign_episodes(config)
    if len(set(feature_hashes.values())) != len(feature_hashes):
        raise ValueError("benign episodes have duplicate encoded observation trajectories; held-out evaluation would not be independent")
    dataset = TrainingDataset(episodes)
    split = create_episode_split(dataset, seed=config.seed,
                                 evaluation_fraction=config.evaluation_fraction)
    assignments = validate_episode_split(dataset, split)
    train_episodes = partition_episodes(dataset, split, "train")
    eval_episodes = partition_episodes(dataset, split, "evaluation")
    if not train_episodes or not eval_episodes:
        raise ValueError("episode split must contain both training and evaluation episodes")
    train_ids, eval_ids = set(assignments["train"]), set(assignments["evaluation"])
    if train_ids & eval_ids:
        raise AssertionError("episode leakage across training and evaluation partitions")
    features_by_id = dict(zip((episode.episode_id for episode in episodes),
                              (feature_hashes[episode.episode_id] for episode in episodes), strict=True))
    if {features_by_id[episode_id] for episode_id in train_ids} & {
            features_by_id[episode_id] for episode_id in eval_ids}:
        raise AssertionError("operational feature trajectory leakage across partitions")

    torch.manual_seed(config.seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    agent = CTMRuntimeAgent()
    agent._materialize()
    head = NextObservationHead(agent.ctm.synch_representation_size_out,
                               agent.encoder.dimension).to(agent.config.device)
    initial_agent_state = {key: value.detach().clone() for key, value in agent.state_dict().items()}
    initial_head_state = {key: value.detach().clone() for key, value in head.state_dict().items()}
    initial_hash = _state_hash(agent, head)
    initial_ctm_hash = _module_hash(agent.ctm)
    initial_head_hash = _module_hash(head)

    baseline_agent = CTMRuntimeAgent(config=agent.config)
    baseline_agent._materialize()
    baseline_agent.load_state_dict(initial_agent_state)
    baseline_head = NextObservationHead(agent.ctm.synch_representation_size_out,
                                        agent.encoder.dimension).to(agent.config.device)
    baseline_head.load_state_dict(initial_head_state)
    baseline_head.eval()
    baseline_agent.eval()
    eval_before = [_run_episode_loss(baseline_agent, baseline_head, episode, train=False)
                   for episode in eval_episodes]

    agent.train(); head.train()
    optimizer = torch.optim.Adam((*agent.ctm.parameters(), *head.parameters()), lr=config.learning_rate)
    history = []
    for epoch in range(1):
        for episode in train_episodes:
            metrics = _run_episode_loss(agent, head, episode, train=True, optimizer=optimizer,
                                        gradient_clip_norm=config.gradient_clip_norm)
            history.append({"epoch": epoch, **metrics})
    trained_hash = _state_hash(agent, head)
    trained_ctm_hash = _module_hash(agent.ctm)
    trained_head_hash = _module_hash(head)
    agent.eval(); head.eval()
    eval_after = [_run_episode_loss(agent, head, episode, train=False) for episode in eval_episodes]

    manifest_path = output / "episode-split.json"
    save_episode_split(manifest_path, split)
    observations_path = output / "matrix-observations.jsonl"
    observation_bytes = "".join(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
                                 for row in observation_rows).encode("utf-8")
    _write_atomic(observations_path, observation_bytes)
    history_payload = {"schema_version": 1, "objective": "masked next OperationalEncoderV1 observation MSE",
                       "training_episode_ids": list(assignments["train"]),
                       "evaluation_episode_ids": list(assignments["evaluation"]),
                       "training_updates": len(history), "history": history,
                       "evaluation_before": eval_before, "evaluation_after": eval_after}
    history_path = output / "training-history.json"
    _write_atomic(history_path, (json.dumps(history_payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())

    checkpoint = {"checkpoint_version": 1, "objective": "masked_next_observation_prediction",
                  "runtime_config": agent.config.__dict__, "model_type": "CTM-RL representation + linear 76-feature head",
                  "encoder_manifest": agent.encoder.schema_manifest(),
                  "target_feature_order": list(agent.encoder.feature_names),
                  "target_mask_rule": "mask unavailable measurement values; supervise .valid and .present indicators",
                  "cmm_config": {"working_limit": agent.cmm.working_limit,
                                 "episodic_limit": agent.cmm.episodic_limit},
                  "split_manifest_hash": split["manifest_hash"],
                  "dataset_identity": split["dataset_identity"],
                  "training_episode_ids": list(assignments["train"]),
                  "evaluation_episode_ids": list(assignments["evaluation"]),
                  "config": config.__dict__, "ctm_state_dict": agent.ctm.state_dict(),
                  "prediction_head_state_dict": head.state_dict(), "optimizer_state_dict": optimizer.state_dict()}
    checkpoint_path = output / "world-prediction-checkpoint.pt"
    torch.save(checkpoint, checkpoint_path.with_name(checkpoint_path.name + ".tmp"))
    os.replace(checkpoint_path.with_name(checkpoint_path.name + ".tmp"), checkpoint_path)

    def mean_mse(records):
        return sum(row["mse"] for row in records) / len(records)

    missing = sum(len(row["observation"]["scripted_peer_reports_to_jarvis"]) < 3
                  for row in observation_rows)
    report = {"report_schema_version": 1, "status": "complete",
        "objective": "predict next tick's 76 OperationalEncoderV1 features from current observation and CTM recurrent state",
        "data": {"source": "fresh simulated Matrix benign operation", "scenario": "benign",
                 "master_seed": config.seed, "episodes": config.episodes,
                 "ticks_per_episode": config.ticks_per_episode,
                 "simulation_timestep_seconds": config.step_seconds,
                 "observations": len(observation_rows), "transition_targets":
                 config.episodes * config.ticks_per_episode,
                 "episode_observation_sha256": stream_hashes,
                 "encoded_feature_sequence_sha256": feature_hashes,
                 "normal_environment_profiles": profiles,
                 "public_observation_file": str(observations_path),
                 "public_observation_file_sha256": _digest(observation_bytes),
                 "observations_with_fewer_than_three_peer_reports": missing},
        "split": {"manifest_path": str(manifest_path), "manifest_hash": split["manifest_hash"],
                  "training_episode_ids": list(assignments["train"]),
                  "evaluation_episode_ids": list(assignments["evaluation"]),
                  "disjoint": not bool(train_ids & eval_ids)},
        "model": {"upstream_core": "ContinuousThoughtMachineRL (unchanged)",
                  "wrapper": "CTMRuntimeAgent.differentiable_representation",
                  "encoder_version": agent.encoder.schema_manifest()["encoder_version"],
                  "encoder_schema_hash": agent.encoder.schema_hash,
                  "operational_input_features": agent.encoder.dimension,
                  "cmm_context_features": agent.encoder.CONTEXT_DIMENSION,
                  "model_input_dimension": agent.input_dimension,
                  "prediction_target_dimension": agent.encoder.dimension,
                  "ctm_internal_iterations": agent.config.iterations,
                  "checkpoint_path": str(checkpoint_path),
                  "checkpoint_sha256": _digest(checkpoint_path.read_bytes()),
                  "initial_trainable_weights_sha256": initial_hash,
                  "trained_weights_sha256": trained_hash,
                  "trainable_weights_changed": initial_hash != trained_hash,
                  "ctm_weights_sha256_before": initial_ctm_hash,
                  "ctm_weights_sha256_after": trained_ctm_hash,
                  "ctm_weights_changed": initial_ctm_hash != trained_ctm_hash,
                  "prediction_head_sha256_before": initial_head_hash,
                  "prediction_head_sha256_after": trained_head_hash,
                  "prediction_head_changed": initial_head_hash != trained_head_hash,
                  "decision_and_diagnosis_heads_trained": False},
        "training": {"optimizer": "Adam", "learning_rate": config.learning_rate,
                     "optimizer_updates": len(history), "training_episode_count": len(train_episodes),
                     "training_history_path": str(history_path), "history": history,
                     "masked_target_features": sum(row["masked_target_features"] for row in history),
                     "valid_target_features": sum(row["valid_target_features"] for row in history)},
        "held_out_prediction": {"metric": "masked mean squared error on normalized encoder features",
            "evaluation_episode_count": len(eval_episodes), "before": eval_before,
            "after": eval_after, "mean_mse_before": mean_mse(eval_before),
            "mean_mse_after": mean_mse(eval_after),
            "improved": mean_mse(eval_after) < mean_mse(eval_before),
            "held_out_episode_ids_used_during_training": sorted(train_ids & eval_ids)},
        "memory": {"CMM_policy": "working-memory observed-fact writes only; no expert labels, decisions, or rule promotion",
                   "training_observation_writes": sum(row["cmm_observation_writes"] for row in history),
                   "evaluation_before_writes": sum(row["cmm_observation_writes"] for row in eval_before),
                   "evaluation_after_writes": sum(row["cmm_observation_writes"] for row in eval_after)},
        "limitations": ["This tests one-step prediction on a tiny deterministic benign curriculum, not diagnosis or emergency behavior.",
                        "Held-out results cover only the generated episode seeds and one benign configuration.",
                        "CMM context is an explicit bounded working-memory summary; no semantic learning or rule promotion occurs.",
                        "A lower held-out prediction error does not establish useful agent decisions or general intelligence."],
        "elapsed_seconds": time.perf_counter() - started}
    report_path = output / "world-learning-report.json"
    _write_atomic(report_path, (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runs/world-learning-pilot"))
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--ticks", type=int, default=24)
    parser.add_argument("--seed", type=int, default=20261015)
    # Keep Matrix's existing default simulated timestep. Acceleration comes
    # from stepping without wall-clock sleeps, not from changing simulation time.
    parser.add_argument("--step-seconds", type=float, default=1.0)
    parser.add_argument("--evaluation-fraction", type=float, default=0.34)
    args = parser.parse_args(argv)
    config = WorldPredictionConfig(seed=args.seed, episodes=args.episodes,
        ticks_per_episode=args.ticks, step_seconds=args.step_seconds,
        evaluation_fraction=args.evaluation_fraction)
    try:
        report = run_world_learning_experiment(output_dir=args.output, config=config)
    except Exception as error:
        parser.exit(1, f"world-learning pilot failed: {type(error).__name__}: {error}\n")
    print(json.dumps({"status": report["status"], "report": str(args.output / "world-learning-report.json"),
                      "train_episodes": len(report["split"]["training_episode_ids"]),
                      "evaluation_episodes": len(report["split"]["evaluation_episode_ids"]),
                      "mse_before": report["held_out_prediction"]["mean_mse_before"],
                      "mse_after": report["held_out_prediction"]["mean_mse_after"],
                      "improved": report["held_out_prediction"]["improved"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
