"""Build a stable view of the latest public farm context and cache answers only while its source data is unchanged.

In: current status, assessment, incident and simulation clock plus a normalized question and index generation. Out: an
immutable context snapshot and a bounded LRU of answers expiring after five farm minutes. Tests:
`tests/llm/test_context_first.py`.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from contracts import FarmAssessment, FarmStatus

MAX_ANSWERS = 128
EXPIRY_FARM_MINUTES = 5


@dataclass(frozen=True)
class ContextSnapshot:
    """The most recent public data available to answer one user or peer question."""

    status: FarmStatus | None
    assessment: FarmAssessment | None
    incident: dict[str, Any] | None
    sim_time: str | None

    @property
    def farm_time(self) -> str | None:
        """The latest farm clock string, if any public feed has arrived."""
        if self.status is not None:
            return self.status.farm_time
        if self.assessment is not None:
            return self.assessment.farm_time
        return self.sim_time


class AnswerCache:
    """A bounded LRU keyed by every piece of farm data and index state used to form an answer."""

    def __init__(self, max_entries: int = MAX_ANSWERS, expiry_farm_minutes: int = EXPIRY_FARM_MINUTES) -> None:
        if not 1 <= max_entries <= MAX_ANSWERS or expiry_farm_minutes < 1:
            raise ValueError("answer cache bounds are invalid")
        self.max_entries = max_entries
        self.expiry_farm_minutes = expiry_farm_minutes
        self._items: OrderedDict[tuple[Any, ...], tuple[int, Any]] = OrderedDict()

    @staticmethod
    def _minutes(farm_time: str | None) -> int | None:
        if farm_time is None or len(farm_time) != 5 or farm_time[2] != ":":
            return None
        try:
            hour, minute = (int(part) for part in farm_time.split(":"))
        except ValueError:
            return None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return hour * 60 + minute

    @staticmethod
    def _key(question: str, snapshot: ContextSnapshot, index_generation: str) -> tuple[Any, ...]:
        incident_stamp = None
        if snapshot.incident is not None:
            incident_stamp = snapshot.incident.get("updated_utc", snapshot.incident.get("updated_at"))
        return (" ".join(question.casefold().split()), snapshot.status.tick if snapshot.status else None,
                snapshot.assessment.tick if snapshot.assessment else None, incident_stamp, index_generation)

    def get(self, question: str, snapshot: ContextSnapshot, index_generation: str) -> Any | None:
        """Return a still-current answer, or None when its data key changed or farm time advanced too far."""
        key = self._key(question, snapshot, index_generation)
        found = self._items.get(key)
        now = self._minutes(snapshot.farm_time)
        if found is None or now is None:
            return None
        elapsed = (now - found[0]) % (24 * 60)
        if elapsed > self.expiry_farm_minutes:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return found[1]

    def put(self, question: str, snapshot: ContextSnapshot, index_generation: str, answer: Any) -> None:
        """Cache an answer while its farm data remains within the five-minute validity window."""
        now = self._minutes(snapshot.farm_time)
        if now is None:
            return
        key = self._key(question, snapshot, index_generation)
        self._items[key] = (now, answer)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)
