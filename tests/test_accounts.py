"""The website's accounts (interface/gateway/accounts.py): sign-up, the two seats, mentor priority, the mentor key,
and the gateway refusing everything but /health and sign-in without a seat.

No NATS and no Matrix: the gateway's routes run on a mock Matrix with an unconnected NATS link.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gateway as gw
from accounts import MENTOR_HASH, SEAT_IDLE_S, AccountError, Accounts, mentor_key_hash
from test_gateway import matrix_mock, offline_gateway

PASSWORD = "correct horse"
MENTOR_KEY = "MentorFrFr"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def accounts(tmp_path: Path, clock: Clock) -> Accounts:
    return Accounts(tmp_path / "accounts.sqlite", clock=clock)


def join(accounts: Accounts, name: str, key: str = "") -> str:
    accounts.sign_up(name, name.title(), PASSWORD, PASSWORD, key, "1.2.3.4")
    return accounts.sign_in(name, PASSWORD, "1.2.3.4")[0]


def test_the_owners_mentor_key_is_the_stored_hash_and_not_in_the_code() -> None:
    assert mentor_key_hash(MENTOR_KEY) == MENTOR_HASH
    assert MENTOR_KEY not in Path(gw.__file__).with_name("accounts.py").read_text(encoding="utf-8")


def test_the_database_keeps_a_random_id_a_hash_the_nickname_history_and_redemptions(accounts: Accounts,
                                                                                    tmp_path: Path) -> None:
    join(accounts, "alice")
    join(accounts, "mentor1", MENTOR_KEY)
    rows = sqlite3.connect(tmp_path / "accounts.sqlite").execute(
        "SELECT user_id, username, nickname, password_hash, role, mentor_redemptions FROM users ORDER BY username"
    ).fetchall()
    assert [(row[1], row[2], row[4], row[5]) for row in rows] == [("alice", "Alice", "user", 0),
                                                                  ("mentor1", "Mentor1", "mentor", 1)]
    assert all(len(row[0]) == 16 and row[0] != rows[0][0] for row in rows[1:])
    assert all(PASSWORD.encode() not in bytes(row[3]) for row in rows)
    assert accounts.mentor_redemptions() == 1
    assert [entry["kind"] for entry in accounts.history(rows[0][0])] == ["sign_in", "sign_up"]


def test_sign_up_checks_the_passwords_and_the_username(accounts: Accounts) -> None:
    with pytest.raises(AccountError, match="not the same"):
        accounts.sign_up("bob", "Bob", PASSWORD, PASSWORD + "x", "", "c")
    with pytest.raises(AccountError, match="username"):
        accounts.sign_up("bob' OR '1'='1", "Bob", PASSWORD, PASSWORD, "", "c")
    accounts.sign_up("bob", "Bob", PASSWORD, PASSWORD, "", "c")
    with pytest.raises(AccountError, match="taken"):
        accounts.sign_up("BOB", "Bob", PASSWORD, PASSWORD, "", "c")


def test_sql_in_a_sign_in_is_only_a_wrong_name(accounts: Accounts) -> None:
    join(accounts, "alice")
    for name in ("alice' --", "' OR 1=1 --", "alice'; DROP TABLE users; --"):
        with pytest.raises(AccountError, match="Wrong username or password"):
            accounts.sign_in(name, "anything", "c")
    with pytest.raises(AccountError, match="Wrong username or password"):
        accounts.sign_in("alice", "' OR '1'='1", "c")
    assert accounts.sign_in("alice", PASSWORD, "c")[1].username == "alice"


def test_a_wrong_mentor_key_is_refused_and_guessing_is_limited(accounts: Accounts) -> None:
    for attempt in range(5):
        with pytest.raises(AccountError) as refused:
            accounts.sign_up(f"guess{attempt}", "G", PASSWORD, PASSWORD, "mentorfrfr", "6.6.6.6")
        assert refused.value.status == 403
    with pytest.raises(AccountError) as blocked:
        accounts.sign_up("guess9", "G", PASSWORD, PASSWORD, MENTOR_KEY, "6.6.6.6")
    assert blocked.value.status == 429                    # even the right key, once the tries are spent
    assert accounts.sign_up("real", "R", PASSWORD, PASSWORD, MENTOR_KEY, "7.7.7.7").role == "mentor"


def test_only_two_people_hold_a_seat_and_a_mentor_takes_a_users(accounts: Accounts, clock: Clock) -> None:
    alice = join(accounts, "alice")
    clock.now += 1
    bob = join(accounts, "bob")
    with pytest.raises(AccountError) as busy:
        join(accounts, "carol")
    assert busy.value.status == 503
    mentor = join(accounts, "mentor1", MENTOR_KEY)
    with pytest.raises(AccountError):
        accounts.check(alice)                             # the user seated longest gave up the seat
    assert accounts.check(bob).username == "bob"
    assert accounts.check(mentor).role == "mentor"
    assert accounts.seats_taken() == 2


def test_a_seat_is_freed_by_signing_out_or_by_silence(accounts: Accounts, clock: Clock) -> None:
    alice = join(accounts, "alice")
    bob = join(accounts, "bob")
    accounts.sign_out(alice)
    carol = join(accounts, "carol")
    clock.now += SEAT_IDLE_S - 1
    accounts.check(carol, heartbeat=True)
    clock.now += 2
    with pytest.raises(AccountError):
        accounts.check(bob)                               # no heartbeat for SEAT_IDLE_S
    join(accounts, "dave")


def test_the_gateway_needs_a_seat_for_everything_but_health_and_sign_in(tmp_path: Path) -> None:
    app = gw.create_app(offline_gateway(matrix_mock([])), accounts=Accounts(tmp_path / "a.sqlite"))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/sim/start").status_code == 401
        assert client.post("/faults/heater_relay_stuck", headers={"Authorization": "Bearer stolen"}).status_code == 401
        signed = client.post("/auth/signup", json={"username": "alice", "nickname": "A", "password": PASSWORD,
                                                   "confirm": PASSWORD})
        assert signed.status_code == 200
        token = signed.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert client.post("/sim/start", headers=auth).status_code == 200
        assert client.post("/mentor/llm-failures", headers=auth, json={}).status_code == 403   # users are not mentors
        assert client.post("/auth/heartbeat", headers=auth).json()["user"]["role"] == "user"
        assert client.post("/auth/signout", headers=auth).status_code == 200
        assert client.post("/sim/start", headers=auth).status_code == 401


def test_without_accounts_the_routes_stay_open() -> None:
    with TestClient(gw.create_app(offline_gateway(matrix_mock([])))) as client:
        assert client.post("/sim/start").status_code == 200
