"""Bounded persistent conversation, farm-event and audit storage for one node.

In: validated public feed messages, audit records, settings and (on the speaker only) conversation turns. Out: recent
events, incidents, audits, settings and FTS5 conversation matches from `<data_dir>/llm.sqlite`. All connection work is
serialized on one asyncio lock and moved off the event loop with `asyncio.to_thread`. Tests: `tests/llm/test_conversation.py`.

Events and incidents are a local copy of the farm feed, kept per farm run (`feed_id`): a reset or restart of the farm
starts its event sequences and incident ids again, so they are unique only within one run, and "recent" means recent
in FARM time (`time_s`), never wall-clock time: at 60x a wall-clock minute is an hour of farm time. Schema 2 added the
run and the farm time; migrating from schema 1 drops the old copies of events and incidents (the farm feed is their
source), and keeps conversations, turns, audit and settings.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from contracts import AuditRecord, FarmEvent, FarmIncident

SCHEMA_VERSION = 2
MAX_EVENTS = 20_000
MAX_AUDIT = 50_000
MAX_TURNS = 2_000
SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


class LlmStore:
    """A node's bounded SQLite data store; only the speaker may persist owner conversations."""

    def __init__(self, path: Path, *, node: str, owner_conversations: bool) -> None:
        self.path = path
        self.node = node
        self.owner_conversations = owner_conversations
        self._lock = asyncio.Lock()
        self._db = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        """Apply ordered schema migrations, refusing databases created by a newer release."""
        self._db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", ("schema_version",)).fetchone()
        version = int(row[0]) if row else 0
        if version > SCHEMA_VERSION:
            self._db.close()
            raise RuntimeError(f"llm.sqlite schema {version} is newer than supported schema {SCHEMA_VERSION}")
        if version < 1:
            with self._db:
                self._db.executescript(
                    "CREATE TABLE conversations (id TEXT PRIMARY KEY, started_utc TEXT NOT NULL, title TEXT NOT NULL, "
                    "last_utc TEXT NOT NULL);"
                    "CREATE TABLE turns (id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES "
                    "conversations(id) ON DELETE CASCADE, request_id TEXT NOT NULL, role TEXT NOT NULL "
                    "CHECK(role IN ('owner','jarvis')), text TEXT NOT NULL, farm_time TEXT, created_utc TEXT NOT NULL);"
                    "CREATE INDEX turns_conversation_id ON turns(conversation_id, id);"
                    "CREATE VIRTUAL TABLE turns_fts USING fts5(text, content='turns', content_rowid='id');"
                    "CREATE TRIGGER turns_ai AFTER INSERT ON turns BEGIN INSERT INTO turns_fts(rowid,text) "
                    "VALUES(new.id,new.text); END;"
                    "CREATE TRIGGER turns_ad AFTER DELETE ON turns BEGIN INSERT INTO turns_fts(turns_fts,rowid,text) "
                    "VALUES('delete',old.id,old.text); END;"
                    "CREATE TABLE audit (id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, kind TEXT NOT NULL, tool TEXT, "
                    "ok INTEGER NOT NULL, error TEXT, duration_ms INTEGER NOT NULL, farm_time TEXT, tick INTEGER, "
                    "inputs_json TEXT NOT NULL, outputs_json TEXT NOT NULL, truncated INTEGER NOT NULL, "
                    "created_utc TEXT NOT NULL);"
                    "CREATE INDEX audit_request_id ON audit(request_id);"
                    "CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_utc TEXT NOT NULL);"
                )
                self._db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", ("schema_version", "1"))
        if version < 2:
            with self._db:
                self._db.executescript(
                    "DROP TABLE IF EXISTS events; DROP TABLE IF EXISTS incidents;"
                    "CREATE TABLE events (id INTEGER PRIMARY KEY, feed_id TEXT NOT NULL, sequence INTEGER NOT NULL, "
                    "tick INTEGER NOT NULL, time_s REAL NOT NULL, farm_time TEXT NOT NULL, type TEXT NOT NULL, "
                    "severity TEXT NOT NULL, code TEXT, data_json TEXT NOT NULL, received_utc TEXT NOT NULL, "
                    "UNIQUE(feed_id, sequence));"
                    "CREATE INDEX events_feed_time ON events(feed_id, time_s);"
                    "CREATE TABLE incidents (feed_id TEXT NOT NULL, incident_id TEXT NOT NULL, state TEXT NOT NULL, "
                    "kind TEXT, farm_time TEXT NOT NULL, tick INTEGER NOT NULL, time_s REAL NOT NULL, "
                    "summary_json TEXT NOT NULL, updated_utc TEXT NOT NULL, PRIMARY KEY(feed_id, incident_id));"
                )
                self._db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", ("schema_version", "2"))

    async def _run(self, function: Any, *args: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(function, *args)

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def _add_event(self, event: FarmEvent) -> None:
        code_value = event.data.get("code")
        code = code_value if isinstance(code_value, str) else None
        received = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO events(feed_id,sequence,tick,time_s,farm_time,type,severity,code,data_json,"
                "received_utc) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (event.feed_id, event.sequence, event.tick, event.time_s, event.farm_time, event.type,
                 event.severity, code, self._dump(event.data), received))
            self._db.execute("DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id DESC LIMIT -1 OFFSET ?)",
                             (MAX_EVENTS,))

    async def add_event(self, event: FarmEvent) -> None:
        """Store an event once by sequence and trim old rows in the same transaction."""
        await self._run(self._add_event, event)

    def _events(self, since_minutes: int, severity: str | None, limit: int, feed_id: str,
                now_s: float) -> list[dict[str, Any]]:
        query = "SELECT * FROM events WHERE feed_id = ? AND time_s >= ?"
        params: list[Any] = [feed_id, now_s - since_minutes * 60.0]
        if severity is not None:
            levels = [name for name, rank in SEVERITY_ORDER.items() if rank >= SEVERITY_ORDER[severity]]
            query += f" AND severity IN ({','.join('?' for _ in levels)})"
            params.extend(levels)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self._db.execute(query, params).fetchall()]

    async def events(self, since_minutes: int, severity: Literal["info", "warning", "critical"] | None,
                     limit: int, *, feed_id: str, now_s: float) -> list[dict[str, Any]]:
        """Events of farm run `feed_id` from the last `since_minutes` of FARM time before `now_s` (the node's latest
        farm time), newest first, optionally at or above a severity."""
        if since_minutes < 1 or limit < 1:
            return []
        return await self._run(self._events, since_minutes, severity, limit, feed_id, now_s)

    def _upsert_incident(self, incident: FarmIncident) -> None:
        updated = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self._db:
            self._db.execute("INSERT INTO incidents(feed_id,incident_id,state,kind,farm_time,tick,time_s,summary_json,"
                             "updated_utc) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(feed_id,incident_id) DO UPDATE SET "
                             "state=excluded.state,kind=excluded.kind,farm_time=excluded.farm_time,tick=excluded.tick,"
                             "time_s=excluded.time_s,summary_json=excluded.summary_json,updated_utc=excluded.updated_utc",
                             (incident.feed_id, incident.incident_id, incident.state, incident.kind, incident.farm_time,
                              incident.tick, incident.time_s, self._dump(incident.summary), updated))

    async def upsert_incident(self, incident: FarmIncident) -> None:
        """Insert or update one public farm incident."""
        await self._run(self._upsert_incident, incident)

    def _incident(self, incident_id: str, feed_id: str) -> dict[str, Any] | None:
        if incident_id == "current":
            row = self._db.execute("SELECT * FROM incidents WHERE feed_id = ? AND state != 'closed' "
                                   "ORDER BY time_s DESC, rowid DESC LIMIT 1", (feed_id,)).fetchone()
        else:
            row = self._db.execute("SELECT * FROM incidents WHERE feed_id = ? AND incident_id = ?",
                                   (feed_id, incident_id)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["summary"] = json.loads(result.pop("summary_json"))
        return result

    async def incident(self, incident_id: str, *, feed_id: str) -> dict[str, Any] | None:
        """An incident of farm run `feed_id` by id, or with "current" its latest incident that is not closed."""
        return await self._run(self._incident, incident_id, feed_id)

    def _add_audit(self, record: AuditRecord) -> None:
        with self._db:
            self._db.execute("INSERT INTO audit(request_id,kind,tool,ok,error,duration_ms,farm_time,tick,inputs_json,"
                             "outputs_json,truncated,created_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                             (record.request_id, record.kind, record.tool, int(record.ok), record.error,
                              record.duration_ms, record.farm_time, record.tick, self._dump(record.inputs),
                              self._dump(record.outputs), int(record.truncated), record.created_utc))
            self._db.execute("DELETE FROM audit WHERE id IN (SELECT id FROM audit ORDER BY id DESC LIMIT -1 OFFSET ?)",
                             (MAX_AUDIT,))

    async def add_audit(self, record: AuditRecord) -> None:
        """Store an audit row and trim the oldest rows atomically."""
        await self._run(self._add_audit, record)

    def _audit_for(self, request_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self._db.execute("SELECT * FROM audit WHERE request_id = ? ORDER BY id",
                                                       (request_id,)).fetchall()]

    async def audit_for(self, request_id: str) -> list[dict[str, Any]]:
        """List audit steps carrying one request id."""
        return await self._run(self._audit_for, request_id)

    def _new_conversation(self, title: str) -> str:
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        conversation_id = str(uuid.uuid4())
        with self._db:
            self._db.execute("INSERT INTO conversations VALUES(?,?,?,?)", (conversation_id, now, title, now))
        return conversation_id

    async def new_conversation(self, title: str = "") -> str:
        """Create a conversation on the speaker node only."""
        self._require_owner_conversations()
        return await self._run(self._new_conversation, title)

    def _require_owner_conversations(self) -> None:
        if not self.owner_conversations:
            raise PermissionError(f"{self.node} cannot store owner conversations")

    def _add_turn(self, conversation_id: str, request_id: str, role: Literal["owner", "jarvis"], text: str,
                  farm_time: str | None) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self._db:
            cursor = self._db.execute("INSERT INTO turns(conversation_id,request_id,role,text,farm_time,created_utc) "
                                      "VALUES(?,?,?,?,?,?)", (conversation_id, request_id, role, text, farm_time, now))
            self._db.execute("UPDATE conversations SET last_utc = ? WHERE id = ?", (now, conversation_id))
            self._db.execute("DELETE FROM turns WHERE conversation_id = ? AND id IN (SELECT id FROM turns "
                             "WHERE conversation_id = ? ORDER BY id DESC LIMIT -1 OFFSET ?)",
                             (conversation_id, conversation_id, MAX_TURNS))
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return the inserted turn id")
            return cursor.lastrowid

    async def add_turn(self, conversation_id: str, request_id: str, role: Literal["owner", "jarvis"], text: str,
                       farm_time: str | None = None) -> int:
        """Append one owner or JARVIS turn and retain only the newest 2,000 for its conversation."""
        self._require_owner_conversations()
        return await self._run(self._add_turn, conversation_id, request_id, role, text, farm_time)

    @staticmethod
    def _fts_query(query: str) -> str:
        words = query.split()
        return " OR ".join('"' + word.replace('"', '""') + '"' for word in words)

    def _search_turns(self, query: str, limit: int) -> list[dict[str, Any]]:
        fts = self._fts_query(query[:1000])
        if not fts:
            return []
        try:
            rows = self._db.execute("SELECT turns.id,turns.conversation_id,turns.request_id,turns.role,turns.text,"
                                    "turns.farm_time,turns.created_utc,bm25(turns_fts) AS rank FROM turns_fts "
                                    "JOIN turns ON turns.id=turns_fts.rowid WHERE turns_fts MATCH ? "
                                    "ORDER BY rank LIMIT ?", (fts, limit)).fetchall()
        except sqlite3.Error:
            return []
        return [dict(row) for row in rows]

    async def search_turns(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search stored turns with an escaped, OR-joined FTS5 query; hostile input returns no hits."""
        self._require_owner_conversations()
        if limit < 1 or not query:
            return []
        return await self._run(self._search_turns, query, limit)

    def _get_setting(self, key: str) -> Any:
        row = self._db.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    async def get_setting(self, key: str) -> Any:
        """Read one JSON setting or return None."""
        return await self._run(self._get_setting, key)

    def _set_setting(self, key: str, value: Any) -> None:
        updated = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self._db:
            self._db.execute("INSERT INTO settings VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
                             "value_json=excluded.value_json,updated_utc=excluded.updated_utc",
                             (key, self._dump(value), updated))

    async def set_setting(self, key: str, value: Any) -> None:
        """Store one JSON setting."""
        await self._run(self._set_setting, key, value)

    async def close(self) -> None:
        """Close the database connection after all queued work completes."""
        await self._run(self._db.close)
