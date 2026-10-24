"""CMM v3: a node's memory, organised like a mind.

One SQLite database per node (``":memory:"`` for simulations). Stores:

- **Working memory (RAM):** open incidents, timers and call status (inherited from ``NodeMemory``). It
  resets on restart, like RAM.
- **Short-term episodic:** salience-gated episodes.
  - Repeated normal states are never stored.
  - An interesting span is kept tick by tick at full resolution, however long it lasts.
  - When the node has been quiet for ``quiet_close_s``, the episode closes and is summarised.
- **Long-term facts:** verified facts with their source, confidence and how often they recurred.
- **Long-term normal profile:** what normal looks like for each hour of the day (running mean and
  spread of the key readings). Only quiet ticks update it: habituation.
- **Procedures:** what was done, in what situation, and how it turned out.
- **Hypotheses:** kept apart from facts. A hypothesis becomes a fact only by verification: a device
  code, the owner confirming, or a repair record. Never because the brain believed it.
- **ROM:** rules and device documents, read-only.

``sleep`` (nightly consolidation, triggered automatically at 02:00 farm time) turns closed episodes into
recurrence facts, records what worked, and forgets the tick detail of old, unreferenced episodes.

The brain-facing memory features are exactly ``NodeMemory.context``, so brains trained on the
recorded dataset run on this memory unchanged. The richer stores serve the LLM tools and the owner.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sqlite3

from .contracts import DANGER_ACTIONS, MACHINE_TYPES
from .memory import NodeMemory

DAY_S = 86_400.0
SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (id INTEGER PRIMARY KEY, opened_s REAL, closed_s REAL, trigger TEXT, kind TEXT,
    diagnosis TEXT, peak TEXT, ticks INTEGER, summary TEXT, referenced INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS episode_ticks (episode_id INTEGER, time_s REAL, assessment TEXT, diagnosis TEXT, readings TEXT);
CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY, subject TEXT, statement TEXT, source TEXT, confidence REAL,
    first_s REAL, last_s REAL, count INTEGER, UNIQUE(subject, statement));
CREATE TABLE IF NOT EXISTS hypotheses (id INTEGER PRIMARY KEY, diagnosis TEXT, statement TEXT, opened_s REAL,
    updated_s REAL, support INTEGER, status TEXT);
CREATE TABLE IF NOT EXISTS profile (hour INTEGER, key TEXT, n INTEGER, mean REAL, m2 REAL, PRIMARY KEY(hour, key));
CREATE TABLE IF NOT EXISTS procedures (id INTEGER PRIMARY KEY, time_s REAL, situation TEXT, action TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS rom (key TEXT PRIMARY KEY, kind TEXT, text TEXT);
"""
RANKS = ("NORMAL", "HANDLED_BY_MACHINE", "WATCHING", "ESCALATE_TO_USER", "DANGER")
# Device codes that verify a hypothesis about a diagnosis (what the machine itself reports).
VERIFYING_CODES = {
    "FAN_FAULT": {"FAN1_FAILURE", "FAN2_FAILURE"}, "HEATER_FAULT": {"HEATER_OPEN_CIRCUIT", "HEATER_RELAY_STUCK"},
    "HUMIDIFICATION_FAULT": {"PUMP_FAULT", "HUMIDIFIER_FAULT"}, "TURNING_FAULT": {"TURNING_FAULT"},
    "WATER_LEAK": {"WATER_LEAK"}, "DOOR_PROBLEM": {"DOOR_SWITCH_FAULT"}, "INTERNAL_POWER_FAULT": {"INTERNAL_POWER_FAULT"},
    "SUPPLY_FAN_FAULT": {"SUPPLY_FAN_FAILURE"}, "RETURN_FAN_FAULT": {"RETURN_FAN_FAILURE"},
    "COMPRESSOR_FAULT": {"COMPRESSOR_FAILURE"}, "REFRIGERANT_LEAK": {"LOW_REFRIGERANT_PRESSURE"},
    "FILTER_CLOGGED": {"FILTER_MAINTENANCE"}, "CONDENSATE_PROBLEM": {"CONDENSATE_OVERFLOW"},
    "FIRE_SUSPECTED": {"SMOKE_DETECTED", "CO_HIGH"}, "FEED_PROBLEM": {"FEEDER_BLOCKED", "FEED_EMPTY"},
    "WATER_PROBLEM": {"WATER_PUMP_FAULT", "TANK_FILL_FAULT"}, "WATER_SUPPLY_LOSS": {"WATER_SUPPLY_LOST"},
    "POWER_OUTAGE": {"POWER_OUTAGE", "MAINS_POWER_FAILURE"}, "SUPPLY_OVERVOLTAGE": {"OVERVOLTAGE"},
    "FLOOD": {"WATER_INTRUSION"}, "GAS_HAZARD": {"CO2_HIGH", "AMMONIA_HIGH", "O2_LOW"},
}
OWNER_RULES = ("Escalate a problem the machine handled when it repeats 3 times in 24 h, is not recovered after 30 "
               "minutes, spreads to another node, or puts life at risk. Do not interfere when the machine handled it. "
               "Fire: call the fire brigade at once and tell the owner. Flood or storm: tell the owner first; call civil "
               "protection only if water enters the barn or there is a tornado strike or extreme wind and the owner "
               "has not answered within 5 minutes.")


