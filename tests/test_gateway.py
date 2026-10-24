"""The gateway (W1): health, farm-clock actions forwarded to the Matrix API, the rate-limited farm stream, the origin check,
and its farm view following NATS.

The Matrix API is an httpx mock; NATS is the private nats-server of the `nats_url` fixture. No farm, no model.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import gateway as gw
from comms.nats_link import NatsLink
from contracts import (Capabilities, FarmAssessment, FarmEvent, FarmStatus, Heartbeat, LinkStatus, SimState, encode,
                       utc_now)

LOG = logging.getLogger("test-gateway")
FARM = dict(tick=5, time_s=5.0, farm_time="06:00", label="live: simulated brains")
SIM = SimState(**FARM, day=1, running=True, target_speed=10.0, actual_speed=9.8, episode_id="e1")  # type: ignore[arg-type]


def matrix_mock(calls: list[tuple[str, object]], *, reply: bytes | None = None, status: int = 200
                ) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content) if request.content else None))
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(status, content=reply if reply is not None else SIM.model_dump_json().encode())
    return httpx.MockTransport(handle)


def offline_gateway(transport: httpx.MockTransport) -> gw.Gateway:
    """A gateway whose NATS link is not connected: enough for the HTTP routes and the stream."""
    return gw.Gateway(NatsLink("gateway", "nats://127.0.0.1:1", logger=LOG), "http://matrix", logger=LOG,
                      transport=transport)


def test_health_reports_nats_the_matrix_and_the_nodes() -> None:
    calls: list[tuple[str, object]] = []
    gateway = offline_gateway(matrix_mock(calls))
    gateway.view.heartbeats["hvac"] = (Heartbeat(node="hvac", seq=1, sent_utc=utc_now(), started_utc=utc_now(),
                                                 uptime_s=1.0, known=["hvac"], alive=["hvac"]), time.monotonic())
    with TestClient(gw.create_app(gateway)) as client:
        health = client.get("/health").json()
    assert health == {"status": "ok", "nats": False, "matrix_api": "ok", "nodes_alive": ["hvac"],
                      "dropped_messages": 0}


def test_farm_clock_actions_are_forwarded_and_checked() -> None:
    calls: list[tuple[str, object]] = []
    with TestClient(gw.create_app(offline_gateway(matrix_mock(calls)))) as client:
        assert client.post("/sim/start").json()["actual_speed"] == 9.8
        assert client.post("/sim/speed", json={"speed": 60}).status_code == 200
        assert client.post("/sim/speed", json={"speed": 7}).status_code == 422       # not a website speed
        assert client.post("/sim/speed").status_code == 422
        assert client.post("/sim/explode").status_code == 422                      # not a farm-clock action
        assert client.post("/links/hvac/cut").status_code in (404, 405)            # mentor faults never pass here
    assert calls == [("/sim/start", None), ("/sim/speed", {"speed": 60.0})]


@pytest.mark.parametrize(("reply", "status", "code"), [(None, 500, 502), (b'{"not": "a sim state"}', 200, 502)])
def test_a_failing_or_invalid_matrix_answer_is_a_502(reply: bytes | None, status: int, code: int) -> None:
    with TestClient(gw.create_app(offline_gateway(matrix_mock([], reply=reply, status=status)))) as client:
        assert client.post("/sim/pause").status_code == code


def test_an_unreachable_matrix_is_a_503() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)
    with TestClient(gw.create_app(offline_gateway(httpx.MockTransport(refuse)))) as client:
        assert client.post("/sim/pause").status_code == 503
        assert client.get("/health").json()["matrix_api"] == "unreachable (ConnectError)"


def test_the_farm_stream_sends_valid_snapshots_at_most_five_a_second() -> None:
    gateway = offline_gateway(matrix_mock([]))
    gateway.view.sim = SIM
    with TestClient(gw.create_app(gateway)) as client, client.websocket_connect(
            "/ws/farm", headers={"origin": "http://127.0.0.1:5173"}) as socket:
        first = gw.FarmSnapshot.model_validate_json(socket.receive_text())
        times = [time.monotonic()]
        for _ in range(3):
            gateway.view.changed()                       # the farm keeps changing; the stream keeps its pace
            gw.FarmSnapshot.model_validate_json(socket.receive_text())
            times.append(time.monotonic())
    assert first.sim == SIM and set(first.nodes) == {"incubator-1", "incubator-2", "hvac", "animal-protection"}
    assert not first.nodes["hvac"].alive and first.nodes["hvac"].heartbeat_age_s is None
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert min(gaps) >= 0.9 / gw.MAX_SNAPSHOTS_PER_S, gaps


def test_other_origins_may_not_open_the_stream() -> None:
    with TestClient(gw.create_app(offline_gateway(matrix_mock([])))) as client:
        with pytest.raises(WebSocketDisconnect) as refused:
            with client.websocket_connect("/ws/farm", headers={"origin": "http://evil.example"}) as socket:
                socket.receive_text()
        assert refused.value.code == 1008
        cors = client.options("/health", headers={"origin": "http://evil.example", "access-control-request-method": "GET"})
        assert "access-control-allow-origin" not in cors.headers


@pytest.mark.asyncio
async def test_the_farm_view_follows_nats(nats_url: str) -> None:
    link = NatsLink("gateway", nats_url, logger=LOG)
    await link.connect(attempts=5)
    gateway = gw.Gateway(link, "http://matrix", logger=LOG, transport=matrix_mock([]))
    await gateway.start()
    farm = NatsLink("farm", nats_url, logger=LOG)
    await farm.connect(attempts=5)
    node = dict(node="hvac", **FARM)
    messages = [
        ("farm.hvac.status", FarmStatus(**node, online=True, readings={"supply_air_temperature_c": 14.2})),  # type: ignore[arg-type]
        ("farm.hvac.assessment", FarmAssessment(**node, assessment="NORMAL", diagnosis="NORMAL", confidence=0.9,  # type: ignore[arg-type]
                                                brain="rule")),
        ("farm.hvac.events", FarmEvent(**node, sequence=1, type="alarm_raised", data={"code": "X"})),  # type: ignore[arg-type]
        ("farm.sim", SIM),
        ("farm.links", LinkStatus(tick=5, time_s=5.0, cut=["hvac"], offline=[])),
        ("jarvis.hvac.heartbeat", Heartbeat(node="hvac", seq=1, sent_utc=utc_now(), started_utc=utc_now(),
                                            uptime_s=1.0, known=["hvac"], alive=["hvac"])),
        ("jarvis.hvac.capabilities", Capabilities(node="hvac", machine="hvac", speaks=False, topics=["status"],
                                                  documents=[], started_utc=utc_now(), reason="start")),
    ]
    for subject, message in messages:
        await farm.publish(subject, message)
    await farm.client.publish("farm.hvac.status", b'{"schema_version": 2}')        # dropped, never shown
    await farm.client.flush()
    for _ in range(50):
        if gateway.view.capabilities and gateway.link.dropped:
            break
        await asyncio.sleep(0.05)
    snapshot = gateway.view.snapshot()
    hvac = snapshot.nodes["hvac"]
    assert hvac.alive and hvac.known == ["hvac"] and hvac.status is not None and hvac.assessment is not None
    assert hvac.capabilities is not None and snapshot.links is not None and snapshot.links.cut == ["hvac"]
    assert snapshot.sim == SIM and [e.type for e in snapshot.events] == ["alarm_raised"]
    assert gateway.link.dropped == 1 and encode(snapshot)
    await farm.close()
    await gateway.close()
