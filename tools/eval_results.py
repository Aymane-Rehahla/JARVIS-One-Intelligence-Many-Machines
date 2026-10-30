"""The shared result format of the evaluation (plan section 8): one JSON file per metric, E1 to E11.

Every script in tests/llm/eval/ ends with `write_result(EvalResult(...))`, which writes `logs/eval/<id>.json`;
`tests/llm/eval/run_all.py` reads them all and writes docs/results-phase6.md. A missed target is reported as it was
measured, with the reason in `notes`; a metric that was not run has no file, and the report says so.

Fixed by the senior before 6C, 6D and 6E were built in parallel (docs/handoff/README.md), so that every builder writes
the same shape.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "eval"
EvalId = Literal["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9", "E10", "E11"]


class EvalResult(BaseModel):
    """One metric's measured result, with everything needed to reproduce it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: EvalId
    title: str = Field(min_length=1)
    target: str                                  # the plan's target in words, e.g. "Recall@4 >= 0.9"
    passed: bool | None                          # None: no pass/fail target (E11), or not decidable from this run
    measured: dict[str, JsonValue]               # the numbers, named; every number in the report comes from here
    command: str = Field(min_length=1)           # the exact command that produced this file
    commit: str = Field(min_length=7)            # git HEAD, with "-dirty" when the work tree had changes
    seeds: list[int]
    models: dict[str, str]                       # model file name -> SHA-256 of every model the run used
    started_utc: str
    duration_s: float = Field(ge=0.0)
    notes: str = ""                              # what was not covered and why; why a target was missed


def git_commit(root: Path = ROOT) -> str:
    """The commit the work tree is at, marked "-dirty" when it has uncommitted changes."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True,
                          timeout=10).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True,
                           timeout=30).stdout.strip()
    return f"{head}-dirty" if dirty else head


def model_hashes(names: list[str], manifest: Path = ROOT / "models" / "MANIFEST.json") -> dict[str, str]:
    """SHA-256 of the named model files, from models/MANIFEST.json (keys are paths under models/)."""
    files: dict[str, dict[str, JsonValue]] = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    found: dict[str, str] = {}
    for name in names:
        match = [path for path in files if Path(path).name == name or path == name]
        if len(match) != 1:
            raise KeyError(f"{name}: {len(match)} entries in {manifest.name}")
        found[match[0]] = str(files[match[0]]["sha256"])
    return found


def file_sha256(path: Path) -> str:
    """SHA-256 of a file that is not in the model manifest (a brain engine, a test set)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_result(result: EvalResult, out: Path = OUT) -> Path:
    """Write `<out>/<id>.json` (replacing an earlier run of the same metric) and return its path."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result.id}.json"
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def read_results(out: Path = OUT) -> dict[str, EvalResult]:
    """Every result written so far, by metric id."""
    return {path.stem: EvalResult.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(out.glob("E*.json"))}
