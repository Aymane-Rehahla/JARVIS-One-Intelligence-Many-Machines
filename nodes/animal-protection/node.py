"""One node of JARVIS (step 6A): its identity, its machine's farm feed, the other nodes, and the questions between them.

A node program (`runtime.py`) builds a Node from its `node.toml` and runs it. The node joins NATS, announces its
capabilities, sends a heartbeat every HEARTBEAT_S seconds (and at once whenever its view of the other nodes changes),
follows its own machine's farm subjects, answers the other nodes' questions through the ZigBee rules, and keeps a
registry of who is alive and what each can answer. Hot-join: a node that starts later announces itself, every node
answers with its own capabilities, and within one round trip all of them know each other, with no restart. If an
announcement is lost, the heartbeats repair it: a node that hears a heartbeat from a node it has no capabilities for
asks everyone to announce again, and a node whose heartbeat peers say they do not know it announces itself again (each
at most once per heartbeat period).

Answers come only from what the farm runtime published for this node's own machine (ground rule 4, public telemetry
only); a node never reads another machine's data except by asking that node. The language model, tools, documents and
voice join in 6B-6E.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from pydantic import JsonValue

from comms.events import EventBus
from comms.jsonlog import log
from comms.nats_link import NatsLink
from comms.peer_network import PeerNetwork, RadioRules
from contracts import (FARM_LINKS, FARM_SIM, NODE_IDS, AskReply, AskRequest, Capabilities, FarmAssessment, FarmEvent,
                       FarmIncident, FarmStatus, Heartbeat, LinkStatus, NodeConfig, NodeId, ProbeRequest, SimState, Topic,
                       farm_subject, jarvis_subject, utc_now)

HEARTBEAT_S = 10.0
SILENT_AFTER_S = 30.0                       # three missed heartbeats: the peer is silent
EVENTS_KEPT = 200
INCIDENTS_KEPT = 20

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


def topics_for(config: NodeConfig) -> list[Topic]:
    """The questions this node can answer (documents join in 6B)."""
    topics: list[Topic] = ["status", "readings", "incident_history", "hazard_check"]
    if config.machine == "incubator":
        topics.insert(3, "incubation")
    return topics


class Node:
    """One node program's state and behaviour (see the module docstring)."""

    def __init__(self, config: NodeConfig, link: NatsLink, *, logger: logging.Logger, rules: RadioRules | None = None,
                 heartbeat_s: float = HEARTBEAT_S, silent_after_s: float = SILENT_AFTER_S, probe: bool = False) -> None:
        if heartbeat_s <= 0 or silent_after_s <= heartbeat_s:
            raise ValueError("the silence limit must be longer than the heartbeat period")
        self.config = config
        self.id: NodeId = config.id
        self.link = link
        self.logger = logger
        self.radio = PeerNetwork(self.id, link, rules=rules, logger=logger)
        self.bus = EventBus()
        self.heartbeat_s = heartbeat_s
        self.silent_after_s = silent_after_s
        self.probe_enabled = probe
        self.started_utc = utc_now()
        self._started = time.monotonic()
        self._seq = 0
        self.peers: dict[NodeId, PeerInfo] = {node: PeerInfo(node) for node in NODE_IDS}
        self.status: FarmStatus | None = None
        self.assessment: FarmAssessment | None = None
        self.sim: SimState | None = None
        self.links: LinkStatus | None = None
        self.events: deque[FarmEvent] = deque(maxlen=EVENTS_KEPT)
        self.incidents: dict[str, FarmIncident] = {}
        self._view: tuple[tuple[NodeId, ...], tuple[NodeId, ...]] = ((), ())
        self._view_changed = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._announced = float("-inf")                  # when this node last re-announced itself (repairs)

    # --- lifecycle ---------------------------------------------------------------------------------------------

    async def start(self) -> None:
        """Join NATS, subscribe, start answering, announce this node, and start the heartbeat."""
        me = self.id
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
        await self.radio.serve(self.answer)
        if self.probe_enabled:
            await self.link.serve(jarvis_subject(me, "probe"), ProbeRequest, self._on_probe)
        await self.link.publish(jarvis_subject(me, "capabilities"), self.capabilities("start"))
        self._tasks = [asyncio.create_task(self._heartbeat_loop()), asyncio.create_task(self._silence_loop())]
        log(self.logger, logging.INFO, "node started", node=me, speaks=self.config.speaks, probe=self.probe_enabled)

    async def stop(self) -> None:
        """Stop the loops and close the connection."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.link.close()
        log(self.logger, logging.INFO, "node stopped", node=self.id)

    # --- what this node says about itself ------------------------------------------------------------------------

    def capabilities(self, reason: str) -> Capabilities:
        """This node's announcement: its machine, whether it speaks, the topics it answers, its documents."""
        return Capabilities(node=self.id, machine=self.config.machine, speaks=self.config.speaks,
                            topics=topics_for(self.config), documents=[], started_utc=self.started_utc,
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
        """Answer another node's question from this machine's published farm data (never invented: no data, no
        answer)."""
        topic = request.topic
        status = self.status
        machine = self.config.machine

        def reply(answer: dict[str, JsonValue]) -> AskReply:
            source = (self.assessment if topic == "status" and self.assessment else status) or self.sim
            return AskReply(request_id=request.request_id, sender=self.id, target=request.sender, topic=topic,
                            ok=True, answer=answer, farm_time=source.farm_time if source else None,
                            tick=source.tick if source else None)

        def refuse(error: str, detail: str) -> AskReply:
            return AskReply(request_id=request.request_id, sender=self.id, target=request.sender, topic=topic,
                            ok=False, error="no_data" if error == "no_data" else "unavailable", detail=detail)

        if topic not in topics_for(self.config):
            return refuse("unavailable", f"{self.id} does not answer {topic!r} questions")
        if topic == "status":
            if self.assessment is None:
                return refuse("no_data", f"{self.id} has no assessment from its reasoning yet")
            a = self.assessment
            answer: dict[str, JsonValue] = {
                "assessment": a.assessment, "diagnosis": a.diagnosis, "confidence": a.confidence,
                "recommendation": a.recommendation, "incident_kind": a.incident_kind, "incident_id": a.incident_id,
                "brain": a.brain, "online": status.online if status else None}
            return reply(answer)
        if topic == "incident_history":
            incidents: list[JsonValue] = [i.model_dump(mode="json", include={"incident_id", "state", "kind",
                                                                              "farm_time", "summary"})
                                          for i in self.incidents.values()]
            return reply({"incidents": incidents})
        if status is None:
            return refuse("no_data", f"{self.id} has no readings from its machine yet")
        if topic == "readings":
            return reply(_pick(status, READING_KEYS[machine]))
        if topic == "incubation":
            return reply(_pick(status, INCUBATION_KEYS))
        return reply(_pick(status, HAZARD_KEYS[machine]))                  # hazard_check

    async def _on_probe(self, probe: ProbeRequest, subject: str) -> AskReply:
        return await self.radio.ask(probe.target, probe.topic, probe.question, deadline_s=probe.deadline_ms / 1000)

    # --- the farm feed -----------------------------------------------------------------------------------------

    async def _on_status(self, status: FarmStatus, subject: str) -> None:
        self.status = status
        self.bus.publish("farm.status", status)

    async def _on_assessment(self, assessment: FarmAssessment, subject: str) -> None:
        self.assessment = assessment
        self.bus.publish("farm.assessment", assessment)

    async def _on_event(self, event: FarmEvent, subject: str) -> None:
        self.events.append(event)
        self.bus.publish("farm.events", event)

    async def _on_incident(self, incident: FarmIncident, subject: str) -> None:
        self.incidents[incident.incident_id] = incident
        while len(self.incidents) > INCIDENTS_KEPT:
            self.incidents.pop(next(iter(self.incidents)))
        self.bus.publish("farm.incident", incident)

    async def _on_links(self, status: LinkStatus, subject: str) -> None:
        self.links = status
        self.radio.update_links(status)

    async def _on_sim(self, sim: SimState, subject: str) -> None:
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


def _pick(status: FarmStatus, keys: tuple[str, ...]) -> dict[str, JsonValue]:
    """The chosen readings that the machine published (missing fields are left out, never guessed)."""
    readings = status.readings
    return {key: readings[key] for key in keys if key in readings}
