"""Bounded integrated Matrix/Jarvis world-learning trial runner."""
from __future__ import annotations

from collections import Counter
import gzip
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

import cloudpickle
import torch

from farm_poc.behavior_cloning.dataset import load_training_dataset
from farm_poc.behavior_cloning.trainer import load_training_checkpoint
from farm_poc.cognitive_runtime.agent import CTMRuntimeAgent
from farm_poc.cognitive_runtime.online_learning import OnlineLearningConfig, OnlineWorldLearner
from farm_poc.matrix.engine import MatrixEngine
from farm_poc.matrix.fault_scheduler import PeriodicFaultScheduler
from farm_poc.training.contract import public_observation
from farm_poc.training.long_trial import create_trial_split, train_decision_diagnosis_heads


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _atomic_pickle(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        cloudpickle.dump(value, stream, protocol=cloudpickle.DEFAULT_PROTOCOL)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _initialise_training(dataset_dir: Path, run_dir: Path, seed: int, epochs: int):
    dataset = load_training_dataset(dataset_dir, allow_legacy_encoder=True)
    split = create_trial_split(dataset, seed=seed)
    summary = train_decision_diagnosis_heads(dataset, split, output_dir=run_dir / "supervised", seed=seed, epochs=epochs)
    agent = CTMRuntimeAgent()
    load_training_checkpoint(run_dir / "supervised" / "jarvis-decision-checkpoint.pt", agent)
    agent.eval()
    _write_json(run_dir / "held-out-benchmark.json", {
        "schema_version": 1,
        "benchmark_role": "whole-episode held-out test, completed before the 10-day run; never used for training",
        "dataset": summary["dataset"], "split": summary["split"],
        "initial_random_model": summary["initial_test_metrics"],
        "after_permitted_supervised_training": summary["final_test_metrics"],
        "label_provenance": summary["label_provenance"],
        "limitations": summary["limitations"],
    })
    return agent, summary


def _new_stats(total_ticks: int) -> dict[str, Any]:
    return {"total_ticks": total_ticks, "ticks_completed": 0, "fault_ticks": Counter(),
            "faults_injected": Counter(), "faults_recovered": Counter(), "fault_outcomes": [],
            "false_alarm_ticks": 0, "false_alarm_incidents": 0,
            "alarm_sequence_active": False, "alarms_during_fault": 0, "alarm_decisions": Counter(),
            "decision_counts": Counter(), "diagnosis_counts": Counter(), "timing_ms": Counter(),
            "timing_samples": 0, "errors": [], "wall_elapsed_seconds": 0.0}


def _jsonable_stats(stats: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in stats.items():
        result[key] = dict(value) if isinstance(value, Counter) else value
    return result


def _restore_stats(raw: dict[str, Any]) -> dict[str, Any]:
    for key in ("fault_ticks", "faults_injected", "faults_recovered", "alarm_decisions",
                "decision_counts", "diagnosis_counts", "timing_ms"):
        raw[key] = Counter(raw.get(key, {}))
    # Checkpoints written before the incident metric was introduced remain
    # resumable. Their old tick totals are valid; incident counts cannot be
    # reconstructed from aggregate stats alone and are therefore marked.
    if "false_alarm_incidents" not in raw:
        raw["false_alarm_incidents"] = 0
        raw["alarm_sequence_active"] = False
        raw["incident_metric_started_at_resume"] = True
    return raw


def _save_run_checkpoint(run_dir: Path, engine: MatrixEngine, scheduler: PeriodicFaultScheduler,
                         learner: OnlineWorldLearner, stats: dict[str, Any], *, status: str = "running") -> None:
    learner.persist(status=status, finalize=False)
    engine.flush_event_log(); engine.close()
    _atomic_pickle(run_dir / "evaluator-private" / "run-checkpoint.pkl", {
        "schema_version": 1, "ticks_completed": stats["ticks_completed"],
        "total_ticks": stats["total_ticks"], "dataset_identity": learner.agent.encoder.schema_hash,
        "engine": engine, "scheduler": scheduler.state_dict(), "stats": _jsonable_stats(stats),
    })


def _metrics(stats: dict[str, Any], scheduler: PeriodicFaultScheduler) -> dict[str, Any]:
    faults = []
    for row in stats["fault_outcomes"]:
        faults.append(row)
    normal_ticks = stats["ticks_completed"] - sum(stats["fault_ticks"].values())
    active_count = len(faults)
    detected = [row for row in faults if row.get("first_alarm_tick") is not None]
    missed = [row for row in faults if row.get("first_alarm_tick") is None]
    day_seconds = 86_400
    simulated_days = stats["ticks_completed"] / day_seconds
    metric_migration = bool(stats.get("incident_metric_started_at_resume", False))
    false_alarm_incidents = None if metric_migration else int(stats.get("false_alarm_incidents", 0))
    false_alarm_ticks = int(stats["false_alarm_ticks"])
    return {
        "ticks_completed": stats["ticks_completed"], "simulated_seconds": stats["ticks_completed"],
        "simulated_days": stats["ticks_completed"] / day_seconds,
        "normal_ticks": normal_ticks, "fault_ticks_by_family": dict(stats["fault_ticks"]),
        "fault_plans_scheduled": len(scheduler.schedule), "faults_injected_by_family": dict(stats["faults_injected"]),
        "faults_recovered_by_family": dict(stats["faults_recovered"]),
        "faults_detected": len(detected), "faults_missed": len(missed),
        "detection_rate": len(detected) / active_count if active_count else None,
        "detection_delay_ticks": [row["detection_delay_ticks"] for row in detected],
        "fault_outcomes": faults,
        "alarm_decision_counts": dict(stats["alarm_decisions"]),
        "non_none_decision_counts": dict(stats["alarm_decisions"]),
        "false_alarm_ticks": false_alarm_ticks,
        "false_alarm_ticks_per_simulated_day": false_alarm_ticks / simulated_days if simulated_days else None,
        "false_alarm_incidents": false_alarm_incidents,
        "false_alarm_incidents_per_simulated_day": (
            false_alarm_incidents / simulated_days if simulated_days and false_alarm_incidents is not None else None
        ),
        # Keep the old report key, but give it the event-count meaning its
        # name implies. The old tick-normalized value is retained above.
        "false_alarms_per_simulated_day": (
            false_alarm_incidents / simulated_days if simulated_days and false_alarm_incidents is not None else None
        ),
        "false_alarm_incidents_complete": not metric_migration,
        "false_alarm_metric_definition": (
            "false_alarm_ticks counts each INVESTIGATE/DIAGNOSE tick with no active scheduled fault; "
            "false_alarm_incidents counts a new contiguous INVESTIGATE/DIAGNOSE sequence that starts "
            "outside an active fault. VERIFY is not an alarm. A sequence continues across a switch "
            "between INVESTIGATE and DIAGNOSE and ends on NONE or VERIFY."
        ),
        "detection_metric_definition": (
            "a scheduled fault is detected only when INVESTIGATE/DIAGNOSE rises from inactive to active "
            "during that fault; an alarm already active before onset does not count as detecting it."
        ),
        "decision_counts": dict(stats["decision_counts"]), "diagnosis_counts": dict(stats["diagnosis_counts"]),
        "supported_diagnosis_assessment": "only WIDER_SUPPLY_ANOMALY exists in the supervised label set; other fault families are detection-only, not exact-root-cause scored",
        "timing_mean_ms": {name: value / stats["timing_samples"] for name, value in stats["timing_ms"].items()}
        if stats["timing_samples"] else {}, "errors": stats["errors"],
    }


def _record_alarm_metrics(stats: dict[str, Any], decision: str, active_fault: Any | None,
                          current_tick: int) -> None:
    """Count alarm ticks/incidents and only post-onset alarm edges.

    INVESTIGATE and DIAGNOSE form one continuing alarm episode. VERIFY closes
    that episode. An alarm that began during normal operation is not credited
    as a new detection when a fault later starts underneath it.
    """
    alarm_active = decision in {"INVESTIGATE", "DIAGNOSE"}
    was_active = bool(stats.get("alarm_sequence_active", False))
    new_alarm = alarm_active and not was_active
    if alarm_active:
        if active_fault is None:
            stats["false_alarm_ticks"] += 1
            if new_alarm:
                stats["false_alarm_incidents"] = stats.get("false_alarm_incidents", 0) + 1
        elif new_alarm:
            stats["alarms_during_fault"] += 1
            outcome = next((row for row in reversed(stats["fault_outcomes"])
                            if row["family"] == active_fault.family
                            and row["onset_tick"] == active_fault.onset_tick), None)
            if outcome is not None and outcome["first_alarm_tick"] is None:
                outcome["first_alarm_tick"] = current_tick
                outcome["detection_delay_ticks"] = max(0, current_tick - active_fault.onset_tick)
    stats["alarm_sequence_active"] = alarm_active


def run_trial(*, run_dir: str | Path, dataset_dir: str | Path, ticks: int = 864_000,
              seed: int = 20261016, checkpoint_interval: int = 5_000,
              progress_interval: int = 5_000, resume: bool = False,
              fault_mean_interval: int = 36_000, fault_interval_jitter: int = 9_000,
              fault_first_onset: tuple[int, int] = (12_000, 24_000),
              fault_families: tuple[str, ...] | None = None,
              peer_mode_weights: dict[str, float] | None = None,
              supervised_epochs: int = 8) -> dict[str, Any]:
    run_started = time.perf_counter()
    run_dir, dataset_dir = Path(run_dir), Path(dataset_dir)
    if ticks < 1 or checkpoint_interval < 1 or progress_interval < 1 or supervised_epochs < 1:
        raise ValueError("ticks and checkpoint/progress intervals must be positive")
    run_checkpoint = run_dir / "evaluator-private" / "run-checkpoint.pkl"
    if resume:
        if not run_checkpoint.exists():
            raise FileNotFoundError(f"no resumable run checkpoint: {run_checkpoint}")
        saved = cloudpickle.loads(run_checkpoint.read_bytes())
        if saved.get("schema_version") != 1 or saved.get("total_ticks") != ticks:
            raise ValueError("run checkpoint schema or requested horizon mismatch")
        engine = saved["engine"]
        scheduler = PeriodicFaultScheduler.from_state_dict(saved["scheduler"])
        stats = _restore_stats(saved["stats"])
        learner = OnlineWorldLearner.restore(run_dir, run_dir / "online-world-learning-checkpoint.pt")
    else:
        if run_dir.exists() and any(run_dir.iterdir()):
            raise FileExistsError(f"run directory is not empty: {run_dir}")
        run_dir.mkdir(parents=True, exist_ok=True)
        agent, supervised = _initialise_training(dataset_dir, run_dir, seed, supervised_epochs)
        _write_json(run_dir / "training-summary.json", supervised)
        supervised_metadata = {
            "checkpoint": str(run_dir / "supervised" / "jarvis-decision-checkpoint.pt"),
            "report": str(run_dir / "supervised" / "supervised-training-report.json"),
            "decision_head_changed": supervised["training"]["decision_head_changed"],
            "diagnosis_head_changed": supervised["training"]["diagnosis_head_changed"],
            "label_provenance": supervised["label_provenance"],
            "held_out_decision_accuracy": supervised["final_test_metrics"]["decision_accuracy"],
            "held_out_diagnosis_accuracy": supervised["final_test_metrics"]["diagnosis_accuracy"],
            "held_out_diagnosis_support": supervised["final_test_metrics"]["diagnosis_support"],
        }
        private_dir = run_dir / "evaluator-private"
        private_dir.mkdir(parents=True, exist_ok=True)
        scheduler = PeriodicFaultScheduler(seed=seed, total_ticks=ticks,
            mean_interval_ticks=fault_mean_interval, interval_jitter_ticks=fault_interval_jitter,
            first_onset_range=fault_first_onset, families=fault_families,
            peer_mode_weights=peer_mode_weights)
        _write_json(private_dir / "fault-schedule.json", {
            "schema_version": 1, "seed": seed, "total_ticks": ticks,
            "schedule": [plan.as_dict() for plan in scheduler.schedule],
            "visibility": "evaluator-private only; never included in Jarvis input or CMM",
        })
        log_path = private_dir / "matrix-events.jsonl"
        log_path.write_text("", encoding="utf-8")
        engine = MatrixEngine(seed=seed, config={"step_seconds": 1.0, "buffered_event_log": True}, log_path=log_path)
        # The integrated runner checkpoints Matrix, scheduler, Jarvis and stats
        # atomically at the same tick boundary. Disable the learner's standalone
        # save cadence to prevent mismatched component checkpoints.
        learner = OnlineWorldLearner(run_dir, OnlineLearningConfig(seed=seed,
            checkpoint_interval_updates=ticks + 1), agent=agent,
            supervised_training_metadata=supervised_metadata)
        stats = _new_stats(ticks)
    prior_elapsed = float(stats.get("wall_elapsed_seconds", 0.0))
    transitions_path = run_dir / "evaluator-private" / "fault-transitions.jsonl"
    transitions_path.parent.mkdir(parents=True, exist_ok=True)
    transition_stream = transitions_path.open("a", encoding="utf-8", newline="\n")
    try:
        while stats["ticks_completed"] < ticks:
            current_tick = engine.clock.tick
            transition_records = scheduler.before_tick(engine)
            for transition in transition_records:
                transition_stream.write(json.dumps(transition, sort_keys=True, allow_nan=False) + "\n")
                family = transition["family"]
                if transition["transition"] == "injected":
                    stats["faults_injected"][family] += 1
                    stats["fault_outcomes"].append({"family": family, "onset_tick": current_tick,
                                                    "planned_duration_ticks": transition["duration_ticks"],
                                                    "first_alarm_tick": None, "detection_delay_ticks": None})
                elif transition["transition"] == "recovered":
                    stats["faults_recovered"][family] += 1
            engine.step(return_snapshot=False)
            observation = public_observation(engine)
            active = scheduler.active
            candidate = learner._last_candidate
            private = {"fault_active": active is not None,
                       "fault_family": active.family if active else None,
                       "fault_onset_tick": active.onset_tick if active else None,
                       "fault_severity": active.severity if active else None,
                       "evaluator_result": engine.results[-1] if engine.results else None}
            # Pass each observation, including tick zero, through Jarvis once.
            learner.observe(observation, episode_id=engine.world.episode_id, evaluator_private=private)
            candidate = learner._last_candidate
            if candidate is not None:
                stats["decision_counts"][candidate.decision] += 1
                if candidate.decision == "DIAGNOSE": stats["diagnosis_counts"][candidate.diagnosis] += 1
                if candidate.decision != "NONE":
                    stats["alarm_decisions"][candidate.decision] += 1
                _record_alarm_metrics(stats, candidate.decision, active, current_tick)
            else:
                stats["alarm_sequence_active"] = False
            if active is not None: stats["fault_ticks"][active.family] += 1
            stats["ticks_completed"] += 1
            if engine.profile_metrics:
                metrics = engine.profile_metrics[-1]
                for name, value in metrics.items():
                    if name.endswith("_ms"):
                        stats["timing_ms"][name] += float(value)
                stats["timing_samples"] += 1
            # Keep bounded live memory while preserving durable JSONL history.
            if stats["ticks_completed"] % 1024 == 0:
                engine.world.events = engine.world.events[-32:]
                engine.evaluator._event_cursor = len(engine.world.events)
                engine.profile_metrics.clear()
                engine.results[:] = engine.results[-1:]
            if (stats["ticks_completed"] % progress_interval == 0 or
                    stats["ticks_completed"] % checkpoint_interval == 0):
                transition_stream.flush()
                elapsed = prior_elapsed + time.perf_counter() - run_started
                stats["wall_elapsed_seconds"] = elapsed
                _save_run_checkpoint(run_dir, engine, scheduler, learner, stats)
                report = _metrics(stats, scheduler)
                report.update({"status": "running", "wall_elapsed_seconds": elapsed,
                               "effective_ticks_per_second": stats["ticks_completed"] / max(1e-9, elapsed),
                               "last_checkpoint_tick": stats["ticks_completed"]})
                _write_json(run_dir / "progress-report.json", report)
                print(f"progress {stats['ticks_completed']}/{ticks} ticks; {report['effective_ticks_per_second']:.2f} ticks/s", flush=True)
        learner.finalize_episode()
        _save_run_checkpoint(run_dir, engine, scheduler, learner, stats, status="complete")
    except KeyboardInterrupt:
        stats["errors"].append({"type": "KeyboardInterrupt", "message": "user requested stop; checkpoint is resumable"})
        _save_run_checkpoint(run_dir, engine, scheduler, learner, stats)
        report = _metrics(stats, scheduler)
        stats["wall_elapsed_seconds"] = prior_elapsed + time.perf_counter() - run_started
        report.update({"status": "interrupted", "wall_elapsed_seconds": stats["wall_elapsed_seconds"],
                       "effective_ticks_per_second": stats["ticks_completed"] / max(1e-9, stats["wall_elapsed_seconds"]),
                       "resume_command": f"python run_matrix.py --trial-days 10 --run-dir {run_dir} --dataset {dataset_dir} --resume"})
        _write_json(run_dir / "final-report.json", report)
    except Exception as error:
        stats["errors"].append({"type": type(error).__name__, "message": str(error)})
        _save_run_checkpoint(run_dir, engine, scheduler, learner, stats)
        report = _metrics(stats, scheduler)
        stats["wall_elapsed_seconds"] = prior_elapsed + time.perf_counter() - run_started
        report.update({"status": "failed", "wall_elapsed_seconds": stats["wall_elapsed_seconds"], "error": str(error)})
        _write_json(run_dir / "final-report.json", report)
        raise
    else:
        report = _metrics(stats, scheduler)
        stats["wall_elapsed_seconds"] = prior_elapsed + time.perf_counter() - run_started
        report.update({"status": "complete", "wall_elapsed_seconds": stats["wall_elapsed_seconds"],
                       "effective_ticks_per_second": stats["ticks_completed"] / max(1e-9, stats["wall_elapsed_seconds"]),
        "run_checkpoint": str(run_checkpoint), "jarvis_report": str(run_dir / "online-learning-report.json"),
                       "held_out_benchmark": str(run_dir / "held-out-benchmark.json"),
                       "fault_schedule": str(run_dir / "evaluator-private" / "fault-schedule.json"),
                       "matrix_event_log": str(run_dir / "evaluator-private" / "matrix-events.jsonl"),
                       "limitation": "online objective predicts next operational features; online decisions are not ground-truth labels; only existing WIDER_SUPPLY_ANOMALY diagnosis has a provisional supervised class"})
        _write_json(run_dir / "final-report.json", report)
    finally:
        transition_stream.flush(); transition_stream.close()
        learner.close(); engine.flush_event_log(); engine.close()
    return report
