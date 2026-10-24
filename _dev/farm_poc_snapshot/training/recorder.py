"""Streaming, crash-visible JSONL episode recorder."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .contract import OBSERVATION_SCHEMA_VERSION, validate_observation
from farm_poc.cognitive_runtime.encoder import OperationalEncoderV1

TARGET_SCHEMA_VERSION = 1
EXPERT_AUDIT_SCHEMA_VERSION = 1


def _json_line(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class EpisodeRecorder:
    """Writes each stream incrementally; only a finalized manifest says complete."""

    def __init__(self, output_dir: str | Path, episode_id: str, *, seed: int,
                 scenario_id: str, episode_index: int,
                 scenario_config: dict | None = None, environment_config: dict | None = None):
        self.root = Path(output_dir)
        self.episode_id = episode_id
        self.seed = int(seed)
        self.scenario_id = scenario_id
        self.episode_index = int(episode_index)
        self.scenario_config = dict(scenario_config or {})
        self.environment_config = dict(environment_config or {})
        self.encoder_schema_hash = OperationalEncoderV1().schema_hash
        self.root.mkdir(parents=True, exist_ok=True)
        self.paths = {
            "observations": self.root / "observations" / f"{episode_id}.jsonl",
            "expert_targets": self.root / "expert_targets" / f"{episode_id}.jsonl",
            "expert_audit": self.root / "expert_audit" / f"{episode_id}.jsonl",
            "evaluator_private": self.root / "evaluator_private" / f"{episode_id}.jsonl",
        }
        self.manifest_path = self.root / "manifests" / f"{episode_id}.json"
        for path in (*self.paths.values(), self.manifest_path):
            path.parent.mkdir(parents=True, exist_ok=True)
        if any(path.exists() or path.with_suffix(path.suffix + ".tmp").exists()
               for path in (*self.paths.values(), self.manifest_path)):
            raise FileExistsError(f"episode output already exists: {episode_id}")
        self.temps = {key: path.with_suffix(path.suffix + ".tmp") for key, path in self.paths.items()}
        self.streams = {key: path.open("x", encoding="utf-8", newline="\n")
                        for key, path in self.temps.items()}
        self.count = 0
        self.closed = False
        self._write_manifest({"schema_version": 1, "episode_id": episode_id,
                              "episode_index": self.episode_index, "seed": self.seed,
                              "scenario_id": scenario_id, "scenario_config": self.scenario_config,
                              "environment_config": self.environment_config, "status": "incomplete",
                              "tick_count": 0, "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
                              "target_schema_version": TARGET_SCHEMA_VERSION,
                              "expert_audit_schema_version": EXPERT_AUDIT_SCHEMA_VERSION,
                              "encoder_schema_hash": self.encoder_schema_hash})

    def _write_manifest(self, data):
        temp = self.manifest_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temp, self.manifest_path)

    def write_tick(self, tick: int, observation: dict, target: dict, evaluator_private: dict,
                   *, expert_audit: dict | None = None, simulation_tick: int | None = None, phase: str = "step"):
        if self.closed:
            raise RuntimeError("recorder is closed")
        validate_observation(observation)
        if target.get("schema_version") != TARGET_SCHEMA_VERSION:
            raise ValueError("unsupported expert target schema")
        if tick < 0 or (self.count == 0 and tick != 0) or (self.count and tick != self.last_tick + 1):
            raise ValueError("recorded ticks must start at zero and be contiguous")
        if simulation_tick is None:
            simulation_tick = int(observation["incubator_1_direct_observation"]["tick"])
        if simulation_tick < 0 or phase not in {"reset", "step"}:
            raise ValueError("simulation_tick must be nonnegative and phase must be reset or step")
        aligned = {"episode_id": self.episode_id, "tick": int(tick),
                   "simulation_tick": int(simulation_tick), "phase": phase}
        rows = {
            "observations": {**aligned, "observation": observation},
            "expert_targets": {**aligned, "target": target},
            "expert_audit": {**aligned, "audit": expert_audit or {
                "schema_version": EXPERT_AUDIT_SCHEMA_VERSION, "available": False,
                "reason": "policy_did_not_emit_a_trace"}},
            "evaluator_private": {**aligned, "private": evaluator_private},
        }
        for key, row in rows.items():
            self.streams[key].write(_json_line(row))
        self.count += 1
        self.last_tick = int(tick)

    def finalize(self, *, completed: bool):
        if self.closed:
            raise RuntimeError("recorder is closed")
        for stream in self.streams.values():
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
        self.closed = True
        if not completed:
            self._write_manifest({"schema_version": 1, "episode_id": self.episode_id,
                                  "episode_index": self.episode_index, "seed": self.seed,
                                  "scenario_id": self.scenario_id, "scenario_config": self.scenario_config,
                                  "environment_config": self.environment_config, "status": "incomplete",
                                  "tick_count": self.count,
                                  "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
                                  "target_schema_version": TARGET_SCHEMA_VERSION,
                                  "expert_audit_schema_version": EXPERT_AUDIT_SCHEMA_VERSION,
                                  "encoder_schema_hash": self.encoder_schema_hash})
            return
        for key, temp in self.temps.items():
            os.replace(temp, self.paths[key])
        manifest = {"schema_version": 1, "episode_id": self.episode_id,
                    "episode_index": self.episode_index, "seed": self.seed,
                    "scenario_id": self.scenario_id, "scenario_config": self.scenario_config,
                    "environment_config": self.environment_config, "status": "complete",
                    "tick_count": self.count,
                    "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
                    "target_schema_version": TARGET_SCHEMA_VERSION,
                    "expert_audit_schema_version": EXPERT_AUDIT_SCHEMA_VERSION,
                    "encoder_schema_hash": self.encoder_schema_hash,
                    "files": {key: {"path": path.relative_to(self.root).as_posix(),
                                    "sha256": _sha256(path)} for key, path in self.paths.items()}}
        self._write_manifest(manifest)

    def fail(self, error: BaseException):
        for stream in self.streams.values():
            if not stream.closed:
                stream.flush()
                stream.close()
        self.closed = True
        self._write_manifest({"schema_version": 1, "episode_id": self.episode_id,
                              "episode_index": self.episode_index, "seed": self.seed,
                              "scenario_id": self.scenario_id, "scenario_config": self.scenario_config,
                              "environment_config": self.environment_config, "status": "incomplete",
                              "tick_count": self.count,
                              "error": f"{type(error).__name__}: {error}"})


def load_training_episode(root: str | Path, episode_id: str) -> dict:
    """Load only public observations and expert targets; private stream is never opened."""
    root = Path(root)
    manifest = json.loads((root / "manifests" / f"{episode_id}.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise ValueError("episode is incomplete")
    streams = {}
    for key in ("observations", "expert_targets"):
        file_info = manifest["files"][key]
        path = root / file_info["path"]
        if _sha256(path) != file_info["sha256"]:
            raise ValueError(f"checksum mismatch: {key}")
        streams[key] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    obs, targets = streams["observations"], streams["expert_targets"]
    if len(obs) != manifest["tick_count"] or len(targets) != manifest["tick_count"]:
        raise ValueError("manifest tick count does not match stream length")
    alignment_fields = ("episode_id", "tick", "simulation_tick", "phase")
    if [tuple(row[field] for field in alignment_fields) for row in obs] != [
            tuple(row[field] for field in alignment_fields) for row in targets]:
        raise ValueError("observation/target alignment failure")
    audits = None
    if "expert_audit" in manifest.get("files", {}):
        info = manifest["files"]["expert_audit"]
        path = root / info["path"]
        if _sha256(path) != info["sha256"]:
            raise ValueError("checksum mismatch: expert_audit")
        audits = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if len(audits) != manifest["tick_count"]:
            raise ValueError("manifest tick count does not match expert audit length")
        if [tuple(row[field] for field in alignment_fields) for row in audits] != [
                tuple(row[field] for field in alignment_fields) for row in obs]:
            raise ValueError("observation/audit alignment failure")
    return {"episode_id": episode_id,
            "observations": [row["observation"] for row in obs],
            "targets": [row["target"] for row in targets],
            "expert_audit": None if audits is None else [row["audit"] for row in audits]}


def validate_episode_bundle(root: str | Path, episode_id: str) -> None:
    """Trusted audit of all streams, including evaluator-private row alignment."""
    root = Path(root)
    manifest = json.loads((root / "manifests" / f"{episode_id}.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise ValueError("episode is incomplete")
    rows = {}
    stream_keys = ("observations", "expert_targets", "evaluator_private")
    if "expert_audit" in manifest.get("files", {}):
        stream_keys = (*stream_keys, "expert_audit")
    for key in stream_keys:
        info = manifest["files"][key]
        path = root / info["path"]
        if _sha256(path) != info["sha256"]:
            raise ValueError(f"checksum mismatch: {key}")
        rows[key] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    expected = [(row.get("episode_id"), row.get("tick"), row.get("simulation_tick"), row.get("phase"))
                for row in rows["observations"]]
    if expected != [(manifest["episode_id"], tick, expected[tick][2], expected[tick][3])
                    for tick in range(manifest["tick_count"])]:
        raise ValueError("observation metadata is not contiguous")
    for key, stream in rows.items():
        alignment = [(row.get("episode_id"), row.get("tick"), row.get("simulation_tick"), row.get("phase"))
                     for row in stream]
        if alignment != expected:
            raise ValueError(f"{key} tick alignment failure")
