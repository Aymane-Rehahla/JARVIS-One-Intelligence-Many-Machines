"""The gateway's 6C routes (P2 section 4): /ws/chat, /ws/audit, the mentor's LLM failure switches and the owner's
acknowledgement, with tools/chat_cli.py as the client.

NATS is the private nats-server of the `nats_url` fixture; the gateway runs for real (uvicorn on a free port, in the
test's own event loop); incubator-1 is a scripted stand-in on NATS; the Matrix API is an httpx mock of exactly the
behaviour P2 section 4 step 5 fixes. No farm, no model.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import pytest_asyncio
import uvicorn
import websockets

import chat_cli
import gateway as gw
from comms.nats_link import NatsLink
from conftest import free_port
from contracts import (MENTOR_LLM_FAILURES, AckCommand, AckResult, AuditRecord, ChatChunk, ChatDone, ChatRequest,
                       LlmFailureSwitches, OwnerAlert, ReplyTimings, utc_now)

LOG = logging.getLogger("test-gateway-chat")
FARM = dict(farm_time="06:08", label="live: simulated brains")


@dataclass
class Incubator:
    """incubator-1's chat and ack subjects, scripted: `replies(request)` says what to publish back."""

    link: NatsLink
    replies: Callable[[ChatRequest], list[Any]] = field(default=lambda request: [])
    asked: list[ChatRequest] = field(default_factory=list[ChatRequest])
    acks: list[AckCommand] = field(default_factory=list[AckCommand])
    ack_ok: bool = True
    delay_s: float = 0.0

    async def start(self) -> None:
        await self.link.subscribe("jarvis.incubator-1.chat", ChatRequest, self._on_chat)
        await self.link.serve("jarvis.incubator-1.ack", AckCommand, self._on_ack)

    async def _on_chat(self, request: ChatRequest, subject: str) -> None:
        self.asked.append(request)
        await asyncio.sleep(self.delay_s)
        for piece in self.replies(request):
            await self.link.publish("jarvis.incubator-1.reply", piece, request_id=request.request_id)

    async def _on_ack(self, command: AckCommand, subject: str) -> AckResult:
        self.acks.append(command)
        return AckResult(request_id=command.request_id, node=command.node, incident_id=command.incident_id,
                         ok=self.ack_ok, reason="" if self.ack_ok else "invalid confirmation token")


def answer(text: str = "The supply fan has stopped, sir. That is as of farm time 06:08.") -> Callable[..., list[Any]]:
    def replies(request: ChatRequest) -> list[Any]:
        half = len(text) // 2
        return [ChatChunk(request_id=request.request_id, seq=0, text=text[:half]),
                ChatChunk(request_id=request.request_id, seq=1, text=text[half:]),
                ChatDone(request_id=request.request_id, conversation_id=request.conversation_id or "conv-1", text=text,
                         chunks=2, outcome="answered", numbers_checked=1, **FARM,  # type: ignore[arg-type]
                         timings=ReplyTimings(first_chunk_ms=10, total_ms=20))]
    return replies


@dataclass
class Rig:
    url: str
    gateway: gw.Gateway
    incubator: Incubator
    matrix_calls: list[str]
    matrix: dict[str, Any]
    nats: NatsLink

    def ws(self, path: str, **options: Any) -> Any:
        return websockets.connect(self.url.replace("http", "ws") + path, **options)


def matrix_mock(calls: list[str], state: dict[str, Any]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path} {len(request.content)}")
        status, body = state["status"], state["body"]
        return httpx.Response(status, content=json.dumps(body).encode(), headers={"content-type": "application/json"})
    return httpx.MockTransport(handle)


@pytest_asyncio.fixture
async def rig(nats_url: str) -> AsyncIterator[Rig]:
    calls: list[str] = []
    state: dict[str, Any] = {"status": 200, "body": {"node": "hvac", "incident_id": "hvac-equipment-0001",
                                                     "acknowledged": 1, "farm_time": "06:09"}}
    link = NatsLink("gateway", nats_url, logger=LOG)
    await link.connect(attempts=5)
    gateway = gw.Gateway(link, "http://matrix", logger=LOG, transport=matrix_mock(calls, state))
    await gateway.start()
    stand_in = NatsLink("incubator-1", nats_url, logger=LOG)
    await stand_in.connect(attempts=5)
    incubator = Incubator(stand_in, replies=answer())
    await incubator.start()
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(gw.create_app(gateway), host="127.0.0.1", port=port, log_level="warning",
                                           lifespan="off"))
    serving = asyncio.create_task(server.serve())
    deadline = time.monotonic() + 10.0
    while not server.started:
        assert time.monotonic() < deadline, "the gateway did not start"
        await asyncio.sleep(0.02)
    try:
        yield Rig(f"http://127.0.0.1:{port}", gateway, incubator, calls, state, stand_in)
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(serving, timeout=10.0)
        await gateway.close()
        await stand_in.close()


