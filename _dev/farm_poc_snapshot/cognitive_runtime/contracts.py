"""Stable contracts shared by the initial encoder, memory manager, and agent."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

DECISIONS = ("NONE", "INVESTIGATE", "DIAGNOSE", "VERIFY")
DIAGNOSES = ("UNKNOWN", "WIDER_SUPPLY_ANOMALY")


@dataclass(frozen=True)
class CandidateDecisionV1:
    schema_version: int
    decision: str
    diagnosis: str
    confidence: float
    decision_probabilities: tuple[float, ...]
    diagnosis_probabilities: tuple[float, ...]
    proposed_commands: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeliberationTelemetryV1:
    schema_version: int
    decision_index: int
    fixed_ctm_iterations: int
    ctm_iterations_executed: int
    inference_time_ns: int
    synchronisation_norm: float
    state_trace_norm: float
    activated_state_trace_norm: float
    cmm_retrieval_count: int
    cmm_retrieved_entry_ids: tuple[str, ...]
    cmm_write_operations: tuple[str, ...]
    tracking_available: bool
    # This records fixed compute only. It is not a reasoning-depth claim.
    adaptive_deliberation: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
