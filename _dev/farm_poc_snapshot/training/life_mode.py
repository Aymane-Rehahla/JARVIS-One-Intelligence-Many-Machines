"""Resumable frozen-weight Matrix life mode with streamed separated records.

This is orchestration around the existing Matrix; it does not train Jarvis.
Public observations are validated by the same operational contract as the API.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import tempfile
import shutil
import time
from typing import Any

import cloudpickle
import torch

from farm_poc.behavior_cloning.trainer import load_training_checkpoint
from farm_poc.cognitive_runtime.agent import CTMRuntimeAgent
from farm_poc.cognitive_runtime.memory import CognitiveMemoryManager
from farm_poc.matrix.engine import MatrixEngine
from farm_poc.matrix.fault_scheduler import PeriodicFaultScheduler
from farm_poc.training.contract import OBSERVATION_SCHEMA_VERSION, public_observation

DAY = 86_400
SCHEMA_VERSION = 1
STREAMS = ("observations", "decisions", "private_events", "grades")
FAULT_FAMILIES = ("supply_voltage", "hvac_supply_fan", "fire_smoke", "sensor_stuck", "sensor_drift")


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                               allow_nan=False).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
                         encoding="utf-8")
    _atomic_replace(temporary, path)


def atomic_pickle(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temporary.open("wb") as stream:
        cloudpickle.dump(value, stream, protocol=cloudpickle.DEFAULT_PROTOCOL)
        stream.flush()
        os.fsync(stream.fileno())
    _atomic_replace(temporary, path)


def _atomic_replace(temporary: Path, destination: Path) -> None:
    """Retry transient Windows sharing/antivirus locks without losing the old file."""
    for attempt in range(8):
        try:
            os.replace(temporary, destination)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.025 * (attempt + 1))


def _derived_seed(master_seed: int, worker_index: int) -> int:
    # Explicit non-overlapping arithmetic assignment, not scheduling-order based.
    return int(master_seed) * 10_000 + int(worker_index) + 1


class LifeModeDriver:
    """Independent deterministic streams for ambient, benign, and fault events."""
    def __init__(self, *, seed: int, total_ticks: int, config: dict[str, Any] | None = None):
        self.seed = int(seed)
        self.total_ticks = int(total_ticks)
        self.config = dict(config or {})
        self.environment_rng = random.Random(self.seed ^ 0x45A19)
        self.benign_rng = random.Random(self.seed ^ 0x6B22D)
        self.base_temperature_c = float(self.config.get("base_temperature_c", 22.0))
        self.base_humidity_pct = float(self.config.get("base_humidity_pct", 50.0))
        self.day_amplitude_c = float(self.config.get("day_amplitude_c", 4.0))
        self.multiday_amplitude_c = float(self.config.get("multiday_amplitude_c", 3.0))
        self.wear_per_day = float(self.config.get("wear_per_day", 0.005))
        if not (-80 <= self.base_temperature_c <= 80 and 0 <= self.base_humidity_pct <= 100):
            raise ValueError("invalid life-mode ambient baseline")
        if not (0 <= self.day_amplitude_c <= 15 and 0 <= self.multiday_amplitude_c <= 15):
            raise ValueError("life-mode ambient amplitudes must be within [0, 15]")
        if not (0 <= self.wear_per_day <= 0.1):
            raise ValueError("wear_per_day must be within [0, 0.1]")
        self.weather_events = self._make_weather_events()
        self.benign_events = self._make_benign_events()
        self.wear_fraction = 0.0
        self.applied_benign: set[int] = set()
        self.fault_scheduler = PeriodicFaultScheduler(
            seed=self.seed ^ 0x1329, total_ticks=self.total_ticks,
            mean_interval_ticks=21 * DAY, interval_jitter_ticks=7 * DAY,
            first_onset_range=(14 * DAY, 28 * DAY), families=FAULT_FAMILIES)

    def _make_weather_events(self) -> tuple[dict[str, Any], ...]:
        rng = self.environment_rng
        result = []
        tick = rng.randint(2 * DAY, 4 * DAY)
        while tick < self.total_ticks:
            duration = rng.randint(4 * 3600, 20 * 3600)
            result.append({"start_tick": tick, "end_tick": tick + duration,
                           "temperature_offset_c": round(rng.uniform(-7, 7), 2),
                           "humidity_offset_pct": round(rng.uniform(-15, 20), 2)})
            tick += rng.randint(4 * DAY, 8 * DAY)
        return tuple(result)

    def _make_benign_events(self) -> tuple[dict[str, Any], ...]:
        rng = self.benign_rng
        kinds = ("cold_snap", "door_opening", "feeding", "cleaning", "maintenance")
        first_range = tuple(self.config.get("benign_first_event_range_ticks", (30 * 60, 3 * 3600)))
        gap_range = tuple(self.config.get("benign_gap_range_ticks", (3 * 3600, 9 * 3600)))
        if (len(first_range) != 2 or first_range[0] < 0 or first_range[1] < first_range[0] or
                len(gap_range) != 2 or gap_range[0] < 1 or gap_range[1] < gap_range[0]):
            raise ValueError("invalid benign event timing configuration")
        result = []
        tick = rng.randint(*first_range)
        while tick < self.total_ticks:
            kind = rng.choices(kinds, weights=(2, 3, 3, 2, 1), k=1)[0]
            durations = {"cold_snap": (2 * 3600, 6 * 3600), "door_opening": (60, 300),
                         "feeding": (300, 1200), "cleaning": (600, 2400),
                         "maintenance": (1, 1)}
            duration = rng.randint(*durations[kind])
            row = {"event_id": f"benign-{len(result)+1:05d}", "family": kind,
                   "start_tick": tick, "end_tick": tick + duration}
            if kind == "cold_snap": row["temperature_offset_c"] = round(rng.uniform(-8, -3), 2)
            if kind == "maintenance": row["wear_reduction"] = round(rng.uniform(.005, .02), 4)
            if kind == "cleaning": row["ammonia_source_ppm_s"] = .00003
            if kind == "feeding": row["co2_source_ppm_s"] = .0002
            result.append(row)
            tick += duration + rng.randint(*gap_range)
        return tuple(result)

    def before_tick(self, engine) -> list[dict[str, Any]]:
        tick = engine.clock.tick
        phase = 2 * math.pi * (tick % DAY) / DAY
        seasonal = 2 * math.pi * tick / (14 * DAY)
        weather = next((row for row in self.weather_events
                        if row["start_tick"] <= tick < row["end_tick"]), None)
        benign = [row for row in self.benign_events if row["start_tick"] <= tick < row["end_tick"]]
        weather_temp = float(weather["temperature_offset_c"]) if weather else 0.0
        weather_rh = float(weather["humidity_offset_pct"]) if weather else 0.0
        benign_cold = sum(float(row.get("temperature_offset_c", 0)) for row in benign
                          if row["family"] == "cold_snap")
        outside_t = (self.base_temperature_c + self.day_amplitude_c * math.sin(phase - math.pi / 2)
                     + self.multiday_amplitude_c * math.sin(seasonal) + weather_temp + benign_cold)
        outside_rh = max(0.0, min(100.0, self.base_humidity_pct - 8 * math.sin(phase - math.pi / 2)
                                  + weather_rh))
        # Sun is up from 06:00 to 18:00 in this driver's day; peak at noon.
        solar = 800.0 * max(0.0, -math.cos(phase))
        engine.physical_world.set_outside_conditions(temperature_c=outside_t,
                                                     relative_humidity_pct=outside_rh,
                                                     solar_w_m2=solar)
        engine.world.devices["incubator-1"].internal["door_open"] = any(
            row["family"] == "door_opening" for row in benign)
        sources = engine.physical_world.state["benign_sources"]
        sources["co2_ppm_s"] = sum(float(row.get("co2_source_ppm_s", 0)) for row in benign)
        sources["ammonia_ppm_s"] = sum(float(row.get("ammonia_source_ppm_s", 0)) for row in benign)
        self.wear_fraction = min(.30, self.wear_fraction + self.wear_per_day / DAY)
        transitions = []
        for index, row in enumerate(self.benign_events):
            if row["start_tick"] == tick:
                transitions.append({"tick": tick, "transition": "started", **row})
                if row["family"] == "maintenance":
                    self.wear_fraction = max(0.0, self.wear_fraction - row["wear_reduction"])
            elif row["end_tick"] == tick:
                transitions.append({"tick": tick, "transition": "ended",
                                    "event_id": row["event_id"], "family": row["family"]})
        engine.world.devices["hvac"].config["supply_fan_wear_fraction"] = self.wear_fraction
        transitions.extend(self.fault_scheduler.before_tick(engine))
        return transitions

    def state_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "seed": self.seed, "total_ticks": self.total_ticks,
                "weather_events": list(self.weather_events), "benign_events": list(self.benign_events),
                "environment_rng_state": self.environment_rng.getstate(),
                "benign_rng_state": self.benign_rng.getstate(), "wear_fraction": self.wear_fraction,
                "fault_scheduler": self.fault_scheduler.state_dict()}

    @classmethod
    def from_state_dict(cls, state: dict[str, Any], config: dict[str, Any] | None = None):
        if state.get("schema_version") != 1:
            raise ValueError("unsupported life-mode driver checkpoint")
        result = cls(seed=state["seed"], total_ticks=state["total_ticks"], config=config)
        if list(result.weather_events) != state["weather_events"] or list(result.benign_events) != state["benign_events"]:
            raise ValueError("life-mode deterministic schedule does not match its seed/config")
        result.environment_rng.setstate(state["environment_rng_state"])
        result.benign_rng.setstate(state["benign_rng_state"])
        result.wear_fraction = float(state["wear_fraction"])
        result.fault_scheduler = PeriodicFaultScheduler.from_state_dict(state["fault_scheduler"])
        return result


class StreamRecorder:
    """Four append-only JSONL streams, flushed at explicit intervals."""
    def __init__(self, directory: Path, *, resume: bool = False):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        private_dir = directory / "evaluator-private"
        private_dir.mkdir(parents=True, exist_ok=True)
        self.paths = {"observations": directory / "observations.jsonl.gz",
                      "decisions": directory / "decisions.jsonl.gz",
                      "private_events": private_dir / "events.jsonl.gz",
                      "grades": private_dir / "grades.jsonl.gz"}
        self.files: dict[str, Any] = {}
        self.write_elapsed_ns = 0
        self.flush_elapsed_ns = 0
        if not resume:
            for path in self.paths.values():
                if path.exists():
                    raise FileExistsError(f"refusing to overwrite existing life-mode stream: {path}")
            self._open()

    def _open(self):
        for name, path in self.paths.items():
            if name not in self.files:
                self.files[name] = gzip.open(path, "at", encoding="utf-8", newline="\n")

    def append(self, name: str, value: dict[str, Any]) -> None:
        if name not in self.paths:
            raise ValueError(f"unknown life-mode stream: {name}")
        self._open()
        started = time.perf_counter_ns()
        self.files[name].write(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False) + "\n")
        self.write_elapsed_ns += time.perf_counter_ns() - started

    def flush(self) -> None:
        started = time.perf_counter_ns()
        for stream in self.files.values():
            stream.flush()
        self.flush_elapsed_ns += time.perf_counter_ns() - started

    def flush_close(self) -> dict[str, int]:
        started = time.perf_counter_ns()
        for stream in self.files.values():
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
        self.files.clear()
        self.flush_elapsed_ns += time.perf_counter_ns() - started
        return {name: path.stat().st_size if path.exists() else 0 for name, path in self.paths.items()}

    def close(self) -> None:
        for stream in self.files.values():
            stream.flush()
            stream.close()
        self.files.clear()


def _agent_weight_hash(agent: CTMRuntimeAgent) -> str:
    digest = hashlib.sha256()
    for name, tensor in sorted(agent.state_dict().items()):
        digest.update(name.encode("utf-8"))
        digest.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def _working_set_bytes() -> int | None:
    """Return current process RSS/working set where the OS exposes it."""
    try:
        import psutil
        return int(psutil.Process().memory_info().rss)
    except ImportError:
        pass
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                               ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                                               wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        ok = psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        return int(counters.WorkingSetSize) if ok else None
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value * (1024 if value < 10**9 else 1))
    except (ImportError, OSError):
        return None


def benchmark_stream_recorder(record_counts=(10_000, 100_000, 500_000), *, flush_interval=10_000) -> dict[str, Any]:
    """Measure bounded JSONL writer behavior without retaining records in memory."""
    results = []
    rss = _working_set_bytes()
    for count in record_counts:
        with tempfile.TemporaryDirectory(prefix="farm-stream-benchmark-") as temporary:
            recorder = StreamRecorder(Path(temporary))
            peak = _working_set_bytes()
            started = time.perf_counter()
            for tick in range(int(count)):
                row = {"tick": tick, "value": 0.125}
                for stream_name in STREAMS:
                    recorder.append(stream_name, row)
                if flush_interval and (tick + 1) % flush_interval == 0:
                    recorder.flush()
                    sampled = _working_set_bytes()
                    if sampled is not None:
                        peak = max(peak or 0, sampled)
            offsets = recorder.flush_close()
            elapsed = time.perf_counter() - started
            sampled = _working_set_bytes()
            if sampled is not None:
                peak = max(peak or 0, sampled)
            results.append({"records_per_stream": int(count),
                "total_records": int(count) * len(STREAMS),
                "bytes_per_stream": offsets,
                "total_bytes": sum(offsets.values()),
                "elapsed_seconds": elapsed,
                "records_per_second_total": int(count) * len(STREAMS) / max(elapsed, 1e-9),
                "rss_start_bytes": rss, "rss_end_bytes": sampled,
                "rss_peak_sampled_bytes": peak,
                "memory_measurement": "process working set sampled at stream flush intervals"})
    return {"schema_version": 1, "kind": "synthetic streaming writer benchmark",
            "record_counts": results,
            "note": "Synthetic rows measure writer overhead only, not the size or validation cost of full Matrix observations."}


def _empty_stats() -> dict[str, Any]:
    return {"ticks_completed": 0, "fault_ticks": Counter(), "fault_injected": Counter(),
            "fault_recovered": Counter(), "benign_events": Counter(), "decisions": Counter(),
            "diagnoses": Counter(), "cmm_writes": Counter(), "cmm_retrieval_calls": 0,
            "agent_process_ns": 0, "ctm_inference_ns": 0,
            "timing_ms": Counter(), "timing_samples": 0,
            "record_write_ms": 0.0, "record_flush_ms": 0.0,
            "cpu_seconds": 0.0, "rss_start_bytes": None, "rss_end_bytes": None,
            "rss_peak_sampled_bytes": None, "elapsed_wall_seconds": 0.0, "errors": []}


def _plain_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {key: dict(value) if isinstance(value, Counter) else value for key, value in stats.items()}


def _restore_stats(stats: dict[str, Any]) -> dict[str, Any]:
    for key in ("fault_ticks", "fault_injected", "fault_recovered", "benign_events",
                "decisions", "diagnoses", "cmm_writes", "timing_ms"):
        stats[key] = Counter(stats.get(key, {}))
    return stats


def _record_event(stats: dict[str, Any], row: dict[str, Any], recorder: StreamRecorder,
                  *, worker_id: str, episode_id: str) -> None:
    family = row.get("family")
    if family in FAULT_FAMILIES:
        if row.get("transition") == "injected": stats["fault_injected"][family] += 1
        elif row.get("transition") == "recovered": stats["fault_recovered"][family] += 1
    elif family:
        stats["benign_events"][family] += row.get("transition") == "started"
    recorder.append("private_events", {"worker_id": worker_id, "episode_id": episode_id,
                                      "schema_version": 1, **row})


def _stream_summary(paths: dict[str, Path], counts: dict[str, int]) -> dict[str, Any]:
    result = {}
    for name, path in paths.items():
        result[name] = {"path": str(path), "bytes": path.stat().st_size if path.exists() else 0,
                        "records": int(counts.get(name, 0)),
                        "sha256": file_sha256(path) if path.exists() else None}
    return result


def _checkpoint(worker_dir: Path, engine: MatrixEngine, agent: CTMRuntimeAgent,
                driver: LifeModeDriver, stats: dict[str, Any], recorder: StreamRecorder,
                *, model_hash: str, config: dict[str, Any], status: str) -> dict[str, int]:
    offsets = recorder.flush_close()
    engine.flush_event_log(); engine.close()
    event_path = worker_dir / "evaluator-private" / "matrix-events.jsonl.gz"
    offsets["matrix_events"] = event_path.stat().st_size if event_path.exists() else 0
    atomic_pickle(worker_dir / "evaluator-private" / "life-checkpoint.pkl", {
        "checkpoint_version": 1, "ticks_completed": stats["ticks_completed"],
        "worker_id": worker_dir.name, "model_hash": model_hash,
        "weight_hash": _agent_weight_hash(agent),
        "config": config, "engine": engine,
        "agent_state_dict": {key: tensor.detach().cpu().clone() for key, tensor in agent.state_dict().items()},
        "agent_recurrent_state": (tuple(t.detach().cpu().clone() for t in agent._state)
                                  if agent._state is not None else None),
        "agent_decision_index": agent._decision_index,
        "agent_episode_active": agent._episode_active,
        "cmm_state": agent.cmm.state_dict(),
        "driver": driver.state_dict(), "stats": _plain_stats(stats),
        "stream_offsets": offsets, "status": status,
    })
    return offsets


def _restore_worker(worker_dir: Path, *, config: dict[str, Any], model_hash: str,
                    model_checkpoint: Path,
                    driver_config: dict[str, Any]):
    checkpoint_path = worker_dir / "evaluator-private" / "life-checkpoint.pkl"
    if not checkpoint_path.exists():
        raise FileNotFoundError(f"missing resumable checkpoint: {checkpoint_path}")
    saved = cloudpickle.loads(checkpoint_path.read_bytes())
    if saved.get("checkpoint_version") != 1 or saved.get("model_hash") != model_hash:
        raise ValueError("checkpoint version or frozen-model identity mismatch")
    if saved.get("config") != config:
        raise ValueError("resume configuration differs from checkpoint")
    engine = saved["engine"]
    agent = CTMRuntimeAgent(cmm=CognitiveMemoryManager(enable_writes=True))
    load_training_checkpoint(model_checkpoint, agent)
    agent.load_state_dict(saved["agent_state_dict"])
    agent.cmm.load_state_dict(saved["cmm_state"])
    recurrent = saved.get("agent_recurrent_state")
    agent._state = tuple(t.to(agent.config.device) for t in recurrent) if recurrent is not None else None
    agent._decision_index = int(saved.get("agent_decision_index", 0))
    agent._episode_active = bool(saved.get("agent_episode_active", False))
    agent.eval()
    if _agent_weight_hash(agent) != saved.get("weight_hash"):
        raise ValueError("checkpoint CTM weights differ from its frozen weight identity")
    driver = LifeModeDriver.from_state_dict(saved["driver"], driver_config)
    stats = _restore_stats(saved["stats"])
    offsets = saved["stream_offsets"]
    paths = {"observations": worker_dir / "observations.jsonl.gz",
             "decisions": worker_dir / "decisions.jsonl.gz",
             "private_events": worker_dir / "evaluator-private" / "events.jsonl.gz",
             "grades": worker_dir / "evaluator-private" / "grades.jsonl.gz"}
    for name, path in paths.items():
        expected = int(offsets[name])
        if not path.exists() or path.stat().st_size < expected:
            raise ValueError(f"stream {name} is shorter than the last valid checkpoint offset")
        with path.open("r+b") as stream:
            stream.truncate(expected)
    event_path = worker_dir / "evaluator-private" / "matrix-events.jsonl.gz"
    expected_events = int(offsets["matrix_events"])
    if event_path.exists():
        if event_path.stat().st_size < expected_events:
            raise ValueError("Matrix event log is shorter than the checkpoint offset")
        with event_path.open("r+b") as stream:
            stream.truncate(expected_events)
    elif expected_events:
        raise ValueError("Matrix event log referenced by checkpoint is missing")
    return engine, agent, driver, stats


def _run_worker(*, worker_dir: Path, worker_index: int, seed: int, total_ticks: int | None,
                duration_seconds: float | None, checkpoint_interval: int, progress_interval: int,
                model_checkpoint: Path, model_hash: str, resume: bool,
                torch_threads_per_worker: int = 1,
                stop_event=None, stop_file: Path | None = None,
                driver_config: dict[str, Any] | None = None) -> dict[str, Any]:
    if not 1 <= torch_threads_per_worker <= 8:
        raise ValueError("torch_threads_per_worker must be within [1, 8]")
    torch.set_num_threads(torch_threads_per_worker)
    worker_id = f"worker-{worker_index:03d}"
    private_dir = worker_dir / "evaluator-private"
    driver_config = dict(driver_config or {})
    run_config = {"seed": seed, "total_ticks": total_ticks, "duration_seconds": duration_seconds,
                  "checkpoint_interval": checkpoint_interval, "driver_config": driver_config,
                  "cmm_writes_enabled": True,
                  "torch_threads_per_worker": torch_threads_per_worker,
                  "observation_schema_version": OBSERVATION_SCHEMA_VERSION}
    if resume:
        private_dir.mkdir(parents=True, exist_ok=True)
        engine, agent, driver, stats = _restore_worker(worker_dir, config=run_config,
            model_hash=model_hash, model_checkpoint=model_checkpoint,
            driver_config=driver_config)
        offsets = cloudpickle.loads((private_dir / "life-checkpoint.pkl").read_bytes())["stream_offsets"]
        recorder = StreamRecorder(worker_dir, resume=True)
        history = json.loads((worker_dir / "manifest.json").read_text(encoding="utf-8"))
        record_counts = dict(history.get("record_counts", {}))
        counts = {name: int(record_counts.get(name, 0)) for name in STREAMS}
        counts["matrix_events"] = int(record_counts.get("matrix_events", 0))
        prior_elapsed = float(stats["elapsed_wall_seconds"])
    else:
        preexisting = ([path for path in worker_dir.iterdir() if path.name != "worker.log"]
                       if worker_dir.exists() else [])
        if preexisting:
            raise FileExistsError(f"refusing to overwrite nonempty life-mode directory: {worker_dir}")
        worker_dir.mkdir(parents=True, exist_ok=True)
        private_dir.mkdir(parents=True, exist_ok=True)
        engine = MatrixEngine(seed=seed, config={"step_seconds": 1.0, "buffered_event_log": True,
                                                 "message_history_limit": 128},
                              log_path=private_dir / "matrix-events.jsonl.gz")
        agent = CTMRuntimeAgent(cmm=CognitiveMemoryManager(enable_writes=True))
        metadata = load_training_checkpoint(model_checkpoint, agent)
        agent.eval()
        agent.begin_episode(engine.world.episode_id)
        driver = LifeModeDriver(seed=seed, total_ticks=total_ticks or (100 * DAY), config=driver_config)
        stats = _empty_stats()
        recorder = StreamRecorder(worker_dir)
        counts = {name: 0 for name in (*STREAMS, "matrix_events")}
        model_copy = worker_dir / "frozen-model-source.pt"
        shutil.copyfile(model_checkpoint, model_copy)
        if file_sha256(model_copy) != model_hash:
            raise IOError("copied model checkpoint checksum mismatch")
        atomic_json(private_dir / "fault-schedule.json", {
            "schema_version": 1, "seed": seed,
            "fault_interval_ticks": {"mean": 21 * DAY, "jitter": 7 * DAY},
            "first_onset_range_ticks": [14 * DAY, 28 * DAY],
            "schedule": [plan.as_dict() for plan in driver.fault_scheduler.schedule],
            "visibility": "evaluator-private; never passed to Jarvis"})
        atomic_json(private_dir / "environment-schedule.json", {
            "schema_version": 1, "weather_events": list(driver.weather_events),
            "benign_events": list(driver.benign_events),
            "visibility": "private reproducibility schedule; generated physical conditions only are observable"})
        agent_weight_hash = _agent_weight_hash(agent)
        atomic_json(worker_dir / "model-identity.json", {
            "source_checkpoint": str(model_checkpoint), "checkpoint_sha256": model_hash,
            "loaded_runtime_metadata": metadata,
            "weight_sha256_before": agent_weight_hash,
            "online_learning": False, "cmm_writes_enabled": agent.cmm.enable_writes})
        prior_elapsed = 0.0
    started = time.perf_counter()
    cpu_started = time.process_time()
    weight_hash = _agent_weight_hash(agent)
    rss = _working_set_bytes()
    if stats.get("rss_start_bytes") is None:
        stats["rss_start_bytes"] = rss
    if rss is not None:
        stats["rss_peak_sampled_bytes"] = max(stats.get("rss_peak_sampled_bytes") or 0, rss)
    last_progress = stats["ticks_completed"]
    status = "running"
    try:
        while ((total_ticks is not None and stats["ticks_completed"] < total_ticks) or
               (duration_seconds is not None and prior_elapsed + time.perf_counter() - started < duration_seconds)):
            if ((stop_event is not None and stop_event.is_set()) or
                    (stop_file is not None and stop_file.exists())):
                status = "interrupted"
                break
            transitions = driver.before_tick(engine)
            episode_id = engine.world.episode_id
            for row in transitions:
                _record_event(stats, row, recorder, worker_id=worker_id, episode_id=episode_id)
                counts["private_events"] += 1
            family = driver.fault_scheduler.active.family if driver.fault_scheduler.active else None
            engine.step(return_snapshot=False)
            if engine.profile_metrics:
                for name, value in engine.profile_metrics[-1].items():
                    if name.endswith("_ms"):
                        stats["timing_ms"][name] += float(value)
                stats["timing_samples"] += 1
            # The projection is the only object passed to the agent. It is
            # validated once by OperationalEncoderV1 before model inference;
            # the default public_observation API still validates on return.
            # Private schedule, grades, and physical state stay in separate records.
            observation = public_observation(engine, validate=False)
            agent_started_ns = time.perf_counter_ns()
            candidate, deliberation = agent.process(observation)
            stats["agent_process_ns"] += time.perf_counter_ns() - agent_started_ns
            stats["ctm_inference_ns"] += deliberation.inference_time_ns
            stats["cmm_retrieval_calls"] += 1
            if agent.cmm.enable_writes:
                stats["cmm_writes"]["working.observed_fact"] += 1
                if candidate.decision in {"INVESTIGATE", "DIAGNOSE", "VERIFY"}:
                    stats["cmm_writes"]["working.hypothesis"] += 1
            tick = int(observation["incubator_1_direct_observation"]["tick"])
            recorder.append("observations", {"schema_version": OBSERVATION_SCHEMA_VERSION,
                "worker_id": worker_id, "episode_id": episode_id, "tick": tick,
                "observation": observation})
            recorder.append("decisions", {"schema_version": 1, "worker_id": worker_id,
                "episode_id": episode_id, "tick": tick,
                "candidate": candidate.as_dict(), "deliberation": deliberation.__dict__})
            counts["observations"] += 1; counts["decisions"] += 1
            private = {"schema_version": 1, "worker_id": worker_id, "episode_id": episode_id,
                       "tick": tick, "active_fault_family": family,
                       "active_plan": (driver.fault_scheduler.active.as_dict()
                                       if driver.fault_scheduler.active else None),
                       "evaluator_result": engine.results[-1] if engine.results else None}
            recorder.append("private_events", private); counts["private_events"] += 1
            evaluated = engine.results[-1] if engine.results else None
            grade_applicable = family == "supply_voltage" and isinstance(evaluated, dict)
            recorder.append("grades", {"schema_version": 1, "worker_id": worker_id,
                "episode_id": episode_id, "tick": tick,
                "evaluator": "MatrixEvaluatorV1", "applicable": grade_applicable,
                "grade": evaluated.get("score") if grade_applicable else None,
                "evaluator_result": evaluated,
                "note": "Existing evaluator scores Incubator 1 overvoltage protection only; null means no defensible grade for this tick."})
            counts["grades"] += 1
            stats["ticks_completed"] += 1
            if family:
                stats["fault_ticks"][family] += 1
            stats["decisions"][candidate.decision] += 1
            if candidate.decision == "DIAGNOSE": stats["diagnoses"][candidate.diagnosis] += 1
            # Keep live Matrix record retention bounded; disk streams remain complete.
            if stats["ticks_completed"] % 1024 == 0:
                engine.world.events = engine.world.events[-32:]
                engine.evaluator._event_cursor = len(engine.world.events)
                engine.profile_metrics.clear()
                engine.results[:] = engine.results[-1:]
            if stats["ticks_completed"] % checkpoint_interval == 0:
                stats["elapsed_wall_seconds"] = prior_elapsed + time.perf_counter() - started
                stats["cpu_seconds"] += time.process_time() - cpu_started
                stats["record_write_ms"] += recorder.write_elapsed_ns / 1_000_000
                recorder.write_elapsed_ns = 0
                flush_before_checkpoint = recorder.flush_elapsed_ns
                offsets = _checkpoint(worker_dir, engine, agent, driver, stats, recorder,
                    model_hash=model_hash, config=run_config, status="running")
                stats["record_flush_ms"] += (recorder.flush_elapsed_ns - flush_before_checkpoint) / 1_000_000
                recorder.flush_elapsed_ns = 0
                current_rss = _working_set_bytes()
                if current_rss is not None:
                    stats["rss_peak_sampled_bytes"] = max(stats.get("rss_peak_sampled_bytes") or 0,
                                                           current_rss)
                event_counts = dict(counts)
                with gzip.open(private_dir / "matrix-events.jsonl.gz", "rt", encoding="utf-8") as matrix_events:
                    event_counts["matrix_events"] = sum(1 for _ in matrix_events)
                stream_paths = {"observations": worker_dir / "observations.jsonl.gz",
                    "decisions": worker_dir / "decisions.jsonl.gz",
                    "private_events": private_dir / "events.jsonl.gz",
                    "grades": private_dir / "grades.jsonl.gz",
                    "matrix_events": private_dir / "matrix-events.jsonl.gz"}
                atomic_json(worker_dir / "manifest.json", {"schema_version": 1, "status": "running",
                    "worker_id": worker_id, "seed": seed, "ticks_completed": stats["ticks_completed"],
                    "last_tick": tick, "model_checkpoint_sha256": model_hash,
                    "encoder_schema_hash": agent.encoder.schema_hash,
                    "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
                    "record_counts": event_counts, "checkpoint_offsets": offsets,
                    "streams": {name: {"path": str(path), "bytes": path.stat().st_size,
                                        "records": event_counts[name]} for name, path in stream_paths.items()},
                    "pid": os.getpid(), "cmm": {"writes_enabled": agent.cmm.enable_writes,
                            "disabled_write_attempts": agent.cmm.disabled_write_attempts,
                            "working_entries": agent.cmm.diagnostics()["working_count"],
                            "context_policy": "Normal bounded CMM writes are enabled; working context updates each tick and is checkpointed for resume."}})
                cpu_started = time.process_time()
            if stats["ticks_completed"] - last_progress >= progress_interval:
                last_progress = stats["ticks_completed"]
                print(f"{worker_id}: {stats['ticks_completed']} ticks, decision={candidate.decision}", flush=True)
        else:
            status = "complete"
    except KeyboardInterrupt:
        status = "interrupted"
    except Exception as error:
        status = "failed"
        stats["errors"].append({"type": type(error).__name__, "message": str(error)})
        raise
    finally:
        stats["elapsed_wall_seconds"] = prior_elapsed + time.perf_counter() - started
        stats["cpu_seconds"] += time.process_time() - cpu_started
        stats["record_write_ms"] += recorder.write_elapsed_ns / 1_000_000
        flush_before_checkpoint = recorder.flush_elapsed_ns
        offsets = _checkpoint(worker_dir, engine, agent, driver, stats, recorder,
            model_hash=model_hash, config=run_config, status=status)
        stats["record_flush_ms"] += (recorder.flush_elapsed_ns - flush_before_checkpoint) / 1_000_000
        # Final status/checksum requires closing streams at a consistent boundary.
        counts = locals().get("counts", {name: 0 for name in STREAMS})
        paths = {"observations": worker_dir / "observations.jsonl.gz",
                 "decisions": worker_dir / "decisions.jsonl.gz",
                 "private_events": private_dir / "events.jsonl.gz",
                 "grades": private_dir / "grades.jsonl.gz"}
        event_path = private_dir / "matrix-events.jsonl.gz"
        if event_path.exists():
            with gzip.open(event_path, "rt", encoding="utf-8") as matrix_events:
                counts["matrix_events"] = sum(1 for _ in matrix_events)
        else:
            counts["matrix_events"] = 0
        paths["matrix_events"] = event_path
        streams = _stream_summary(paths, counts)
        after_hash = _agent_weight_hash(agent)
        stats["rss_end_bytes"] = _working_set_bytes()
        if after_hash != weight_hash:
            status = "failed"
            stats["errors"].append({"type": "FrozenWeightMismatch", "message": "agent weights changed during life mode"})
        report = {"schema_version": 1, "status": status, "worker_id": worker_id,
            "pid": os.getpid(),
            "seed": seed, "ticks_completed": stats["ticks_completed"],
            "simulated_seconds": stats["ticks_completed"],
            "simulated_days": stats["ticks_completed"] / DAY,
            "normal_ticks": stats["ticks_completed"] - sum(stats["fault_ticks"].values()),
            "wall_elapsed_seconds": stats["elapsed_wall_seconds"],
            "ticks_per_wall_second": stats["ticks_completed"] / max(stats["elapsed_wall_seconds"], 1e-9),
            "cpu_seconds": stats["cpu_seconds"], "rss_start_bytes": stats["rss_start_bytes"],
            "rss_end_bytes": stats["rss_end_bytes"],
            "rss_peak_sampled_bytes": stats["rss_peak_sampled_bytes"],
            "matrix_timing_ms_totals": dict(stats["timing_ms"]),
            "agent_process_seconds": stats["agent_process_ns"] / 1_000_000_000,
            "ctm_inference_seconds": stats["ctm_inference_ns"] / 1_000_000_000,
            "cmm_retrieval_calls": stats["cmm_retrieval_calls"],
            "cmm_writes_by_category": dict(stats["cmm_writes"]),
            "cmm_state": agent.cmm.diagnostics(),
            "matrix_timing_samples": stats["timing_samples"],
            "record_serialization_write_ms": stats["record_write_ms"],
            "record_flush_ms": stats["record_flush_ms"],
            "fault_ticks_by_family": dict(stats["fault_ticks"]),
            "fault_injected_by_family": dict(stats["fault_injected"]),
            "fault_recovered_by_family": dict(stats["fault_recovered"]),
            "benign_events_started_by_family": dict(stats["benign_events"]),
            "decision_counts": dict(stats["decisions"]), "diagnosis_counts": dict(stats["diagnoses"]),
            "weight_sha256_before": weight_hash,
            "weight_sha256_after": after_hash,
            "weights_unchanged": after_hash == weight_hash,
            "online_optimizer_updates": 0, "cmm_writes_enabled": agent.cmm.enable_writes,
            "cmm_disabled_write_attempts": agent.cmm.disabled_write_attempts,
            "streams": streams, "errors": stats["errors"],
            "limitations": ["Frozen inference only: no online weight updates.",
                "Existing evaluator has meaningful per-tick scoring only for Incubator 1 overvoltage.",
                "Fire model is a bounded toy heat/smoke source, not validated fire dynamics."]}
        atomic_json(worker_dir / "worker-report.json", report)
        atomic_json(worker_dir / "manifest.json", {"schema_version": 1, "status": status,
            "worker_id": worker_id, "pid": os.getpid(), "seed": seed,
            "ticks_completed": stats["ticks_completed"],
            "last_tick": stats["ticks_completed"] - 1 if stats["ticks_completed"] else None,
            "model_checkpoint_sha256": model_hash, "encoder_schema_hash": agent.encoder.schema_hash,
            "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
            "record_counts": {name: row["records"] for name, row in streams.items()},
            "checkpoint_offsets": offsets, "streams": streams,
            "checkpoint_path": str(private_dir / "life-checkpoint.pkl"),
            "resume_history": ["resumed"] if resume else [], "errors": stats["errors"]})
        engine.close()
    return report


def _worker_entry(kwargs: dict[str, Any], stop_event) -> None:
    worker_dir = kwargs["worker_dir"]
    worker_dir.mkdir(parents=True, exist_ok=True)
    log_path = worker_dir / "worker.log"
    try:
        with log_path.open("a", encoding="utf-8", buffering=1) as log, redirect_stdout(log), redirect_stderr(log):
            _run_worker(**kwargs, stop_event=stop_event)
    except Exception as error:
        atomic_json(worker_dir / "worker-report.json", {"schema_version": 1, "status": "failed",
            "worker_id": worker_dir.name, "error": f"{type(error).__name__}: {error}"})


def run_life_mode(*, output_dir: str | Path, model_checkpoint: str | Path,
                  master_seed: int = 20261016, workers: int = 1,
                  ticks: int | None = None, duration_hours: float | None = None,
                  checkpoint_interval: int = 10_000, progress_interval: int = 10_000,
                  torch_threads_per_worker: int = 1,
                  resume: bool = False, stop_file: str | Path | None = None,
                  driver_config: dict[str, Any] | None = None) -> dict[str, Any]:
    call_started = time.perf_counter()
    if not 1 <= workers <= 6:
        raise ValueError("workers must be between 1 and 6")
    if checkpoint_interval < 1 or progress_interval < 1:
        raise ValueError("checkpoint and progress intervals must be positive")
    if (ticks is None) == (duration_hours is None):
        raise ValueError("specify exactly one of ticks or duration_hours")
    if ticks is not None and ticks < 1:
        raise ValueError("ticks must be positive")
    if duration_hours is not None and (not math.isfinite(duration_hours) or duration_hours <= 0):
        raise ValueError("duration_hours must be positive and finite")
    output = Path(output_dir)
    source = Path(model_checkpoint)
    if not source.is_file():
        raise FileNotFoundError(f"frozen model checkpoint not found: {source}")
    model_hash = file_sha256(source)
    driver_config = dict(driver_config or {})
    stop_path = Path(stop_file).resolve() if stop_file is not None else None
    if resume:
        manifest_path = output / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"cannot resume without root manifest: {manifest_path}")
        root = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (root.get("model_checkpoint_sha256") != model_hash or root.get("workers") != workers or
                root.get("master_seed") != master_seed or
                root.get("cmm_policy") != "normal governed writes enabled; bounded working memory"):
            raise ValueError("resume model, CMM policy, seed, or worker count differs from original run")
        if root.get("ticks") != ticks or root.get("duration_hours") != duration_hours:
            raise ValueError("resume horizon differs from original run")
        if (root.get("checkpoint_interval") != checkpoint_interval or
                root.get("progress_interval") != progress_interval):
            raise ValueError("resume checkpoint or progress interval differs from original run")
        if root.get("torch_threads_per_worker") != torch_threads_per_worker:
            raise ValueError("resume PyTorch thread configuration differs from original run")
    else:
        if output.exists() and any(output.iterdir()):
            raise FileExistsError(f"refusing to overwrite existing run artifacts: {output}")
        output.mkdir(parents=True, exist_ok=True)
        root = {"schema_version": 1, "status": "running", "run_id": output.name,
                "master_seed": master_seed, "workers": workers, "ticks": ticks,
                "duration_hours": duration_hours, "model_checkpoint": str(source.resolve()),
                "checkpoint_interval": checkpoint_interval,
                "progress_interval": progress_interval,
                "torch_threads_per_worker": torch_threads_per_worker,
                "model_checkpoint_sha256": model_hash, "weight_training": "frozen; optimizer updates=0",
                "cmm_policy": "normal governed writes enabled; bounded working memory",
                "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
                "fault_rate_target": {"mean_interval_days": 21, "jitter_days": 7,
                                      "first_onset_days": [14, 28]},
                "seed_assignments": {f"worker-{index:03d}": _derived_seed(master_seed, index)
                                     for index in range(workers)}, "workers_status": {}}
        atomic_json(output / "seed-manifest.json", {"schema_version": 1,
            "seed_role": "life-run workers only; no train/validation/test partitioning",
            "master_seed": master_seed, "worker_seeds": root["seed_assignments"]})
    atomic_json(output / "manifest.json", root)
    duration_seconds = duration_hours * 3600 if duration_hours is not None else None
    kwargs_list = []
    for index in range(workers):
        worker_id = f"worker-{index:03d}"
        worker_dir = output / worker_id
        kwargs_list.append({"worker_dir": worker_dir, "worker_index": index,
            "seed": _derived_seed(master_seed, index), "total_ticks": ticks,
            "duration_seconds": duration_seconds, "checkpoint_interval": checkpoint_interval,
            "progress_interval": progress_interval, "model_checkpoint": source,
            "model_hash": model_hash, "resume": resume, "driver_config": driver_config,
            "stop_file": stop_path,
            "torch_threads_per_worker": torch_threads_per_worker})
    if workers == 1:
        worker_reports = [_run_worker(**kwargs_list[0])]
    else:
        import multiprocessing
        context = multiprocessing.get_context("spawn")
        stop_event = context.Event()
        processes = [context.Process(target=_worker_entry, args=(kwargs, stop_event), daemon=False)
                     for kwargs in kwargs_list]
        try:
            for process in processes:
                process.start()
            atomic_json(output / "worker-processes.json", {"schema_version": 1,
                "workers": [{"worker_id": f"worker-{index:03d}", "pid": process.pid,
                             "seed": kwargs_list[index]["seed"]}
                            for index, process in enumerate(processes)]})
            while any(process.is_alive() for process in processes):
                for index, process in enumerate(processes):
                    worker_dir = output / f"worker-{index:03d}"
                    report_file = worker_dir / "manifest.json"
                    if report_file.exists():
                        root["workers_status"][worker_dir.name] = json.loads(report_file.read_text(encoding="utf-8")).get("status")
                atomic_json(output / "manifest.json", root)
                time.sleep(1.0)
            for process in processes:
                process.join()
            worker_reports = [json.loads((output / f"worker-{index:03d}" / "worker-report.json").read_text(encoding="utf-8"))
                              for index in range(workers)]
        except KeyboardInterrupt:
            stop_event.set()
            for process in processes:
                process.join(timeout=15)
            for process in processes:
                if process.is_alive():
                    process.terminate(); process.join()
            worker_reports = []
            for index in range(workers):
                worker_dir = output / f"worker-{index:03d}"
                report_path = worker_dir / "worker-report.json"
                manifest_path = worker_dir / "manifest.json"
                if report_path.exists():
                    worker_reports.append(json.loads(report_path.read_text(encoding="utf-8")))
                elif manifest_path.exists():
                    saved_worker = json.loads(manifest_path.read_text(encoding="utf-8"))
                    worker_reports.append({"worker_id": worker_dir.name, "status": "interrupted",
                        "ticks_completed": int(saved_worker.get("ticks_completed", 0)),
                        "checkpoint_path": saved_worker.get("checkpoint_path"),
                        "note": "Worker stopped before final report; last manifest/checkpoint is authoritative."})
    model_hash_after = file_sha256(source)
    statuses = [report.get("status", "failed") for report in worker_reports]
    status = "complete" if statuses and all(item == "complete" for item in statuses) else (
        "failed" if "failed" in statuses else "interrupted")
    root.update({"status": status, "model_checkpoint_sha256_after": model_hash_after,
                 "model_checkpoint_unchanged": model_hash_after == model_hash,
                 "worker_reports": [str(output / f"worker-{i:03d}" / "worker-report.json")
                                    for i in range(len(worker_reports))],
                 "workers_status": {f"worker-{i:03d}": report.get("status", "failed")
                                    for i, report in enumerate(worker_reports)}})
    atomic_json(output / "manifest.json", root)
    aggregate = {"schema_version": 1, "status": status, "workers": workers,
                 "ticks_completed": sum(row.get("ticks_completed", 0) for row in worker_reports),
                 "simulated_days_per_worker": [row.get("simulated_days", 0) for row in worker_reports],
                 "wall_elapsed_seconds": max((row.get("wall_elapsed_seconds", 0) for row in worker_reports), default=0),
                 "aggregate_wall_elapsed_seconds": time.perf_counter() - call_started,
                 "aggregate_ticks_per_second": sum(row.get("ticks_completed", 0) for row in worker_reports) /
                    max(time.perf_counter() - call_started, 1e-9),
                 "worker_reports": worker_reports,
                 "model_checkpoint_sha256_before": model_hash,
                 "model_checkpoint_sha256_after": model_hash_after,
                 "model_checkpoint_unchanged": model_hash_after == model_hash,
                 "note": "No fault schedule, hidden truth, or grades were supplied to Jarvis; evaluator results are private stream data."}
    atomic_json(output / "REPORT.json", aggregate)
    return aggregate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-checkpoint", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--workers", type=int, default=1)
    horizon = parser.add_mutually_exclusive_group(required=True)
    horizon.add_argument("--ticks", type=int)
    horizon.add_argument("--duration-hours", type=float)
    parser.add_argument("--checkpoint-interval", type=int, default=10_000)
    parser.add_argument("--progress-interval", type=int, default=10_000)
    parser.add_argument("--torch-threads-per-worker", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-file", type=Path)
    args = parser.parse_args(argv)
    result = run_life_mode(output_dir=args.output, model_checkpoint=args.model_checkpoint,
        master_seed=args.seed, workers=args.workers, ticks=args.ticks,
        duration_hours=args.duration_hours, checkpoint_interval=args.checkpoint_interval,
        progress_interval=args.progress_interval, resume=args.resume,
        stop_file=args.stop_file, torch_threads_per_worker=args.torch_threads_per_worker)
    print(json.dumps({key: result[key] for key in ("status", "ticks_completed", "model_checkpoint_unchanged")}, sort_keys=True))
    return 0 if result["status"] in {"complete", "interrupted"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
