"""Which machines a question is about, and what to ask each one (6C, decision 0018; E5 measures it).

Code decides, not the language model: names and aliases of the machines, then farm-wide words, then the speaking
node's own machine. A question names a machine when it says its name ("the HVAC", "incubator two") or something only
that machine has ("the supply fan", "ammonia", "the flock"). "Both incubators" and "the incubators" mean both; "the
incubator" alone means the speaking node's own (the one nearest the owner). A farm-wide question ("anything I should
know?", "how is the farm?") goes to every machine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from contracts import NODE_IDS, NodeId, Topic

_I = re.IGNORECASE
ALIASES: dict[NodeId, tuple[re.Pattern[str], ...]] = {
    "incubator-1": tuple(re.compile(p, _I) for p in (
        r"\bincubator[\s-]*(?:1|one|#1|no\.?\s*1)\b", r"\bfirst incubator\b", r"\bincubator\s+number\s+(?:1|one)\b")),
    "incubator-2": tuple(re.compile(p, _I) for p in (
        r"\bincubator[\s-]*(?:2|two|#2|no\.?\s*2)\b", r"\bsecond incubator\b", r"\bother incubator\b",
        r"\bincubator\s+number\s+(?:2|two)\b")),
    "hvac": tuple(re.compile(p, _I) for p in (
        r"\bhvac\b", r"\bair[\s-]?(?:handler|handling unit|handling|con(?:ditioning|ditioner)?)\b", r"\bahu\b",
        r"\b(?:supply|return)[\s-]?(?:air|fan)s?\b", r"\bcompressor\b", r"\bventilation\b", r"\bcooling\b",
        r"\bducts?\b", r"\bair filters?\b", r"\bfilters?\b", r"\brefrigerant\b", r"\bdampers?\b", r"\bcondensate\b",
        r"\bstorage room\b", r"\bworkspace\b", r"\bairflow\b")),
    "animal-protection": tuple(re.compile(p, _I) for p in (
        r"\banimal[\s-]?protection\b", r"\bbarn\b", r"\bchickens?\b", r"\bbroilers?\b", r"\bbirds?\b", r"\bflock\b",
        r"\bhens?\b", r"\bco2\b", r"co₂", r"\bcarbon dioxide\b", r"\bammonia\b", r"\bfeed(?:er|ers|ing|store)?\b",
        r"\bdrinkers?\b", r"\bwater tank\b", r"\bsmoke\b", r"\bflood(?:ing)?\b", r"\bwindows?\b", r"\bvents?\b",
        r"\bshutters?\b", r"\bnorth gate\b", r"\bair quality\b", r"\bcarbon monoxide\b", r"\boxygen\b")),
}
BOTH_INCUBATORS = re.compile(r"\b(?:both|two|all)\s+(?:of\s+the\s+)?incubators\b|\bincubators\b|\beach incubator\b", _I)
ONE_INCUBATOR = re.compile(r"\b(?:the|my|this|that|your)\s+incubator\b|\bincubator\b|\beggs?\b|\bhatch\w*\b|\bembryos?\b",
                           _I)
FARM_WIDE = re.compile(
    r"\b(?:the\s+)?(?:whole\s+)?farm\b|\beverything\b|\ball\s+(?:the\s+)?machines\b|\bevery\s+machine\b|"
    r"\banything\s+(?:i\s+should|to)\s+know\b|\bstatus\s+report\b|\bhow\s+(?:are|is)\s+(?:things|everything|we)\b|"
    r"\bany\s+(?:problems?|issues?|alarms?|alerts?)\b|\ball\s+(?:good|well|ok|okay|fine|normal)\b|\boverview\b|"
    r"\bwhat'?s\s+(?:new|happening|going on)\b|\bmachines\b", _I)
NEEDS_MANUAL = re.compile(
    r"\bwhy\b|\bwhat\s+should\b|\bhow\s+(?:do|should|can|to)\b|\bwrong\b|\bfault\w*\b|\bfail\w*\b|\bbroken\b|"
    r"\bfix\b|\brepair\w*\b|\bnormal\s+(?:range|value|reading)s?\b|\bprocedure\b|\bmanual\b|\bcause\w*\b|"
    r"\bmean(?:s|ing)?\b|\bstruggl\w*\b|\bproblem\w*\b|\bissue\w*\b|\bsafe\b|\bdanger\w*\b|\brecommend\w*\b", _I)


@dataclass(frozen=True)
class Route:
    """The machines a question is about, and the topics to ask each one."""

    machines: tuple[NodeId, ...]
    topics: tuple[Topic, ...]
    farm_wide: bool
    needs_manual: bool


def machines_in(question: str, *, self_node: NodeId = "incubator-1") -> tuple[tuple[NodeId, ...], bool]:
    """(the machines the question is about, in farm order; whether it was a farm-wide question)."""
    named = {node for node, patterns in ALIASES.items() if any(p.search(question) for p in patterns)}
    if BOTH_INCUBATORS.search(question):
        named |= {"incubator-1", "incubator-2"}
    elif not named & {"incubator-1", "incubator-2"} and ONE_INCUBATOR.search(question):
        named.add(self_node if self_node in ("incubator-1", "incubator-2") else "incubator-1")
    if named:
        return tuple(node for node in NODE_IDS if node in named), False
    if FARM_WIDE.search(question):
        return tuple(NODE_IDS), True
    return (self_node,), False


def route(question: str, *, self_node: NodeId = "incubator-1") -> Route:
    """Where to look for a question's answer (see the module docstring). The topics always include the machine's
    conclusion ("status") and its readings; its manual is searched when the question asks why, what to do, or about
    a fault, and its incident when it asks about alarms."""
    from llm.capabilities import classify_topic

    machines, farm_wide = machines_in(question, self_node=self_node)
    topics: list[Topic] = ["status", "readings"]
    topic = classify_topic(question)
    if topic not in topics:
        topics.append(topic)
    needs_manual = bool(NEEDS_MANUAL.search(question)) and not farm_wide
    if needs_manual and "documents" not in topics:
        topics.append("documents")
    if needs_manual and "incident_history" not in topics:
        topics.append("incident_history")
    return Route(machines=machines, topics=tuple(topics), farm_wide=farm_wide, needs_manual=needs_manual)
