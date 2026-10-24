"""Deterministic safety boundary between candidate diagnostics and Matrix commands."""
from __future__ import annotations

from .contracts import CandidateDecisionV1, DECISIONS, DIAGNOSES


class SafetyActionValidator:
    """V1 permits no model-originated command; Matrix remains authoritative."""
    def validate_candidate(self, candidate: CandidateDecisionV1) -> CandidateDecisionV1:
        if candidate.schema_version != 1 or candidate.decision not in DECISIONS or candidate.diagnosis not in DIAGNOSES:
            return CandidateDecisionV1(1, "NONE", "UNKNOWN", 0.0, (1.0, 0.0, 0.0, 0.0), (1.0, 0.0), ())
        if not 0.0 <= candidate.confidence <= 1.0 or candidate.proposed_commands:
            return CandidateDecisionV1(1, "NONE", "UNKNOWN", 0.0, (1.0, 0.0, 0.0, 0.0), (1.0, 0.0), ())
        if candidate.decision != "DIAGNOSE" and candidate.diagnosis != "UNKNOWN":
            return CandidateDecisionV1(1, candidate.decision, "UNKNOWN", candidate.confidence,
                                       candidate.decision_probabilities, candidate.diagnosis_probabilities, ())
        return candidate
