"""JARVIS's tone for this turn (A8): a deterministic mode from the most serious level it knows of in this farm run.

DANGER or ESCALATE_TO_USER anywhere: serious and short, no humour, fewer tokens. WATCHING: plain. Small talk: a
friend's tone. Otherwise: relaxed, a light joke allowed. The levels come from incubator-1's own assessment, the peers' answers of this turn and the alerts it has heard
in this farm run (persona spec, "Tone by situation"). Code decides; the language model only receives the instruction.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Mode = Literal["calm", "plain", "dry", "chat"]
LEVELS = ("NORMAL", "HANDLED_BY_MACHINE", "WATCHING", "ESCALATE_TO_USER", "DANGER")


@dataclass(frozen=True)
class Style:
    """What the speaking model is told about tone, and how long it may speak."""

    mode: Mode
    instruction: str
    max_tokens: int
    temperature: float


STYLES: dict[Mode, Style] = {
    # final review (31 Oct): "what is happening, on which machine" pushed the model to name a problem for a machine
    # the question was about even when its data was normal; the facts asked about come first, from the data only
    "calm": Style("calm", "Something on the farm needs the owner. Drop the jokes and get serious: calm, clear and "
                  "brief. Answer the question from the data only; name a problem only where the data shows one.",
                  90, 0.3),
    "plain": Style("plain", "A machine is being watched. Stay friendly but focused, and keep it short.", 110, 0.6),
    "dry": Style("dry", "All is normal. Talk like a relaxed colleague; a light joke is fine, but the facts come "
                 "first.", 120, 0.7),
    # the owner (31 Oct): "chat with me normally, like I'm chatting to a friend"
    "chat": Style("chat", "The owner is just chatting, not asking about the farm. Reply the way a friend would: "
                  "warm, natural and short, and ask something back when it fits. Don't recite readings or bring "
                  "up the machines.", 120, 0.8),
}


def worst_level(levels: Iterable[str]) -> str:
    """The most serious of the given levels (unknown names are ignored); NORMAL when there are none."""
    ranks = [LEVELS.index(level) for level in levels if level in LEVELS]
    return LEVELS[max(ranks)] if ranks else "NORMAL"


def style_for(levels: Iterable[str], *, chatting: bool = False) -> Style:
    """The tone for a turn in which these levels are known; small talk gets the friend's tone unless the farm needs
    the owner."""
    worst = worst_level(levels)
    if worst in ("ESCALATE_TO_USER", "DANGER"):
        return STYLES["calm"]
    if chatting:
        return STYLES["chat"]
    if worst == "WATCHING":
        return STYLES["plain"]
    return STYLES["dry"]
