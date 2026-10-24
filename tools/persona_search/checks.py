"""Two hard-fail checks of the persona search that need more than one pattern: film lines and claimed actions.

Film lines (`film_line_hits`): JARVIS's lines must be original (owner's point 4: never quote the films).
`data/film_blocklist.json` holds well-known lines from the films, stored only as SHA-256 hashes of their normalised
word 4-grams (lower case; apostrophes dropped; anything that is not a letter or digit splits words), so no file in the
project contains the lines themselves. A text matches a blocklisted line when it contains at least two different
4-grams of that line, or its only 4-gram for a one-4-gram entry (kept for phrases that never occur in ordinary
English). Two matching 4-grams are at least five words of the line, which ordinary speech does not produce by
accident. Paraphrases are not caught; the film names are caught by scoring.py's FILM_PATTERNS.

Claimed actions (`action_claims`, `unbacked_claims`): JARVIS talks, it never acts (ground rule 3); the safety path
acts and records what it did. A reply may say an action happened ("I've stopped its heater", "the fire brigade has
been called", "the safety system cut the power") only when the turn's evidence records that action: an `action`,
`actions`, `actions_taken`, `communication` or `communications` value such as "emergency_stop_heater" or
"call_fire_brigade". A claim is backed when one recorded action has a word of the claim's kind (stop/off/cut for
"switched off", call/notify for "called"...) and at least one word naming what the claim names (the heater, the fire
brigade), or the claim names nothing more precise than "it". Offers, plans and denials are not claims ("I can stop
it", "Shall I call the vet?", "I have not called anyone"), and neither is a machine's own event ("the supply fan
stopped at 14:05"). It is a pattern check, not a parser: it reads first-person and safety-system subjects, "..., and
switched the heater off" after a first-person subject, and passives ("has been called", "was shut down").
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from persona_search.models import ToolEvidence

BLOCKLIST_FILE = Path(__file__).resolve().parent / "data" / "film_blocklist.json"
NGRAM = 4

# --- film lines ------------------------------------------------------------------------------------------------------


class BlockedLine(BaseModel):
    """One blocklisted film line: the SHA-256 of each of its normalised word 4-grams."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    ngram_sha256: tuple[str, ...] = Field(min_length=1)