async def receive(socket: Any, timeout_s: float = 5.0) -> dict[str, Any]:
    message: dict[str, Any] = json.loads(await asyncio.wait_for(socket.recv(), timeout=timeout_s))
    return message


# --- /ws/chat --------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_question_streams_its_chunks_and_its_done(rig: Rig) -> None:
    async with rig.ws("/ws/chat") as socket:
        got = await chat_cli.ask(socket, "What's wrong with the HVAC?", answer_s=5.0)
    assert got.request_id is not None and got.error is None and got.done is not None
    assert "".join(got.chunks) == got.done["text"] and got.done["outcome"] == "answered"
    asked = rig.incubator.asked
    assert len(asked) == 1 and asked[0].request_id == got.request_id and asked[0].source == "chat"
    assert asked[0].text == "What's wrong with the HVAC?" and asked[0].conversation_id is None
    assert "06:08" in chat_cli.describe(got)


@pytest.mark.asyncio
async def test_one_question_at_a_time_per_socket(rig: Rig) -> None:
    rig.incubator.delay_s = 0.5
    async with rig.ws("/ws/chat") as socket:
        for client_msg_id in ("m1", "m2"):
            await socket.send(json.dumps({"type": "chat_send", "client_msg_id": client_msg_id, "text": "status?"}))
        first, second = await receive(socket), await receive(socket)
        assert first["type"] == "chat_accepted" and first["client_msg_id"] == "m1"
        assert second == {"schema_version": 1, "type": "chat_error", "client_msg_id": "m2", "request_id": None,
                          "reason": "one question at a time"}
        kinds = [(await receive(socket))["type"] for _ in range(3)]
        assert kinds == ["chat_chunk", "chat_chunk", "chat_done"]
        again = await chat_cli.ask(socket, "and now?", answer_s=5.0)            # the done freed the socket
        assert again.done is not None
    assert len(rig.incubator.asked) == 2


@pytest.mark.asyncio
async def test_invalid_messages_are_chat_errors_and_the_socket_stays_open(rig: Rig) -> None:
    async with rig.ws("/ws/chat") as socket:
        await socket.send(json.dumps({"type": "chat_send", "client_msg_id": "bad-1", "text": ""}))
        error = await receive(socket)
        assert error["type"] == "chat_error" and error["client_msg_id"] == "bad-1"
        await socket.send("not json")
        assert (await receive(socket))["client_msg_id"] is None
        await socket.send(b"\x00\x01")
        assert "JSON text" in (await receive(socket))["reason"]
        await socket.send(json.dumps({"type": "chat_send", "client_msg_id": "c", "text": "x\x07"}))
        assert (await receive(socket))["type"] == "chat_error"
        assert (await chat_cli.ask(socket, "still there?", answer_s=5.0)).done is not None
    assert [r.text for r in rig.incubator.asked] == ["still there?"]


@pytest.mark.asyncio
async def test_no_done_within_the_deadline_is_an_error_and_late_pieces_are_dropped(rig: Rig,
                                                                                  monkeypatch: Any) -> None:
    monkeypatch.setattr(gw, "CHAT_DEADLINE_S", 0.4)
    rig.incubator.delay_s = 1.0
    async with rig.ws("/ws/chat") as socket:
        got = await chat_cli.ask(socket, "slow?", answer_s=5.0)
        assert got.error is not None and got.error["request_id"] == got.request_id
        assert "did not answer within 0.4 s" in got.error["reason"]
        await asyncio.sleep(1.0)                                            # the late chunks and done arrive now
        assert rig.gateway.late_pieces == 3
        rig.incubator.delay_s = 0.0
        follow = await chat_cli.ask(socket, "fast?", answer_s=5.0)
        assert follow.done is not None and follow.chunks                     # nothing of the old one leaked in
        assert follow.done["request_id"] == follow.request_id != got.request_id


