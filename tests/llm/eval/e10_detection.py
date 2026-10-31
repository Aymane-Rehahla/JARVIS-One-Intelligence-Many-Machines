"""E10, detection part (plan section 8, P3 section 7): are the four demo faults caught, and how fast?

The four demo faults (decision 0020) x 5 engine seeds that no training, judging or exam episode used, each run twice
on the same release copy (`matrix/release_v31`): with the trained brains (the trained feed, decision 0021) and with
the release's own rule brains (`NodeRuntime(engine)` without brains). Each run: the trained feed's ordinary day with
that seed, WARMUP ticks, the fault injected as the website injects it, then at most TICKS ticks.
**Caught:** a faulted machine's node leaves NORMAL with a diagnosis of the fault's kind (FAULT_DIAGNOSES below); the
time is farm seconds from the injection to that first output. Nothing is tuned.
**New seeds, proven:** the seeds are `_seed("jarvis-e10", k)` (the library's own 48-bit hash, another prefix); the
script rebuilds the seed of every spec of every library family for the variant blocks the release's code used
(library 0..999, check 25000, tune 50000, judge 60000, exam 90000, confirm 95000, fresh exam 97000; 1,000 each, ordinary
and novel; plus the normal days of every length from 1 to 48 hours) and checks that none of the five is among them.
Out: `logs/eval/E10.json` through `write_result`, and a table on stdout.

Run: `.venv\\Scripts\\python tests\\llm\\eval\\e10_detection.py [--ticks N]`
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
for _extra in (ROOT / "tools", ROOT / "nodes" / "incubator-1", ROOT / "matrix"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import farm_runtime as fr  # noqa: E402
from eval_results import EvalResult, git_commit, utc_now, write_result  # noqa: E402

TICKS = 900
WARMUP = 30
SEEDS = 5
BLOCKS = (0, 25000, 50000, 60000, 90000, 95000, 97000)
#: The fault's kind as the brains name it (the diagnoses of farm_poc/nodes/contracts.py).
FAULT_DIAGNOSES = {"heater_relay_stuck": {"HEATER_FAULT"}, "supply_fan_failure": {"SUPPLY_FAN_FAULT"},
                   "humidity_sensor_drift": {"SENSOR_FAULT"}, "supply_overvoltage": {"SUPPLY_OVERVOLTAGE"}}


class Quiet:
    async def publish(self, *args: Any, **kwargs: Any) -> None:
        pass


def fresh_seeds() -> tuple[list[int], int]:
    """The five seeds, and how many library seeds they were checked against."""
    from farm_poc.scenarios.library import NOVEL_PLAN, PLAN, _make, _normal, _seed
    used: set[int] = set()
    for family in sorted(set(PLAN) | (set(NOVEL_PLAN) - {"equipment"})):
        for block in BLOCKS:
            for variant in range(block, block + 1000):
                for novel in (False, True):
                    used.add(int(_make(family, variant, novel).seed))
    for variant in range(1000):
        for novel in (False, True):
            for hours in range(1, 49):
                used.add(int(_normal(variant, novel, hours).seed))
    seeds = [_seed("jarvis-e10", k) for k in range(SEEDS)]
    clash = [s for s in seeds if s in used]
    if clash:
        raise SystemExit(f"seeds already used by the training: {clash}")
    return seeds, len(used)


async def one(seed: int, scenario: str, brains: str, ticks: int, folder: Path) -> dict[str, Any]:
    farm = fr.FarmRuntime(Quiet(), logger=logging.getLogger("e10"), feed="trained", seed=seed,  # type: ignore[arg-type]
                          running=False, data_dir=folder)
    if brains == "rule":
        farm.close_memories()
        farm.runtime = farm._release.NodeRuntime(farm.engine)    # the release's own rule brains, same engine
    for _ in range(WARMUP):
        farm.step()
    result = await farm.inject_fault(scenario)  # type: ignore[arg-type]
    start_s = farm.time_s
    wanted = FAULT_DIAGNOSES[scenario]
    caught: dict[str, Any] | None = None
    first_other: dict[str, Any] | None = None
    for _ in range(ticks):
        outputs = farm.step()
        for node in result.devices:
            output = outputs[node]["output"]
            if output.assessment != "NORMAL":
                row = {"node": node, "after_s": round(farm.time_s - start_s, 1), "assessment": output.assessment,
                       "diagnosis": output.diagnosis}
                if output.diagnosis in wanted:
                    caught = row
                elif first_other is None:
                    first_other = row
        if caught:
            break
    farm.close_memories()
    return {"caught": caught is not None, "detection": caught, "first_other_conclusion": first_other,
            "variant": result.variant}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticks", type=int, default=TICKS)
    args = parser.parse_args(argv)
    started_utc, started = utc_now(), time.monotonic()
    fr.load_release()
    seeds, checked = fresh_seeds()
    print(f"seeds {seeds}: new (checked against {checked} library seeds)", flush=True)
    runs: dict[str, dict[str, list[dict[str, Any]]]] = {}
    with tempfile.TemporaryDirectory() as folder:
        for brains in ("trained", "rule"):
            runs[brains] = {}
            for scenario in fr.SCENARIO_IDS:
                runs[brains][scenario] = [asyncio.run(one(seed, scenario, brains, args.ticks, Path(folder)))
                                          for seed in seeds]
                caught = [r for r in runs[brains][scenario] if r["caught"]]
                times = sorted(r["detection"]["after_s"] for r in caught)
                print(f"{brains:8} {scenario:22} caught {len(caught)}/{len(seeds)}  seconds {times}", flush=True)
    summary = {brains: {scenario: {"caught": sum(r["caught"] for r in rows), "of": len(rows),
                                   "median_s": (sorted(r["detection"]["after_s"] for r in rows if r["caught"])
                                                [sum(r["caught"] for r in rows) // 2]
                                                if any(r["caught"] for r in rows) else None)}
                        for scenario, rows in by.items()} for brains, by in runs.items()}
    path = write_result(EvalResult(
        id="E10", title="Detection of the four demo faults: trained brains vs the rule brains",
        target="each demo fault caught by the trained brains (reported, not tuned)", passed=None,
        measured=json.loads(json.dumps({"summary": summary, "seeds": [str(s) for s in seeds], "library_seeds_checked": checked,
                  "ticks_after_injection": args.ticks, "warmup_ticks": WARMUP,
                  "fault_diagnoses": {k: sorted(v) for k, v in FAULT_DIAGNOSES.items()},
                  "runs": runs})),
        command=f".venv\\Scripts\\python tests\\llm\\eval\\e10_detection.py --ticks {args.ticks}",
        commit=git_commit(), seeds=[int(s) for s in seeds], models={}, started_utc=started_utc,
        duration_s=round(time.monotonic() - started, 1),
        notes="Detection part only; the explanation part (the agent's words) is measured with the agent. "
              "Trained feed with renamed random seeds (decision 0021). A fault ending before detection counts as "
              "not caught."))
    print(f"E10 written -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
