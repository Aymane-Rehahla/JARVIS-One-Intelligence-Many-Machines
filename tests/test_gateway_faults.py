"""The gateway's demo-fault route (6W, P1 section A6) and its farm view following the farm run (P1 section B).

The Matrix API is an httpx mock, or the real farm runtime's API in this process (httpx's ASGI transport, no port).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import farm_runtime as fr
import gateway as gw
from comms.nats_link import NatsLink
from contracts import FarmAssessment, FarmEvent, FarmStatus, FaultResult, SimState

LOG = logging.getLogger("test-gateway-faults")
ROOT = Path(__file__).resolve().parents[1]
RESULT = FaultResult(scenario_id="supply_fan_failure", family="supply_fan_failure", variant=1, devices=["hvac"],
                     tick=12, end_tick=None, time_s=12.0, farm_time="06:00", feed_id="0000000000a1")


def gateway_with(transport: httpx.AsyncBaseTransport) -> gw.Gateway:
    return gw.Gateway(NatsLink("gateway", "nats://127.0.0.1:1", logger=LOG), "http://matrix", logger=LOG,
                      transport=transport)


def answering(status: int, body: bytes, calls: list[str] | None = None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(f"{request.method} {request.url.path} {len(request.content)}")
        return httpx.Response(status, content=body, headers={"content-type": "application/json"})
    return httpx.MockTransport(handle)


def test_a_demo_fault_is_forwarded_and_its_answer_checked() -> None:
    calls: list[str] = []
    with TestClient(gw.create_app(gateway_with(answering(200, RESULT.model_dump_json().encode(), calls)))) as client:
        answer = client.post("/faults/supply_fan_failure")
        assert answer.status_code == 200 and answer.json() == RESULT.model_dump(mode="json")
        assert client.post("/faults/fire_in_the_barn").status_code == 422        # not one of the four
    assert calls == ["POST /faults/supply_fan_failure 0"]                        # no body, nothing else


@pytest.mark.parametrize("status", [404, 409])
def test_the_matrix_refusals_pass_through_with_their_reason(status: int) -> None:
    body = json.dumps({"detail": "a demo fault (heater_relay_stuck) is already active"}).encode()
    with TestClient(gw.create_app(gateway_with(answering(status, body)))) as client:
        answer = client.post("/faults/supply_overvoltage")
    assert answer.status_code == status and answer.json()["detail"].startswith("a demo fault")


@pytest.mark.parametrize(("status", "body"), [(500, b"{}"), (200, b'{"not": "a fault result"}'), (409, b"oops")])
def test_a_bad_matrix_answer_is_a_502_or_keeps_a_plain_reason(status: int, body: bytes) -> None:
    with TestClient(gw.create_app(gateway_with(answering(status, body)))) as client:
        answer = client.post("/faults/heater_relay_stuck")
    if status == 409:                                                             # refused, but without a reason
        assert answer.status_code == 409 and answer.json()["detail"] == "the Matrix API answered HTTP 409"
    else:
        assert answer.status_code == 502


def test_an_unreachable_matrix_is_a_503() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)
    with TestClient(gw.create_app(gateway_with(httpx.MockTransport(refuse)))) as client:
        assert client.post("/faults/heater_relay_stuck").status_code == 503


class Quiet:
    """A NATS link stand-in that publishes nowhere."""

    connected = True

    async def publish(self, subject: str, message: object, *, request_id: str | None = None) -> None:
        pass


@pytest.mark.skipif(not (ROOT / "_dev" / "farm_poc_snapshot").is_dir(), reason="the farm snapshot is not here")
def test_the_gateway_drives_the_real_farm_runtimes_api() -> None:
    farm = fr.FarmRuntime(Quiet(), logger=LOG, running=False)  # type: ignore[arg-type]
    gateway = gateway_with(httpx.ASGITransport(app=fr.create_api(farm)))
    with TestClient(gw.create_app(gateway)) as client:
        first = client.post("/faults/humidity_sensor_drift")
        assert first.status_code == 200 and first.json()["devices"] == ["incubator-2"]
        assert first.json()["feed_id"] == farm.feed_id
        second = client.post("/faults/supply_fan_failure")
        assert second.status_code == 409 and "already active" in second.json()["detail"]
        assert client.post("/sim/reset").status_code == 200
        assert client.post("/faults/supply_fan_failure").json()["feed_id"] == farm.feed_id


# --- the farm view follows the farm run (rule 7) ---------------------------------------------------------------------

def farm_messages(feed_id: str, tick: int) -> tuple[FarmStatus, FarmAssessment, FarmEvent, SimState]:
    common: dict[str, Any] = dict(node="hvac", tick=tick, time_s=float(tick), farm_time="06:00",
                                  label="live: simulated brains", feed_id=feed_id)
    return (FarmStatus(**common, online=True, readings={"supply_fan_on": True}),
            FarmAssessment(**common, assessment="NORMAL", diagnosis="NONE", confidence=0.9, incident_kind=None,
                           incident_id=None, recommendation="", evidence=[], brain="rule", action=None,
                           communication=None),
            FarmEvent(**common, sequence=tick, type="alarm_raised", severity="warning",
                      data={"code": f"A{tick}", "source": "alarm"}),
            SimState(tick=tick, time_s=float(tick), farm_time="06:00", day=1, running=True, target_speed=10.0,
                     actual_speed=9.9, label="live: simulated brains", episode_id="e", feed_id=feed_id))


@pytest.mark.asyncio
async def test_a_new_farm_run_clears_the_old_one_and_late_messages_are_dropped() -> None:
    gateway = gateway_with(answering(200, b"{}"))
    handlers: dict[str, Any] = {}

    async def capture(subject: str, model: type, handler: Any, **_: Any) -> None:
        handlers[subject] = handler
    gateway.link.subscribe = capture  # type: ignore[method-assign,assignment]
    await gateway.start()
    on = [handlers[subject] for subject in ("farm.*.status", "farm.*.assessment", "farm.*.events", "farm.sim")]

    async def feed(feed_id: str, tick: int) -> None:
        for handler, message in zip(on, farm_messages(feed_id, tick)):
            await handler(message, "subject")

    first, second = "0000000000a1", "0000000000b2"
    await feed(first, 5)
    await feed(first, 6)
    snap = gateway.view.snapshot()
    assert snap.sim is not None and snap.sim.feed_id == first and len(snap.events) == 2
    await feed(second, 1)                                                         # the farm was reset
    snap = gateway.view.snapshot()
    assert snap.sim is not None and snap.sim.feed_id == second and snap.sim.tick == 1
    assert [e.feed_id for e in snap.events] == [second]
    status, assessment = snap.nodes["hvac"].status, snap.nodes["hvac"].assessment
    assert status is not None and status.feed_id == second
    assert assessment is not None and assessment.feed_id == second
    version = gateway.view.version
    await feed(first, 7)                                                          # a late message from the old run
    snap = gateway.view.snapshot()
    assert gateway.view.version == version and [e.feed_id for e in snap.events] == [second]
    assert snap.sim is not None and snap.sim.feed_id == second and snap.sim.tick == 1
    status = snap.nodes["hvac"].status
    assert status is not None and status.feed_id == second and status.tick == 1


# --- review fixes (6W review, 30 Oct) ----------------------------------------------------------------------------------

@pytest.mark.parametrize("origin", ["http://evil.example", "null"])
def test_a_foreign_page_cannot_change_the_farm(origin: str) -> None:
    """M1: a POST with no body is a CORS "simple request": the browser sends it without asking. A foreign origin
    must be refused before anything is forwarded."""
    calls: list[str] = []
    with TestClient(gw.create_app(gateway_with(answering(200, RESULT.model_dump_json().encode(), calls)))) as client:
        for path in ("/faults/supply_fan_failure", "/sim/reset"):
            assert client.post(path, headers={"Origin": origin}).status_code == 403
        assert client.post("/faults/supply_fan_failure", headers={"Origin": "http://127.0.0.1:5173"}).status_code == 200
    assert calls == ["POST /faults/supply_fan_failure 0"]


def test_a_matrix_timeout_says_the_fault_may_have_started() -> None:
    """L4: a timeout is not "unreachable": the Matrix may have injected the fault."""
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)
    with TestClient(gw.create_app(gateway_with(httpx.MockTransport(slow)))) as client:
        answer = client.post("/faults/heater_relay_stuck")
    assert answer.status_code == 504 and "may have started" in answer.json()["detail"]


def test_the_gateway_publishes_nothing_about_a_fault() -> None:
    """L6: the fault click never reaches NATS through the gateway either."""
    sent: list[str] = []
    gateway = gateway_with(answering(200, RESULT.model_dump_json().encode()))

    async def record(subject: str, message: object, **_: Any) -> None:
        sent.append(subject)
    gateway.link.publish = record  # type: ignore[method-assign,assignment]
    with TestClient(gw.create_app(gateway)) as client:
        assert client.post("/faults/supply_fan_failure").status_code == 200
    assert sent == []


def test_a_snapshot_holds_at_most_the_events_shown() -> None:
    """L7: the website's 50-event bound is the contract's own, not the gateway's habit."""
    from pydantic import ValidationError
    view = gw.FarmView()
    snapshot = view.snapshot().model_dump()
    event = farm_messages("0000000000a1", 1)[2].model_dump()
    with pytest.raises(ValidationError):
        gw.FarmSnapshot.model_validate({**snapshot, "events": [event] * (gw.EVENTS_SHOWN + 1)})
