"""The website's accounts: who may use JARVIS, and the two seats (owner's request, 31 Oct).

A visitor creates an account (username, nickname, password, and a mentor key if they have one), then signs in.
Only SEATS people use JARVIS at a time. A seat is held while the website sends a heartbeat at least every SEAT_IDLE_S;
signing out frees it at once. Mentors have priority: when every seat is taken, a mentor takes the seat of the user
(not mentor) who has held one the longest, and that user's session ends.

The database (SQLite) keeps for each person: a random user id, the password's scrypt hash (never the password), the
nickname, a history of what they did, and how many times they redeemed the mentor key. Session tokens are kept only
as SHA-256 hashes.

The mentor key is never stored or sent in clear: the code keeps its scrypt hash only (JARVIS_MENTOR_KEY_HASH may
replace it). Guessing is resisted three ways: every mentor-key try is compared in constant time against the hash; a
wrong key fails the whole sign-up with the same message as a malformed one; and wrong tries are limited per client
and for everyone (MENTOR_TRIES in MENTOR_WINDOW_S), each one written to the history.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SEATS = 2
SEAT_IDLE_S = 45.0                       # a seat with no heartbeat for this long is free again
SESSION_S = 12 * 3600.0                  # a sign-in lasts at most this long
MENTOR_TRIES = 5                         # wrong mentor keys allowed per client in MENTOR_WINDOW_S
MENTOR_TRIES_ALL = 20                    # ... and for all clients together
MENTOR_WINDOW_S = 600.0
SIGNIN_TRIES = 10                        # wrong passwords per client in SIGNIN_WINDOW_S
SIGNIN_WINDOW_S = 300.0
USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,24}$")
NICKNAME_MAX = 32
PASSWORD_MIN = 8
PASSWORD_MAX = 128
HISTORY_TEXT_MAX = 500
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
# The salt and scrypt hash of the mentor key the owner chose; the key itself is not in the code.
MENTOR_SALT = bytes.fromhex("6a61727669732d6d656e746f722d6b6579")
MENTOR_HASH = "1dd871912cb7e02f08f6d3131775ec377e0593dff22d68abf8063bdfb70543e1"

Role = Literal["user", "mentor"]


class AccountError(Exception):
    """A refusal the website shows as it is: `status` is the HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Session:
    user_id: str
    username: str
    nickname: str
    role: Role


def _scrypt(secret: str, salt: bytes) -> bytes:
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, **SCRYPT)


