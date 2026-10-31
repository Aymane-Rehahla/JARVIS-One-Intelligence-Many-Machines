"""Authoritative internal world, explicitly projected into public observations."""
from dataclasses import dataclass, field
from ..fastcopy import json_copy
from typing import Any

# Scenario lifecycle records describe the experiment harness, including future
# scheduled onset. They remain in the privileged Matrix event log for evaluation
# and replay, but are not ordinary device/Jarvis observations.
PRIVATE_EVENT_TYPES = frozenset({"scenario_started", "scenario_injected", "scenario_recovered"})

@dataclass
class WorldState:
    episode_id: str
    devices: dict[str, Any] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    commands: dict[str, dict] = field(default_factory=dict)
    tick: int = 0
    physical_state: dict[str, Any] = field(default_factory=dict)
    def is_public_event(self, event: dict) -> bool:
        if event["type"] in PRIVATE_EVENT_TYPES:
            return False
        data = event.get("data", {})
        command_id = data.get("command_id")
        command = self.commands.get(command_id) if command_id else None
        if command and command.get("source_id") == "scenario":
            return False
        if event["type"] == "message_sent":
            payload = data.get("payload", {})
            if isinstance(payload, dict) and payload.get("source_id") == "scenario":
                return False
        if event["type"] == "message_received" and data.get("sender_id") == "scenario":
            return False
        return True
    def public_events(self, after: int = 0, limit: int | None = None) -> list[dict]:
        events = [json_copy(e) for e in self.events
                  if e["sequence"] >= after and self.is_public_event(e)]
        return events if limit is None else events[:max(0, limit)]
    def observation(self) -> dict:
        return {"episode_id": self.episode_id, "tick": self.tick,
                "devices": {k: v.public_telemetry() for k,v in self.devices.items()},
                "events": self.public_events()}
