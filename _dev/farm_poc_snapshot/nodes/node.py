"""One Jarvis node: identity, its own machine, brain, memory, and LLM slot."""
from __future__ import annotations

from collections import deque

from . import evidence
from .contracts import MACHINE_TYPES, NODE_IDS, Answer, NodeOutput, PeerMessage, Question
from .encoders import NodeEncoderV1
from .memory import NodeMemory
from .rule_brain import NodeContext

# Language models are loaded in the LLM phase; nodes only declare their slot now.
LLM_SLOTS = {
    "incubator-1": {"model": "Qwen-3.5-2b-roleplay-tuned.Q8_0.gguf", "role": "voice"},
    "incubator-2": {"model": "Qwen3-0.6B-Q8_0.gguf", "role": "silent"},
    "hvac": {"model": "Qwen3-0.6B-Q8_0.gguf", "role": "silent"},
    "animal-protection": {"model": "Qwen3-0.6B-Q8_0.gguf", "role": "silent"},
}


class JarvisNode:
    HEARTBEAT_S = 10.0

    def __init__(self, node_id: str, *, mode: str, brain, encoder: NodeEncoderV1 | None = None, memory=None):
        if node_id not in NODE_IDS:
            raise ValueError(f"unknown node {node_id}")
        if mode not in ("real", "simulated"):
            raise ValueError("node mode must be real or simulated")
        self.node_id, self.mode, self.brain = node_id, mode, brain
        self.machine = MACHINE_TYPES[node_id]
        self.encoder = encoder or NodeEncoderV1(node_id)
        self.memory = memory if memory is not None else NodeMemory(node_id)   # CMM v3 when given (opt-in)
        self.llm_slot = dict(LLM_SLOTS[node_id])
        self.peers: dict[str, PeerMessage] = {}
        self.answers: deque = deque(maxlen=50)
        self.pending_answers: list[Answer] = []
        self.monitoring: dict | None = None
        self.monitoring_time_s: float | None = None
        self.last_output: NodeOutput | None = None
        self.incident_id: str | None = None
        self._last_sent = None
        self._last_sent_s = -1e18

    # ------------------------------------------------------------ inputs
    def observe(self, monitoring: dict | None, time_s: float) -> None:
        """Latest dataset from this node's own machine (None when the machine is silent)."""
        if monitoring is not None:
            self.monitoring, self.monitoring_time_s = monitoring, time_s
            readings = evidence.key_readings(self.machine, monitoring)
            if self.machine == "incubator":
                readings["heater_output_pct"] = monitoring["heating"]["output_pct"]
                self.memory.track_water_inlet(time_s, bool(monitoring["humidification"]["pump_on"]),
                                              monitoring["water"].get("inlet_flow_ml_min"))
            if self.machine == "animal_protection":
                readings["barn_humidity_pct"] = monitoring["animal_environment"]["humidity_pct"]
                readings["flood_warning"] = 1.0 if readings.get("flood_warning") else 0.0
            if self.machine == "hvac":
                self.memory.track_zone_sensors(readings.get("zone_temperatures_c"))
            self.memory.remember_readings(time_s, readings)

    def receive(self, items, tick: int) -> None:
        for item in items:
            if isinstance(item, PeerMessage):
                previous = self.peers.get(item.sender)
                if previous is None or item.sample_tick >= previous.sample_tick:
                    self.peers[item.sender] = item
            elif isinstance(item, Question):
                self.pending_answers.append(self.answer(item, tick))
            elif isinstance(item, Answer):
                self.answers.append(item)

    # ------------------------------------------------------------ thinking
    def context(self, tick: int, time_s: float, step_seconds: float, comms_status: dict) -> NodeContext:
        age = 1e9 if self.monitoring_time_s is None else time_s - self.monitoring_time_s
        return NodeContext(self.node_id, tick, time_s, step_seconds, self.monitoring, age, dict(self.peers),
                           self.memory, comms_status)

    def learn_call_status(self, time_s: float, comms_status: dict) -> None:
        order = ("received", "dispatched", "on_scene", "cleared", "false_alarm")
        reports = [(call["service"], call["status"]) for call in comms_status.get("calls", [])]
        for message in self.peers.values():
            for entry in message.calls_made:
                service, _, status = entry.partition(":")
                if status in order:
                    reports.append((service, status))
        statuses: dict[str, str] = {}
        for service, status in reports:  # keep the most advanced status any source reports
            if service not in statuses or order.index(status) > order.index(statuses[service]):
                statuses[service] = status
        self.memory.note_call_status(time_s, statuses)

    def encode_input(self, ctx: NodeContext):
        """The CTM input for this tick as the brain sees it: before deciding, so memory holds only earlier ticks."""
        return self.encoder.encode(ctx.monitoring, observation_age_s=min(ctx.observation_age_s, 1e6),
                                   peers=ctx.peers, memory=self.memory, comms_status=ctx.comms_status,
                                   time_s=ctx.time_s, step_seconds=ctx.step_seconds)

    def think(self, ctx: NodeContext) -> NodeOutput:
        if getattr(self.brain, "name", "") == "ctm":
            output = self.brain.decide(ctx, self.encode_input(ctx))
        else:
            output = self.brain.decide(ctx)
        reports = {s: (m.incident_kind, m.incident_id) for s, m in self.peers.items() if m.assessment != "NORMAL"}
        self.incident_id = self.memory.record(ctx.time_s, output.assessment, output.diagnosis, output.incident_kind, reports)
        self.last_output = output
        return output

    # ------------------------------------------------------------ outputs
    def outgoing(self, tick: int, time_s: float) -> PeerMessage | None:
        output = self.last_output
        if output is None:
            return None
        signature = (output.assessment, output.diagnosis, self.incident_id)
        heartbeat = time_s - self._last_sent_s >= self.HEARTBEAT_S
        if signature == self._last_sent and not heartbeat:
            return None
        incident = self.memory.active()
        sample_tick = tick if self.monitoring_time_s == time_s else int(tick - (time_s - (self.monitoring_time_s or 0.0)))
        message = PeerMessage(self.node_id, max(0, sample_tick), tick, output.assessment, output.diagnosis,
                              round(output.confidence, 4), self.incident_id, output.incident_kind,
                              evidence.key_readings(self.machine, self.monitoring),
                              tuple(incident["actions"]) if incident else (),
                              tuple(sorted(f"{service}:{status}" for service, status in self.memory.call_status.items())),
                              heartbeat=signature == self._last_sent)
        self._last_sent, self._last_sent_s = signature, time_s
        return message

    def answer(self, question: Question, tick: int) -> Answer:
        topic, content = question.topic, {}
        if topic == "status" and self.last_output is not None:
            content = {k: self.last_output.as_dict()[k] for k in ("assessment", "diagnosis", "confidence", "recommendation")}
        elif topic == "readings":
            content = evidence.key_readings(self.machine, self.monitoring)
        elif topic == "incident_history":
            content = {"incidents": self.memory.summary()}
        elif topic == "incubation" and self.machine == "incubator" and self.monitoring:
            content = dict(self.monitoring["eggs"])
        elif topic == "hazard_check":
            content = {"fire_signs": evidence.fire_indicators(self.machine, self.monitoring, self.peers, self.memory),
                       "gas_signs": evidence.gas_hazard(self.machine, self.monitoring, self.peers),
                       "flood": evidence.flood_state(self.monitoring, self.peers, self.machine)}
        else:
            content = {"unavailable": f"{self.node_id} has no answer for {topic}"}
        return Answer(question.question_id, self.node_id, question.sender, topic, tick, content)
