"""Prove that the installed copy decides exactly like the release (P3 step 4, decision 0015).

    <python> -B tools/release_equivalence.py run --code DIR --brains DIR [--upstream DIR] --out FILE [--max-ticks N]
    .venv\\Scripts\\python tools\\release_equivalence.py compare A.json B.json

`run` uses the exam's harness from the given code (`farm_poc.scenarios.episode.build` with a
`ctm_training.practice.CheckpointBrains` factory: the tuned decision layer, hide_notes=True, hold_calls=True, one torch
thread) on one ordinary library episode per demo-fault family (P1's mapping, decision 0020), and records for every tick
and node: assessment, diagnosis, confidence (6 decimals), the safety gate's action and communication decisions, the
incident id. Run it twice: with the training's Python on the release (`--code <release>/code_snapshot --brains
<release>/brains --upstream <upstream folder>`, all on the command line) and with .venv on the installed copy
(`--code matrix/release_v31 --brains matrix/release_v31/brains`; its `ctm_training/model.py` finds the upstream modules
by its own path). `compare` passes when every assessment, diagnosis, gate decision and incident id is identical and
every confidence is within 1e-5; otherwise it prints the first differing tick and node, and exits 1.

`--restore-salts` (diagnosis, installed copy only): the Matrix code seeds its random streams with strings that begin
with the old project name (the phone service, the sensors, the weather, the peer bus, the episode id). The word map
turns them into "testbed-...", so the copy's farm draws different random numbers. This option maps those seed strings
back at run time (from name_guard.OLD, never written anywhere) without changing a file of the copy, to show whether
that is the whole difference.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ENGINE_FILES = {"incubator": "reasoning_engine_INCv3.1.pt", "hvac": "reasoning_engine_HVACv3.1.pt",
                "animal_protection": "reasoning_engine_APv3.1.pt"}
#: P1's demo faults (decision 0020): the library family and the variant matrix/farm_runtime.py found for each.
EPISODES = (("heater_relay_stuck", 9), ("supply_fan_failure", 1), ("sensor_fault", 228), ("overvoltage", 1))
CONFIDENCE_TOLERANCE = 1e-5


def restore_salts() -> None:
    """Map the copy's "testbed-..." random-seed strings back to the release's, in this process only."""
    import random
    import uuid
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from name_guard import OLD

    def original(value: Any) -> Any:
        return OLD + value[len("testbed"):] if isinstance(value, str) and value.startswith("testbed-") else value

    class SaltedRandom(random.Random):
        def seed(self, a: Any = None, version: int = 2) -> None:
            super().seed(original(a), version)

    uuid5 = uuid.uuid5
    random.Random = SaltedRandom  # type: ignore[misc]
    uuid.uuid5 = lambda namespace, name: uuid5(namespace, original(name))  # type: ignore[assignment]


def run(code: Path, brains: Path, upstream: Path | None, max_ticks: int | None) -> dict[str, Any]:
    if upstream is not None:
        sys.path.insert(0, str(upstream))
    sys.path.insert(0, str(code))
    import torch
    torch.set_num_threads(1)
    from ctm_training.practice import CheckpointBrains
    from farm_poc.nodes.contracts import NODE_IDS
    from farm_poc.scenarios.episode import build
    from farm_poc.scenarios.library import _make

    checkpoints = {machine: brains / machine / name for machine, name in ENGINE_FILES.items()}
    result: dict[str, Any] = {"python": sys.version.split()[0], "torch": torch.__version__,
                              "threads": torch.get_num_threads(), "episodes": {}}
    for family, variant in EPISODES:
        spec = _make(family, variant, False)
        factory = CheckpointBrains(checkpoints, str(brains), decide=True, hide_notes=True, hold_calls=True)
        _, runtime = build(spec, with_faults=True, brain_factory=factory)
        ticks = int(spec.duration_ticks) if max_ticks is None else min(int(spec.duration_ticks), max_ticks)
        started = time.perf_counter()
        rows: list[dict[str, list[Any]]] = []
        for _ in range(ticks):
            results = runtime.step()
            row: dict[str, list[Any]] = {}
            for node in NODE_IDS:
                found = results[node]
                output = found["output"]
                row[node] = [output.assessment, output.diagnosis, round(float(output.confidence), 6),
                             json.dumps(found.get("action"), sort_keys=True, default=str),
                             json.dumps(found.get("communication"), sort_keys=True, default=str),
                             found.get("incident_id")]
            rows.append(row)
        result["episodes"][f"{family}-{variant}"] = {"ticks": ticks, "duration_ticks": int(spec.duration_ticks),
                                                     "seconds": round(time.perf_counter() - started, 1),
                                                     "rows": rows}
        print(f"{family} v{variant}: {ticks} ticks in {result['episodes'][f'{family}-{variant}']['seconds']} s",
              flush=True)
    return result


def compare(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """Every difference that breaks the rule, first one first (empty: equivalent)."""
    problems: list[str] = []
    for name in a["episodes"]:
        if name not in b["episodes"]:
            problems.append(f"{name}: missing on one side")
            continue
        rows_a, rows_b = a["episodes"][name]["rows"], b["episodes"][name]["rows"]
        if len(rows_a) != len(rows_b):
            problems.append(f"{name}: {len(rows_a)} vs {len(rows_b)} ticks")
        for tick, (row_a, row_b) in enumerate(zip(rows_a, rows_b)):
            for node in row_a:
                left, right = row_a[node], row_b.get(node)
                if right is None:
                    problems.append(f"{name} tick {tick} {node}: missing")
                    continue
                same = (left[0], left[1], left[3], left[4], left[5]) == (right[0], right[1], right[3], right[4], right[5])
                if not same or abs(left[2] - right[2]) > CONFIDENCE_TOLERANCE:
                    problems.append(f"{name} tick {tick} {node}: {left} vs {right}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run")
    runner.add_argument("--code", type=Path, required=True)
    runner.add_argument("--brains", type=Path, required=True)
    runner.add_argument("--upstream", type=Path)
    runner.add_argument("--out", type=Path, required=True)
    runner.add_argument("--max-ticks", type=int)
    runner.add_argument("--restore-salts", action="store_true", help="diagnosis: see the module docstring")
    checker = commands.add_parser("compare")
    checker.add_argument("a", type=Path)
    checker.add_argument("b", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run":
        if not sys.flags.dont_write_bytecode:
            print("run this with -B: no .pyc may be written next to the code", file=sys.stderr)
            return 2
        if args.restore_salts:
            restore_salts()
        result = run(args.code.resolve(), args.brains.resolve(), args.upstream.resolve() if args.upstream else None,
                     args.max_ticks)
        result["restored_salts"] = bool(args.restore_salts)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result), encoding="utf-8")
        return 0
    a, b = (json.loads(path.read_text(encoding="utf-8")) for path in (args.a, args.b))
    problems = compare(a, b)
    summary = {name: {"ticks": episode["ticks"], "of": episode["duration_ticks"],
                      "seconds": [episode["seconds"], b["episodes"].get(name, {}).get("seconds")]}
               for name, episode in a["episodes"].items()}
    print(json.dumps({"sides": [{k: a[k] for k in ("python", "torch", "threads")},
                                {k: b[k] for k in ("python", "torch", "threads")}],
                      "episodes": summary, "differences": len(problems), "first": problems[:3]}, indent=1))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