class CognitiveMemory(NodeMemory):
    QUIET_CLOSE_S = 300.0      # quiet this long closes an episode (its tail is kept)
    NOVELTY_Z = 4.0            # a reading this many spreads from normal for the hour is interesting
    PROFILE_MIN_SAMPLES = 60
    KEEP_TICKS_S = 7 * DAY_S   # tick detail of unreferenced episodes is forgotten after a week
    SLEEP_HOUR = 2

    def __init__(self, node_id: str, path: str | Path = ":memory:", documents: dict[str, str] | None = None):
        super().__init__(node_id)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)
        self._episode: int | None = None
        self._episode_peak = "NORMAL"
        self._episode_codes: set[str] = set()
        self._last_interest_s: float | None = None
        self._previous_codes: set[str] = set()
        self._profile = {(h, k): [n, mean, m2] for h, k, n, mean, m2 in self.db.execute("SELECT * FROM profile")}
        self._last_sleep_day: int | None = None
        self._load_rom(documents or {})

    # ------------------------------------------------------------ ROM
    def _load_rom(self, documents):
        machine = MACHINE_TYPES[self.node_id]
        rows = [("owner_rules", "rules", OWNER_RULES),
                ("safety_actions", "rules", "Approved protective actions (DANGER only, own machine only): "
                                            + ", ".join(a for a in DANGER_ACTIONS[machine] if a != "none"))]
        rows += [(f"doc:{name}", "document", text) for name, text in documents.items()]
        self.db.executemany("INSERT OR REPLACE INTO rom VALUES (?, ?, ?)", rows)
        self.db.commit()

    def rom(self, key: str) -> str | None:
        """Read-only: rules and documents are written by people, never by the node."""
        row = self.db.execute("SELECT text FROM rom WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def rom_search(self, words: str, limit: int = 3) -> list[tuple[str, str]]:
        terms = [w.lower() for w in words.split() if len(w) > 2]
        rows = self.db.execute("SELECT key, text FROM rom").fetchall()
        scored = sorted(((sum(text.lower().count(t) for t in terms), key, text) for key, text in rows), reverse=True)
        return [(key, text) for score, key, text in scored[:limit] if score > 0]

    # ------------------------------------------------------------ every tick
    def observe_tick(self, time_s: float, hour: float, readings: dict, assessment: str, diagnosis: str,
                     kind: str | None, codes=(), events=()) -> None:
        """Salience gate: store what is interesting at full resolution, let normal fade into the profile."""
        codes = set(codes)
        new_codes = codes - self._previous_codes
        numeric = {k: float(v) for k, v in readings.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        novel = self._novel(int(hour) % 24, numeric)
        reasons = [r for r, hit in (("assessment", assessment != "NORMAL"), ("new_code", bool(new_codes)),
                                    ("event", bool(events)), ("unusual_reading", bool(novel))) if hit]
        self._previous_codes = codes
        if reasons:
            self._last_interest_s = time_s
            if self._episode is None:
                cur = self.db.execute("INSERT INTO episodes (opened_s, trigger, kind, diagnosis, peak, ticks) VALUES "
                                      "(?, ?, ?, ?, ?, 0)", (time_s, ",".join(reasons + novel[:3]), kind, diagnosis, assessment))
                self._episode, self._episode_peak, self._episode_codes = cur.lastrowid, assessment, set()
        if self._episode is not None:
            self.db.execute("INSERT INTO episode_ticks VALUES (?, ?, ?, ?, ?)",
                            (self._episode, time_s, assessment, diagnosis, json.dumps(readings, default=str)))
            self._episode_codes |= codes
            if RANKS.index(assessment) > RANKS.index(self._episode_peak):
                self._episode_peak = assessment
                self.db.execute("UPDATE episodes SET peak = ?, diagnosis = ?, kind = ? WHERE id = ?",
                                (assessment, diagnosis, kind, self._episode))
            for event in events:
                self.db.execute("INSERT INTO procedures (time_s, situation, action, result) VALUES (?, ?, ?, ?)",
                                (time_s, diagnosis, event.get("action", ""), event.get("result", "")))
            if not reasons and time_s - self._last_interest_s >= self.QUIET_CLOSE_S:
                self._close_episode(time_s)
        elif assessment == "NORMAL":
            self._learn_normal(int(hour) % 24, numeric)
        if assessment == "WATCHING" and diagnosis not in ("NONE", "UNKNOWN_ANOMALY"):
            self.hypothesize(diagnosis, f"possible {diagnosis.lower().replace('_', ' ')}", time_s)
        for open_diagnosis in [d for (d,) in self.db.execute("SELECT diagnosis FROM hypotheses WHERE status = 'open'")]:
            if codes & VERIFYING_CODES.get(open_diagnosis, set()):
                self.verify(open_diagnosis, time_s, source="device code: " + ", ".join(sorted(codes)))
        confirming = new_codes & VERIFYING_CODES.get(diagnosis, set())
        if confirming:   # the machine itself reports it: a fact, even without an earlier suspicion
            self.verify(diagnosis, time_s, source="device code: " + ", ".join(sorted(confirming)))
        if int(hour) % 24 == self.SLEEP_HOUR and self._last_sleep_day != int(time_s // DAY_S):
            self._last_sleep_day = int(time_s // DAY_S)
            self.sleep(time_s)

    def _novel(self, hour, numeric):
        out = []
        for key, value in numeric.items():
            n, mean, m2 = self._profile.get((hour, key), (0, 0.0, 0.0))
            if n >= self.PROFILE_MIN_SAMPLES:
                std = math.sqrt(m2 / max(n - 1, 1)) or 1e-6
                if abs(value - mean) / max(std, 1e-3 * max(abs(mean), 1.0)) > self.NOVELTY_Z:
                    out.append(key)
        return out

    def _learn_normal(self, hour, numeric):
        for key, value in numeric.items():   # Welford's running mean and variance
            n, mean, m2 = self._profile.get((hour, key), (0, 0.0, 0.0))
            n += 1
            delta = value - mean
            mean += delta / n
            m2 += delta * (value - mean)
            self._profile[(hour, key)] = [n, mean, m2]

    def _close_episode(self, time_s):
        ticks = self.db.execute("SELECT COUNT(*) FROM episode_ticks WHERE episode_id = ?", (self._episode,)).fetchone()[0]
        summary = {"codes": sorted(self._episode_codes), "peak": self._episode_peak,
                   "duration_s": time_s - self.db.execute("SELECT opened_s FROM episodes WHERE id = ?",
                                                         (self._episode,)).fetchone()[0]}
        self.db.execute("UPDATE episodes SET closed_s = ?, ticks = ?, summary = ? WHERE id = ?",
                        (time_s, ticks, json.dumps(summary), self._episode))
        self.db.commit()
        self._episode = None

    # ------------------------------------------------------------ hypotheses and facts
    def hypothesize(self, diagnosis: str, statement: str, time_s: float) -> None:
        row = self.db.execute("SELECT id FROM hypotheses WHERE diagnosis = ? AND status = 'open'", (diagnosis,)).fetchone()
        if row:
            self.db.execute("UPDATE hypotheses SET updated_s = ?, support = support + 1 WHERE id = ?", (time_s, row[0]))
        else:
            self.db.execute("INSERT INTO hypotheses (diagnosis, statement, opened_s, updated_s, support, status) VALUES "
                            "(?, ?, ?, ?, 1, 'open')", (diagnosis, statement, time_s, time_s))

    def verify(self, diagnosis: str, time_s: float, source: str) -> None:
        """Only verification (device code, owner, repair) turns a hypothesis into a fact."""
        self.db.execute("UPDATE hypotheses SET status = 'verified', updated_s = ? WHERE diagnosis = ? AND status = 'open'",
                        (time_s, diagnosis))
        self.remember_fact(diagnosis, f"{diagnosis.lower().replace('_', ' ')} confirmed", source, 1.0, time_s)

    def reject(self, diagnosis: str, time_s: float) -> None:
        self.db.execute("UPDATE hypotheses SET status = 'rejected', updated_s = ? WHERE diagnosis = ? AND status = 'open'",
                        (time_s, diagnosis))

    def owner_confirms(self, diagnosis: str, time_s: float) -> None:
        self.verify(diagnosis, time_s, source="owner confirmed")

    def record_repair(self, diagnosis: str, time_s: float, note: str = "repaired") -> None:
        self.remember_fact(diagnosis, note, "repair record", 1.0, time_s)

    def remember_fact(self, subject, statement, source, confidence, time_s) -> None:
        self.db.execute("INSERT INTO facts (subject, statement, source, confidence, first_s, last_s, count) VALUES "
                        "(?, ?, ?, ?, ?, ?, 1) ON CONFLICT(subject, statement) DO UPDATE SET last_s = excluded.last_s, "
                        "count = count + 1, source = excluded.source, confidence = MAX(confidence, excluded.confidence)",
                        (subject, statement, source, confidence, time_s, time_s))
        self.db.commit()

    # ------------------------------------------------------------ sleep
    def sleep(self, time_s: float) -> None:
        """Consolidate: recurrence facts from closed episodes, persist the profile, forget old tick detail."""
        if self._episode is not None and self._last_interest_s is not None and \
                time_s - self._last_interest_s >= self.QUIET_CLOSE_S:
            self._close_episode(time_s)
        for diagnosis, count in self.db.execute(
                "SELECT diagnosis, COUNT(*) FROM episodes WHERE closed_s IS NOT NULL AND opened_s >= ? AND diagnosis "
                "NOT IN ('NONE', 'UNKNOWN_ANOMALY') GROUP BY diagnosis", (time_s - 7 * DAY_S,)).fetchall():
            self.remember_fact(diagnosis, f"{count} episode(s) in the last 7 days", "consolidated memory", 1.0, time_s)
        self.db.executemany("INSERT OR REPLACE INTO profile VALUES (?, ?, ?, ?, ?)",
                            [(h, k, n, mean, m2) for (h, k), (n, mean, m2) in self._profile.items()])
        old = [i for (i,) in self.db.execute("SELECT id FROM episodes WHERE closed_s IS NOT NULL AND closed_s < ? AND "
                                             "referenced = 0", (time_s - self.KEEP_TICKS_S,))]
        self.db.executemany("DELETE FROM episode_ticks WHERE episode_id = ?", [(i,) for i in old])
        self.db.commit()

    # ------------------------------------------------------------ recall (for the LLM tools and the owner)
    def recall(self, diagnosis: str | None = None, since_s: float | None = None, limit: int = 10) -> list[dict]:
        sql, args = "SELECT id, opened_s, closed_s, trigger, kind, diagnosis, peak, ticks, summary FROM episodes WHERE 1=1", []
        if diagnosis:
            sql += " AND diagnosis = ?"; args.append(diagnosis)
        if since_s is not None:
            sql += " AND opened_s >= ?"; args.append(since_s)
        rows = self.db.execute(sql + " ORDER BY opened_s DESC LIMIT ?", (*args, limit)).fetchall()
        self.db.executemany("UPDATE episodes SET referenced = 1 WHERE id = ?", [(r[0],) for r in rows])
        keys = ("id", "opened_s", "closed_s", "trigger", "kind", "diagnosis", "peak", "ticks", "summary")
        return [dict(zip(keys, r)) for r in rows]

    def episode_ticks(self, episode_id: int) -> list[dict]:
        rows = self.db.execute("SELECT time_s, assessment, diagnosis, readings FROM episode_ticks WHERE episode_id = ? "
                               "ORDER BY time_s", (episode_id,)).fetchall()
        return [{"time_s": t, "assessment": a, "diagnosis": d, "readings": json.loads(r)} for t, a, d, r in rows]

    def facts(self, subject: str | None = None) -> list[dict]:
        rows = self.db.execute("SELECT subject, statement, source, confidence, first_s, last_s, count FROM facts" +
                               (" WHERE subject = ?" if subject else ""), (subject,) if subject else ()).fetchall()
        keys = ("subject", "statement", "source", "confidence", "first_s", "last_s", "count")
        return [dict(zip(keys, r)) for r in rows]

    def hypotheses(self, status: str = "open") -> list[dict]:
        rows = self.db.execute("SELECT diagnosis, statement, opened_s, updated_s, support, status FROM hypotheses "
                               "WHERE status = ?", (status,)).fetchall()
        return [dict(zip(("diagnosis", "statement", "opened_s", "updated_s", "support", "status"), r)) for r in rows]

    def normal(self, hour: int, key: str) -> tuple[float, float] | None:
        n, mean, m2 = self._profile.get((hour % 24, key), (0, 0.0, 0.0))
        return (mean, math.sqrt(m2 / max(n - 1, 1))) if n >= 2 else None

    def close(self):
        if self._last_interest_s is not None and self._episode is not None:
            self._close_episode(self._last_interest_s)
        self.sleep_profile_only()
        self.db.close()

    def sleep_profile_only(self):
        self.db.executemany("INSERT OR REPLACE INTO profile VALUES (?, ?, ?, ?, ?)",
                            [(h, k, n, mean, m2) for (h, k), (n, mean, m2) in self._profile.items()])
        self.db.commit()
