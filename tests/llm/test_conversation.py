"""The SQLite conversation store migrates, bounds and serializes all node-local LLM data."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

import pytest

from contracts import AuditRecord, FarmEvent, utc_now
import llm.conversation as conversation_module
from llm.conversation import LlmStore


def event(sequence: int, *, severity: Literal["info", "warning", "critical"] = "warning") -> FarmEvent:
    return FarmEvent(node="hvac", tick=sequence, time_s=float(sequence), farm_time="06:00",
                     label="live: simulated brains", sequence=sequence, type="alarm_raised", severity=severity,
                     data={"code": f"A{sequence}", "source": "alarm"})  # type: ignore[arg-type]


def audit(request_id: str) -> AuditRecord:
    return AuditRecord(request_id=request_id, node="incubator-1", kind="tool_call", tool="get_status", ok=True,
                       error=None, duration_ms=1, farm_time="06:00", tick=1, inputs={}, outputs={}, truncated=False,
                       created_utc=utc_now())


@pytest.mark.asyncio
async def test_migrations_reopening_and_wal_keep_data(tmp_path: Path) -> None:
    path = tmp_path / "llm.sqlite"
    store = LlmStore(path, node="incubator-1", owner_conversations=True)
    conversation_id = await store.new_conversation("heater check")
    await store.add_turn(conversation_id, "r-1", "owner", "How is the HVAC?")
    await store.add_event(event(1))
    await store.set_setting("voice", {"name": "bm_george"})
    assert store._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store._db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    await store.close()

    reopened = LlmStore(path, node="incubator-1", owner_conversations=True)
    assert (await reopened.events(5, None, 10))[0]["sequence"] == 1
    assert (await reopened.search_turns("HVAC"))[0]["conversation_id"] == conversation_id
    assert await reopened.get_setting("voice") == {"name": "bm_george"}
    await reopened.close()


def test_newer_schema_is_refused_with_a_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "llm.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO meta VALUES('schema_version','9')")
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match="newer than supported"):
        LlmStore(path, node="hvac", owner_conversations=False)


@pytest.mark.asyncio
async def test_bounds_duplicate_sequences_and_readonly_conversation_policy(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conversation_module, "MAX_EVENTS", 2)
    monkeypatch.setattr(conversation_module, "MAX_AUDIT", 2)
    monkeypatch.setattr(conversation_module, "MAX_TURNS", 2)
    store = LlmStore(tmp_path / "llm.sqlite", node="incubator-1", owner_conversations=True)
    await store.add_event(event(1))
    await store.add_event(event(1, severity="critical"))
    await store.add_event(event(2))
    await store.add_event(event(3))
    assert [row["sequence"] for row in await store.events(10, None, 10)] == [3, 2]
    for index in range(3):
        await store.add_audit(audit(f"r-{index}"))
    assert len(store._db.execute("SELECT id FROM audit").fetchall()) == 2
    conversation_id = await store.new_conversation()
    for index in range(3):
        await store.add_turn(conversation_id, f"r-{index}", "owner", f"turn {index}")
    assert store._db.execute("SELECT count(*) FROM turns").fetchone()[0] == 2
    await store.close()

    silent = LlmStore(tmp_path / "silent.sqlite", node="hvac", owner_conversations=False)
    with pytest.raises(PermissionError):
        await silent.new_conversation()
    await silent.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ['"', "NEAR(", "*", "OR", ")", "", "x" * 1000, 1])
async def test_hostile_fts_queries_never_raise(tmp_path: Path, query: object) -> None:
    store = LlmStore(tmp_path / "llm.sqlite", node="incubator-1", owner_conversations=True)
    conversation_id = await store.new_conversation()
    await store.add_turn(conversation_id, "r-1", "owner", "A normal conversation about airflow")
    assert isinstance(await store.search_turns(query if isinstance(query, str) else str(query)), list)
    await store.close()
