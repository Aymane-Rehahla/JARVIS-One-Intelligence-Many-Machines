"""Runs the four Jarvis nodes next to the Matrix, one decision per tick."""
from __future__ import annotations

from . import evidence
from .bus import PeerBus
from .contracts import MACHINE_TYPES, NODE_IDS, Question
from .ctm_brain import NodeBrainConfig, NodeCTMBrain
from .encoders import NodeEncoderV1
from .gate import SafetyGateV2, jarvis_id
from .node import JarvisNode
from .rule_brain import RuleBrain


class NodeRuntime:
    """Four independent nodes; each machine runs a real AI (CTM) or a simulated one (rules).

    Order each tick: the Matrix advances (physics, devices, phone service), then
    every node reads its own machine and its delivered peer messages, decides,
    passes proposals through the safety gate, and broadcasts.
    """

    def __init__(self, engine, *, modes: dict[str, str] | None = None, brains: dict | None = None,
                 latency_ticks: int = 1, drop_probability: float = 0.0, memory_factory=None, advisors: dict | None = None):
        """``memory_factory(node_id)``: give each node a CMM v3 memory (default: the plain working memory).

        ``advisors`` ({node_id: trained brain}): "listen to rules, decide with the CTM". The node itself runs
        the rule brain, which keeps driving what the brains' inputs are made of: the node's memory status,
        its messages to peers, the owner notifications, calls and actions (through the gate). So every
        input a trained brain sees is made exactly as in its training data, which was recorded with rule
        brains driving. Each tick the advisor reads that same input, before the node decides, and gives
        the node's reported verdict (``results[node]["verdict"]``); a CMM memory stores that verdict."""
        self.engine = engine
        self.advisors = dict(advisors or {})
        modes = {node_id: "simulated" for node_id in NODE_IDS} | dict(modes or {})
        self.bus = PeerBus(engine.seed, latency_ticks=latency_ticks, drop_probability=drop_probability)
        self.gate = SafetyGateV2(engine)
        self.nodes: dict[str, JarvisNode] = {}
        brains = dict(brains or {})
        for node_id in NODE_IDS:
            encoder = NodeEncoderV1(node_id)
            if modes[node_id] == "real":
                brain = brains.get(node_id) or NodeCTMBrain(NodeBrainConfig(MACHINE_TYPES[node_id]), encoder)
            else:
                brain = RuleBrain()
            self.nodes[node_id] = JarvisNode(node_id, mode=modes[node_id], brain=brain, encoder=encoder,
                                             memory=memory_factory(node_id) if memory_factory else None)
        self.outputs: list = []
        self.verifications: list = []
        self.recorded_inputs: dict | None = None  # set to {} to capture every node's input each tick
        self._questions = 0

    @classmethod
    def live(cls, engine, memory_dir="runs/cmm", **kwargs):
        """The farm as it runs for real: every node remembers with CMM v3 (SQLite per node in ``memory_dir``).
        Dataset recording and training keep the plain working memory (``NodeRuntime(engine)``)."""
        from pathlib import Path
        from .cmm import CognitiveMemory
        folder = Path(memory_dir)
        folder.mkdir(parents=True, exist_ok=True)
        return cls(engine, memory_factory=lambda node_id: CognitiveMemory(node_id, folder / f"{node_id}.sqlite"), **kwargs)

    def ask(self, sender: str, target: str, topic: str) -> Question:
        if topic not in Question.TOPICS:
            raise ValueError(f"unknown question topic {topic}")
        self._questions += 1
        tick = self.engine.clock.tick
        question = Question(f"q-{self._questions:05d}", sender, target, topic, tick)
        self.bus.send(question, sender, (target,), tick)
        return question

    def step(self) -> dict:
        engine = self.engine
        engine.step(return_snapshot=False)
        tick = engine.clock.tick - 1
        time_s = tick * engine.clock.step_seconds
        for record in self.gate.verify():
            self.nodes[record["node"]].memory.action_results[record["action"]] = record["status"]
            self.verifications.append(record)
        inbox = self.bus.deliver(tick)
        results = {}
        for node_id, node in self.nodes.items():
            node.receive(inbox.get(node_id, []), tick)
            device = engine.world.devices[node_id]
            # Read-only view: telemetry is rebuilt each publish and nodes never mutate it.
            node.observe(device.monitoring_output(copy=False) if device.online else None, time_s)
        for node_id, node in self.nodes.items():
            comms_status = engine.comms.status_for(jarvis_id(node_id))
            node.learn_call_status(time_s, comms_status)
            ctx = node.context(tick, time_s, engine.clock.step_seconds, comms_status)
            advisor = self.advisors.get(node_id)
            encoded = node.encode_input(ctx) if self.recorded_inputs is not None or advisor else None
            if self.recorded_inputs is not None:  # dataset recording: the input as of decision time
                self.recorded_inputs[node_id] = encoded
            verdict = advisor.decide(ctx, encoded) if advisor else None   # same input and memory as the node's rules
            output = node.think(ctx)
            action = self.gate.apply_action(output, ctx)
            communication = self.gate.apply_communication(output, ctx, node.incident_id)
            results[node_id] = {"output": output, "action": action, "communication": communication,
                                "incident_id": node.incident_id, "verdict": verdict}
            if hasattr(node.memory, "observe_tick"):
                self._remember(node, verdict or output, (action, communication), time_s)
            self.outputs.append(output)
        for node_id, node in self.nodes.items():
            message = node.outgoing(tick, time_s)
            if message is not None:
                self.bus.send(message, node_id, [n for n in NODE_IDS if n != node_id], tick)
            for answer in node.pending_answers:
                self.bus.send(answer, node_id, (answer.target,), tick)
            node.pending_answers.clear()
        return results

    def _remember(self, node, output, decisions, time_s):
        """Feed a CMM v3 memory: what the node saw, concluded and did this tick."""
        readings = evidence.key_readings(node.machine, node.monitoring) if node.monitoring else {}
        events = [{"action": d.get("action") or d.get("communication"),
                   "result": "done" if d.get("allowed") else d.get("reason", "refused")} for d in decisions if d]
        start = float((self.engine.config.get("weather") or {}).get("start_hour", 0.0))
        source = next((e.split("=", 1)[1] for e in output.evidence if e.startswith("cause=")), None)
        peers = [{"node": sender, "diagnosis": m.diagnosis, "codes": tuple(m.key_readings.get("codes", ()))}
                 for sender, m in node.peers.items() if m.assessment != "NORMAL"]
        node.memory.observe_tick(time_s, (start + time_s / 3600.0) % 24.0,
                                 {k: v for k, v in readings.items() if k not in ("codes", "online")},
                                 output.assessment, output.diagnosis, output.incident_kind,
                                 codes=readings.get("codes", ()), events=events, source=source, peers=peers)

    def run(self, ticks: int) -> list[dict]:
        return [self.step() for _ in range(ticks)]