def mentor_key_hash(key: str) -> str:
    """The hash stored for a mentor key (for JARVIS_MENTOR_KEY_HASH)."""
    return _scrypt(key, MENTOR_SALT).hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Limiter:
    """At most `limit` events per key in `window_s` seconds (a sliding window)."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self.events: dict[str, deque[float]] = {}

    def blocked(self, key: str, now: float) -> bool:
        events = self.events.setdefault(key, deque())
        while events and now - events[0] > self.window_s:
            events.popleft()
        return len(events) >= self.limit

    def add(self, key: str, now: float) -> None:
        self.events.setdefault(key, deque()).append(now)


class Accounts:
    """The account database and the seats. Thread-safe; every method is short and bounded."""

    def __init__(self, path: Path | str, *, mentor_hash: str | None = None, seats: int = SEATS,
                 clock: Callable[[], float] | None = None) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        self.seats = seats
        self.clock = clock or time.time
        self.mentor_hash = mentor_hash or os.environ.get("JARVIS_MENTOR_KEY_HASH") or MENTOR_HASH
        self.mentor_client = Limiter(MENTOR_TRIES, MENTOR_WINDOW_S)
        self.mentor_all = Limiter(MENTOR_TRIES_ALL, MENTOR_WINDOW_S)
        self.signin_client = Limiter(SIGNIN_TRIES, SIGNIN_WINDOW_S)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, nickname TEXT NOT NULL,
                password_salt BLOB NOT NULL, password_hash BLOB NOT NULL, role TEXT NOT NULL,
                mentor_redemptions INTEGER NOT NULL DEFAULT 0, created_utc REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id),
                started REAL NOT NULL, seen REAL NOT NULL, ended REAL);
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY, user_id TEXT, at_utc REAL NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mentor_key (id INTEGER PRIMARY KEY CHECK (id = 1), redemptions INTEGER NOT NULL);
            INSERT OR IGNORE INTO mentor_key (id, redemptions) VALUES (1, 0);
        """)
        self.db.commit()

    # --- helpers (call with the lock held) ---

    def _log(self, user_id: str | None, kind: str, text: str) -> None:
        self.db.execute("INSERT INTO history (user_id, at_utc, kind, text) VALUES (?, ?, ?, ?)",
                        (user_id, self.clock(), kind, text[:HISTORY_TEXT_MAX]))

    def _mentor_key_ok(self, key: str, client: str, now: float) -> bool:
        if self.mentor_client.blocked(client, now) or self.mentor_all.blocked("*", now):
            self._log(None, "mentor_key_blocked", f"client {client}")
            self.db.commit()
            raise AccountError(429, "Too many mentor-key attempts. Wait ten minutes and try again.")
        ok = hmac.compare_digest(mentor_key_hash(key), self.mentor_hash)
        if not ok:
            self.mentor_client.add(client, now)
            self.mentor_all.add("*", now)
            self._log(None, "mentor_key_wrong", f"client {client}")
        return ok

    def _seated(self, now: float) -> list[tuple[str, str, str, float]]:
        """(token_hash, user_id, role, started) of the live sessions, oldest first."""
        return self.db.execute(
            "SELECT s.token_hash, s.user_id, u.role, s.started FROM sessions s JOIN users u USING (user_id) "
            "WHERE s.ended IS NULL AND s.seen > ? AND s.started > ? ORDER BY s.started",
            (now - SEAT_IDLE_S, now - SESSION_S)).fetchall()

    def _end(self, token_hash: str, now: float) -> None:
        self.db.execute("UPDATE sessions SET ended = ? WHERE token_hash = ?", (now, token_hash))

    # --- the website's calls ---

    def sign_up(self, username: str, nickname: str, password: str, confirm: str, mentor_key: str,
                client: str) -> Session:
        username, nickname, mentor_key = username.strip(), nickname.strip(), mentor_key.strip()
        if not USERNAME.match(username):
            raise AccountError(422, "A username has 3 to 24 letters, digits, dots, dashes or underscores.")
        if not nickname or len(nickname) > NICKNAME_MAX:
            raise AccountError(422, f"A nickname has 1 to {NICKNAME_MAX} characters.")
        if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
            raise AccountError(422, f"A password has {PASSWORD_MIN} to {PASSWORD_MAX} characters.")
        if password != confirm:
            raise AccountError(422, "The two passwords are not the same.")
        with self.lock:
            now = self.clock()
            role: Role = "user"
            if mentor_key:
                if not self._mentor_key_ok(mentor_key, client, now):
                    self.db.commit()
                    raise AccountError(403, "That mentor key is not valid. Leave it empty to join as a regular user.")
                role = "mentor"
            if self.db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise AccountError(409, "That username is taken.")
            user_id = secrets.token_hex(8)
            salt = secrets.token_bytes(16)
            self.db.execute(
                "INSERT INTO users (user_id, username, nickname, password_salt, password_hash, role, "
                "mentor_redemptions, created_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, username, nickname, salt, _scrypt(password, salt), role, int(role == "mentor"), now))
            if role == "mentor":
                self.db.execute("UPDATE mentor_key SET redemptions = redemptions + 1 WHERE id = 1")
            self._log(user_id, "sign_up", f"as {role}")
            self.db.commit()
            return Session(user_id, username, nickname, role)

    def sign_in(self, username: str, password: str, client: str) -> tuple[str, Session]:
        """A new session token for this person, taking a seat (or a user's seat, for a mentor)."""
        with self.lock:
            now = self.clock()
            if self.signin_client.blocked(client, now):
                raise AccountError(429, "Too many wrong passwords. Wait five minutes and try again.")
            row = self.db.execute("SELECT user_id, username, nickname, password_salt, password_hash, role "
                                  "FROM users WHERE username = ?", (username.strip(),)).fetchone()
            given = _scrypt(password, row[3] if row else MENTOR_SALT)            # the same work for a wrong name
            if row is None or not hmac.compare_digest(given, row[4]):
                self.signin_client.add(client, now)
                if row is not None:
                    self._log(row[0], "sign_in_wrong_password", f"client {client}")
                    self.db.commit()
                raise AccountError(401, "Wrong username or password.")
            session = Session(row[0], row[1], row[2], row[5])
            seated = [seat for seat in self._seated(now) if seat[1] != session.user_id]
            for token_hash, user_id, _, _ in self._seated(now):
                if user_id == session.user_id:
                    self._end(token_hash, now)                    # signing in again replaces their old session
            if len(seated) >= self.seats:
                users = [seat for seat in seated if seat[2] == "user"]
                if session.role != "mentor" or not users:
                    self.db.commit()
                    raise AccountError(503, "JARVIS is at capacity right now. Try again in a minute.")
                bumped = users[0]
                self._end(bumped[0], now)
                self._log(bumped[1], "seat_taken_by_mentor", f"by {session.username}")
            token = secrets.token_urlsafe(32)
            self.db.execute("INSERT INTO sessions (token_hash, user_id, started, seen) VALUES (?, ?, ?, ?)",
                            (_token_hash(token), session.user_id, now, now))
            self._log(session.user_id, "sign_in", f"client {client}")
            self.db.commit()
            return token, session

    def check(self, token: str | None, *, heartbeat: bool = False) -> Session:
        """The seated session of this token, or AccountError 401 (signed out, seat lost or expired)."""
        if not token:
            raise AccountError(401, "Sign in first.")
        with self.lock:
            now = self.clock()
            row = self.db.execute(
                "SELECT u.user_id, u.username, u.nickname, u.role, s.started, s.seen, s.ended FROM sessions s "
                "JOIN users u USING (user_id) WHERE s.token_hash = ?", (_token_hash(token),)).fetchone()
            if row is None or row[6] is not None or now - row[5] > SEAT_IDLE_S or now - row[4] > SESSION_S:
                raise AccountError(401, "Your session ended. Sign in again.")
            if heartbeat:
                self.db.execute("UPDATE sessions SET seen = ? WHERE token_hash = ?", (now, _token_hash(token)))
                self.db.commit()
            return Session(row[0], row[1], row[2], row[3])

    def sign_out(self, token: str) -> None:
        with self.lock:
            row = self.db.execute("SELECT user_id FROM sessions WHERE token_hash = ? AND ended IS NULL",
                                  (_token_hash(token),)).fetchone()
            if row:
                self._end(_token_hash(token), self.clock())
                self._log(row[0], "sign_out", "")
                self.db.commit()

    def record(self, user_id: str, kind: str, text: str) -> None:
        """One line in a person's history (a chat question, a fault they injected)."""
        with self.lock:
            self._log(user_id, kind, text)
            self.db.commit()

    def seats_taken(self) -> int:
        with self.lock:
            return len(self._seated(self.clock()))

    def history(self, user_id: str, limit: int = 50) -> list[dict[str, object]]:
        with self.lock:
            rows = self.db.execute("SELECT at_utc, kind, text FROM history WHERE user_id = ? ORDER BY id DESC "
                                   "LIMIT ?", (user_id, limit)).fetchall()
        return [{"at_utc": at, "kind": kind, "text": text} for at, kind, text in rows]

    def mentor_redemptions(self) -> int:
        with self.lock:
            return int(self.db.execute("SELECT redemptions FROM mentor_key WHERE id = 1").fetchone()[0])
