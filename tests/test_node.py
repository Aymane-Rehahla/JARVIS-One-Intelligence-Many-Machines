"""node.py and runtime.py (6A): capabilities, heartbeats, hot-join, answers from the node's own machine only, probes,
silence, and the node program itself.

The nodes run in this process on the private nats-server of the `nats_url` fixture, with short heartbeats and no radio
delay; the last test starts one real node program. No farm and no model.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Literal, cast

import pytest
import pytest_asyncio

import runtime
from conftest import fixture_approved_rom
from comms.nats_link import NatsLink
from comms.peer_network import RadioRules
from contracts import (NODE_IDS, AskReply, Capabilities, DocumentInfo, FarmAssessment, FarmIncident, FarmStatus, Heartbeat, NodeId, OwnerAlert,
                       ProbeRequest, SimState, load_node_config, utc_now)
from node import Node

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("test-node")
BEAT_S, SILENT_S = 0.2, 0.7
NO_DELAY = RadioRules(latency_ticks=0)


def feed(node: NodeId, **readings: Any) -> FarmStatus:
    return FarmStatus(node=node, tick=7, time_s=7.0, farm_time="06:07", label="live: simulated brains", feed_id="0000000000a1", online=True,
                      readings=readings)


async def until(check: Callable[[], bool], timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


class Farm:
    """Starts nodes and stands in for the farm runtime's publications."""

    def __init__(self, url: str, data_dir: Path) -> None:
        self.url = url
        self.data_dir = data_dir
        self.nodes: dict[NodeId, Node] = {}
        self.link = NatsLink("farm", url, logger=LOG)

    async def start(self, node: NodeId, **options: Any) -> Node:
        link = NatsLink(node, self.url, logger=LOG)
        await link.connect(attempts=5)
        if "rom_dir" not in options:                     # the owner's approvals of the real ROM are not this test's
            options["rom_dir"] = self.data_dir / node / "rom"
            options["rom_dir"].mkdir(parents=True, exist_ok=True)
        started = Node(load_node_config(ROOT / "nodes" / node / "node.toml"), link, logger=LOG, rules=NO_DELAY,
                       heartbeat_s=BEAT_S, silent_after_s=SILENT_S, data_dir=self.data_dir / node, **options)
        await started.start()
        self.nodes[node] = started
        return started

    async def stop(self, node: NodeId) -> None:
        await self.nodes.pop(node).stop()


@pytest_asyncio.fixture
async def farm(nats_url: str, tmp_path: Path) -> AsyncIterator[Farm]:
    world = Farm(nats_url, tmp_path / "nodes")
    await world.link.connect(attempts=5)
    yield world
    for node in list(world.nodes):
        await world.stop(node)
    await world.link.close()


@pytest.mark.asyncio
async def test_a_node_that_starts_later_is_known_by_all_without_a_restart(farm: Farm) -> None:
    for node in ("incubator-1", "incubator-2", "hvac"):
        await farm.start(node)
    await until(lambda: all(n.known() == ["incubator-1", "incubator-2", "hvac"] for n in farm.nodes.values()))
    assert all(n.documents is not None and n.documents.status() == "no approved documents" for n in farm.nodes.values())
    capabilities = [n.peers[n.id].capabilities for n in farm.nodes.values()]
    assert all(capabilities) and all(not capability.documents and "documents" not in capability.topics
                                     for capability in capabilities if capability is not None)
    joined = time.monotonic()
    late = await farm.start("animal-protection")
    await until(lambda: all(n.known() == list(NODE_IDS) for n in farm.nodes.values()))
    assert time.monotonic() - joined < 1.0                                   # one round trip, no restart
    await until(lambda: all(n.alive() == list(NODE_IDS) for n in farm.nodes.values()))
    hvac = late.peers["hvac"].capabilities
    assert hvac is not None and hvac.machine == "hvac" and not hvac.speaks and "incubation" not in hvac.topics
    speaker = late.peers["incubator-1"].capabilities
    assert speaker is not None and speaker.speaks and "incubation" in speaker.topics