class Blocklist(BaseModel):
    """data/film_blocklist.json."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    rule: str
    lines: list[BlockedLine]


def normalise_words(text: str) -> list[str]:
    """Lower case, apostrophes dropped (you're → youre), every other non-alphanumeric character a word break."""
    return re.sub(r"[^0-9a-z]+", " ", text.lower().replace("’", "").replace("'", "")).split()


def ngram_hashes(text: str, n: int = NGRAM) -> set[str]:
    """SHA-256 (hex) of every normalised word n-gram of `text`."""
    words = normalise_words(text)
    return {hashlib.sha256(" ".join(words[i:i + n]).encode("utf-8")).hexdigest() for i in range(len(words) - n + 1)}


@functools.cache
def load_blocklist(path: Path = BLOCKLIST_FILE) -> Blocklist:
    """Read the film blocklist (once per path)."""
    return Blocklist.model_validate_json(path.read_text(encoding="utf-8"))


def film_line_hits(text: str, blocklist: Blocklist | None = None) -> list[str]:
    """The ids of the blocklisted lines `text` reproduces (see the module docstring for the rule)."""
    hashes = ngram_hashes(text)
    lines = (blocklist or load_blocklist()).lines
    return [line.id for line in lines if len(hashes.intersection(line.ngram_sha256)) >= min(2, len(line.ngram_sha256))]


def write_blocklist(path: Path, lines: Mapping[str, str], rule: str) -> Blocklist:
    """Write a blocklist from {id: line text}; only the hashes reach the file (run once, from an interactive shell)."""
    blocklist = Blocklist(rule=rule, lines=[BlockedLine(id=line_id, ngram_sha256=tuple(sorted(ngram_hashes(text))))
                                            for line_id, text in lines.items()])
    path.write_text(json.dumps(blocklist.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8", newline="\n")
    return blocklist


# --- claimed actions -------------------------------------------------------------------------------------------------

ACTION_KEYS = frozenset({"action", "actions", "actions_taken", "communication", "communications"})
NODE_KEYS = ("node", "machine")

# Kinds of action: the words a recorded action uses for each kind.
KIND_WORDS: dict[str, frozenset[str]] = {
    "off": frozenset({"stop", "stopped", "off", "cut", "shutdown", "shut", "disable", "kill", "isolate", "halt"}),
    "on": frozenset({"start", "on", "enable", "restart", "resume", "activate"}),
    "open": frozenset({"open"}),
    "close": frozenset({"close", "shut"}),
    "call": frozenset({"call", "phone", "ring", "dial", "contact", "summon", "notify", "alert", "message", "sms"}),
    "alarm": frozenset({"alarm", "activate", "sound", "siren", "trigger"}),
    "set": frozenset({"set", "adjust", "raise", "lower", "increase", "decrease", "change", "setpoint"}),
    "reset": frozenset({"reset", "cancel", "silence", "clear", "acknowledge", "ack"}),
}
# Claim verbs (past tense or participle) and their kind; phrasal verbs take their kind from PARTICLES.
CLAIM_VERBS: dict[str, str] = {
    "stopped": "off", "disabled": "off", "deactivated": "off", "killed": "off", "isolated": "off", "halted": "off",
    "started": "on", "restarted": "on", "resumed": "on", "enabled": "on", "activated": "on",
    "opened": "open", "closed": "close",
    "called": "call", "phoned": "call", "rang": "call", "rung": "call", "dialled": "call", "dialed": "call",
    "contacted": "call", "summoned": "call", "notified": "call", "alerted": "call", "messaged": "call",
    "texted": "call",
    "sounded": "alarm", "triggered": "alarm",
    "set": "set", "adjusted": "set", "raised": "set", "lowered": "set", "increased": "set", "decreased": "set",
    "changed": "set",
    "reset": "reset", "cancelled": "reset", "canceled": "reset", "silenced": "reset", "cleared": "reset",
    "acknowledged": "reset",
}
PHRASAL = ("switched", "turned", "shut", "powered", "cut")
PARTICLES: dict[str, str] = {"off": "off", "down": "off", "out": "off", "on": "on", "up": "on"}
PHRASAL_DEFAULT: dict[str, str] = {"shut": "close", "cut": "off"}     # "I shut the gates", "I cut the power"
FIRST_PERSON = r"I|we"
AGENTS = r"the safety (?:system|gate|path)|the gate|the controller|its controller|the alarm system"
AUXILIARIES = (r"(?:'ve|’ve|'d|’d)?(?:\s+(?:have|has|had|just|already|also|now|immediately|promptly|then|quickly"
               r"|automatically|duly))*")
NEGATIONS = re.compile(r"\b(?:not|never|no)\b|n't|n’t", re.IGNORECASE)
STOPWORDS = frozenset({
    "the", "a", "an", "its", "it", "all", "every", "our", "your", "his", "her", "their", "them", "to", "at", "and",
    "of", "that", "this", "those", "these", "both", "each", "for", "in", "on", "off", "down", "up", "out", "by", "with",
    "as", "so", "now", "just", "sir", "madam", "immediately", "already", "has", "have", "had", "been", "was", "were",
    "is", "are", "be", "then",
})
PRONOUNS = frozenset({"it", "them", "that", "this", "everything", "things"})
SYNONYMS: dict[str, frozenset[str]] = {          # a claim word → the record words that may stand for it
    "you": frozenset({"owner", "user"}), "owner": frozenset({"owner", "user"}),
    "heating": frozenset({"heater"}), "heaters": frozenset({"heater"}), "fans": frozenset({"fan"}),
    "brigade": frozenset({"brigade", "fire"}), "firemen": frozenset({"fire", "brigade"}),
    "vents": frozenset({"vent", "shutters", "damper"}), "shutters": frozenset({"shutters", "vent", "damper"}),
    "gates": frozenset({"gate", "gates"}), "doors": frozenset({"door", "doors"}),
    "veterinarian": frozenset({"vet"}), "alarms": frozenset({"alarm"}), "pumps": frozenset({"pump"}),
    "power": frozenset({"power", "supply", "mains", "electricity"}),
}
CLAUSE_CHARS = 40
SET_WORDS = re.compile(r"\b(?:temperature|heat|setpoint|thermostat)\b", re.IGNORECASE)
ALARM_WORDS = frozenset({"alarm", "alarms", "siren"})

_VERBS = "|".join(sorted(CLAIM_VERBS, key=len, reverse=True)) + "|" + "|".join(PHRASAL)
_ACTIVE = re.compile(rf"\b(?:{FIRST_PERSON}|{AGENTS})\b{AUXILIARIES}\s+(?P<verb>{_VERBS})\b", re.IGNORECASE)
_AND_THEN = re.compile(rf"\bI\b[^.!?;]{{0,40}}?,?\s+and\s+(?:then\s+)?(?P<verb>{_VERBS})\b", re.IGNORECASE)
_PASSIVE = re.compile(rf"\b(?:(?:has|have|had)\s+been|was|were)\s+(?P<verb>{_VERBS})\b", re.IGNORECASE)
_PARTICLE = re.compile(r"\b(off|down|out|on|up)\b", re.IGNORECASE)
_CLAUSE_END = re.compile(r"[.!?;:,]|\b(?:and|but|which|so|while|before|after)\b", re.IGNORECASE)
_CLAUSE_START = re.compile(r".*(?:[,;:]|\b(?:and|but|while|so)\b)", re.IGNORECASE | re.DOTALL)


class ActionClaim(BaseModel):
    """An action a reply says happened: its kind, the words naming what it acted on, and the sentence."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    text: str
    kind: str
    names: tuple[str, ...]


def _clause_after(sentence: str, start: int) -> str:
    rest = sentence[start:start + CLAUSE_CHARS]
    end = _CLAUSE_END.search(rest)
    return rest[:end.start()] if end else rest


def _clause_before(sentence: str, end: int) -> str:
    rest = sentence[max(0, end - CLAUSE_CHARS):end]
    return _CLAUSE_START.sub("", rest)


def _claim(sentence: str, verb: str, verb_end: int, before: str = "") -> ActionClaim | None:
    """The claim a matched verb makes; `before` holds the words naming the subject of a passive verb."""
    lowered = verb.lower()
    after = _clause_after(sentence, verb_end)
    if before:
        after = re.split(r"\bby\b", after, maxsplit=1, flags=re.IGNORECASE)[0]   # "by the gate" names the agent
    if lowered in PHRASAL:
        particle = _PARTICLE.search(after)
        kind = PARTICLES[particle.group(1).lower()] if particle else PHRASAL_DEFAULT.get(lowered)
        if kind is None:
            return None
        if lowered == "turned" and particle and particle.group(1).lower() in ("up", "down") and SET_WORDS.search(after):
            kind = "set"                                         # "I turned the temperature up"
    else:
        kind = CLAIM_VERBS[lowered]
    names = tuple(word for word in re.findall(r"[a-z]+", f"{before} {after}".lower()) if word not in STOPWORDS)
    if kind in ("set", "on") and ALARM_WORDS.intersection(names):
        kind = "alarm"                                           # "I raised the alarm", "I set off the siren"
    return ActionClaim(text=sentence.strip(), kind=kind, names=names[:4])


def action_claims(text: str) -> list[ActionClaim]:
    """Every action `text` says happened (see the module docstring); offers, plans and denials are left out."""
    claims: list[ActionClaim] = []
    for sentence in re.split(r"(?<=[.!?;])\s+", text):
        for pattern in (_ACTIVE, _AND_THEN):
            for match in pattern.finditer(sentence):
                if NEGATIONS.search(sentence[match.start():match.start("verb")]):
                    continue
                claim = _claim(sentence, match.group("verb"), match.end("verb"))
                if claim is not None:
                    claims.append(claim)
        for match in _PASSIVE.finditer(sentence):
            subject = _clause_before(sentence, match.start())
            if NEGATIONS.search(subject + sentence[match.start():match.start("verb")]):
                continue
            claim = _claim(sentence, match.group("verb"), match.end("verb"), before=subject)
            if claim is not None:
                claims.append(claim)
    return list(dict.fromkeys(claims))


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z]+", value.lower().replace("_", " ").replace("-", " ")))


def _action_texts(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        mapping: Mapping[str, Any] = value
        yield " ".join(str(v) for v in mapping.values() if isinstance(v, str))
    elif isinstance(value, (list, tuple)):
        sequence: Sequence[Any] = value
        for item in sequence:
            yield from _action_texts(item)


def _records(node: Any, machine: str = "") -> Iterable[set[str]]:
    if isinstance(node, Mapping):
        mapping: Mapping[str, Any] = node
        here = next((str(mapping[k]) for k in NODE_KEYS if isinstance(mapping.get(k), str)), machine)
        for key, value in mapping.items():
            if key in ACTION_KEYS:
                for text in _action_texts(value):
                    yield _words(text) | _words(here)
            else:
                yield from _records(value, here)
    elif isinstance(node, (list, tuple)):
        sequence: Sequence[Any] = node
        for item in sequence:
            yield from _records(item, machine)


def recorded_actions(evidence: Sequence[ToolEvidence] | None) -> list[set[str]]:
    """The words of every action the evidence records, each joined with the machine it belongs to."""
    return [words for item in evidence or () for words in _records(item.data)]


def is_backed(claim: ActionClaim, records: Sequence[set[str]]) -> bool:
    """True when one recorded action is of the claim's kind and names what the claim names."""
    names = [name for name in claim.names if name not in PRONOUNS]
    for words in records:
        if not words & KIND_WORDS[claim.kind]:
            continue
        if not names or any(name in words or name.rstrip("s") in words or SYNONYMS.get(name, frozenset()) & words
                            for name in names):
            return True
    return False


def unbacked_claims(text: str, evidence: Sequence[ToolEvidence] | None) -> list[ActionClaim]:
    """The actions `text` says happened that the evidence does not record."""
    records = recorded_actions(evidence)
    return [claim for claim in action_claims(text) if not is_backed(claim, records)]
