"""Classify questions into a fixed read-only node topic using deterministic keyword rules.

In: a natural-language question. Out: one announced `Topic`; ambiguous questions default to `status`. Tests:
`tests/llm/test_tools.py`.
"""
from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from contracts import Capabilities, DocumentInfo, NodeId, Topic

_RULES: tuple[tuple[Topic, re.Pattern[str]], ...] = (
    ("hazard_check", re.compile(r"\b(smoke|fire|flood|storm|hazard|danger|gas leak|sparks?)\b", re.I)),
    ("documents", re.compile(r"\b(manual|documents?|guides?|sources?|citations?|instructions)\b", re.I)),
    ("incident_history", re.compile(r"\b(incidents?|alarms?|faults?|events?|history|previous|cleared|alerts?)\b", re.I)),
    ("incubation", re.compile(r"\b(egg|eggs|incubat\w*|hatch\w*|embryo)\b", re.I)),
    ("readings", re.compile(r"\b(reading|readings|temperature|humidity|pressure|fan|compressor|airflow|setpoint|"
                             r"supply|return|refrigerant|cooling|heating)\b", re.I)),
)


def classify_topic(question: str) -> Topic:
    """Choose the first deterministic topic rule; default to status when no rule clearly applies."""
    for topic, pattern in _RULES:
        if pattern.search(question):
            return topic
    return "status"


class CapabilityRegistry:
    """Expose known topics, documents and liveness while delegating questions to this node's radio."""

    def __init__(self, node: NodeId, states: Mapping[NodeId, Any], radio: Any, silent_after_s: float) -> None:
        self.node = node
        self.states = states
        self.radio = radio
        self.silent_after_s = silent_after_s

    def capabilities_of(self, node: NodeId) -> Capabilities | None:
        """Return the last capabilities announcement for one known node."""
        state = self.states.get(node)
        capabilities = getattr(state, "capabilities", None)
        return capabilities if isinstance(capabilities, Capabilities) else None

    def who_knows(self, topic: Topic) -> list[NodeId]:
        """List nodes whose latest announcement includes the requested topic."""
        return [node for node in self.states if (capabilities := self.capabilities_of(node)) is not None
                and topic in capabilities.topics]

    def documents_of(self, node: NodeId) -> list[DocumentInfo]:
        """List public metadata for documents announced by one node."""
        capabilities = self.capabilities_of(node)
        return list(capabilities.documents) if capabilities is not None else []

    def is_alive(self, node: NodeId) -> bool:
        """Check whether the node is itself or has been heard from within the silence window."""
        if node == self.node:
            return True
        state = self.states.get(node)
        last_heard = getattr(state, "last_heard", float("-inf"))
        return time.monotonic() - last_heard <= self.silent_after_s

    async def ask(self, node: NodeId, topic: Topic, question: str, *, deadline_s: float,
                  request_id: str | None) -> Any:
        """Ask a known, live node through this node's radio with the caller's request id."""
        return await self.radio.ask(node, topic, question, deadline_s=deadline_s, request_id=request_id)