@pytest.mark.asyncio
async def test_lost_announcements_are_repaired_by_the_heartbeats(farm: Farm) -> None:
    hvac, incubator = await farm.start("hvac"), await farm.start("incubator-1")
    await until(lambda: hvac.known() == incubator.known() == ["incubator-1", "hvac"])
    hvac.peers["incubator-1"].capabilities = None                            # as if its announcement was lost
    incubator.peers["hvac"].capabilities = None
    await until(lambda: hvac.known() == incubator.known() == ["incubator-1", "hvac"], timeout_s=3 * BEAT_S + 1)


@pytest.mark.asyncio
async def test_heartbeats_say_who_is_known_and_alive_and_silence_is_noticed(farm: Farm) -> None:
    beats: list[Heartbeat] = []

    async def heard(beat: Heartbeat, subject: str) -> None:
        beats.append(beat)

    await farm.link.subscribe("jarvis.hvac.heartbeat", Heartbeat, heard)
    hvac = await farm.start("hvac")
    await farm.start("incubator-2")
    await until(lambda: any(b.alive == ["incubator-2", "hvac"] and b.known == ["incubator-2", "hvac"] for b in beats))
    assert [b.seq for b in beats] == sorted({b.seq for b in beats})
    assert [b.uptime_s for b in beats] == sorted(b.uptime_s for b in beats)  # Windows' clock ticks every ~15 ms
    await farm.stop("incubator-2")
    await until(lambda: hvac.alive() == ["hvac"], timeout_s=SILENT_S + 1.5)
    await until(lambda: beats[-1].alive == ["hvac"])                        # its view changed: it says so at once
    assert hvac.known() == ["incubator-2", "hvac"]                           # still known, no longer alive


@pytest.mark.asyncio
async def test_answers_come_from_the_nodes_own_machine_and_say_their_farm_time(farm: Farm) -> None:
    hvac, asker = await farm.start("hvac"), await farm.start("incubator-1")
    await until(lambda: asker.known() == ["incubator-1", "hvac"])
    nothing = await asker.radio.ask("hvac", "readings", deadline_s=2.0)
    assert not nothing.ok and nothing.error == "no_data"                     # nothing published yet: no answer
    await farm.link.publish("farm.incubator-1.status", feed("incubator-1", temperature_mean_c=37.7))
    await farm.link.publish("farm.hvac.status", feed("hvac", supply_air_temperature_c=14.2, secret_internal=1,
                                                      alarms=[]))
    await farm.link.publish("farm.hvac.assessment", FarmAssessment(
        node="hvac", tick=8, time_s=8.0, farm_time="06:08", label="live: simulated brains", feed_id="0000000000a1", assessment="WATCHING",
        diagnosis="FILTER_LOADING", confidence=0.6, brain="rule"))
    await farm.link.publish("farm.hvac.incident", FarmIncident(
        node="hvac", tick=8, time_s=8.0, farm_time="06:08", label="live: simulated brains", feed_id="0000000000a1", incident_id="hvac-1",
        state="opened", kind="equipment", summary={"cause": "filter"}))
    await until(lambda: hvac.status is not None and hvac.assessment is not None and bool(hvac.incidents))
    assert hvac.status is not None and hvac.status.node == "hvac"            # never another machine's feed
    readings = await asker.radio.ask("hvac", "readings", deadline_s=2.0)
    reading_data = cast(dict[str, Any], readings.answer["get_status"])
    assert readings.ok and reading_data == {
        "online": True, "farm_time": "06:07", "tick": 7, "label": "live: simulated brains",
        "readings": {"supply_air_temperature_c": 14.2, "alarms": []}}
    assert (readings.farm_time, readings.tick) == ("06:07", 7)
    status = await asker.radio.ask("hvac", "status", deadline_s=2.0)
    status_tool = cast(dict[str, Any], status.answer["get_assessment"])
    assessment_data = cast(dict[str, Any], status_tool["assessment"])
    assert status.ok and assessment_data["diagnosis"] == "FILTER_LOADING"
    assert status.farm_time == "06:08"
    history = await asker.radio.ask("hvac", "incident_history", deadline_s=2.0)
    incident_tool = cast(dict[str, Any], history.answer["get_incident"])
    incident = cast(dict[str, Any], incident_tool["incident"])
    assert history.ok and incident["incident_id"] == "hvac-1"
    event_tool = cast(dict[str, Any], history.answer["list_events"])
    assert event_tool["events"] == []
    refused = await asker.radio.ask("hvac", "incubation", deadline_s=2.0)
    assert not refused.ok and refused.error == "unavailable"                 # an HVAC has no eggs