@pytest.mark.asyncio
async def test_without_nats_a_question_is_refused_at_once(rig: Rig) -> None:
    async with rig.ws("/ws/chat") as socket:
        await rig.gateway.link.close()
        got = await chat_cli.ask(socket, "anyone?", answer_s=5.0)
    assert got.error is not None and "not connected to NATS" in got.error["reason"]


@pytest.mark.asyncio
async def test_owner_alerts_reach_every_chat_socket(rig: Rig) -> None:
    alert = OwnerAlert(alert_id="alert-1", node="hvac", level="ESCALATE_TO_USER", incident_id="hvac-equipment-0001",
                       text="Sir, the HVAC needs you: supply fan fault. Farm time 06:08.", farm_time="06:08",
                       feed_id="0000000000a1", label="live: simulated brains", reworded=False, sent_utc=utc_now())
    async with rig.ws("/ws/chat") as one, rig.ws("/ws/chat") as two:
        for _ in range(100):                                                 # both sockets registered
            if len(rig.gateway.chats) == 2:
                break
            await asyncio.sleep(0.02)
        await rig.nats.publish("jarvis.incubator-1.alert", alert)
        for socket in (one, two):
            assert await receive(socket) == json.loads(alert.model_dump_json())


@pytest.mark.asyncio
async def test_other_origins_may_not_open_chat_or_audit(rig: Rig) -> None:
    for path in ("/ws/chat", "/ws/audit"):
        with pytest.raises(websockets.InvalidStatus):
            async with rig.ws(path, origin="http://evil.example") as socket:   # type: ignore[arg-type]
                await socket.recv()
        async with rig.ws(path, origin="http://127.0.0.1:5173") as socket:     # type: ignore[arg-type]
            assert socket is not None


# --- /ws/audit -------------------------------------------------------------------------------------------------------

def record(index: int) -> AuditRecord:
    return AuditRecord(request_id=f"r-{index}", node="hvac", kind="tool_call", tool="get_status", ok=True, error=None,
                       duration_ms=1, inputs={}, outputs={"n": index}, truncated=False, created_utc=utc_now())


@pytest.mark.asyncio
async def test_audit_records_stream_at_most_twenty_a_second_oldest_dropped_first(rig: Rig) -> None:
    async with rig.ws("/ws/audit") as socket:
        for _ in range(100):
            if rig.gateway.audits:
                break
            await asyncio.sleep(0.02)
        for index in range(100):
            await rig.nats.publish("jarvis.hvac.audit", record(index))
        started = time.monotonic()
        received = [await receive(socket) for _ in range(gw.AUDIT_QUEUE + 1)]   # one went out before it filled
        elapsed = time.monotonic() - started
    numbers = [message["outputs"]["n"] for message in received]
    assert numbers == sorted(numbers) and numbers[-1] == 99                    # in order, the newest kept
    assert rig.gateway.audit_dropped >= 100 - gw.AUDIT_QUEUE - 1               # the oldest were dropped, counted
    assert elapsed >= (gw.AUDIT_QUEUE - 2) / gw.AUDIT_PER_S                    # never faster than 20 a second


# --- the mentor's LLM failure switches ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failure_switches_are_published_shown_and_republished(rig: Rig, monkeypatch: Any) -> None:
    heard: list[LlmFailureSwitches] = []

    async def on_switches(message: LlmFailureSwitches, subject: str) -> None:
        heard.append(message)
    await rig.nats.subscribe(MENTOR_LLM_FAILURES, LlmFailureSwitches, on_switches)
    body = {"llm_timeout": ["hvac"], "broken_json": [], "silent_peer": ["incubator-2"], "server_crash": []}
    async with httpx.AsyncClient(base_url=rig.url) as client:
        sent = await client.post("/mentor/llm-failures", json=body)
        assert sent.status_code == 200 and sent.json()["llm_timeout"] == ["hvac"]
        assert (await client.get("/mentor/llm-failures")).json() == sent.json()
        assert (await client.post("/mentor/llm-failures", json={**body, "llm_timeout": ["hvac", "hvac"]})
                ).status_code == 422
        assert (await client.post("/mentor/llm-failures", json={"llm_timeout": []})).status_code == 422
        assert (await client.post("/mentor/llm-failures", json=body, headers={"origin": "http://evil.example"})
                ).status_code == 403
    for _ in range(100):
        if heard:
            break
        await asyncio.sleep(0.02)
    assert heard and heard[0].silent_peer == ["incubator-2"] and heard[0].llm_timeout == ["hvac"]


