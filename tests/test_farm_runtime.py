"""matrix/farm_runtime.py (6A test feed): the snapshot farm with rule brains, what it publishes, its pace, the Matrix API,
and one node answering from that feed over NATS.

The farm is the read-only snapshot in _dev/ (rule brains, the CTM module stubbed: decision 0012). It runs in this
process at a few ticks per second; NATS is the private nats-server of the `nats_url` fixture. No model, no torch.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

import farm_runtime as fr
from comms.nats_link import NatsLink
from comms.peer_network import RadioRules
from contracts import NODE_IDS, FarmAssessment, FarmStatus, LinkStatus, SimState, load_node_config
from node import READING_KEYS, Node

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("test-farm")
pytestmark = pytest.mark.skipif(not (ROOT / "_dev" / "farm_poc_snapshot").is_dir(),
                                reason="the farm snapshot (_dev/farm_poc_snapshot) is not here")


class CaptureLink:
    """Stands in for the NATS link: keeps what the farm publishes."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, BaseModel]] = []
        self.connected = True

    async def publish(self, subject: str, message: BaseModel, *, request_id: str | None = None) -> None:
        self.sent.append((subject, message))

    def subjects(self, prefix: str) -> list[str]:
        return [subject for subject, _ in self.sent if subject.startswith(prefix)]


def farm(**options: Any) -> tuple[fr.FarmRuntime, CaptureLink]:
    link = CaptureLink()
    return fr.FarmRuntime(link, logger=LOG, **options), link  # type: ignore[arg-type]


def test_the_test_feed_runs_rule_brains_and_refuses_ctm() -> None:
    runtime, _ = farm()
    assert {type(node.brain).__name__ for node in runtime.runtime.nodes.values()} == {"RuleBrain"}
    from farm_poc_snapshot.nodes.ctm_brain import NodeBrainConfig, NodeCTMBrain    # the stub, at run time
    with pytest.raises(RuntimeError, match="rule brains only"):
        NodeCTMBrain(NodeBrainConfig("hvac"), None)  # type: ignore[arg-type]
    assert not list((ROOT / "_dev" / "farm_poc_snapshot").rglob("__pycache__"))   # the snapshot stays read-only


def test_each_tick_publishes_valid_public_messages_with_the_farm_time() -> None:
    runtime, _ = farm()
    results = runtime.step()
    messages = runtime.messages_for_tick(results, include_state=True)
    statuses = {m.node: m for _, m in messages if isinstance(m, FarmStatus)}
    assessments = {m.node: m for _, m in messages if isinstance(m, FarmAssessment)}
    assert set(statuses) == set(assessments) == set(NODE_IDS)
    for node, status in statuses.items():
        assert status.readings == runtime.engine.device(node)                 # the machine's public telemetry, no more
        assert status.tick == runtime.tick and status.label == "live: simulated brains"
        assert status.farm_time == runtime.farm_time()[0] and assessments[node].brain == "rule"
    text = json.dumps([m.model_dump(mode="json") for _, m in messages])
    for hidden in ('"internal"', '"scenario', '"fault_schedule"', '"truth"', '"label_'):
        assert hidden not in text, hidden                                       # ground rule 4


def test_the_nodes_answer_keys_exist_in_their_machines_telemetry() -> None:
    runtime, _ = farm()
    runtime.step()
    for node in NODE_IDS:
        machine = load_node_config(ROOT / "nodes" / node / "node.toml").machine
        missing = [key for key in READING_KEYS[machine] if key not in runtime.engine.device(node)]
        assert missing == [], f"{node}: {missing}"


def test_alarms_offline_and_gate_decisions_become_events() -> None:
    runtime, _ = farm()
    raised = runtime._events("hvac", True, {"alarms": [{"code": "SUPPLY_FAN_FAILURE"}, "FILTER_DIRTY"],
                                             "faults": [{"code": "SUPPLY_FAN_FAILURE"}, "COMPRESSOR"]}, {})
    cleared = runtime._events("hvac", True, {"alarms": ["FILTER_DIRTY"]}, {})
    offline = runtime._events("hvac", False, {}, {"action": {"action": "SHUTDOWN", "allowed": False,
                                                              "reason": "not allowed"}})
    assert [(e.type, e.data, e.severity) for e in raised] == [
        ("alarm_raised", {"code": "COMPRESSOR", "source": "fault"}, "critical"),
        ("alarm_raised", {"code": "FILTER_DIRTY", "source": "alarm"}, "warning"),
        ("alarm_raised", {"code": "SUPPLY_FAN_FAILURE", "source": "fault"}, "critical")]
    assert [(e.type, e.data, e.severity) for e in cleared] == [
        ("alarm_cleared", {"code": "COMPRESSOR", "source": "fault"}, "info"),
        ("alarm_cleared", {"code": "SUPPLY_FAN_FAILURE", "source": "fault"}, "info")]
    assert [e.type for e in offline] == ["machine_offline", "safety_action"]
    assert [e.severity for e in offline] == ["critical", "warning"]
    assert offline[1].data == {"proposed": "SHUTDOWN", "allowed": False, "reason": "not allowed"}
    sequences = [e.sequence for e in raised + cleared + offline]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)


