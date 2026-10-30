"""The website's live mapping and gateway client (6W, P1 sections D and E), on real snapshots.

The snapshots are made in this process by the real farm runtime (the snapshot farm, rule brains) and the real gateway
view (its NATS handlers called directly), then fed to interface/web/scripts/check-live-map.ts under Node 24. The test
skips with a message when Node is not there, as tests/test_contracts_zod.py does.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import farm_runtime as fr
import gateway as gw
from comms.nats_link import NatsLink

LOG = logging.getLogger("test-live-map")
ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "interface" / "web"
NODE = Path(os.environ.get("JARVIS_NODE", r"C:\Users\Aymane\node\node-v24.22.1-win-x64\node.exe"))
METRICS = {"incubator-1": 6, "incubator-2": 6, "hvac": 6, "animal-protection": 6}
pytestmark = pytest.mark.skipif(not (ROOT / "_dev" / "farm_poc_snapshot").is_dir(), reason="the farm snapshot is not here")


class Wire:
    """The farm's NATS link and the gateway's subscriptions, joined in-process: what the farm publishes reaches the
    gateway's own handlers, after the same JSON round trip as on NATS."""

    connected = True

    def __init__(self) -> None:
        self.handlers: list[tuple[str, type, Any]] = []

    async def subscribe(self, subject: str, model: type, handler: Any, **_: Any) -> None:
        self.handlers.append((subject, model, handler))

    async def publish(self, subject: str, message: Any, *, request_id: str | None = None) -> None:
        for pattern, model, handler in self.handlers:
            parts, wanted = subject.split("."), pattern.split(".")
            if len(parts) == len(wanted) and all(w in ("*", p) for p, w in zip(parts, wanted)):
                await handler(model.model_validate_json(message.model_dump_json()), subject)


async def record(ticks_before_reset: int, ticks_after_reset: int, fault: fr.ScenarioId | None,
                 every: int = 1) -> list[dict[str, Any]]:
    wire = Wire()
    gateway = gw.Gateway(NatsLink("gateway", "nats://127.0.0.1:1", logger=LOG), "http://matrix", logger=LOG)
    gateway.link.subscribe = wire.subscribe  # type: ignore[method-assign,assignment]
    await gateway.start()
    farm = fr.FarmRuntime(wire, logger=LOG)  # type: ignore[arg-type]
    snapshots: list[dict[str, Any]] = []

    async def run(ticks: int) -> None:
        for tick in range(ticks):
            if fault is not None and tick == 10 and farm.demo_fault is None:
                await farm.inject_fault(fault)
            await farm.publish(farm.messages_for_tick(farm.step(), include_state=True))
            await farm.publish_clock(force=True)
            if tick % every == 0:
                snapshots.append(json.loads(gateway.view.snapshot().model_dump_json()))

    await run(ticks_before_reset)
    if ticks_after_reset:
        await farm.control("reset")
        await run(ticks_after_reset)
    await gateway.matrix.aclose()
    return snapshots


