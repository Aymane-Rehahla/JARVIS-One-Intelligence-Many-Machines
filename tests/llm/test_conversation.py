"""The SQLite conversation store migrates, bounds and serializes all node-local LLM data."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

import pytest

from contracts import AuditRecord, FarmEvent, FarmIncident, utc_now
import llm.conversation as conversation_module
from llm.conversation import LlmStore


FEED = "0000000000a1"


def event(sequence: int, *, severity: Literal["info", "warning", "critical"] = "warning", feed_id: str = FEED,
          time_s: float | None = None, code: str | None = None) -> FarmEvent:
    return FarmEvent(node="hvac", tick=sequence, time_s=float(sequence) if time_s is None else time_s,
                     farm_time="06:00", label="live: simulated brains", feed_id=feed_id, sequence=sequence,
                     type="alarm_raised", severity=severity,
                     data={"code": code or f"A{sequence}", "source": "alarm"})  # type: ignore[arg-type]


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
    assert (await reopened.events(5, None, 10, feed_id=FEED, now_s=60.0))[0]["sequence"] == 1
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
    assert [row["sequence"] for row in await store.events(10, None, 10, feed_id=FEED, now_s=60.0)] == [3, 2]
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


@pytest.mark.asyncio
async def test_recent_means_farm_time_not_wall_clock(tmp_path: Path) -> None:
    """Review H1: at 60x, events two farm hours old arrive within seconds of wall clock; "last 60 minutes" must be
    measured on the farm clock."""
    store = LlmStore(tmp_path / "llm.sqlite", node="hvac", owner_conversations=False)
    await store.add_event(event(1, time_s=0.0, code="OLD"))              # farm 00:00
    await store.add_event(event(2, time_s=7_200.0, code="NEW"))          # farm 02:00, received a moment later
    recent = await store.events(60, None, 10, feed_id=FEED, now_s=7_200.0)
    assert [row["code"] for row in recent] == ["NEW"]
    assert len(await store.events(1440, None, 10, feed_id=FEED, now_s=7_200.0)) == 2
    await store.close()


@pytest.mark.asyncio
async def test_a_farm_reset_starts_a_new_run_and_nothing_is_lost(tmp_path: Path) -> None:
    """Review H2: after a reset the farm's sequences and incident ids start again; the new run's records must be kept
    and must not mix with the old run's."""
    store = LlmStore(tmp_path / "llm.sqlite", node="hvac", owner_conversations=False)
    await store.add_event(event(1, code="BEFORE_RESET"))
    await store.add_event(event(1, feed_id="0000000000b2", code="AFTER_RESET"))
    assert [r["code"] for r in await store.events(60, None, 10, feed_id="0000000000b2", now_s=60.0)] == ["AFTER_RESET"]
    assert [r["code"] for r in await store.events(60, None, 10, feed_id=FEED, now_s=60.0)] == ["BEFORE_RESET"]
    common = dict(node="hvac", tick=5, time_s=5.0, farm_time="06:00", label="live: simulated brains", kind="equipment")
    await store.upsert_incident(FarmIncident(**common, feed_id=FEED, incident_id="hvac-equipment-0001",  # type: ignore[arg-type]
                                             state="opened", summary={"run": "old"}))
    await store.upsert_incident(FarmIncident(**common, feed_id="0000000000b2",  # type: ignore[arg-type]
                                             incident_id="hvac-equipment-0001", state="opened", summary={"run": "new"}))
    current = await store.incident("current", feed_id="0000000000b2")
    assert current is not None and current["summary"] == {"run": "new"}
    old = await store.incident("hvac-equipment-0001", feed_id=FEED)
    assert old is not None and old["summary"] == {"run": "old"}
    await store.close()


@pytest.mark.asyncio
async def test_schema_1_migrates_to_2_keeping_conversations(tmp_path: Path) -> None:
    path = tmp_path / "llm.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES('schema_version','1');"
        "CREATE TABLE conversations (id TEXT PRIMARY KEY, started_utc TEXT NOT NULL, title TEXT NOT NULL, "
        "last_utc TEXT NOT NULL);"
        "INSERT INTO conversations VALUES('c1','t','kept','t');"
        "CREATE TABLE turns (id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, request_id TEXT NOT NULL, "
        "role TEXT NOT NULL, text TEXT NOT NULL, farm_time TEXT, created_utc TEXT NOT NULL);"
        "CREATE VIRTUAL TABLE turns_fts USING fts5(text, content='turns', content_rowid='id');"
        "CREATE TABLE events (id INTEGER PRIMARY KEY, sequence INTEGER NOT NULL UNIQUE, tick INTEGER NOT NULL, "
        "farm_time TEXT NOT NULL, type TEXT NOT NULL, severity TEXT NOT NULL, code TEXT, data_json TEXT NOT NULL, "
        "received_utc TEXT NOT NULL);"
        "INSERT INTO events VALUES(1,1,1,'06:00','alarm_raised','warning','X','{}','t');"
        "CREATE TABLE incidents (incident_id TEXT PRIMARY KEY, state TEXT NOT NULL, kind TEXT, farm_time TEXT NOT NULL, "
        "tick INTEGER NOT NULL, summary_json TEXT NOT NULL, updated_utc TEXT NOT NULL);"
        "CREATE TABLE audit (id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, kind TEXT NOT NULL, tool TEXT, "
        "ok INTEGER NOT NULL, error TEXT, duration_ms INTEGER NOT NULL, farm_time TEXT, tick INTEGER, "
        "inputs_json TEXT NOT NULL, outputs_json TEXT NOT NULL, truncated INTEGER NOT NULL, created_utc TEXT NOT NULL);"
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_utc TEXT NOT NULL);")
    db.commit()
    db.close()
    store = LlmStore(path, node="incubator-1", owner_conversations=True)
    assert store._db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "2"
    assert store._db.execute("SELECT title FROM conversations").fetchone()[0] == "kept"
    assert store._db.execute("SELECT count(*) FROM events").fetchone()[0] == 0      # the old copy is dropped
    await store.add_event(event(1))
    assert len(await store.events(60, None, 10, feed_id=FEED, now_s=60.0)) == 1
    await store.close()
