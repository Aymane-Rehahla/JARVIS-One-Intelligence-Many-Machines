"""Node contracts v1: what each Jarvis node concludes, proposes, and tells its peers.

A node outputs an *assessment*, not a command. Only DANGER may carry a
protective action, and only for the node's own machine; the safety gate checks
it against an approved list and deterministic evidence before anything runs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 1

ASSESSMENTS = ("NORMAL", "HANDLED_BY_MACHINE", "WATCHING", "ESCALATE_TO_USER", "DANGER")

MACHINE_TYPES = {"incubator-1": "incubator", "incubator-2": "incubator",
                 "hvac": "hvac", "animal-protection": "animal_protection"}
NODE_IDS = tuple(MACHINE_TYPES)

DIAGNOSES = {
    "incubator": ("NONE", "SUPPLY_OVERVOLTAGE", "POWER_OUTAGE", "OVERHEATING", "HEATER_FAULT", "FAN_FAULT",
                  "HUMIDIFICATION_FAULT", "WATER_LEAK", "TURNING_FAULT", "DOOR_PROBLEM", "INTERNAL_POWER_FAULT",
                  "SENSOR_FAULT", "EXTERNAL_HAZARD", "COMMUNICATION_LOSS", "UNKNOWN_ANOMALY",
                  # own hose or fill valve, versus the farm water main (several machines dry at once)
                  "WATER_INLET_FAULT", "WATER_SUPPLY_LOSS"),
    "hvac": ("NONE", "SUPPLY_FAN_FAULT", "RETURN_FAN_FAULT", "COMPRESSOR_FAULT", "REFRIGERANT_LEAK",
             "FILTER_CLOGGED", "CONDENSATE_PROBLEM", "POWER_OUTAGE", "FIRE_SUSPECTED", "AIR_QUALITY_HAZARD",
             "SENSOR_FAULT", "EXTERNAL_HAZARD", "COMMUNICATION_LOSS", "UNKNOWN_ANOMALY"),
    "animal_protection": ("NONE", "FIRE_SUSPECTED", "GAS_HAZARD", "FEED_PROBLEM", "WATER_PROBLEM", "FLOOD",
                          "SEVERE_WEATHER", "POWER_OUTAGE", "HEAT_STRESS", "ACTUATOR_PROBLEM", "SENSOR_FAULT",
                          "COMMUNICATION_LOSS", "UNKNOWN_ANOMALY", "WATER_SUPPLY_LOSS", "EXTERNAL_HAZARD"),
}

# Where a node places the cause of what it sees: itself, another machine (by role), the weather
# outside, or the site (mains power, the farm water main). "incubator" from an incubator means the other one.
SOURCES = ("none", "self", "incubator", "hvac", "animal_protection", "outside", "site")

# Protective actions a node may take on its own machine, and only under DANGER.
DANGER_ACTIONS = {
    "incubator": ("none", "emergency_stop_heater"),
    "hvac": ("none", "fire_shutdown", "emergency_ventilation"),
    "animal_protection": ("none", "deploy_shutters", "deploy_flood_barrier", "close_gates", "activate_alarm"),
}

COMMUNICATIONS = ("none", "notify_owner", "call_fire_brigade", "call_civil_protection")

# Incident kinds shared across nodes so they can agree on one incident.
INCIDENT_KINDS = ("fire", "gas", "flood", "storm", "power", "water", "equipment", "environment", "network")


@dataclass(frozen=True)
class PeerMessage:
    """Broadcast from one node to the others (ZigBee-like peer link)."""
    sender: str
    sample_tick: int
    sent_tick: int
    assessment: str
    diagnosis: str
    confidence: float
    incident_id: str | None = None
    incident_kind: str | None = None
    key_readings: dict[str, Any] = field(default_factory=dict)
    actions_taken: tuple[str, ...] = ()
    calls_made: tuple[str, ...] = ()
    heartbeat: bool = False
    schema_version: int = SCHEMA_VERSION

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Question:
    """Structured question from one node to another (natural language comes with the LLM layer)."""
    question_id: str
    sender: str
    target: str
    topic: str
    asked_tick: int

    TOPICS = ("status", "readings", "incident_history", "incubation", "hazard_check")


@dataclass(frozen=True)
class Answer:
    question_id: str
    sender: str
    target: str
    topic: str
    answered_tick: int
    content: dict[str, Any]


@dataclass(frozen=True)
class NodeOutput:
    """One node's conclusion for one tick."""
    node_id: str
    tick: int
    assessment: str
    diagnosis: str
    confidence: float
    incident_kind: str | None
    recommendation: str
    action: str = "none"
    communication: str = "none"
    evidence: tuple[str, ...] = ()
    probabilities: dict[str, float] = field(default_factory=dict)
    brain: str = "rule"
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        machine = MACHINE_TYPES[self.node_id]
        if self.assessment not in ASSESSMENTS:
            raise ValueError(f"unknown assessment {self.assessment}")
        if self.diagnosis not in DIAGNOSES[machine]:
            raise ValueError(f"unknown {machine} diagnosis {self.diagnosis}")
        if self.action not in DANGER_ACTIONS[machine]:
            raise ValueError(f"{self.action} is not an approved {machine} action")
        if self.action != "none" and self.assessment != "DANGER":
            raise ValueError("protective actions are only allowed under DANGER")
        if self.communication not in COMMUNICATIONS:
            raise ValueError(f"unknown communication {self.communication}")
        if self.incident_kind is not None and self.incident_kind not in INCIDENT_KINDS:
            raise ValueError(f"unknown incident kind {self.incident_kind}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be within [0, 1]")

    def as_dict(self):
        return asdict(self)
