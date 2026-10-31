"""Small helpers for capability-aware simulated measurements."""
from ..fastcopy import json_copy


def capability_value(capabilities, name, value, availability, reason="sensor not installed"):
    if capabilities.get(name, True):
        availability[name] = {"status": "available", "reason": None}
        return json_copy(value)
    availability[name] = {"status": "unavailable", "reason": reason}
    return None


def rate_per_minute(current, previous, dt_seconds):
    if current is None or previous is None or dt_seconds <= 0:
        return None
    return round((float(current) - float(previous)) * 60.0 / dt_seconds, 6)