@pytest.mark.asyncio
async def test_an_answer_without_readings_still_states_the_farm_time(farm: Farm) -> None:
    await farm.start("hvac")
    asker = await farm.start("animal-protection")
    await farm.link.publish("farm.sim", SimState(tick=3, time_s=3.0, farm_time="06:03", day=1, running=True,
                                                 target_speed=1.0, actual_speed=1.0, label="live: simulated brains", feed_id="0000000000a1",
                                                 episode_id="e"))
    await until(lambda: farm.nodes["hvac"].sim is not None)
    history = await asker.radio.ask("hvac", "incident_history", deadline_s=2.0)
    # No incident and no events is the normal state of a healthy farm: an answer, with the farm time (6A's intent;
    # 6B had changed this expectation to "no_data" to match the bug the senior review then fixed, H4).
    assert history.ok and history.farm_time == "06:03"
    assert history.answer["get_incident"]["incident"] is None                  # type: ignore[index]
    assert history.answer["list_events"]["events"] == []                       # type: ignore[index]


@pytest.mark.asyncio
async def test_a_probe_makes_a_node_ask_a_peer_only_when_enabled(farm: Farm) -> None:
    await farm.start("hvac")
    await farm.start("incubator-1", probe=True)
    await farm.start("incubator-2")
    probe = ProbeRequest(target="hvac", topic="hazard_check", deadline_ms=2000)
    reply = await farm.link.request("jarvis.incubator-1.probe", probe, AskReply, timeout_s=3.0)
    assert reply.sender == "hvac" and reply.target == "incubator-1" and reply.error == "no_data"
    from comms.nats_link import RequestFailed
    with pytest.raises(RequestFailed) as off:
        await farm.link.request("jarvis.incubator-2.probe", probe, AskReply, timeout_s=1.0)
    assert off.value.kind == "no_responders"


def test_bad_heartbeat_settings_are_refused() -> None:
    config = load_node_config(ROOT / "nodes" / "hvac" / "node.toml")
    with pytest.raises(ValueError, match="silence limit"):
        Node(config, NatsLink("hvac", logger=LOG), logger=LOG, heartbeat_s=1.0, silent_after_s=1.0)


def test_capability_registry_lists_topic_knowledge_and_documents() -> None:
    config = load_node_config(ROOT / "nodes" / "incubator-1" / "node.toml")
    node = Node(config, NatsLink("incubator-1", logger=LOG), logger=LOG)
    info = DocumentInfo(file="manual.md", title="Manual", kind="shared", version=1, sha256="a" * 64)
    node.peers["hvac"].capabilities = Capabilities(node="hvac", machine="hvac", speaks=False,
                                                   topics=["readings", "documents"], documents=[info],
                                                   started_utc=utc_now(), reason="start")
    node.peers["hvac"].last_heard = time.monotonic()
    assert node.capability_registry.who_knows("readings") == ["hvac"]
    assert node.capability_registry.documents_of("hvac") == [info]
    assert node.capability_registry.is_alive("hvac")


