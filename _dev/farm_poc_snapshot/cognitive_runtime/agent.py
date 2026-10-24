"""Thin wrapper over the unmodified upstream CTM-RL implementation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter_ns
import sys
from typing import Any

import torch
from torch import nn

from .contracts import CandidateDecisionV1, DECISIONS, DIAGNOSES, DeliberationTelemetryV1
from .encoder import EncodedInputV1, OperationalEncoderV1
from .memory import CognitiveMemoryManager
from .safety import SafetyActionValidator


@dataclass(frozen=True)
class CTMRuntimeConfig:
    schema_version: int = 1
    iterations: int = 3
    d_model: int = 32
    d_input: int = 24
    n_synch_out: int = 8
    memory_length: int = 4
    synapse_depth: int = 1
    device: str = "cpu"


class CTMRuntimeAgent(nn.Module):
    """Randomly initialized integration runtime, not a trained Jarvis."""
    CHECKPOINT_VERSION = 1

    def __init__(self, config: CTMRuntimeConfig | None = None, *, cmm: CognitiveMemoryManager | None = None):
        super().__init__()
        self.config = config or CTMRuntimeConfig()
        self.encoder = OperationalEncoderV1()
        self.cmm = cmm or CognitiveMemoryManager()
        self.safety = SafetyActionValidator()
        source = Path(__file__).resolve().parents[2] / "base ctm" / "continuous-thought-machines-main"
        if not source.exists(): raise FileNotFoundError(f"upstream CTM source unavailable: {source}")
        if str(source) not in sys.path: sys.path.insert(0, str(source))
        from models.ctm_rl import ContinuousThoughtMachineRL
        self.ctm = ContinuousThoughtMachineRL(
            iterations=self.config.iterations, d_model=self.config.d_model, d_input=self.config.d_input,
            n_synch_out=self.config.n_synch_out, synapse_depth=self.config.synapse_depth,
            memory_length=self.config.memory_length, deep_nlms=False, memory_hidden_dims=2,
            do_layernorm_nlm=False, backbone_type="classic-control-backbone", dropout=0.0,
            neuron_select_type="first-last").to(self.config.device)
        self.input_dimension = self.encoder.dimension + self.encoder.CONTEXT_DIMENSION
        representation = self.ctm.synch_representation_size_out
        self.decision_head = nn.Linear(representation, len(DECISIONS)).to(self.config.device)
        self.diagnosis_head = nn.Linear(representation, len(DIAGNOSES)).to(self.config.device)
        self._state: tuple[torch.Tensor, torch.Tensor] | None = None
        self._decision_index = 0
        self._episode_active = False

    def _initial_state(self) -> tuple[torch.Tensor, torch.Tensor]:
        return (self.ctm.start_trace.unsqueeze(0).clone(), self.ctm.start_activated_trace.unsqueeze(0).clone())

    def begin_episode(self, episode_id: str | None = None) -> None:
        self.cmm.begin_episode(episode_id); self._state = self._initial_state(); self._decision_index = 0; self._episode_active = True

    def encode(self, observation: dict[str, Any]) -> EncodedInputV1:
        return self.encoder.encode(observation)

    def _model_input(self, encoded: EncodedInputV1, context_override: tuple[float, ...] | None = None) -> torch.Tensor:
        context = self.cmm.context_vector() if context_override is None else context_override
        if len(context) != self.encoder.CONTEXT_DIMENSION: raise RuntimeError("CMM context dimension changed")
        return torch.tensor([*encoded.values, *context], dtype=torch.float32, device=self.config.device).unsqueeze(0)

    def differentiable_representation(self, encoded: EncodedInputV1,
                                     state: tuple[torch.Tensor, torch.Tensor] | None = None,
                                     context_override: tuple[float, ...] | None = None):
        """Expose the upstream CTM-RL representation for task-specific heads."""
        if state is None:
            state = self._initial_state()
        return self.ctm(self._model_input(encoded, context_override), state)

    def differentiable_forward(self, encoded: EncodedInputV1, state: tuple[torch.Tensor, torch.Tensor] | None = None):
        """Run one CTM-RL transition with gradients enabled.

        The behavior-cloning trainer owns the returned recurrent state and uses
        this method instead of :meth:`process`, which deliberately performs
        inference under ``torch.no_grad``.
        """
        representation, next_state = self.differentiable_representation(encoded, state)
        return self.decision_head(representation), self.diagnosis_head(representation), next_state

    def record_training_observation(self, encoded: EncodedInputV1, *, simulation_tick: int,
                                    decision: str, diagnosis: str) -> None:
        """Advance governed CMM state without using an expert target or private data."""
        self.cmm.record_observation(
            {"encoded_feature_count": len(encoded.values), "decision_index": self._decision_index},
            simulation_tick=simulation_tick, tags=("current",))
        if decision in {"INVESTIGATE", "DIAGNOSE", "VERIFY"}:
            if decision == "VERIFY":
                self.cmm.request_verification()
            self.cmm.record_hypothesis({"decision": decision, "diagnosis": diagnosis},
                                       simulation_tick=simulation_tick,
                                       tags=("current", decision.lower()))
        self._decision_index += 1

    def process(self, observation: dict[str, Any], *, track: bool = False) -> tuple[CandidateDecisionV1, DeliberationTelemetryV1]:
        if not self._episode_active: self.begin_episode()
        encoded = self.encode(observation)
        tick = int(observation["incubator_1_direct_observation"]["tick"])
        retrieval = self.cmm.retrieve(query_tags=("current",), categories=("working", "procedural", "mandatory_rule"), limit=8)
        started = perf_counter_ns()
        with torch.no_grad():
            if track:
                representation, self._state, pre, post = self.ctm(self._model_input(encoded), self._state, track=True)
            else:
                representation, self._state = self.ctm(self._model_input(encoded), self._state)
                pre = post = None
            decision_probs = torch.softmax(self.decision_head(representation), dim=-1)[0]
            diagnosis_probs = torch.softmax(self.diagnosis_head(representation), dim=-1)[0]
        elapsed = perf_counter_ns() - started
        decision = DECISIONS[int(decision_probs.argmax())]
        diagnosis = DIAGNOSES[int(diagnosis_probs.argmax())] if decision == "DIAGNOSE" else "UNKNOWN"
        raw = CandidateDecisionV1(1, decision, diagnosis, float(decision_probs.max()),
                                  tuple(float(x) for x in decision_probs), tuple(float(x) for x in diagnosis_probs), ())
        candidate = self.safety.validate_candidate(raw)
        writes = []
        if self.cmm.enable_writes:
            writes.append(self.cmm.record_observation({"encoded_feature_count": len(encoded.values), "decision_index": self._decision_index}, simulation_tick=tick, tags=("current",)).entry_id)
            if candidate.decision in {"INVESTIGATE", "DIAGNOSE", "VERIFY"}:
                if candidate.decision == "VERIFY": self.cmm.request_verification()
                writes.append(self.cmm.record_hypothesis({"decision": candidate.decision, "diagnosis": candidate.diagnosis}, simulation_tick=tick, tags=("current", candidate.decision.lower())).entry_id)
        else:
            # Retain CMM retrieval/context behavior but block all episode writes.
            self.cmm.disabled_write_attempts += 1 + int(candidate.decision in {"INVESTIGATE", "DIAGNOSE", "VERIFY"})
        telemetry = DeliberationTelemetryV1(1, self._decision_index, self.config.iterations,
                                            self.config.iterations, elapsed, float(representation.norm()),
                                            float(self._state[0].norm()), float(self._state[1].norm()),
                                            len(retrieval.entries), tuple(entry.entry_id for entry in retrieval.entries),
                                            tuple(writes), track)
        self._decision_index += 1
        return candidate, telemetry

    def target_loss(self, encoded: EncodedInputV1, target: dict[str, Any]) -> torch.Tensor:
        """Supervised loss for recorder target schema; no private data is accepted."""
        if target.get("schema_version") != 1 or target.get("decision") not in DECISIONS or target.get("diagnosis") not in DIAGNOSES:
            raise ValueError("invalid ExpertTargetV1")
        state = self._initial_state(); representation, _ = self.ctm(self._model_input(encoded), state)
        decision_loss = nn.functional.cross_entropy(self.decision_head(representation), torch.tensor([DECISIONS.index(target["decision"])], device=self.config.device))
        if target["decision"] == "DIAGNOSE":
            diagnosis_loss = nn.functional.cross_entropy(self.diagnosis_head(representation), torch.tensor([DIAGNOSES.index(target["diagnosis"])], device=self.config.device))
            return decision_loss + diagnosis_loss
        return decision_loss

    def save_checkpoint(self, path: str | Path) -> None:
        torch.save({"checkpoint_version": self.CHECKPOINT_VERSION, "config": asdict(self.config), "state_dict": self.state_dict()}, path)

    @classmethod
    def load_checkpoint(cls, path: str | Path) -> "CTMRuntimeAgent":
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
        if checkpoint.get("checkpoint_version") != cls.CHECKPOINT_VERSION: raise ValueError("unsupported cognitive runtime checkpoint")
        agent = cls(CTMRuntimeConfig(**checkpoint["config"])); agent._materialize(); agent.load_state_dict(checkpoint["state_dict"]); agent.eval(); return agent

    def _materialize(self) -> None:
        with torch.no_grad(): self.ctm(torch.zeros((1, self.input_dimension), device=self.config.device), self._initial_state())

    def diagnostics(self) -> dict[str, Any]:
        return {"schema_version": 1, "input_dimension": self.input_dimension,
                "encoder_dimension": self.encoder.dimension, "context_dimension": self.encoder.CONTEXT_DIMENSION,
                "episode_active": self._episode_active, "decision_index": self._decision_index,
                "cmm": self.cmm.diagnostics(), "fixed_ctm_iterations": self.config.iterations}
