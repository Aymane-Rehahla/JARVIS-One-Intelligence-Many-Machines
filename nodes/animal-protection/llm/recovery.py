"""Fallback replies, built from tool data only, never invented (A9; P2 section 3, "Recovery").

When the language model, a peer or a deadline fails, JARVIS still answers from what the tools returned: each machine's
level, diagnosis and a few key readings, with the farm time they are from. A peer that does not answer is described
from its last good answer of this farm run, with that answer's own farm time ("HVAC isn't answering; its last report,
at farm time 06:12, said ..."). `PeerMemory` keeps those last answers, bounded. Every number written here is taken
from tool data with the decimals the data allows, so verify.py matches it.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from contracts import MACHINES, NodeId

NAMES: dict[NodeId, str] = {"incubator-1": "Incubator one", "incubator-2": "Incubator two", "hvac": "The HVAC",
                            "animal-protection": "Animal protection"}
def in_sentence(node: NodeId) -> str:
    """A machine's name inside a sentence: "the HVAC", "incubator one"."""
    return "the HVAC" if node == "hvac" else NAMES[node].lower()


LEVEL_WORDS = {"NORMAL": "normal", "HANDLED_BY_MACHINE": "handling a problem by itself",
               "WATCHING": "watching something", "ESCALATE_TO_USER": "needs you", "DANGER": "in danger"}
#: The readings a fallback reply gives per machine: (key, words, unit, decimals).
KEY_READINGS: dict[str, tuple[tuple[str, str, str, int], ...]] = {
    "incubator": (("temperature_mean_c", "temperature", " °C", 1), ("humidity_mean_pct", "humidity", " %", 1),
                  ("ac_voltage_v", "supply", " V", 0)),
    "hvac": (("supply_air_temperature_c", "supply air", " °C", 1), ("airflow_m3_h", "airflow", " m³/h", 0)),
    "animal_protection": (("co2_ppm", "CO₂", " ppm", 0), ("ammonia_ppm", "ammonia", " ppm", 1)),
}
MAX_REMEMBERED = 64


def words(code: str) -> str:
    """"SUPPLY_FAN_FAULT" → "supply fan fault"."""
    return code.replace("_", " ").lower()


@dataclass
class MachineData:
    """What this turn learned about one machine: its conclusion, its readings, and when they are from."""

    node: NodeId
    assessment: dict[str, Any] | None = None
    readings: dict[str, Any] | None = None
    farm_time: str | None = None
    tick: int | None = None
    label: str | None = None
    error: str | None = None                       # why this machine's data is missing (it did not answer...)
    remembered: bool = False                       # the data is the last good answer of this run, not this turn's
    documents: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    incident: dict[str, Any] | None = None


def _number(value: Any, decimals: int) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return f"{float(value):,.{decimals}f}"


def why_silent(error: str | None) -> str:
    """Why a machine did not answer, in fixed words with no number (an internal error text is never said)."""
    text = (error or "").lower()
    if "cut" in text:
        return "its radio link is cut"
    if "within" in text or "timeout" in text or "timed out" in text:
        return "it did not answer in time"
    return "it did not answer"


def describe(data: MachineData) -> str:
    """One sentence about one machine, from its data only."""
    name = NAMES[data.node]
    if data.assessment is None and data.readings is None:
        return f"{name} isn't answering ({why_silent(data.error)}), and I have no earlier report from it in this run."
    parts: list[str] = []
    if data.assessment is not None:
        level = str(data.assessment.get("assessment", ""))
        diagnosis = str(data.assessment.get("diagnosis", "NONE"))
        same = diagnosis in ("NONE", "", level)                   # NORMAL/NORMAL says it once
        parts.append(LEVEL_WORDS.get(level, words(level)) + ("" if same else f", {words(diagnosis)}"))
    readings = data.readings or {}
    shown = [f"{label} {text}{unit}" for key, label, unit, decimals in KEY_READINGS[MACHINES[data.node]]
             if (text := _number(readings.get(key), decimals)) is not None]
    if shown:
        parts.append(", ".join(shown))
    when = f"at farm time {data.farm_time}" if data.farm_time else "with no farm time"
    if data.remembered:
        return f"{name} isn't answering ({why_silent(data.error)}); its last report, {when}, said: {'; '.join(parts)}."
    return f"{name} {when}: {'; '.join(parts)}."


def fallback_text(machines: list[MachineData], reason: str) -> str:
    """A whole fallback reply: why it is a fallback, then one sentence per machine."""
    opening = {"model": "My language model isn't answering, so here is the data itself.",
               "deadline": "I couldn't finish that reply in time; here is the data itself.",
               "verify": "I'll stick to the data itself."}.get(reason, "Here is the data itself.")
    return " ".join([opening, *(describe(machine) for machine in machines)])


class PeerMemory:
    """The last good peer answer per (farm run, node, topic), bounded: what a fallback may cite when a peer is silent."""

    def __init__(self, limit: int = MAX_REMEMBERED) -> None:
        self.limit = limit
        self._answers: OrderedDict[tuple[str, NodeId, str], tuple[dict[str, Any], str | None, int | None]] = OrderedDict()

    def remember(self, feed_id: str, node: NodeId, topic: str, answer: dict[str, Any], farm_time: str | None,
                 tick: int | None) -> None:
        key = (feed_id, node, topic)
        self._answers.pop(key, None)
        self._answers[key] = (answer, farm_time, tick)
        while len(self._answers) > self.limit:
            self._answers.popitem(last=False)

    def last(self, feed_id: str, node: NodeId, topic: str) -> tuple[dict[str, Any], str | None, int | None] | None:
        """The last good answer of this farm run, or None (another run's answers never count: rule 7)."""
        return self._answers.get((feed_id, node, topic))