def check(payload: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    if not NODE.is_file():
        pytest.skip(f"Node 24 not found at {NODE} (set JARVIS_NODE)")
    path = tmp_path / "input.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    run = subprocess.run([str(NODE), "--no-warnings", "scripts/check-live-map.ts", str(path)], cwd=WEB,
                         capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


def churned(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The recorded snapshots with a full event window: alarm raises and clears added before the recorded events,
    so every snapshot carries the 50 events the gateway shows at most (the smoothness case of P1 section E)."""
    out = []
    for index, snapshot in enumerate(snapshots):
        feed = snapshot["sim"]["feed_id"]
        base = {k: v for k, v in snapshot["events"][0].items()} if snapshot["events"] else None
        extra = []
        for n in range(50 - len(snapshot["events"])):
            sequence = 100_000 + index * 50 + n
            extra.append({**(base or {}), "node": "hvac", "tick": snapshot["sim"]["tick"], "time_s": snapshot["sim"]["time_s"],
                          "farm_time": snapshot["sim"]["farm_time"], "label": snapshot["sim"]["label"], "feed_id": feed,
                          "schema_version": 1, "sequence": sequence, "type": "alarm_raised" if n % 2 else "alarm_cleared",
                          "severity": "warning" if n % 2 else "info", "data": {"code": f"C{n % 7}", "source": "alarm"}})
        out.append({**snapshot, "events": extra + snapshot["events"]})
    return out


def test_real_snapshots_map_onto_the_pages_and_a_reset_starts_afresh(tmp_path: Path) -> None:
    run = asyncio.run(record(60, 15, "supply_fan_failure"))
    bench = churned(asyncio.run(record(1000, 0, "heater_relay_stuck")))
    invalid: list[Any] = ["not json", {"type": "farm_snapshot"}, {**run[-1], "schema_version": 2},
                          {**run[-1], "surprise": True}, []]
    result = check({"runs": [run], "invalid": invalid, "bench": bench}, tmp_path)
    states = result["runs"][0]
    first_feed, last_feed = run[0]["sim"]["feed_id"], run[-1]["sim"]["feed_id"]
    assert first_feed != last_feed
    for raw, state in zip(run, states):
        assert state["source"] == "live" and state["preview"] is False
        assert state["label"] == raw["sim"]["label"] == "live: simulated brains"          # exactly as received
        assert state["farmTime"] == raw["sim"]["farm_time"] and state["feedId"] == raw["sim"]["feed_id"]
        assert state["targetSpeed"] == raw["sim"]["target_speed"]
        assert state["actualSpeed"] == raw["sim"]["actual_speed"]
        assert {d["id"]: len(d["metrics"]) for d in state["devices"]} == METRICS          # every device and metric
        for device in state["devices"]:
            assert device["connection"] == "connected", device
            assert all(value != "No data" for _, value in device["metrics"]), device       # all readings mapped
        assert len(state["events"]) <= 50
        assert state["eventFeeds"] in ([], [state["feedId"]])                             # never another run's
    before = states[59]
    after = states[60]
    assert before["events"] and before["feedId"] == first_feed
    assert after["feedId"] == last_feed and all(e["id"].startswith(last_feed) for e in after["events"])
    assert after["activity"] <= 4                                                         # history started afresh
    hvac_before = next(d for d in before["devices"] if d["id"] == "hvac")
    assert hvac_before["operational"] in ("warning", "critical")                          # the fan fault shows
    # The client: invalid messages counted, never thrown, never delivered; logged at most once a minute.
    assert result["client"] == {"invalidCount": len(invalid), "thrown": 0, "warnings": 1, "delivered": 1,
                                "connections": ["connected"]}
    bench_result = result["bench"]
    print(f"\nmapping: {bench_result['snapshots']} snapshots, mean {bench_result['meanMs']:.4f} ms, "
          f"p95 {bench_result['p95Ms']:.4f} ms, max {bench_result['maxMs']:.4f} ms, "
          f"{bench_result['finalEvents']} events at the end")
    assert bench_result["snapshots"] == 1000 and bench_result["finalEvents"] == 50 and bench_result["p95Ms"] < 2.0


def test_no_invented_conclusion_and_an_escalated_alarm_stays_open(tmp_path: Path) -> None:
    """6W review M2 and L1: a node that has sent no conclusion is shown as such, never as NORMAL; without a farm clock
    the speed and day are unknown, not 60x and day 0; an alarm raised again at a higher severity is still one open
    alarm (both raises open) until its machine clears it."""
    run = asyncio.run(record(3, 0, None))
    first = run[-1]
    no_conclusion = {**first, "nodes": {node: {**view, "assessment": None} for node, view in first["nodes"].items()},
                     "sim": None}
    events = [e for e in first["events"]][:0]
    common = {"schema_version": 1, "node": "hvac", "tick": 3, "time_s": 3.0, "farm_time": first["sim"]["farm_time"],
              "label": first["sim"]["label"], "feed_id": first["sim"]["feed_id"]}
    events = [{**common, "sequence": 1, "type": "alarm_raised", "severity": "warning", "data": {"code": "F", "source": "alarm"}},
              {**common, "sequence": 2, "type": "alarm_raised", "severity": "critical", "data": {"code": "F", "source": "fault"}}]
    escalated = {**first, "events": events}
    result = check({"runs": [[no_conclusion], [escalated]], "invalid": [], "bench": run}, tmp_path)
    state = result["runs"][0][0]
    assert all(node[3] is True for node in state["nodes"].values()), state["nodes"]   # no conclusion yet
    assert state["worst"] is None and state["targetSpeed"] is None and state["day"] is None
    assert [e["open"] for e in result["runs"][1][0]["events"]] == [True, True]
