"""One node of JARVIS (steps 6A–6B): its public farm view, peer tools, approved ROM index and node-local LLM data.

A node program (`runtime.py`) builds a Node from its `node.toml` and runs it. The node joins NATS, announces its
capabilities, sends a heartbeat every HEARTBEAT_S seconds (and at once whenever its view of the other nodes changes),
follows its own machine's farm subjects, answers the other nodes' questions through the ZigBee rules, and keeps a
registry of who is alive and what each can answer. Hot-join: a node that starts later announces itself, every node
answers with its own capabilities, and within one round trip all of them know each other, with no restart. If an
announcement is lost, the heartbeats repair it: a node that hears a heartbeat from a node it has no capabilities for
asks everyone to announce again, and a node whose heartbeat peers say they do not know it announces itself again (each
at most once per heartbeat period).

In: this node's farm feed and NATS peer questions. Out: bounded local events, incidents and conversations, capability
announcements and tool-data peer answers carrying farm time. Answers use only this node's published feed; a node never
reads another machine's data except by asking that node. Tests: `tests/test_node.py`, `tests/test_farm_runtime.py` and
`tests/llm/integration/test_hot_join_tools.py`.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import JsonValue

from comms.events import EventBus
from comms.jsonlog import log
from comms.mcp import NodeMCP
from comms.nats_link import NatsLink
from comms.peer_network import PeerNetwork, RadioRules
from contracts import (FARM_LINKS, FARM_SIM, NODE_IDS, AckCommand, AckResult, AlertNotice, AskError, AskReply,
                       AskRequest, Capabilities, ChatChunk, ChatDone, ChatRequest, FarmAssessment, FarmEvent, FarmIncident,
                       FarmStatus, Heartbeat, LinkStatus, NodeConfig, NodeId, ProbeRequest, ReplyTimings, SimState,
                       Topic, farm_subject, jarvis_subject, utc_now)
from llm.alerts import AlertDesk, AlertWatcher
from llm.audit import Auditor
from llm.capabilities import CapabilityRegistry
from llm.conversation import LlmStore
from llm.documents import BgeEmbedder, Documents
from llm.peer_minds import PeerMind, budget_s
from llm.tools import ConfirmTokens, ToolRegistry

HEARTBEAT_S = 10.0
SILENT_AFTER_S = 30.0                       # three missed heartbeats: the peer is silent
EVENTS_KEPT = 200
INCIDENTS_KEPT = 20
DOCUMENT_REFRESH_S = 5.0                     # how often the approved-document index is checked for changes
MAX_TURNS_AT_ONCE = 2                        # owner turns served at once (the 2B server has one slot)
MAX_BACKGROUND = 64                          # notices and turns in flight, at most


class _PeerAnswerFailure(Exception):
    """A fixed peer tool set could not produce a data-backed answer."""

    kind: Literal["no_data", "unavailable"]
    detail: str

    def __init__(self, kind: Literal["no_data", "unavailable"], detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail

# The readings a node gives when asked, per machine: the public telemetry fields that matter, in this order.
READING_KEYS: dict[str, tuple[str, ...]] = {
    "incubator": (
        "online", "health", "temperature_mean_c", "temperature_min_c", "temperature_max_c",
        "temperature_sensor_spread_c", "humidity_mean_pct", "humidity_sensor_spread_pct", "heater_on",
        "heater_output_pct", "heater_power_w", "fan_rpm", "fan_command_pct", "door_open", "reservoir_pct",
        "ac_voltage_v", "power_source", "power_status", "overtemperature_cutoff", "ambient_temperature_c",
        "ambient_relative_humidity_pct", "alarms", "faults"),
    "hvac": (
        "online", "health", "mode", "zones", "zone_mean_temperature_c", "zone_max_temperature_c",
        "supply_air_temperature_c", "return_air_temperature_c", "supply_fan_state", "supply_fan_actual_rpm",
        "return_fan_actual_rpm", "airflow_m3_h", "cooling", "cooling_system_state", "compressor_feedback_on",
        "refrigerant_pressure_kpa", "filter_differential_pressure_pa", "filter_status", "condensate_pan_level_l",
        "duct_static_pressure_pa", "outdoor", "power_status", "alarms", "faults"),
    "animal_protection": (
        "online", "health", "overall_protection_state", "environment_temperature_c", "environment_humidity_pct",
        "co2_ppm", "o2_pct", "ammonia_ppm", "co_ppm", "smoke_detected", "smoke_obscuration_pct_m", "flood",
        "floodwater_level_cm", "water_available", "water_tank_level_pct", "feed_available", "feed_storage_level_pct",
        "wind_speed_m_s", "severe_weather_warning", "tornado_warning", "flood_warning", "population_count",
        "mains_power_status", "alarms", "faults"),
}
INCUBATION_KEYS = ("incubation_day", "days_remaining", "eggs", "current_egg_count", "egg_type", "egg_species",
                   "expected_hatch", "incubation_phase", "turning", "turn_count", "turns_per_day",
                   "seconds_since_last_turn")
HAZARD_KEYS: dict[str, tuple[str, ...]] = {
    "incubator": ("overtemperature_cutoff", "temperature_max_c", "heater_outlet_c", "ambient_temperature_c",
                  "water_leak_detected", "alarms"),
    "hvac": ("mode", "emergency_mode", "zone_max_temperature_c", "duct_static_pressure_pa", "alarms"),
    "animal_protection": ("smoke_detected", "smoke_obscuration_pct_m", "co_ppm", "co2_ppm", "ammonia_ppm", "o2_pct",
                          "flood", "floodwater_level_cm", "severe_weather_warning", "tornado_warning",
                          "flood_warning", "environment_temperature_c", "alarms"),
}


@dataclass
class PeerInfo:
    """What a node knows about another node (or itself)."""

    node: NodeId
    capabilities: Capabilities | None = None
    heartbeat: Heartbeat | None = None
    last_heard: float = field(default=float("-inf"))


def topics_for(config: NodeConfig, *, has_documents: bool = False) -> list[Topic]:
    """The questions this node can answer; documents join only while its approved index is nonempty."""
    topics: list[Topic] = ["status", "readings", "incident_history", "hazard_check"]
    if config.machine == "incubator":
        topics.insert(3, "incubation")
    if has_documents:
        topics.append("documents")
    return topics


class Node:
    """One node program's state and behaviour (see the module docstring)."""

    def __init__(self, config: NodeConfig, link: NatsLink, *, logger: logging.Logger, rules: RadioRules | None = None,
                 heartbeat_s: float = HEARTBEAT_S, silent_after_s: float = SILENT_AFTER_S, probe: bool = False,
                 data_dir: Path | None = None, engine: Any | None = None, switches: Any | None = None,
                 persona: Any | None = None,
                 mcp_enabled: bool = False, mcp_port: int | None = None, rom_dir: Path | None = None) -> None:
        if heartbeat_s <= 0 or silent_after_s <= heartbeat_s:
            raise ValueError("the silence limit must be longer than the heartbeat period")
        self.config = config
        self.id: NodeId = config.id
        self.allowed_reading_keys = READING_KEYS[config.machine]
        self.link = link
        self.logger = logger
        self.radio = PeerNetwork(self.id, link, rules=rules, logger=logger)
        self.bus = EventBus()
        self.heartbeat_s = heartbeat_s
        self.silent_after_s = silent_after_s
        self.probe_enabled = probe
        self.node_dir = Path(__file__).resolve().parents[1] / self.id
        self.data_dir = data_dir or self.node_dir / "data"
        self.rom_dir = rom_dir or self.node_dir / "memory" / "rom"
        self.engine = engine                              # this node's own LLM server (failure-injected), or None
        self.switches = switches                          # the mentor's LLM failure switches (llm/failure_injection.py)
        self.persona = persona                            # the speaking node's persona (llm/jarvis_speaker.py)
        self.agent: Any | None = None                     # incubator-1 only (llm/agent.py)
        self.peer_mind: PeerMind | None = None            # the three silent nodes
        self.alert_desk: AlertDesk | None = None          # incubator-1 only
        self.alert_watcher = AlertWatcher(config.id, self._send_notice)
        self._turns = asyncio.Semaphore(MAX_TURNS_AT_ONCE)
        self._background: set[asyncio.Task[Any]] = set()
        self.mcp_enabled = mcp_enabled
        self.mcp_port = mcp_port
        self.mcp: NodeMCP | None = None
        self.started_utc = utc_now()
        self._started = time.monotonic()
        self._seq = 0
        self.peers: dict[NodeId, PeerInfo] = {node: PeerInfo(node) for node in NODE_IDS}
        self.capability_registry = CapabilityRegistry(self.id, self.peers, self.radio, silent_after_s)
        self.status: FarmStatus | None = None
        self.assessment: FarmAssessment | None = None
        self.sim: SimState | None = None
        self.links: LinkStatus | None = None
        self.events: deque[FarmEvent] = deque(maxlen=EVENTS_KEPT)
        self.trend_buffer: deque[FarmStatus] = deque(maxlen=7200)
        self.feed_id: str | None = None                    # the farm run this node follows (FarmMessage.feed_id)
        self._retired_feeds: deque[str] = deque(maxlen=16)
        self.incidents: dict[str, FarmIncident] = {}
        self.store: LlmStore | None = None
        self.auditor: Auditor | None = None
        self.documents: Documents | None = None
        self.tools: ToolRegistry | None = None
        self.confirm_tokens = ConfirmTokens(secrets.token_bytes(32))
        self._view: tuple[tuple[NodeId, ...], tuple[NodeId, ...]] = ((), ())
        self._view_changed = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._announced = float("-inf")                  # when this node last re-announced itself (repairs)

    # --- lifecycle ---------------------------------------------------------------------------------------------

    async def start(self) -> None:
        """Open the bounded node data stores, index approved ROM documents, then join and announce on NATS."""
        me = self.id
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = LlmStore(self.data_dir / "llm.sqlite", node=me, owner_conversations=self.config.speaks)
        self.documents = Documents(self.rom_dir, self.data_dir / "docs_index.sqlite", BgeEmbedder(),
                                   logger=self.logger)
        async with asyncio.timeout(30.0):
            await asyncio.to_thread(self.documents.rebuild)
        self.auditor = Auditor(self.store, self.link, me, logger=self.logger)
        self.tools = ToolRegistry(self.config, self, self.store, self.documents, self.capability_registry,
                                  self.auditor, self.confirm_tokens, logger=self.logger)
        if self.switches is not None:
            await self.switches.serve(self.link)
        if self.config.speaks:
            if self.engine is not None and self.persona is not None:
                from llm.agent import Agent
                self.agent = Agent(self, self.engine, self.persona, logger=self.logger)
                self.agent.rewarm()
            self.alert_desk = AlertDesk(self.link, logger=self.logger,
                                        deliver=self.agent.alert if self.agent is not None else None)
        elif self.engine is not None:
            self.peer_mind = PeerMind(self.engine)
        if self.mcp_enabled and self.mcp_port is not None:
            self.mcp = NodeMCP(me, self.tools, self.mcp_port, logger=self.logger)
        self.peers[me].capabilities = self.capabilities("start")
        self.peers[me].last_heard = time.monotonic()
        await self.link.subscribe(farm_subject(me, "status"), FarmStatus, self._on_status)
        await self.link.subscribe(farm_subject(me, "assessment"), FarmAssessment, self._on_assessment)
        await self.link.subscribe(farm_subject(me, "events"), FarmEvent, self._on_event)
        await self.link.subscribe(farm_subject(me, "incident"), FarmIncident, self._on_incident)
        await self.link.subscribe(FARM_LINKS, LinkStatus, self._on_links)
        await self.link.subscribe(FARM_SIM, SimState, self._on_sim)
        await self.link.subscribe(jarvis_subject("*", "heartbeat"), Heartbeat, self._on_heartbeat)
        await self.link.subscribe(jarvis_subject("*", "capabilities"), Capabilities, self._on_capabilities)
        await self.radio.serve(self._answer_unless_silent)
        if self.alert_desk is not None:
            await self.radio.serve_notices(self.alert_desk.receive)
            await self.link.serve(jarvis_subject(me, "ack"), AckCommand, self._on_ack)
        if self.agent is not None:
            await self.link.subscribe(jarvis_subject(me, "chat"), ChatRequest, self._on_chat)
        if self.probe_enabled:
            await self.link.serve(jarvis_subject(me, "probe"), ProbeRequest, self._on_probe)
        await self.link.publish(jarvis_subject(me, "capabilities"), self.capabilities("start"))
        if self.mcp is not None:
            await self.mcp.start()
        self._tasks = [asyncio.create_task(self._heartbeat_loop()), asyncio.create_task(self._silence_loop()),
                       asyncio.create_task(self._document_refresh_loop())]
        log(self.logger, logging.INFO, "node started", node=me, speaks=self.config.speaks, probe=self.probe_enabled)

    async def stop(self) -> None:
        """Stop the loops and close the connection."""
        if self.mcp is not None:
            await self.mcp.stop()
            self.mcp = None
        for task in [*self._tasks, *self._background]:
            task.cancel()
        await asyncio.gather(*self._tasks, *self._background, return_exceptions=True)
        if self.agent is not None:
            await self.agent.close()
        if self.engine is not None:
            await self.engine.close()
        await self.link.close()
        if self.documents is not None:
            await asyncio.to_thread(self.documents.close)
            self.documents = None
        if self.store is not None:
            await self.store.close()
            self.store = None
        log(self.logger, logging.INFO, "node stopped", node=self.id)

    # --- what this node says about itself ------------------------------------------------------------------------

    def capabilities(self, reason: str) -> Capabilities:
        """This node's announcement: its machine, whether it speaks, the topics it answers, its documents."""
        documents = self.documents.infos() if self.documents is not None else []
        return Capabilities(node=self.id, machine=self.config.machine, speaks=self.config.speaks,
                            topics=topics_for(self.config, has_documents=bool(documents)), documents=documents,
                            started_utc=self.started_utc,
                            reason="reply" if reason == "reply" else "change" if reason == "change" else "start")

    def known(self) -> list[NodeId]:
        """Nodes whose capabilities this node has (itself included), in farm order."""
        return [node for node in NODE_IDS if self.peers[node].capabilities is not None]

    def alive(self) -> list[NodeId]:
        """Nodes heard from within the silence limit (itself included)."""
        now = time.monotonic()
        return [node for node in NODE_IDS if node == self.id or now - self.peers[node].last_heard <= self.silent_after_s]

    def heartbeat(self) -> Heartbeat:
        """The next heartbeat."""
        self._seq += 1
        return Heartbeat(node=self.id, seq=self._seq, sent_utc=utc_now(), started_utc=self.started_utc,
                         uptime_s=time.monotonic() - self._started, farm_tick=self.sim.tick if self.sim else None,
                         known=self.known(), alive=self.alive())

    # --- answering questions -----------------------------------------------------------------------------------

    async def answer(self, request: AskRequest) -> AskReply:
        """Answer from this node's fixed read-only tool set, never from another machine's feed."""
        started = time.monotonic()
        auditor, registry = self.auditor, self.tools
        if auditor is None or registry is None:
            return AskReply(request_id=request.request_id, sender=self.id, target=request.sender, topic=request.topic,
                            ok=False, error="no_data", detail="node tools are not ready")
        tool_calls: dict[Topic, list[tuple[str, dict[str, Any]]]] = {
            "status": [("get_assessment", {}), ("get_status", {"fields": ["online", "health"]})],
            "readings": [("get_status", {"fields": list(READING_KEYS[self.config.machine])})],
            "incident_history": [("get_incident", {"incident_id": "current"}),
                                 ("list_events", {"since_minutes": 60, "severity": "warning", "limit": 50})],
            "hazard_check": [("get_status", {"fields": list(HAZARD_KEYS[self.config.machine])}),
                             ("list_events", {"since_minutes": 30, "severity": "critical", "limit": 50})],
            # a question may be 2,000 characters (AskRequest); a search query at most 300 (SearchArgs)
            "documents": [("search_documents", {"query": request.question[:300] or request.topic, "k": 4})],
        }
        if self.config.machine == "incubator":
            tool_calls["incubation"] = [("get_status", {"fields": list(INCUBATION_KEYS)})]

        try:
            async with auditor.step(request.request_id, "peer_answer", None,
                                    {"sender": request.sender, "topic": request.topic,
                                     "question": request.question}) as audit_step:
                if request.topic not in topics_for(self.config, has_documents=bool(self.documents and self.documents.infos())):
                    raise _PeerAnswerFailure("unavailable", f"{self.id} does not answer {request.topic!r} questions")
                outcomes = await asyncio.gather(*(registry.call(name, args, request_id=request.request_id, caller="peer")
                                                  for name, args in tool_calls[request.topic]))
                failed = next((outcome for outcome in outcomes if not outcome.ok), None)
                if failed is not None:
                    raise _PeerAnswerFailure("no_data", failed.error or "required farm data is not available")
                answer: dict[str, JsonValue] = {name: outcome.data for (name, _), outcome
                                                in zip(tool_calls[request.topic], outcomes, strict=True)}
                if request.topic == "documents" and not answer["search_documents"]["results"]:  # type: ignore[index]
                    raise _PeerAnswerFailure("no_data", "no approved document matched the question")
                # the answer's farm time is its newest data's (an incident's own time may be older than the readings)
                timed = [outcome for outcome in outcomes if outcome.farm_time is not None and outcome.tick is not None]
                source = max(timed, key=lambda outcome: outcome.tick or 0, default=None)
                if source is None:
                    raise _PeerAnswerFailure("no_data", "tool results have no farm time")
                if self.peer_mind is not None:                 # a verified summary, never at the answer's expense
                    budget = budget_s(request.deadline_ms, self.radio.latency_s, time.monotonic() - started)
                    summary, summary_status = await self.peer_mind.summarize(request.question, dict(answer), budget)
                    if summary is not None:
                        answer["summary"] = summary
                    else:
                        answer["summary_status"] = summary_status
                audit_step.done(answer, source.farm_time, source.tick)
                return AskReply(request_id=request.request_id, sender=self.id, target=request.sender, topic=request.topic,
                                ok=True, answer=answer, farm_time=source.farm_time, tick=source.tick)
        except _PeerAnswerFailure as error:
            return AskReply(request_id=request.request_id, sender=self.id, target=request.sender, topic=request.topic,
                            ok=False, error=cast(AskError, error.kind), detail=error.detail[:500])

    async def _answer_unless_silent(self, request: AskRequest) -> AskReply | None:
        """The mentor's silent_peer switch (test only): this node stops answering, as a dead one would."""
        if self.switches is not None and self.switches.on("silent_peer"):
            log(self.logger, logging.INFO, "question ignored: silent_peer switch", request_id=request.request_id)
            return None
        return await self.answer(request)

    # --- the owner: chat, acknowledgements, alerts (incubator-1) ---------------------------------------------------

    def _spawn(self, work: Awaitable[Any]) -> bool:
        """Run one piece of background work (bounded): False when too much is already in flight."""
        if len(self._background) >= MAX_BACKGROUND:
            return False
        task = asyncio.ensure_future(work)
        self._background.add(task)
        task.add_done_callback(self._finished)
        return True

    def _finished(self, task: asyncio.Future[Any]) -> None:
        """A background task ended: forget it, and log its failure (a lost exception would hide a lost alert)."""
        self._background.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log(self.logger, logging.ERROR, "background task failed", error=repr(task.exception()))

    async def _on_chat(self, request: ChatRequest, subject: str) -> None:
        if not self._spawn(self._serve_turn(request)):
            await self._busy(request, "too many requests in flight")

    async def _busy(self, request: ChatRequest, reason: str) -> None:
        done = ChatDone(request_id=request.request_id, conversation_id=request.conversation_id or "none", text="",
                        chunks=0, outcome="error", reason=reason, numbers_checked=0, timings=ReplyTimings(total_ms=0))
        await self.link.publish(jarvis_subject(self.id, "reply"), done, request_id=request.request_id)

    async def _serve_turn(self, request: ChatRequest) -> None:
        """One owner turn: every piece the agent yields goes out on jarvis.incubator-1.reply, in order."""
        async for item in self.owner_turn(request.text, request_id=request.request_id,
                                          conversation_id=request.conversation_id, source=request.source):
            await self.link.publish(jarvis_subject(self.id, "reply"), item, request_id=request.request_id)

    async def owner_turn(self, text: str, *, request_id: str, conversation_id: str | None,
                         source: Literal["chat", "voice"]) -> AsyncIterator[ChatChunk | ChatDone]:
        """The agent's reply to one owner turn, chat or voice, at most MAX_TURNS_AT_ONCE at once (the 2B server has
        one slot); a turn past the limit gets a ChatDone saying so at once."""
        agent = self.agent
        reason = ("the agent is not running" if agent is None else
                  "JARVIS is already answering two questions" if self._turns.locked() else "")
        if agent is None or reason:
            yield ChatDone(request_id=request_id, conversation_id=conversation_id or "none", text="", chunks=0,
                           outcome="error", reason=reason, numbers_checked=0, timings=ReplyTimings(total_ms=0))
            return
        async with self._turns:
            async for item in agent.reply(text, request_id=request_id, conversation_id=conversation_id,
                                          source=source):
                yield item

    async def _on_ack(self, command: AckCommand, subject: str) -> AckResult:
        """The owner's confirm click (through the gateway): the token is checked and the approval audited by the
        acknowledge_alert tool (caller "owner"); only on ok does the gateway tell the Matrix."""
        registry = self.tools
        if registry is None:
            return AckResult(request_id=command.request_id, node=command.node, incident_id=command.incident_id,
                             ok=False, reason="node tools are not ready")
        outcome = await registry.call("acknowledge_alert", {"node": command.node, "incident_id": command.incident_id,
                                                            "feed_id": command.feed_id,
                                                            "confirm_token": command.confirm_token},
                                      request_id=command.request_id, caller="owner")
        return AckResult(request_id=command.request_id, node=command.node, incident_id=command.incident_id,
                         ok=outcome.ok, reason="" if outcome.ok else (outcome.error or "refused")[:300])

    async def _send_notice(self, notice: AlertNotice) -> bool:
        """This node's own alert, through the radio to incubator-1 (incubator-1's own goes to its desk directly).
        False when it did not leave (a cut link or a lost notice): the watcher sends it again."""
        return await self.radio.notify(notice)

    async def _on_probe(self, probe: ProbeRequest, subject: str) -> AskReply:
        return await self.radio.ask(probe.target, probe.topic, probe.question, deadline_s=probe.deadline_ms / 1000)

    # --- the farm feed -----------------------------------------------------------------------------------------

    def _follow_feed(self, feed_id: str) -> bool:
        """Track the farm run. A message from a new run (a farm reset or restart) starts the node's in-memory view
        afresh, because the new run's ticks, sequences and incident ids begin again; a late message from a run that
        has already been replaced is dropped (False). The stored events and incidents are kept per run by LlmStore."""
        if feed_id == self.feed_id:
            return True
        if feed_id in self._retired_feeds:
            return False
        if self.feed_id is not None:
            self._retired_feeds.append(self.feed_id)
            log(self.logger, logging.INFO, "a new farm run", node=self.id, feed_id=feed_id, previous=self.feed_id)
        self.feed_id = feed_id
        self.status = self.assessment = None
        self.trend_buffer.clear()
        self.events.clear()
        self.incidents.clear()
        return True

    async def _on_status(self, status: FarmStatus, subject: str) -> None:
        if not self._follow_feed(status.feed_id):
            return
        self.status = status
        self.trend_buffer.append(status)
        cutoff = status.time_s - 2 * 60 * 60
        while self.trend_buffer and self.trend_buffer[0].time_s < cutoff:
            self.trend_buffer.popleft()
        self.bus.publish("farm.status", status)

    async def _on_assessment(self, assessment: FarmAssessment, subject: str) -> None:
        if not self._follow_feed(assessment.feed_id):
            return
        self.assessment = assessment
        self.bus.publish("farm.assessment", assessment)
        # The alert path never waits on the feed. The calm farm (decision 0023): only a machine the farm clock names
        # may reach the owner; the brains' own escalations on a normal day stay on the feed and the events page.
        if assessment.assessment in ("ESCALATE_TO_USER", "DANGER") and self.sim is not None                 and assessment.node in self.sim.alerts_for:
            self._spawn(self.alert_desk.own(assessment) if self.alert_desk is not None
                        else self.alert_watcher.on_assessment(assessment))

    async def _on_event(self, event: FarmEvent, subject: str) -> None:
        if not self._follow_feed(event.feed_id):
            return
        self.events.append(event)
        if self.store is not None:
            await self.store.add_event(event)
        self.bus.publish("farm.events", event)

    async def _on_incident(self, incident: FarmIncident, subject: str) -> None:
        if not self._follow_feed(incident.feed_id):
            return
        self.incidents[incident.incident_id] = incident
        while len(self.incidents) > INCIDENTS_KEPT:
            self.incidents.pop(next(iter(self.incidents)))
        if self.store is not None:
            await self.store.upsert_incident(incident)
        self.bus.publish("farm.incident", incident)

    async def _on_links(self, status: LinkStatus, subject: str) -> None:
        self.links = status
        self.radio.update_links(status)

    async def _on_sim(self, sim: SimState, subject: str) -> None:
        if not self._follow_feed(sim.feed_id):
            return
        self.sim = sim
        self.radio.update_sim(sim)

    # --- the other nodes ---------------------------------------------------------------------------------------

    async def _on_heartbeat(self, heartbeat: Heartbeat, subject: str) -> None:
        if heartbeat.node == self.id:
            return
        peer = self.peers[heartbeat.node]
        peer.heartbeat = heartbeat
        peer.last_heard = time.monotonic()
        if peer.capabilities is None:                     # its announcement was lost: ask everyone again
            await self._announce("start")
        elif self.id not in heartbeat.known:              # ours was lost on its side: announce again
            await self._announce("change")
        self._check_view()

    async def _announce(self, reason: str) -> None:
        """Re-announce this node, at most once per heartbeat period ("start" makes every node answer)."""
        now = time.monotonic()
        if now - self._announced < self.heartbeat_s:
            return
        self._announced = now
        log(self.logger, logging.INFO, "announcing again", node=self.id, reason=reason)
        await self.link.publish(jarvis_subject(self.id, "capabilities"), self.capabilities(reason))

    async def _on_capabilities(self, capabilities: Capabilities, subject: str) -> None:
        if capabilities.node == self.id:
            return
        peer = self.peers[capabilities.node]
        new = peer.capabilities is None or capabilities.reason == "start"
        peer.capabilities = capabilities
        peer.last_heard = time.monotonic()
        if new:
            log(self.logger, logging.INFO, "a node joined", node=capabilities.node, reason=capabilities.reason,
                topics=list(capabilities.topics))
        if capabilities.reason == "start":               # a newcomer: tell it who we are
            await self.link.publish(jarvis_subject(self.id, "capabilities"), self.capabilities("reply"))
        self._check_view()

    def _check_view(self) -> None:
        view = (tuple(self.known()), tuple(self.alive()))
        if view != self._view:
            self._view = view
            self._view_changed.set()

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._view_changed.wait(), timeout=self.heartbeat_s)
            except asyncio.TimeoutError:
                pass
            self._view_changed.clear()
            await self.link.publish(jarvis_subject(self.id, "heartbeat"), self.heartbeat())

    async def _silence_loop(self) -> None:
        while True:
            await asyncio.sleep(min(1.0, self.heartbeat_s))
            before = self._view[1]
            self._check_view()
            if self._view[1] != before:
                silent = sorted(set(before) - set(self._view[1]))
                if silent:
                    log(self.logger, logging.WARNING, "peers went silent", silent=silent)

    async def _document_refresh_loop(self) -> None:
        """Rebuild the ROM index periodically and announce only when its indexed set changes. A failed rebuild is
        logged and retried at the next period: one bad moment (a file being written, a locked index) must not end the
        refreshing for the node's whole life."""
        while True:
            await asyncio.sleep(DOCUMENT_REFRESH_S)
            documents = self.documents
            if documents is None:
                continue
            try:
                async with asyncio.timeout(30.0):
                    changed = await asyncio.to_thread(documents.rebuild)
            except Exception as error:                     # never CancelledError: that is a BaseException
                log(self.logger, logging.WARNING, "document index refresh failed; retrying", node=self.id,
                    error=f"{type(error).__name__}: {error}")
                continue
            if changed:
                capabilities = self.capabilities("change")
                self.peers[self.id].capabilities = capabilities
                await self.link.publish(jarvis_subject(self.id, "capabilities"), capabilities)


def _pick(status: FarmStatus, keys: tuple[str, ...]) -> dict[str, JsonValue]:
    """The chosen readings that the machine published (missing fields are left out, never guessed)."""
    readings = status.readings
    return {key: readings[key] for key in keys if key in readings}
