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
from typing import Any, cast

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
async def test_a_node_answers_from_the_live_feed(nats_url: str, tmp_path: Path) -> None:
    farm_link = NatsLink("farm-runtime", nats_url, logger=LOG)
    await farm_link.connect(attempts=5)
    runtime = fr.FarmRuntime(farm_link, logger=LOG, speed=10.0)
    nodes: list[Node] = []
    for node_id in ("hvac", "incubator-1"):
        link = NatsLink(node_id, nats_url, logger=LOG)
        await link.connect(attempts=5)
        node = Node(load_node_config(ROOT / "nodes" / node_id / "node.toml"), link, logger=LOG,
                    rules=RadioRules(latency_ticks=1), heartbeat_s=0.5, silent_after_s=2.0,
                    data_dir=tmp_path / node_id)
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
        readings_data = cast(dict[str, Any], reply.answer["get_status"])
        readings = cast(dict[str, Any], readings_data["readings"])
        assert reply.ok and "supply_air_temperature_c" in readings
        assert reply.farm_time is not None
        assert 0.15 <= elapsed < 1.5                                           # two hops of 0.1 s at 10x
        assert asker.status is not None and asker.status.node == "incubator-1"
    finally:
        stop.set()
        await task
        for node in nodes:
            await node.stop()
        await farm_link.close()


def test_a_rising_severity_is_news_and_each_run_has_its_own_feed_id() -> None:
    """Review L3 and H2: a warning that becomes critical is raised again; a reset starts a new run (new feed_id) whose
    event sequences start again, which is why the nodes keep events per run."""
    runtime, _ = farm()
    first = runtime._events("hvac", True, {"alarms": [{"code": "FILTER_DIRTY", "severity": "warning"}]}, {})
    worse = runtime._events("hvac", True, {"alarms": [{"code": "FILTER_DIRTY", "severity": "critical"}]}, {})
    same = runtime._events("hvac", True, {"alarms": [{"code": "FILTER_DIRTY", "severity": "critical"}]}, {})
    assert [(e.type, e.severity) for e in first] == [("alarm_raised", "warning")]
    assert [(e.type, e.severity) for e in worse] == [("alarm_raised", "critical")]
    assert same == []                                                         # unchanged: no news
    before = runtime.feed_id
    assert first[0].feed_id == before and runtime.sim_state().feed_id == before
    runtime.reset()
    assert runtime.feed_id != before
    again = runtime._events("hvac", True, {"alarms": [{"code": "X"}]}, {})
    assert again[0].sequence == 1 and again[0].feed_id == runtime.feed_id     # sequences restart, in a new run


# --- 6W: the four demo faults (P1 section A; decision 0020) ---------------------------------------------------------

FAULT_LIMIT_TICKS = 600          # an upper bound for the search only; the measured N is printed (run with -s)


def _twins() -> tuple[fr.FarmRuntime, fr.FarmRuntime]:
    one, _ = farm()
    two, _ = farm()
    for _ in range(5):
        one.step()
        two.step()
    return one, two


def test_twin_farms_stay_identical_without_a_fault() -> None:
    """The control for the next test: the same seed gives the same public telemetry, tick for tick."""
    one, two = _twins()
    for _ in range(FAULT_LIMIT_TICKS):
        one.step()
        two.step()
    assert all(one.engine.device(node) == two.engine.device(node) for node in NODE_IDS)


@pytest.mark.parametrize("scenario_id", fr.SCENARIO_IDS)
@pytest.mark.asyncio
async def test_each_demo_fault_changes_the_public_telemetry(scenario_id: fr.ScenarioId) -> None:
    faulted, twin = _twins()
    result = await faulted.inject_fault(scenario_id)
    assert result.tick == 5 and result.feed_id == faulted.feed_id and result.devices
    changed_after = None
    for ticks in range(1, FAULT_LIMIT_TICKS + 1):
        faulted.step()
        twin.step()
        if any(faulted.engine.device(node) != twin.engine.device(node) for node in result.devices):
            changed_after = ticks
            break
    print(f"\n{scenario_id}: {result.family} variant {result.variant} on {result.devices}; "
          f"public telemetry changed after N = {changed_after} farm ticks")
    assert changed_after is not None


def test_each_demo_fault_uses_an_ordinary_library_variant_unchanged() -> None:
    runtime, _ = farm()
    expected = {"heater_relay_stuck": "heater_relay_stuck", "supply_fan_failure": "supply_fan_failure",
                "humidity_sensor_drift": "sensor_fault", "supply_overvoltage": "overvoltage"}
    for scenario_id, family in expected.items():
        variant, spec = runtime.demo_spec(scenario_id)                     # type: ignore[arg-type]
        assert spec.family == family and spec.tags["novel"] is False
        assert spec.as_dict() == runtime._make_spec(family, variant, False).as_dict()   # the library's own spec
    _, drift = runtime.demo_spec("humidity_sensor_drift")
    assert drift.faults[0]["channel"] == "incubator-2.humidity_1" and drift.faults[0]["mode"] == "drift"