@pytest.mark.asyncio
async def test_the_clock_keeps_its_speed_and_states_are_rate_limited() -> None:
    runtime, link = farm(speed=20.0, max_publish_hz=5.0)
    stop = asyncio.Event()
    started = time.monotonic()
    task = asyncio.create_task(runtime.run(stop))
    await asyncio.sleep(2.0)
    stop.set()
    await task
    elapsed = time.monotonic() - started
    farm_seconds = runtime.time_s
    assert 0.6 * 20 * elapsed <= farm_seconds <= 1.1 * 20 * elapsed       # it may fall behind, never run ahead
    per_node = len(link.subjects("farm.hvac.status"))
    assert 5 <= per_node <= 5 * elapsed + 2                               # at most 5 states a second
    sims = [m for s, m in link.sent if s == "farm.sim"]
    assert sims and isinstance(sims[-1], SimState) and sims[-1].actual_speed > 10


@pytest.mark.asyncio
async def test_control_actions_and_cut_links() -> None:
    runtime, link = farm(running=False)
    paused_tick = runtime.tick
    assert (await runtime.control("speed", speed=60.0)).target_speed == 60.0
    assert (await runtime.control("start")).running
    runtime.step()
    assert (await runtime.control("pause")).running is False and runtime.tick == paused_tick + 1
    cut = await runtime.cut("hvac", 30.0)
    assert cut.cut == ["hvac"] and runtime.cut_links() == ["hvac"]
    assert (await runtime.restore("hvac")).cut == []
    assert (await runtime.control("reset")).tick == 0
    links = [m for s, m in link.sent if s == "farm.links"]
    assert [m.cut for m in links if isinstance(m, LinkStatus)][-3:] == [["hvac"], [], []]
    with pytest.raises(ValueError):
        await runtime.control("explode")
    with pytest.raises(ValueError):
        farm(speed=0)


def test_the_matrix_api() -> None:
    runtime, _ = farm(running=False)
    with TestClient(fr.create_api(runtime)) as client:
        assert client.get("/health").json()["label"] == "live: simulated brains"
        assert client.post("/sim/speed", json={"speed": 30}).json()["target_speed"] == 30.0
        assert client.post("/sim/speed", json={"speed": 0}).status_code == 422
        assert client.post("/sim/speed").status_code == 422
        assert client.post("/sim/start").json()["running"] is True
        assert client.post("/links/hvac/cut", json={"seconds": 10}).json()["cut"] == ["hvac"]
        assert client.post("/links/barn-9/cut").status_code == 422
        assert client.post("/links/hvac/restore").json()["cut"] == []
        assert client.post("/faults/x").status_code == 404                    # demo faults come in 6W


@pytest.mark.asyncio
async def test_a_node_answers_from_the_live_feed(nats_url: str) -> None:
    farm_link = NatsLink("farm-runtime", nats_url, logger=LOG)
    await farm_link.connect(attempts=5)
    runtime = fr.FarmRuntime(farm_link, logger=LOG, speed=10.0)
    nodes: list[Node] = []
    for node_id in ("hvac", "incubator-1"):
        link = NatsLink(node_id, nats_url, logger=LOG)
        await link.connect(attempts=5)
        node = Node(load_node_config(ROOT / "nodes" / node_id / "node.toml"), link, logger=LOG,
                    rules=RadioRules(latency_ticks=1), heartbeat_s=0.5, silent_after_s=2.0)
        await node.start()
        nodes.append(node)
    hvac, asker = nodes
    stop = asyncio.Event()
    task = asyncio.create_task(runtime.run(stop))
    try:
        for _ in range(100):
            if hvac.status is not None and asker.sim is not None:
                break
            await asyncio.sleep(0.05)
        started = time.monotonic()
        reply = await asker.radio.ask("hvac", "readings", deadline_s=3.0)
        elapsed = time.monotonic() - started
        assert reply.ok and "supply_air_temperature_c" in reply.answer and reply.farm_time is not None
        assert 0.15 <= elapsed < 1.5                                           # two hops of 0.1 s at 10x
        assert asker.status is not None and asker.status.node == "incubator-1"
    finally:
        stop.set()
        await task
        for node in nodes:
            await node.stop()
        await farm_link.close()