# --- acknowledging an alert ------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_confirmed_acknowledgement_reaches_the_matrix(rig: Rig) -> None:
    async with httpx.AsyncClient(base_url=rig.url) as client:
        done = await client.post("/alerts/hvac/hvac-equipment-0001/ack", json={"confirm_token": "123.abc"})
    assert done.status_code == 200
    assert done.json() == {"schema_version": 1, "type": "ack_response", "node": "hvac", "incident_id": "hvac-equipment-0001", "ok": True,
                           "stage": "done", "reason": ""}
    (command,) = rig.incubator.acks
    assert (command.node, command.incident_id, command.confirm_token) == ("hvac", "hvac-equipment-0001", "123.abc")
    assert rig.matrix_calls == ["POST /alerts/hvac/hvac-equipment-0001/ack 0"]


@pytest.mark.asyncio
async def test_a_refused_token_never_reaches_the_matrix(rig: Rig) -> None:
    rig.incubator.ack_ok = False
    async with httpx.AsyncClient(base_url=rig.url) as client:
        refused = await client.post("/alerts/hvac/hvac-equipment-0001/ack", json={"confirm_token": "used"})
    assert refused.json()["stage"] == "token" and "invalid confirmation token" in refused.json()["reason"]
    assert rig.matrix_calls == []


@pytest.mark.asyncio
async def test_no_token_or_a_bad_path_is_refused_before_anything(rig: Rig) -> None:
    async with httpx.AsyncClient(base_url=rig.url) as client:
        assert (await client.post("/alerts/hvac/hvac-equipment-0001/ack", json={})).status_code == 422
        assert (await client.post("/alerts/hvac/hvac-equipment-0001/ack")).status_code == 422
        assert (await client.post("/alerts/barn/x/ack", json={"confirm_token": "t"})).status_code == 422
        assert (await client.post("/alerts/hvac/hvac-equipment-0001/ack", json={"confirm_token": "t"},
                                  headers={"origin": "http://evil.example"})).status_code == 403
    assert rig.incubator.acks == [] and rig.matrix_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "body", "why"), [
    (404, {"detail": "this farm run has no delivered, unacknowledged owner notification"}, "no delivered"),
    (500, {}, "HTTP 500"),
    (200, {"node": "hvac", "incident_id": "another", "acknowledged": 1, "farm_time": "06:09"}, "another incident"),
    (200, {"node": "hvac", "incident_id": "hvac-equipment-0001", "acknowledged": 0, "farm_time": "06:09"},
     "not a valid acknowledgement")])
async def test_a_matrix_refusal_or_bad_answer_stops_at_the_matrix_stage(rig: Rig, status: int, body: Any,
                                                                        why: str) -> None:
    rig.matrix.update(status=status, body=body)
    async with httpx.AsyncClient(base_url=rig.url) as client:
        answer_ = await client.post("/alerts/hvac/hvac-equipment-0001/ack", json={"confirm_token": "t"})
    assert answer_.status_code == 200
    assert answer_.json()["stage"] == "matrix" and not answer_.json()["ok"] and why in answer_.json()["reason"]


@pytest.mark.asyncio
async def test_no_answer_from_incubator_1_stops_at_the_token_stage(rig: Rig) -> None:
    await rig.incubator.link.close()
    async with httpx.AsyncClient(base_url=rig.url) as client:
        answer_ = await client.post("/alerts/hvac/hvac-equipment-0001/ack", json={"confirm_token": "t"})
    assert answer_.json()["stage"] == "token" and "did not confirm" in answer_.json()["reason"]
    assert rig.matrix_calls == []


def test_the_chat_deadline_nests_outside_the_agents_turn() -> None:
    """Rule 9: the gateway waits longer than incubator-1's whole turn, so the agent's own fallback arrives first."""
    from llm.agent import GATEWAY_CHAT_S, TURN_S
    assert gw.CHAT_DEADLINE_S == GATEWAY_CHAT_S > TURN_S + 2.0
    assert chat_cli.ANSWER_S > gw.CHAT_DEADLINE_S
