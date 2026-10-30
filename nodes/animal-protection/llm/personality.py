"""JARVIS's tone for this turn (A8): a deterministic mode from the most serious level it knows of in this farm run.

DANGER or ESCALATE_TO_USER anywhere: calm and short, no humour, fewer tokens. WATCHING: plain. Otherwise: dry wit is
allowed. The levels come from incubator-1's own assessment, the peers' answers of this turn and the alerts it has heard
in this farm run (persona spec, "Tone by situation"). Code decides; the language model only receives the instruction.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Mode = Literal["calm", "plain", "dry"]
LEVELS = ("NORMAL", "HANDLED_BY_MACHINE", "WATCHING", "ESCALATE_TO_USER", "DANGER")


@dataclass(frozen=True)
class Style:
    """What the speaking model is told about tone, and how long it may speak."""

    mode: Mode
    instruction: str
    max_tokens: int
    temperature: float


STYLES: dict[Mode, Style] = {
    "calm": Style("calm", "Something on the farm needs the owner. Be calm and brief: what is happening, on which "
                  "machine, and what the owner should do. No humour.", 90, 0.3),
    "plain": Style("plain", "A machine is being watched. Be attentive and measured. No jokes.", 110, 0.6),
    "dry": Style("dry", "All is normal. A touch of dry wit is allowed, but the facts come first.", 120, 0.7),
}


def worst_level(levels: Iterable[str]) -> str:
    """The most serious of the given levels (unknown names are ignored); NORMAL when there are none."""
    ranks = [LEVELS.index(level) for level in levels if level in LEVELS]
    return LEVELS[max(ranks)] if ranks else "NORMAL"


def style_for(levels: Iterable[str]) -> Style:
    """The tone for a turn in which these levels are known."""
    worst = worst_level(levels)
    if worst in ("ESCALATE_TO_USER", "DANGER"):
        return STYLES["calm"]
    if worst == "WATCHING":
        return STYLES["plain"]
    return STYLES["dry"]
