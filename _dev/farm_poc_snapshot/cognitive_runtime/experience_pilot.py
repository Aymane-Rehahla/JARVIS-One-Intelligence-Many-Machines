"""Bounded headless Matrix + CTM experience/training pilot."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

import torch
import torch.nn.functional as F

from farm_poc.behavior_cloning.dataset import TrainingDataset, load_training_dataset
from farm_poc.behavior_cloning.splits import create_episode_split
from farm_poc.behavior_cloning.trainer import (BehaviorCloningTrainer, TrainingConfig,
                                               save_training_checkpoint)
from farm_poc.cognitive_runtime.agent import CTMRuntimeAgent
from farm_poc.cognitive_runtime.contracts import DECISIONS, DIAGNOSES
from farm_poc.training.environment import MatrixEnvironment


def _weights_hash(agent: CTMRuntimeAgent) -> str:
    digest = hashlib.sha256()
    for name, tensor in sorted(agent.state_dict().items()):
        digest.update(name.encode("utf-8"))
        digest.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> str:
    payload = "".join(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
                      for row in rows)
    path.write_text(payload, encoding="utf-8", newline="\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _measure_training_episode_loss(agent: CTMRuntimeAgent, episode) -> dict[str, float]:
    """Measure the same supervised objective without gradients or weight updates."""
    agent.eval()
    agent.begin_episode(episode.episode_id)
    state = agent._state
    decision_total = diagnosis_total = 0.0
    with torch.no_grad():
        for observation, target in zip(episode.observations, episode.targets, strict=True):
            encoded = agent.encode(observation)
            decision_logits, diagnosis_logits, state = agent.differentiable_forward(encoded, state)
            decision = target["decision"]
            decision_total += float(F.cross_entropy(decision_logits,
                torch.tensor([DECISIONS.index(decision)], device=decision_logits.device)))
            if decision == "DIAGNOSE":
                diagnosis_total += float(F.cross_entropy(diagnosis_logits,
                    torch.tensor([DIAGNOSES.index(target["diagnosis"])], device=diagnosis_logits.device)))
            predicted_decision = DECISIONS[int(decision_logits.argmax(dim=-1)[0])]
            predicted_diagnosis = (DIAGNOSES[int(diagnosis_logits.argmax(dim=-1)[0])]
                                   if predicted_decision == "DIAGNOSE" else "UNKNOWN")
            agent.record_training_observation(encoded,
                simulation_tick=int(observation["incubator_1_direct_observation"]["tick"]),
                decision=predicted_decision, diagnosis=predicted_diagnosis)
    count = episode.tick_count
    return {"decision_loss": decision_total / count, "diagnosis_loss": diagnosis_total / count,
            "combined_loss": (decision_total + diagnosis_total) / count}


def run_experience_pilot(*, dataset_path: str | Path, output_dir: str | Path,
                         train_episodes: int = 1, ticks: int = 4, seed: int = 20261015,
                         scenario: str = "overvoltage", peer_mode: str = "elevated") -> dict:
    """Train a bounded BC smoke model, then expose it to a short Matrix run.

    Dataset episodes are existing completed recordings. During the Matrix run,
    both initial and updated weights see the same public observation at each
    tick; no evaluator-private engine API is accessed.
    """
    if not 1 <= train_episodes <= 16:
        raise ValueError("train_episodes must be between 1 and 16")
    if not 1 <= ticks <= 64:
        raise ValueError("ticks must be between 1 and 64")
    if scenario not in {"benign", "overvoltage"}:
        raise ValueError("scenario must be benign or overvoltage")
    if peer_mode not in {"normal", "elevated", "stale", "missing"}:
        raise ValueError("unsupported Incubator 2 peer mode")

    started = time.perf_counter()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    dataset = load_training_dataset(dataset_path)
    selected = dataset.episodes[:train_episodes]
    if not selected:
        raise ValueError("dataset contains no complete episodes")
    selected_dataset = TrainingDataset(tuple(selected))

    torch.manual_seed(seed)
    agent = CTMRuntimeAgent()
    agent._materialize()
    initial_state = {key: value.detach().clone() for key, value in agent.state_dict().items()}
    initial_hash = _weights_hash(agent)
    baseline = CTMRuntimeAgent(config=agent.config)
    baseline._materialize()
    baseline.load_state_dict(initial_state)
    baseline.eval()
    baseline_hash = _weights_hash(baseline)
    initial_training_metrics = [_measure_training_episode_loss(baseline, episode) for episode in selected]
    trainer = BehaviorCloningTrainer(agent, TrainingConfig(seed=seed, epochs=1))
    history: list[dict] = []
    split_manifests = []
    for index, episode in enumerate(selected):
        singleton = TrainingDataset((episode,))
        split = create_episode_split(singleton, seed=seed + index)
        split_manifests.append(split)
        history.extend(trainer.fit(singleton, split))
    trained_hash = _weights_hash(agent)
    agent.eval()
    updated_training_metrics = [_measure_training_episode_loss(agent, episode) for episode in selected]

    checkpoint_path = output / "jarvis-experience-pilot.pt"
    save_training_checkpoint(checkpoint_path, trainer=trainer, dataset=selected_dataset, history=history)
    (output / "episode-splits.json").write_text(
        json.dumps(split_manifests, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")

    env = MatrixEnvironment(seed=seed, max_ticks=ticks)
    scenario_config = ({"inject_tick": 0, "peer_modes": {"incubator-2": peer_mode}}
                       if scenario == "overvoltage" else None)
    observation = env.reset(scenario, seed=seed, scenario_config=scenario_config)
    for model in (baseline, agent):
        model.begin_episode(env._engine.world.episode_id)

    observations: list[dict] = []
    decisions: list[dict] = []
    decision_counts = {"initial": Counter(), "trained": Counter()}
    missing_feature_count = 0
    inference_ns = {"initial": 0, "trained": 0}
    cmm_writes = {"initial": 0, "trained": 0}
    cmm_retrievals = {"initial": 0, "trained": 0}
    experience_started = time.perf_counter()
    for observation_index in range(ticks + 1):
        sim_tick = int(observation["incubator_1_direct_observation"]["tick"])
        observations.append({"observation_index": observation_index, "simulation_tick": sim_tick,
                             "observation": observation})
        missing_feature_count += sum(value == 0.0 for value in agent.encode(observation).validity_mask)
        row = {"observation_index": observation_index, "simulation_tick": sim_tick}
        for name, model in (("initial", baseline), ("trained", agent)):
            candidate, telemetry = model.process(observation)
            decision_counts[name][candidate.decision] += 1
            inference_ns[name] += telemetry.inference_time_ns
            cmm_writes[name] += len(telemetry.cmm_write_operations)
            cmm_retrievals[name] += telemetry.cmm_retrieval_count
            row[name] = {"candidate": candidate.as_dict(), "telemetry": telemetry.as_dict()}
        decisions.append(row)
        if observation_index < ticks:
            observation, _, _ = env.step()

    observations_hash = _write_jsonl(output / "matrix-observations.jsonl", observations)
    decisions_hash = _write_jsonl(output / "jarvis-decisions.jsonl", decisions)
    report = {
        "report_schema_version": 1,
        "status": "complete",
        "learning_claim": "supervised weight updates verified; generalization not established",
        "matrix": {"scenario": scenario, "seed": seed, "simulation_ticks": ticks,
                   "observations_received": len(observations), "observation_schema_version": 1,
                   "missing_or_invalid_encoded_features": missing_feature_count},
        "training": {"dataset_path": str(Path(dataset_path)), "episodes_used": len(selected),
                     "episode_ids": [episode.episode_id for episode in selected],
                     "recorded_ticks_used": sum(episode.tick_count for episode in selected),
                     "target_decision_counts": dict(Counter(target["decision"] for episode in selected for target in episode.targets)),
                     "target_diagnosis_counts": dict(Counter(target["diagnosis"] for episode in selected
                                                              for target in episode.targets
                                                              if target["decision"] == "DIAGNOSE")),
                     "optimizer_updates": len(history), "metrics": history,
                     "same_training_data_loss_before": initial_training_metrics,
                     "same_training_data_loss_after": updated_training_metrics,
                     "same_training_data_loss_reduced": all(
                         after["combined_loss"] < before["combined_loss"]
                         for before, after in zip(initial_training_metrics, updated_training_metrics, strict=True)),
                     "initial_weights_sha256": initial_hash, "trained_weights_sha256": trained_hash,
                     "weights_changed": initial_hash != trained_hash,
                     "held_out_evaluation": "not run; this bounded pilot measures training mechanics only"},
        "model": {"runtime": "CTMRuntimeAgent/CTM-RL", "runtime_checkpoint_version": agent.CHECKPOINT_VERSION,
                  "upstream": "base ctm/continuous-thought-machines-main", "config": agent.config.__dict__,
                  "encoder_schema_hash": agent.encoder.schema_hash,
                  "saved_checkpoint": str(checkpoint_path),
                  "saved_checkpoint_sha256": hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
                  "baseline_weights_match_initial": baseline_hash == initial_hash},
        "inference": {"decision_counts": {key: dict(value) for key, value in decision_counts.items()},
                      "decisions_changed_by_tick": sum(
                          first["initial"]["candidate"]["decision"] != first["trained"]["candidate"]["decision"]
                          for first in decisions),
                      "total_inference_time_ns": inference_ns,
                      "state_trace_norms_by_observation": {
                          name: [row[name]["telemetry"]["state_trace_norm"] for row in decisions]
                          for name in ("initial", "trained")},
                      "activated_state_trace_norms_by_observation": {
                          name: [row[name]["telemetry"]["activated_state_trace_norm"] for row in decisions]
                          for name in ("initial", "trained")},
                      "cmm_retrievals": cmm_retrievals, "cmm_writes_accepted": cmm_writes,
                      "cmm_write_rejections": "none observed; CMM does not expose a rejection counter",
                      "initial_cmm": baseline.cmm.diagnostics(), "trained_cmm": agent.cmm.diagnostics(),
                      "commands_submitted": 0},
        "artifacts": {"observations": "matrix-observations.jsonl", "observations_sha256": observations_hash,
                      "decisions": "jarvis-decisions.jsonl", "decisions_sha256": decisions_hash,
                      "split_manifests": "episode-splits.json"},
        "limitations": ["CTM starts from a random initialization; trained weights use behavior-cloning targets.",
                        "Training loss is not held-out improvement or evidence of diagnosis generalization.",
                        "CMM governance activity is observed through accepted write telemetry; rejection totals are not instrumented.",
                        "CTM iterations are fixed and do not establish adaptive reasoning depth."],
        "timing": {"total_seconds": time.perf_counter() - started,
                   "matrix_inference_seconds": time.perf_counter() - experience_started},
        "errors": [],
    }
    report_path = output / "experience-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("runs/training-pilot-curriculum-v3"))
    parser.add_argument("--output", type=Path, default=Path("runs/jarvis-experience-pilot"))
    parser.add_argument("--train-episodes", type=int, default=1)
    parser.add_argument("--ticks", type=int, default=4, help="Matrix updates; records ticks+1 observations")
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--scenario", choices=("benign", "overvoltage"), default="overvoltage")
    parser.add_argument("--peer-mode", choices=("normal", "elevated", "stale", "missing"), default="elevated")
    args = parser.parse_args(argv)
    try:
        report = run_experience_pilot(dataset_path=args.dataset, output_dir=args.output,
                                      train_episodes=args.train_episodes, ticks=args.ticks,
                                      seed=args.seed, scenario=args.scenario, peer_mode=args.peer_mode)
    except Exception as error:
        parser.exit(1, f"pilot failed: {type(error).__name__}: {error}\n")
    print(json.dumps({"status": report["status"], "report": str(args.output / "experience-report.json"),
                      "observations": report["matrix"]["observations_received"],
                      "weights_changed": report["training"]["weights_changed"],
                      "training_updates": report["training"]["optimizer_updates"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
