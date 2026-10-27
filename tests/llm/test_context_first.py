"""The context snapshot and answer cache track farm data revisions and farm-clock expiry."""
from __future__ import annotations

from contracts import FarmStatus
from llm.context_first import AnswerCache, ContextSnapshot


def snapshot(tick: int, farm_time: str = "06:00") -> ContextSnapshot:
    return ContextSnapshot(FarmStatus(node="hvac", tick=tick, time_s=float(tick), farm_time=farm_time,
                                      label="live: simulated brains", online=True, readings={}), None, None, farm_time)


def test_cache_reuses_only_same_question_and_data_key() -> None:
    cache = AnswerCache()
    first = snapshot(1)
    cache.put("  HVAC   status? ", first, "g1", {"answer": "normal"})
    assert cache.get("hvac status?", first, "g1") == {"answer": "normal"}
    assert cache.get("hvac status?", first, "g2") is None
    assert cache.get("hvac status?", snapshot(2), "g1") is None


def test_cache_expires_after_five_farm_minutes() -> None:
    cache = AnswerCache()
    cache.put("status", snapshot(1, "06:00"), "g1", "cached")
    assert cache.get("status", snapshot(1, "06:05"), "g1") == "cached"
    assert cache.get("status", snapshot(1, "06:06"), "g1") is None


def test_cache_is_bounded_and_bad_clock_is_not_cached() -> None:
    cache = AnswerCache(max_entries=2)
    cache.put("one", snapshot(1), "g", 1)
    cache.put("two", snapshot(1), "g", 2)
    cache.put("three", snapshot(1), "g", 3)
    assert len(cache._items) == 2
    cache.put("bad", ContextSnapshot(None, None, None, "unknown"), "g", 4)
    assert len(cache._items) == 2
