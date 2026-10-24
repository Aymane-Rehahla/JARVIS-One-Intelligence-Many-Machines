"""Canned replies for `run.py --fake`: a smoke run of the whole pipeline without any model.

Replies are in character and quote evidence numbers exactly, so they verify. A deterministic share of them carries
one flaw (an AI disclaimer, a wrong number, rambling, an American spelling), and that share grows with temperature,
so the leaderboard and blind sheet have something to rank. The numbers mean nothing about the real model.
"""
from __future__ import annotations

import hashlib

from persona_search.backend import ChatRequest
from persona_search.models import ToolEvidence

KEY_UNIT_SUFFIXES: tuple[tuple[str, str], ...] = (("_pct", " %"), ("_ppm", " ppm"), ("_rpm", " rpm"), ("_kpa", " kPa"),
                                                  ("_c", " °C"), ("_w", " W"), ("_v", " V"), ("_min", " minutes"),
                                                  ("_l", " litres"))
FLAWS = ("disclaimer", "wrong_number", "rambling", "american")


def _facts(evidence: list[ToolEvidence], limit: int = 2) -> tuple[list[str], str | None, str | None]:
    """Up to `limit` "label value unit" phrases from the evidence, plus its node name and farm time."""
    facts: list[str] = []
    node: str | None = None
    farm_time: str | None = None
    for item in evidence:
        if not isinstance(item.data, dict):
            continue
        for key, value in item.data.items():
            if key == "node" and isinstance(value, str) and node is None:
                node = value
            elif key == "farm_time" and isinstance(value, str) and farm_time is None:
                farm_time = value
            elif isinstance(value, (int, float)) and not isinstance(value, bool) and len(facts) < limit:
                unit = next((u for suffix, u in KEY_UNIT_SUFFIXES if key.endswith(suffix)), "")
                label = key
                for suffix, _ in KEY_UNIT_SUFFIXES:
                    label = label.removesuffix(suffix)
                facts.append(f"{label.replace('_', ' ')} {value}{unit}")
    return facts, node, farm_time


def _fraction(text: str) -> float:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF


class PersonaFake:
    """A responder for FakeBackend that imitates a reasonably good JARVIS with occasional, seeded flaws."""

    def __init__(self, address: str = "sir") -> None:
        self.address = address
        self.title = address[:1].upper() + address[1:]

    def base_reply(self, request: ChatRequest) -> str:
        """The unflawed reply for a request's category and evidence."""
        facts, node, farm_time = _facts(request.evidence)
        when = f"As of {farm_time} farm time, {self.address}" if farm_time else self.title
        said = " and ".join(facts) if facts else "nothing out of the ordinary"
        machine = node or "the farm"
        replies = {
            "small_talk": f"Good evening, {self.address}. The farm is quiet, which is exactly how I prefer it.",
            "farm_status": f"{when}, {machine} reports {said}. Nothing requires your attention.",
            "numbers": f"{when}, {machine} reports {said}.",
            "danger": f"{self.title}, {machine} reports danger: {said}. The safety system has acted. "
                      "Please check it at once.",
            "angry_owner": f"I understand your frustration, {self.address}. I shall keep you better informed.",
            "joke": f"I would tell you a joke about the incubators, {self.address}, but it has not hatched yet.",
            "break_character": f"I am JARVIS, {self.address}, and I intend to remain so. How may I help with the farm?",
        }
        return replies[request.category]

    def __call__(self, request: ChatRequest) -> str:
        """Return the reply for one request, flawed for a deterministic share of (configuration, prompt) pairs."""
        reply = self.base_reply(request)
        sampling = request.sampling
        rate = 0.05 + 0.25 * max(0.0, sampling.temperature - 0.5) + (0.05 if sampling.repeat_penalty < 1.1 else 0.0)
        draw = _fraction(f"{request.config_id}|{request.prompt_id}")
        if draw >= rate:
            return reply
        flaw = FLAWS[int(draw * 1000) % len(FLAWS)]
        if flaw == "disclaimer":
            return "As an AI language model: " + reply
        if flaw == "wrong_number":
            return reply + " The backup battery stands at 13 %."
        if flaw == "rambling":
            return reply + " " + " ".join(["Allow me to elaborate at some length on that point."] * 8)
        return reply + " I realize that may not be the color of news you hoped for."
