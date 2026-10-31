"""E9, the epistemic wall (plan section 8, P3 section 7): nothing a node can hear names a fault.

In: the trained feed (`matrix/release_v31`, decision 0021) in this process on a private nats-server, the four node
programs (no language model) in this process, and a recorder on every subject (`>`), which covers everything a node
subscribes to: `farm.<node>.*`, `farm.links`, `farm.sim`, `jarvis.*`, `mentor.llm_failures`. For each of the four
demo faults: a fresh farm run, the fault injected, TICKS farm ticks at full speed.
The forbidden strings are collected in this process from the engine: the website's scenario id, the library family,
the episode ids, every string value of the fault's spec (its mode, channel...), and every string in the engine's
hidden truth and fault log; the machines' names and "injected"/"recovered" are public words and left out. Each
message is scanned for each string as an exact token (case-sensitive: the machines' own alarm codes are upper case).
Judgement, reported per match: a value a machine publishes itself in a readings field, of a (field, value) pair
listed in MACHINE_TELEMETRY, is its public telemetry (a physical effect), not a leak; a word that is part of a code
the node's own reasoning published in this run (its diagnosis or evidence on farm.<node>.assessment, e.g. an owner
alert saying "supply overvoltage" after the incubator concluded SUPPLY_OVERVOLTAGE) is that node's own conclusion, not a
leak. Target: 0 leaks.
Out: `logs/eval/E9.json` through `write_result`, and a summary on stdout.

Run: `.venv\\Scripts\\python tests\\llm\\eval\\e9_epistemic_wall.py [--ticks N]`
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import socket
import subprocess
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
from comms.nats_link import NatsLink  # noqa: E402
from contracts import NODE_IDS, load_node_config  # noqa: E402
from eval_results import EvalResult, git_commit, utc_now, write_result  # noqa: E402
from node import Node  # noqa: E402

NATS_SERVER = ROOT / "third_party" / "nats-server" / "bin" / "nats-server.exe"
TICKS = 300
PUBLIC = set(NODE_IDS) | {"injected", "recovered", ""}
#: Readings a machine publishes by itself whose value happens to equal a fault's word (6W review, L2).
MACHINE_TELEMETRY = {("power_status", "overvoltage")}
LOG = logging.getLogger("e9")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        return [found for key, item in value.items() for found in strings(item, f"{path}/{key}")]
    if isinstance(value, list):
        return [found for item in value for found in strings(item, f"{path}/*")]
    return [(path, value)] if isinstance(value, str) else []


def forbidden(farm: fr.FarmRuntime, scenario_id: str) -> set[str]:
    words = {scenario_id, fr.DEMO_FAULTS[scenario_id][0], str(farm.engine.world.episode_id)}  # type: ignore[index]
    _, spec = farm.demo_spec(scenario_id)  # type: ignore[arg-type]
    words.add(str(getattr(spec, "episode_id", "")))
    for fault in spec.faults:
        words |= {v for k, v in fault.items() if isinstance(v, str) and k != "device"}
    words |= {text for _, text in strings(farm.engine.hidden_truth)}
    words |= {text for _, text in strings(json.loads(json.dumps(farm.engine.fault_log, default=str)))}
    return words - PUBLIC


async def run_fault(farm: fr.FarmRuntime, scenario_id: str, ticks: int, recorded: list[tuple[str, bytes]]) -> dict[str, Any]:
    await farm.control("reset")
    recorded.clear()
    await farm.inject_fault(scenario_id)  # type: ignore[arg-type]
    stop = asyncio.Event()
    start = farm.tick
    task = asyncio.create_task(farm.run(stop))
    while farm.tick < start + ticks:
        await asyncio.sleep(0.2)
    stop.set()
    await task
    await asyncio.sleep(1.0)                                     # the last messages arrive
    words = forbidden(farm, scenario_id)
    patterns = {w: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(w)}(?![A-Za-z0-9_])") for w in sorted(words)}
    leaks: list[dict[str, str]] = []
    telemetry: dict[str, int] = {}
    concluded: dict[str, int] = {}
    codes: set[str] = set()                                     # what the reasoning itself published in this run
    for subject, data in list(recorded):
        if subject.startswith("farm.") and subject.endswith(".assessment"):
            body = json.loads(data)
            codes |= {str(body.get("diagnosis", ""))} | {str(e) for e in body.get("evidence") or []}
    subjects: dict[str, int] = {}
    for subject, data in list(recorded):
        family = subject.split(".")[0] + "." + (subject.split(".")[2] if subject.count(".") >= 2 else subject.split(".")[-1])
        subjects[family] = subjects.get(family, 0) + 1
        text = data.decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except ValueError:
            payload = text
        for path, value in strings(payload):
            for word, pattern in patterns.items():
                if (match := pattern.search(value)) is None:
                    continue
                field = path.rsplit("/", 1)[-1]
                if subject.endswith(".status") and "/readings/" in path and (field, value) in MACHINE_TELEMETRY:
                    key = f"{subject} {path} = {value!r}"
                    telemetry[key] = telemetry.get(key, 0) + 1
                elif any(word.upper().replace(" ", "_") in code for code in codes):
                    key = f"{subject} {path}: {word!r} (own conclusion)"
                    concluded[key] = concluded.get(key, 0) + 1
                else:
                    leaks.append({"word": word, "subject": subject, "path": path,
                                  "context": value[max(0, match.start() - 60):match.end() + 60]})
    return {"messages": len(recorded), "subjects": subjects, "forbidden": sorted(words), "leaks": leaks[:20],
            "leak_count": len(leaks), "telemetry_judged": telemetry, "own_conclusion_judged": concluded}


async def amain(ticks: int) -> dict[str, Any]:
    port = free_port()
    server = subprocess.Popen([str(NATS_SERVER), "-a", "127.0.0.1", "-p", str(port)], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    url = f"nats://127.0.0.1:{port}"
    nodes: list[Node] = []
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(0.1)
        recorder = NatsLink("e9-recorder", url, logger=LOG)
        await recorder.connect(attempts=5)
        recorded: list[tuple[str, bytes]] = []

        async def keep(msg: Any) -> None:
            recorded.append((msg.subject, bytes(msg.data)))
        await recorder.client.subscribe(">", cb=keep)
        with tempfile.TemporaryDirectory() as folder:
            for node in NODE_IDS:
                link = NatsLink(node, url, logger=LOG)
                await link.connect(attempts=5)
                started = Node(load_node_config(ROOT / "nodes" / node / "node.toml"), link, logger=LOG,
                               data_dir=Path(folder) / node)
                await started.start()
                nodes.append(started)
            farm_link = NatsLink("farm-runtime", url, logger=LOG)
            await farm_link.connect(attempts=5)
            farm = fr.FarmRuntime(farm_link, logger=LOG, feed="trained", speed=fr.MAX_SPEED,
                                  data_dir=Path(folder) / "matrix")
            results = {}
            for scenario_id in fr.SCENARIO_IDS:
                results[scenario_id] = await run_fault(farm, scenario_id, ticks, recorded)
                print(f"{scenario_id}: {results[scenario_id]['messages']} messages, "
                      f"{results[scenario_id]['leak_count']} leaks, telemetry judged: "
                      f"{sum(results[scenario_id]['telemetry_judged'].values())}", flush=True)
            farm.close_memories()
            for started in nodes:
                await started.stop()
            nodes.clear()
            await farm_link.close()
            await recorder.close()
        return results
    finally:
        for started in nodes:
            await started.stop()
        server.terminate()
        server.wait(timeout=10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticks", type=int, default=TICKS)
    args = parser.parse_args(argv)
    started_utc, started = utc_now(), time.monotonic()
    logging.basicConfig(level=logging.ERROR)
    results = asyncio.run(amain(args.ticks))
    leaks = sum(r["leak_count"] for r in results.values())
    path = write_result(EvalResult(
        id="E9", title="The epistemic wall: no fault named on any subject a node hears", target="0 leaks",
        passed=leaks == 0,
        measured={"leaks": leaks, "ticks_per_fault": args.ticks, "feed": "trained",
                  "faults": json.loads(json.dumps(results))},
        command=f".venv\\Scripts\\python tests\\llm\\eval\\e9_epistemic_wall.py --ticks {args.ticks}",
        commit=git_commit(), seeds=[7], models={}, started_utc=started_utc,
        duration_s=round(time.monotonic() - started, 1),
        notes="Trained feed with renamed random seeds (decision 0021); nodes without language models; every subject "
              "recorded. Telemetry judged per MACHINE_TELEMETRY."))
    print(f"E9 {'PASSED' if leaks == 0 else 'FAILED'}: {leaks} leaks -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
