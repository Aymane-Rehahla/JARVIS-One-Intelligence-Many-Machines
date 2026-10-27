"""Classify questions into a fixed read-only node topic using deterministic keyword rules.

In: a natural-language question. Out: one announced `Topic`; ambiguous questions default to `status`. Tests:
`tests/llm/test_tools.py`.
"""
from __future__ import annotations

import re

from contracts import Topic

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
