"""Per-episode generation with deterministic index-to-seed assignment."""
from __future__ import annotations

import hashlib
import time
from collections import Counter

from .environment import MatrixEnvironment
from .expert_policy import ExpertPolicy
from .recorder import EpisodeRecorder


def episode_seed(master_seed: int, episode_index: int) -> int:
    digest = hashlib.sha256(f"farm-training-v1:{int(master_seed)}:{int(episode_index)}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def run_episode(*, environment: MatrixEnvironment, policy: ExpertPolicy,
                scenario_id: str, seed: int, episode_index: int,
                output_dir=None, scenario_config: dict | None = None,
                recording: bool = True) -> dict:
    observation = environment.reset(scenario_id, seed, scenario_config)
    policy.reset()
    engine = environment._engine  # trusted offline recorder; never exposed to policy
    recorder = EpisodeRecorder(output_dir, engine.world.episode_id, seed=seed,
                               scenario_id=scenario_id, episode_index=episode_index,
                               scenario_config=scenario_config,
                               environment_config=environment._config) if recording else None
    labels = Counter()
    ticks = 0
    simulation_steps = 0
    started = time.perf_counter()
    try:
        while True:
            target, audit_trace = policy.decide_with_audit(observation)
            labels[target["decision"]] += 1
            if recorder:
                recorder.write_tick(ticks, observation, target, environment._private_episode_record(),
                                    expert_audit=audit_trace,
                                    simulation_tick=int(observation["incubator_1_direct_observation"]["tick"]),
                                    phase="reset" if ticks == 0 else "step")
            ticks += 1
            if environment._done:
                break
            observation, done, _ = environment.step()
            simulation_steps += 1
            if done:
                # Record the terminal observation on the next pass.
                continue
        if recorder:
            recorder.finalize(completed=True)
    except BaseException as exc:
        if recorder and not recorder.closed:
            recorder.fail(exc)
        raise
    elapsed = time.perf_counter() - started
    return {"episode_id": engine.world.episode_id, "episode_index": episode_index,
            "seed": seed, "scenario_id": scenario_id, "ticks_recorded": ticks,
            "simulation_steps": simulation_steps,
            "seconds": elapsed, "labels": dict(labels), "recording": recording}


_WORKER = None


def _worker_init(config, max_ticks):
    global _WORKER
    _WORKER = (MatrixEnvironment(config=config, max_ticks=max_ticks), ExpertPolicy())


def _worker_run(job):
    index, seed, scenario_id, scenario_config, output_dir, recording = job
    env, policy = _WORKER
    return run_episode(environment=env, policy=policy, scenario_id=scenario_id,
                       seed=seed, episode_index=index, output_dir=output_dir,
                       scenario_config=scenario_config, recording=recording)
