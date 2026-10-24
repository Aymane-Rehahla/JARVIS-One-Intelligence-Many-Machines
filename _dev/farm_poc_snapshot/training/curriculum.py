"""Small deterministic scenario curriculum for dataset-readiness review.

This module intentionally records a review-sized pilot only. It does not train
the CTM or launch a bulk dataset job.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from farm_poc.behavior_cloning.dataset import assess_episode_splits, load_training_dataset
from farm_poc.cognitive_runtime.encoder import OperationalEncoderV1

from .environment import MatrixEnvironment
from .expert_policy import ExpertPolicy
from .pilot_audit import audit_pilot
from .runner import episode_seed, run_episode


@dataclass(frozen=True)
class CurriculumEpisode:
    scenario_id: str
    scenario_config: dict
    description: str
    environment_config: dict | None = None


def readiness_curriculum() -> tuple[CurriculumEpisode, ...]:
    """Independent, parameter-varied episodes for the first split review."""
    plan: list[CurriculumEpisode] = []
    for outside in (20.0, 22.0, 24.0):
        plan.append(CurriculumEpisode("benign", {}, "normal baseline with configured outside boundary",
                                      {"environment": {"outside_temperature_c": outside}}))
    for index, load in enumerate((280.0, 300.0, 320.0)):
        plan.append(CurriculumEpisode("thermal_self_correcting", {
            "inject_tick": 2 + index, "heat_load_w": load, "duration_ticks": 3,
            "cycle_interval_ticks": 28}, "bounded one-cycle external heat load"))
        plan.append(CurriculumEpisode("thermal_recurring", {
            "inject_tick": 2 + index, "heat_load_w": load, "duration_ticks": 3,
            "cycles": 2, "cycle_interval_ticks": (28, 30, 28)[index]}, "bounded separated heat-load cycles"))
    # Five independently configured fresh corroboration episodes provide a
    # minimal train/validation/test allocation for the only diagnosis class.
    for index, voltage in enumerate((261.0, 262.0, 263.0, 264.0, 265.0)):
        plan.append(CurriculumEpisode("overvoltage", {
            "inject_tick": 1 + index, "voltage_v": voltage,
            "peer_modes": {"incubator-2": "elevated"}}, "shared-supply evidence"))
    for index, mode in enumerate(("normal", "stale", "missing")):
        plan.append(CurriculumEpisode("overvoltage", {
            "inject_tick": 2 + index, "peer_modes": {"incubator-2": mode}},
            "local voltage with non-corroborating peer evidence"))
    for scenario_id in ("sensor_bias", "sensor_drift", "sensor_stuck", "sensor_dropout"):
        plan.append(CurriculumEpisode(scenario_id, {"inject_tick": 2, "end_tick": 12},
                                      "measurement-path fault without changed hidden cause"))
    return tuple(plan)


def run_readiness_pilot(output_dir: str | Path, *, master_seed: int = 20261015,
                        max_ticks: int = 76) -> dict:
    """Generate and validate the bounded readiness pilot in a new directory."""
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("readiness pilot output directory must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    plan = readiness_curriculum()
    results = []
    for index, item in enumerate(plan):
        environment = MatrixEnvironment(config={"step_seconds": 60.0, **(item.environment_config or {})},
                                        max_ticks=max_ticks)
        results.append(run_episode(environment=environment, policy=ExpertPolicy(),
                                   scenario_id=item.scenario_id, seed=episode_seed(master_seed, index),
                                   episode_index=index, output_dir=output,
                                   scenario_config=item.scenario_config, recording=True))
    return validate_readiness_pilot(output, plan=plan)


def validate_readiness_pilot(output_dir: str | Path, *, plan: tuple[CurriculumEpisode, ...] | None = None) -> dict:
    """Validate existing readiness artifacts and write their derived report."""
    output = Path(output_dir)
    dataset = load_training_dataset(output)
    split = assess_episode_splits(dataset)
    report = {
        "schema_version": 1,
        "encoder_schema_hash": OperationalEncoderV1().schema_hash,
        "environment_config": {"step_seconds": 60.0},
        "episodes": [asdict(item) for item in (plan or readiness_curriculum())],
        "episode_results": [{"episode_id": episode.episode_id,
                             "episode_index": episode.manifest["episode_index"],
                             "scenario_id": episode.manifest["scenario_id"],
                             "ticks_recorded": episode.tick_count}
                            for episode in dataset.episodes],
        "dataset_audit": audit_pilot(output),
        "split_assessment": asdict(split),
        "legacy_encoder_episodes": list(dataset.legacy_encoder_episodes),
        "legacy_audit_episodes": list(dataset.legacy_audit_episodes),
    }
    (output / "readiness-report.json").write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runs/training-pilot-curriculum-v1"))
    parser.add_argument("--master-seed", type=int, default=20261015)
    parser.add_argument("--ticks", type=int, default=76)
    args = parser.parse_args()
    print(json.dumps(run_readiness_pilot(args.output, master_seed=args.master_seed, max_ticks=args.ticks),
                     sort_keys=True, indent=2))