def test_the_program_gives_up_cleanly_when_nats_is_not_there(new_port: Callable[[], int]) -> None:
    assert runtime.main(["--nats", f"nats://127.0.0.1:{new_port()}", "--connect-attempts", "1", "--no-logs"]) == 2


@pytest.mark.asyncio
async def test_the_node_program_runs_and_announces_itself(farm: Farm, tmp_path: Path) -> None:
    announced: list[Capabilities] = []

    async def heard(capabilities: Capabilities, subject: str) -> None:
        announced.append(capabilities)

    await farm.link.subscribe("jarvis.hvac.capabilities", Capabilities, heard)
    program = subprocess.Popen([sys.executable, str(ROOT / "nodes" / "hvac" / "runtime.py"), "--nats", farm.url,
                                "--heartbeat-s", "0.5", "--no-logs", "--data-dir", str(tmp_path / "program-data")],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
    try:
        await until(lambda: bool(announced), timeout_s=20.0)
        assert announced[0].node == "hvac" and announced[0].reason == "start"
        assert program.poll() is None
    finally:
        program.terminate()
        program.wait(timeout=10)


# --- senior review of 6B ---------------------------------------------------------------------------------------------

def test_a_new_farm_run_starts_the_view_afresh_and_late_old_messages_are_dropped() -> None:
    """Review H2: after a farm reset the ticks and sequences start again; mixing runs would make trends and "recent"
    events wrong. The node follows the newest run and drops a late message from the run it replaced."""
    node = Node(load_node_config(ROOT / "nodes" / "hvac" / "node.toml"), NatsLink("hvac", logger=LOG), logger=LOG)
    old = feed("hvac", temperature_c=20.0)
    new = old.model_copy(update={"feed_id": "0000000000b2", "tick": 1, "time_s": 1.0})
    asyncio.run(node._on_status(old, "farm.hvac.status"))
    assert node.feed_id == "0000000000a1" and len(node.trend_buffer) == 1
    asyncio.run(node._on_status(new, "farm.hvac.status"))
    assert node.feed_id == "0000000000b2" and list(node.trend_buffer) == [new] and node.status == new
    asyncio.run(node._on_status(old, "farm.hvac.status"))                     # late, from the replaced run
    assert node.status == new and list(node.trend_buffer) == [new]


@pytest.mark.asyncio
async def test_a_long_document_question_still_searches(farm: Farm, tmp_path: Path) -> None:
    """Review M2: a question may be 2,000 characters but a search query 300; the documents answer failed instead."""
    rom = fixture_approved_rom(ROOT / "nodes" / "hvac" / "memory" / "rom" / "hvac_manual.md",
                               tmp_path / "copy" / "nodes" / "hvac" / "memory" / "rom")
    await farm.start("hvac", rom_dir=rom)
    asker = await farm.start("incubator-1")
    await until(lambda: asker.peers["hvac"].capabilities is not None
                and "documents" in asker.peers["hvac"].capabilities.topics, timeout_s=10.0)
    await farm.link.publish("farm.hvac.status", feed("hvac", supply_air_temperature_c=14.0))
    await until(lambda: farm.nodes["hvac"].status is not None)
    question = "What does the manual say about the compressor? " + "Please be thorough. " * 90
    assert len(question) > 1500
    reply = await asker.radio.ask("hvac", "documents", question, deadline_s=5.0)
    assert reply.ok, reply.detail
    results = reply.answer["search_documents"]["results"]                     # type: ignore[index]
    assert results and any("compressor" in r["text"].lower() for r in results)  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_the_document_refresh_survives_a_failed_rebuild(farm: Farm, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review M3: the first exception in the refresh loop ended document refreshing for the node's whole life."""
    import node as node_module

    monkeypatch.setattr(node_module, "DOCUMENT_REFRESH_S", 0.05)
    hvac = await farm.start("hvac")
    assert hvac.documents is not None
    calls = {"n": 0}

    def flaky_rebuild() -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("the index is locked")
        return calls["n"] == 2                                                 # the second rebuild finds a change

    monkeypatch.setattr(hvac.documents, "rebuild", flaky_rebuild)
    await until(lambda: calls["n"] >= 3, timeout_s=5.0)
    own = hvac.peers["hvac"].capabilities
    assert own is not None and own.reason == "change"


@pytest.mark.asyncio
async def test_alerts_reach_the_owner_from_incubator_1s_own_machine_and_through_the_radio(farm: Farm) -> None:
    """6C (decision 0019): incubator-1 alerts on its own machine directly and on a peer's notice; once per level."""
    alerts: list[OwnerAlert] = []

    async def heard(alert: OwnerAlert, subject: str) -> None:
        alerts.append(alert)
    await farm.link.subscribe("jarvis.incubator-1.alert", OwnerAlert, heard)
    await farm.start("incubator-1")
    await farm.start("hvac")
    await until(lambda: farm.nodes["incubator-1"].known() == ["incubator-1", "hvac"])

    def escalate(node: NodeId, level: str = "ESCALATE_TO_USER", tick: int = 9) -> FarmAssessment:
        return FarmAssessment(node=node, tick=tick, time_s=float(tick), farm_time="06:09", label="live: simulated brains",
                              feed_id="0000000000a1", assessment=level, diagnosis="HEATER_FAULT", confidence=0.9,  # type: ignore[arg-type]
                              brain="rule", incident_id=f"{node}-equipment-0001", evidence=["HEATER_FAULT"])
    await farm.link.publish("farm.incubator-1.assessment", escalate("incubator-1"))
    await until(lambda: len(alerts) == 1)
    await farm.link.publish("farm.hvac.assessment", escalate("hvac"))
    await until(lambda: len(alerts) == 2)
    await farm.link.publish("farm.hvac.assessment", escalate("hvac", tick=10))              # the same level: no alert
    await farm.link.publish("farm.hvac.assessment", escalate("hvac", "DANGER", tick=11))     # a rising level
    await until(lambda: len(alerts) == 3)
    await asyncio.sleep(0.3)
    assert [(a.node, a.level) for a in alerts] == [("incubator-1", "ESCALATE_TO_USER"), ("hvac", "ESCALATE_TO_USER"),
                                                   ("hvac", "DANGER")]
    assert all(a.text.endswith("Farm time 06:09.") and not a.reworded for a in alerts)


@pytest.mark.asyncio
async def test_voice_turns_share_the_limit_on_owner_turns() -> None:
    """Review (31 Oct): the voice box called agent.reply directly, so two chat turns and a voice turn could all wait on
    the 2B server's one slot. Every owner turn, chat or voice, goes through the same limit."""
    from contracts import ChatDone, ReplyTimings
    node = Node(load_node_config(ROOT / "nodes" / "incubator-1" / "node.toml"), NatsLink("incubator-1", logger=LOG),
                logger=LOG)
    gate = asyncio.Event()

    class Slow:
        async def reply(self, text: str, **options: Any) -> AsyncIterator[Any]:
            await gate.wait()
            yield ChatDone(request_id=options["request_id"], conversation_id="c-1", text="Done.", chunks=0,
                           outcome="error", reason="test", numbers_checked=0, timings=ReplyTimings(total_ms=0))

    node.agent = cast(Any, Slow())

    async def drain(source: Literal["chat", "voice"], request_id: str) -> list[Any]:
        return [item async for item in node.owner_turn("How is it?", request_id=request_id, conversation_id=None,
                                                       source=source)]
    holders = [asyncio.create_task(drain("chat", f"r-{index}")) for index in range(2)]
    await asyncio.sleep(0.05)
    (busy,) = await asyncio.wait_for(drain("voice", "v-1"), 2.0)
    assert busy.outcome == "error" and "already answering" in busy.reason and busy.request_id == "v-1"
    gate.set()
    assert all(len(items) == 1 for items in await asyncio.gather(*holders))
