"""CTM node brain: the unmodified upstream CTM-RL core with Testbed heads.

One brain type per machine (incubator, HVAC, animal protection). Each running
node owns its own instance and recurrent state; the two incubators load the
same trained weights into two independent instances.

Heads:
- assessment (5 levels), diagnosis (per machine), protective action (per machine),
  communication (owner / fire brigade / civil protection);
- expectation: predicts the node's own next monitoring features. The gap between
  prediction and what then arrives ("surprise") is the learned version of
  checks like "it is raining, so humidity should rise".
Untrained until Phase 4: Phase 2 runs these brains for plumbing only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import sys

import torch
from torch import nn

from .contracts import ASSESSMENTS, COMMUNICATIONS, DANGER_ACTIONS, DIAGNOSES, MACHINE_TYPES, NodeOutput
from .encoders import EncodedNodeInput, NodeEncoderV1


@dataclass(frozen=True)
class NodeBrainConfig:
    machine: str
    iterations: int = 3
    d_model: int = 48
    d_input: int = 32
    n_synch_out: int = 12
    memory_length: int = 4
    synapse_depth: int = 1
    schema_version: int = 1


def _upstream():
    source = Path(__file__).resolve().parents[2] / "base ctm" / "continuous-thought-machines-main"
    if not source.exists():
        raise FileNotFoundError(f"upstream CTM source unavailable: {source}")
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from models.ctm_rl import ContinuousThoughtMachineRL
    return ContinuousThoughtMachineRL


class NodeCTMBrain(nn.Module):
    name = "ctm"
    CHECKPOINT_VERSION = 1

    def __init__(self, config: NodeBrainConfig, encoder: NodeEncoderV1):
        super().__init__()
        if config.machine != encoder.machine:
            raise ValueError("brain and encoder machine types differ")
        self.config, self.encoder = config, encoder
        CTM = _upstream()
        self.ctm = CTM(iterations=config.iterations, d_model=config.d_model, d_input=config.d_input,
                       n_synch_out=config.n_synch_out, synapse_depth=config.synapse_depth,
                       memory_length=config.memory_length, deep_nlms=False, memory_hidden_dims=2,
                       do_layernorm_nlm=False, backbone_type="classic-control-backbone", dropout=0.0,
                       neuron_select_type="first-last")
        width = self.ctm.synch_representation_size_out
        machine = config.machine
        self.assessment_head = nn.Linear(width, len(ASSESSMENTS))
        self.diagnosis_head = nn.Linear(width, len(DIAGNOSES[machine]))
        self.action_head = nn.Linear(width, len(DANGER_ACTIONS[machine]))
        self.communication_head = nn.Linear(width, len(COMMUNICATIONS))
        self.expectation_head = nn.Linear(width, len(encoder.own_names))
        with torch.no_grad():
            self.ctm(torch.zeros((1, encoder.dimension)), self.initial_state())  # materialise lazy layers
        self.state = self.initial_state()

    def initial_state(self):
        return (self.ctm.start_trace.unsqueeze(0).clone(), self.ctm.start_activated_trace.unsqueeze(0).clone())

    def reset(self):
        self.state = self.initial_state()

    def forward(self, encoded: EncodedNodeInput, state=None):
        x = torch.tensor([encoded.vector], dtype=torch.float32)
        representation, next_state = self.ctm(x, state if state is not None else self.initial_state())
        return {"assessment": self.assessment_head(representation), "diagnosis": self.diagnosis_head(representation),
                "action": self.action_head(representation), "communication": self.communication_head(representation),
                "expectation": self.expectation_head(representation)}, next_state

    def surprise(self, expected: torch.Tensor, encoded: EncodedNodeInput) -> float:
        """Mean squared gap between expected and observed own features (valid ones only)."""
        own = [n for n, name in enumerate(encoded.feature_names) if name.startswith("own.")]
        actual = torch.tensor([encoded.values[n] for n in own])
        valid = torch.tensor([encoded.validity[n] for n in own])
        return float(((expected[0] - actual) ** 2 * valid).sum() / valid.sum().clamp(min=1.0))

    def decide(self, ctx, encoded: EncodedNodeInput) -> NodeOutput:
        machine = MACHINE_TYPES[ctx.node_id]
        with torch.no_grad():
            surprise = self.surprise(self._last_expectation, encoded) if getattr(self, "_last_expectation", None) is not None else 0.0
            heads, self.state = self.forward(encoded, self.state)
            self._last_expectation = heads["expectation"]
        probabilities = {key: torch.softmax(heads[key], dim=-1)[0] for key in ("assessment", "diagnosis", "action", "communication")}
        assessment = ASSESSMENTS[int(probabilities["assessment"].argmax())]
        diagnosis = DIAGNOSES[machine][int(probabilities["diagnosis"].argmax())]
        action = DANGER_ACTIONS[machine][int(probabilities["action"].argmax())] if assessment == "DANGER" else "none"
        communication = COMMUNICATIONS[int(probabilities["communication"].argmax())]
        if assessment == "NORMAL":
            diagnosis, communication = "NONE", "none"
        kind = _kind_for(machine, diagnosis)
        return NodeOutput(ctx.node_id, ctx.tick, assessment, diagnosis, float(probabilities["assessment"].max()), kind,
                          "", action, communication, (f"surprise={surprise:.4f}",),
                          {f"assessment.{name}": float(p) for name, p in zip(ASSESSMENTS, probabilities["assessment"])},
                          self.name)

    def save(self, path):
        torch.save({"checkpoint_version": self.CHECKPOINT_VERSION, "config": asdict(self.config),
                    "schema_hash": self.encoder.schema_hash, "state_dict": self.state_dict()}, path)

    @classmethod
    def load(cls, path, encoder: NodeEncoderV1):
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
        if checkpoint.get("checkpoint_version") != cls.CHECKPOINT_VERSION:
            raise ValueError("unsupported node brain checkpoint")
        if checkpoint["schema_hash"] != encoder.schema_hash:
            raise ValueError("checkpoint was trained on a different input schema")
        brain = cls(NodeBrainConfig(**checkpoint["config"]), encoder)
        brain.load_state_dict(checkpoint["state_dict"])
        brain.eval()
        return brain


_KIND = {"FIRE_SUSPECTED": "fire", "GAS_HAZARD": "gas", "AIR_QUALITY_HAZARD": "gas", "FLOOD": "flood",
         "SEVERE_WEATHER": "storm", "POWER_OUTAGE": "power", "SUPPLY_OVERVOLTAGE": "power",
         "EXTERNAL_HAZARD": "environment", "HEAT_STRESS": "environment", "COMMUNICATION_LOSS": "network",
         "WATER_SUPPLY_LOSS": "water", "NONE": None}


def _kind_for(machine, diagnosis):
    return _KIND.get(diagnosis, "equipment")