@pytest.mark.asyncio
async def test_one_demo_fault_per_run_and_a_reset_clears_it() -> None:
    runtime, _ = farm(running=False)
    await runtime.inject_fault("supply_fan_failure")
    with pytest.raises(fr.DemoFaultRefused, match="already active"):
        await runtime.inject_fault("heater_relay_stuck")
    await runtime.control("reset")
    assert runtime.demo_fault is None
    assert (await runtime.inject_fault("heater_relay_stuck")).feed_id == runtime.feed_id


def test_the_matrix_api_demo_faults() -> None:
    runtime, _ = farm(running=False)
    with TestClient(fr.create_api(runtime)) as client:
        assert client.post("/faults/fire_in_the_barn").status_code == 404
        first = client.post("/faults/supply_overvoltage")
        assert first.status_code == 200
        body = first.json()
        assert body["scenario_id"] == "supply_overvoltage" and body["devices"] == ["incubator-1", "incubator-2"]
        assert body["feed_id"] == runtime.feed_id and body["farm_time"] == runtime.farm_time()[0]
        second = client.post("/faults/heater_relay_stuck")
        assert second.status_code == 409 and "already active" in second.json()["detail"]
        assert client.post("/sim/reset").status_code == 200
        assert client.post("/faults/heater_relay_stuck").status_code == 200


@pytest.mark.asyncio
async def test_injecting_while_paused_publishes_nothing() -> None:
    runtime, link = farm(running=False)
    await runtime.publish_clock(force=True)
    link.sent.clear()
    for scenario_id in fr.SCENARIO_IDS:
        await runtime.control("reset")
        link.sent.clear()
        await runtime.inject_fault(scenario_id)
        assert link.sent == [], scenario_id


def _hidden_words(runtime: fr.FarmRuntime, result: fr.FaultResult) -> set[str]:
    """Every string the engine keeps about the fault for the evaluator (hidden truth, fault log), plus the website's
    scenario id, the family and the mode; machine names are public and left out."""
    words = {result.scenario_id, result.family}
    _, spec = runtime.demo_spec(result.scenario_id)
    for fault in spec.faults:
        words |= {str(v) for k, v in fault.items() if isinstance(v, str) and k not in ("device",)}
    stack: list[Any] = [runtime.engine.hidden_truth, runtime.engine.fault_log]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
            words |= {str(k) for k in item if k in ("scenario", "fault_label")}
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str):
            words.add(item)
    return words - set(NODE_IDS) - {"injected", "recovered"}


def _strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    """(path, text) of every string inside a JSON-like value."""
    if isinstance(value, dict):
        return [found for key, item in value.items() for found in _strings(item, f"{path}/{key}")]
    if isinstance(value, list):
        return [found for item in value for found in _strings(item, f"{path}/*")]
    return [(path, value)] if isinstance(value, str) else []


def _paths(value: Any, path: str = "") -> set[str]:
    if isinstance(value, dict):
        return {path} | {found for key, item in value.items() for found in _paths(item, f"{path}/{key}")}
    if isinstance(value, list):
        return {path} | {found for item in value for found in _paths(item, f"{path}/*")}
    return {path}


@pytest.mark.parametrize("scenario_id", fr.SCENARIO_IDS)
@pytest.mark.asyncio
async def test_the_epistemic_wall_holds_for_120_ticks_after_a_demo_fault(scenario_id: fr.ScenarioId) -> None:
    """No published message names the fault. The one judgement (as E9's): a word that the machine itself reports in a
    readings field it also publishes on a fault-free twin farm is that machine's public telemetry (for example the
    incubator's `power_status` "overvoltage"), a physical effect, not a leak. Such matches are printed."""
    import re
    runtime, link = farm()
    twin, _ = farm()
    result = await runtime.inject_fault(scenario_id)
    normal_fields: set[str] = set()
    for _ in range(120):
        await runtime.publish(runtime.messages_for_tick(runtime.step(), include_state=True))
        await runtime.publish_clock(force=True)
        twin.step()
        normal_fields |= {f"{node}{path}" for node in NODE_IDS for path in _paths(twin.engine.device(node))}
    words = _hidden_words(runtime, result)
    assert result.scenario_id in words and result.family in words and runtime.engine.fault_log
    patterns = {word: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])") for word in sorted(words)}
    leaks: dict[str, str] = {}
    telemetry: set[str] = set()
    for _, message in link.sent:
        payload = message.model_dump(mode="json")
        readings = payload.pop("readings", None) if isinstance(message, FarmStatus) else None
        text = json.dumps(payload)
        for word, pattern in patterns.items():
            if (match := pattern.search(text)) is not None:
                leaks.setdefault(word, text[max(0, match.start() - 60):match.end() + 60])
        for path, value in _strings(readings or {}):
            for word, pattern in patterns.items():
                if pattern.search(value):
                    field = f"{payload['node']}{path}"
                    if field in normal_fields:
                        telemetry.add(f"{field} = {value!r}")
                    else:
                        leaks.setdefault(word, f"{field} = {value!r}")
    print(f"\n{scenario_id}: {len(link.sent)} messages scanned for {sorted(words)}; "
          f"the machine's own telemetry: {sorted(telemetry) or 'none'}")
    assert len(link.sent) > 120 * len(NODE_IDS) and leaks == {}
