"""Small pilot/data generation CLI; no bulk job is started implicitly."""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .environment import MatrixEnvironment
from .expert_policy import ExpertPolicy
from .runner import _worker_init, _worker_run, episode_seed, run_episode
from .pilot_audit import audit_pilot


def _scenario_config(scenario_id: str, variant: int) -> dict | None:
    if scenario_id.startswith("sensor_"):
        return {"inject_tick": 2, "end_tick": 8}
    if scenario_id != "overvoltage":
        if scenario_id == "thermal_self_correcting":
            return {"inject_tick": 2 + (variant % 3), "heat_load_w": 280.0 + 20.0 * (variant % 3),
                    "duration_ticks": 3, "cycle_interval_ticks": 28}
        if scenario_id == "thermal_recurring":
            return {"inject_tick": 2 + (variant % 3), "heat_load_w": 280.0 + 20.0 * (variant % 3),
                    "duration_ticks": 3, "cycles": 2, "cycle_interval_ticks": 28 + 2 * (variant % 3)}
        return None
    modes = ({}, {"incubator-2": "elevated"}, {"incubator-2": "stale"},
             {"incubator-2": "missing"},
             {"incubator-2": {"mode": "normal", "delay_ticks": 2}})
    return {"peer_modes": modes[variant % len(modes)]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="benign,overvoltage",
                        help="comma-separated IDs: benign,overvoltage,sensor_bias,sensor_drift,sensor_stuck,sensor_dropout,thermal_self_correcting,thermal_recurring")
    parser.add_argument("--master-seed", type=int, default=20261014)
    parser.add_argument("--episodes", type=int, default=4)
    parser.add_argument("--ticks", type=int, default=24)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--step-seconds", type=float, default=1.0,
                        help="authoritative Matrix timestep; 60 seconds is suitable for thermal curriculum demonstrations")
    parser.add_argument("--output", type=Path, default=Path("runs/training-pilot"))
    parser.add_argument("--recording", choices=("on", "off"), default="on")
    args = parser.parse_args(argv)
    scenario_ids = [item.strip() for item in args.scenario.split(",") if item.strip()]
    if args.episodes < 1 or args.ticks < 1 or args.workers < 1 or not scenario_ids:
        parser.error("episodes, ticks, workers, and scenario list must be non-empty and positive")
    if set(scenario_ids) - MatrixEnvironment.SCENARIOS:
        parser.error(f"unsupported scenario IDs: {sorted(set(scenario_ids) - MatrixEnvironment.SCENARIOS)}")
    recording = args.recording == "on"
    jobs = []
    occurrences = {}
    for i in range(args.episodes):
        scenario_id = scenario_ids[i % len(scenario_ids)]
        variant = occurrences.get(scenario_id, 0)
        occurrences[scenario_id] = variant + 1
        jobs.append((i, episode_seed(args.master_seed, i), scenario_id,
                     _scenario_config(scenario_id, variant), str(args.output), recording))
    started = time.perf_counter()
    if args.workers == 1:
        env, policy = MatrixEnvironment(config={"step_seconds": args.step_seconds}, max_ticks=args.ticks), ExpertPolicy()
        results = [run_episode(environment=env, policy=policy, scenario_id=scenario,
                               seed=seed, episode_index=index, output_dir=args.output,
                               scenario_config=config, recording=recording)
                   for index, seed, scenario, config, _, recording in jobs]
    else:
        with ProcessPoolExecutor(max_workers=args.workers, initializer=_worker_init,
                                 initargs=({"step_seconds": args.step_seconds}, args.ticks)) as pool:
            results = list(pool.map(_worker_run, jobs))
    elapsed = time.perf_counter() - started
    summary = {"episodes_completed": len(results), "episodes_failed": 0,
               "ticks_recorded": sum(item["ticks_recorded"] for item in results),
               "simulation_steps": sum(item["simulation_steps"] for item in results),
               "seconds": elapsed,
               "ticks_per_second": sum(item["simulation_steps"] for item in results) / elapsed,
               "observations_per_second": sum(item["ticks_recorded"] for item in results) / elapsed,
               "label_frequencies": {}, "episode_results": results,
               "observation_schema_version": 1, "target_schema_version": 1,
               "recording": recording}
    for item in results:
        for label, count in item["labels"].items():
            summary["label_frequencies"][label] = summary["label_frequencies"].get(label, 0) + count
    if recording:
        summary["dataset_audit"] = audit_pilot(args.output)
    args.output.mkdir(parents=True, exist_ok=True)
    report = args.output / "pilot-report.json"
    report.write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
