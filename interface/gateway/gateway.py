"""The gateway: the website's only door into JARVIS (plan section 4.4, W1), on 127.0.0.1 (`gateway` in ports.toml).

It follows NATS and streams the website one farm snapshot at a time, at most MAX_SNAPSHOTS_PER_S per second (the
latest state wins, nothing queues up), and forwards only the farm-clock actions to the Matrix API. Step 6A built this
skeleton, 6W the demo faults, 6C the chat, audit, acknowledgement and mentor routes; voice comes in 6E.

    GET  /health          the gateway, its NATS link, the Matrix API and the nodes it hears
    WS   /ws/farm         FarmSnapshot messages (contract below; the website checks each one with zod)
    POST /sim/{action}    start | pause | reset | speed {"speed": S} → the Matrix API (S one of ALLOWED_SPEEDS)
    POST /faults/{id}     one of the four demo faults → the Matrix API (6W); FaultResult, or its 404/409 with the reason
    WS   /ws/chat         ChatSend in; ChatAccepted, that request's ChatChunks and ChatDone, ChatError, and every
                          OwnerAlert out (6C; incubator-1 over jarvis.incubator-1.chat / .reply / .alert)
    WS   /ws/audit        every AuditRecord from jarvis.*.audit, at most AUDIT_PER_S a second (oldest dropped first)
    GET  /mentor/llm-failures   the current LlmFailureSwitches (test only)
    POST /mentor/llm-failures   LlmFailureBody → LlmFailureSwitches on mentor.llm_failures, republished every
                                MENTOR_REPUBLISH_S so a restarted node learns it
    POST /alerts/{node}/{incident_id}/ack   AckBody (the owner's confirm click) → incubator-1 checks the token
                                (jarvis.incubator-1.ack) → the Matrix API; AckResponse with the stage reached
    WS   /ws/voice        every VoiceStatus from jarvis.incubator-1.voice (6E), the latest one first
    POST /voice/settings  VoiceSettings → jarvis.incubator-1.voice_settings (request-reply) → VoiceSettingsResult;
                          503 with the reason when incubator-1 does not answer in time
    POST /voice/ptt       PttBody → PushToTalk on jarvis.incubator-1.ptt; OkResponse
    POST /auth/signup | /auth/signin | /auth/heartbeat | /auth/signout, GET /auth/me   the accounts and the two seats
                          (accounts.py). With accounts on (the default when run as a program) every other route except
                          /health needs a seated session: `Authorization: Bearer <token>`, or `?token=` on a socket.

Mentor fault clicks never go on NATS (ground rule 4). Only the website's own origins may connect (Q7).

Usage: python interface/gateway/gateway.py [--nats URL] [--matrix URL] [--port P] [--logs DIR | --no-logs]
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import sys
import time
import tomllib
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_NODE = ROOT / "nodes" / "incubator-1"            # the four copies are identical (tests/test_nodes_in_sync.py)
if str(REFERENCE_NODE) not in sys.path:
    sys.path.insert(0, str(REFERENCE_NODE))
if str(Path(__file__).resolve().parent) not in sys.path:     # accounts.py, beside this file
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import (FastAPI, HTTPException, Path as PathParam, Request, Response, WebSocket,  # noqa: E402
                     WebSocketDisconnect)
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import (BaseModel, ConfigDict, Field, JsonValue, RootModel, ValidationError,  # noqa: E402
                      field_validator, model_validator)

from accounts import AccountError, Accounts, Session  # noqa: E402
from comms.jsonlog import log, program_logger  # noqa: E402
from comms.nats_link import NatsLink, RequestFailed  # noqa: E402
from contracts import (CLIENT_MSG_ID, CONTROL_CHARACTERS, CONVERSATION_ID, FARM_LINKS, FARM_SIM,  # noqa: E402
                       FARM_TIME, FEED_ID, MACHINES, MENTOR_LLM_FAILURES, NODE_IDS, REQUEST_ID, AckCommand, AckResult,
                       AuditRecord, Capabilities, ChatChunk, ChatDone, ChatRequest, Contract, ContractError,
                       FarmAssessment, FarmEvent, FarmStatus, FaultResult, Heartbeat, LinkStatus, LlmFailureSwitches,
                       Machine, NodeId, OwnerAlert, PushToTalk, ScenarioId, SimState, VoiceSettings, VoiceSettingsResult,
                       VoiceStatus, decode, farm_subject, jarvis_subject, utc_now)

MAX_SNAPSHOTS_PER_S = 5.0
EVENTS_SHOWN = 50
KEEPALIVE_S = 5.0                                          # a snapshot at least this often, changed or not
RETIRED_FEEDS = 16                                         # replaced farm runs remembered, to drop their late messages
SILENT_AFTER_S = 30.0
MATRIX_TIMEOUT_S = 5.0
MISSING_PEER_S = 86_400.0                                 # a farm day: the cut lasts until the next reset
ALLOWED_SPEEDS = (1.0, 10.0, 30.0, 60.0, 120.0)            # the test feed's 1x and the website's 10x-120x
WEBSITE_ORIGINS = ("http://127.0.0.1:{port}", "http://localhost:{port}")
# 6C: chat, audit, acknowledgement, mentor switches
CHAT_DEADLINE_S = 30.0                                     # ChatAccepted → ChatDone; the agent's own turn deadline is 25 s
AUDIT_PER_S = 20.0                                         # audit records sent per second per /ws/audit socket
AUDIT_QUEUE = 40                                           # audit records waiting per socket (2 s); oldest dropped first
SOCKET_QUEUE = 256                                         # messages waiting per /ws/chat socket; oldest dropped first
MAX_CHAT_MESSAGE = 16_384                                  # characters in one /ws/chat message (a ChatSend is far less)
MENTOR_REPUBLISH_S = 10.0                                  # the current LlmFailureSwitches again, for a restarted node
ACK_TIMEOUT_S = 5.0                                        # incubator-1's answer to an AckCommand
VOICE_TIMEOUT_S = 3.0                                      # incubator-1's answer to VoiceSettings
VOICE_QUEUE = 16                                           # VoiceStatus messages waiting per /ws/voice socket
IDLE_CHECK_S = 1.0                                         # a socket's sender wakes at least this often
ACCOUNTS_DB = ROOT / "interface" / "gateway" / "data" / "accounts.sqlite"
OPEN_PATHS = ("/health", "/auth/signup", "/auth/signin")
INCIDENT_ID = r"^[^/\\\x00-\x1f]{1,80}$"                    # a path segment: no slash or backslash, no control character


class NodeView(BaseModel):
    """One node as the website sees it: its machine's readings, its reasoning's output and its liveness."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    id: NodeId
    machine: Machine
    alive: bool
    heartbeat_age_s: float | None = Field(default=None, ge=0.0)
    known: list[NodeId]
    capabilities: Capabilities | None = None
    status: FarmStatus | None = None
    assessment: FarmAssessment | None = None


class FarmSnapshot(Contract):
    """`/ws/farm`: the whole farm as one message. The website maps it onto its FarmState (6W)."""

    type: Literal["farm_snapshot"] = "farm_snapshot"
    sent_utc: str
    sequence: int = Field(ge=0)
    sim: SimState | None
    links: LinkStatus | None
    nodes: dict[NodeId, NodeView]
    events: list[FarmEvent] = Field(max_length=EVENTS_SHOWN)


class SpeedBody(BaseModel):
    """POST /sim/speed."""

    model_config = ConfigDict(extra="forbid", strict=True)
    speed: float


# --- the website's chat, acknowledgement, mentor and voice messages (6C, 6E; fixed by the senior, see contracts.py) ---


class ChatSend(Contract):
    """`/ws/chat`, website → gateway: one message the owner typed. A null conversation_id starts a new conversation;
    the website then sends the conversation_id of the ChatDone it received."""

    type: Literal["chat_send"] = "chat_send"
    client_msg_id: str = Field(pattern=CLIENT_MSG_ID)
    conversation_id: str | None = Field(default=None, pattern=CONVERSATION_ID)
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def _text_is_printable(cls, value: str) -> str:
        if re.search(CONTROL_CHARACTERS, value):
            raise ValueError("control characters are not allowed")
        return value


class ChatAccepted(Contract):
    """`/ws/chat`, gateway → website: the message was passed to incubator-1 under this request id."""

    type: Literal["chat_accepted"] = "chat_accepted"
    client_msg_id: str = Field(pattern=CLIENT_MSG_ID)
    request_id: str = Field(pattern=REQUEST_ID)


class ChatError(Contract):
    """`/ws/chat`, gateway → website: a message could not be handled (one question at a time, an invalid message,
    incubator-1 not answering within the gateway's deadline...)."""

    type: Literal["chat_error"] = "chat_error"
    client_msg_id: str | None = Field(default=None, pattern=CLIENT_MSG_ID)
    request_id: str | None = Field(default=None, pattern=REQUEST_ID)
    reason: str = Field(min_length=1, max_length=300)


class AckBody(BaseModel):
    """POST /alerts/{node}/{incident_id}/ack: the confirm token of the AckOffer the owner clicked."""

    model_config = ConfigDict(extra="forbid", strict=True)
    confirm_token: str = Field(min_length=1, max_length=256)
    feed_id: str = Field(pattern=FEED_ID)                   # the AckOffer's farm run (rule 7)


class AckResponse(Contract):
    """The answer to POST /alerts/{node}/{incident_id}/ack. `stage` says how far it got: refused at the token check
    (incubator-1), refused or unreachable at the Matrix API, or done."""

    type: Literal["ack_response"] = "ack_response"
    node: NodeId
    incident_id: str = Field(min_length=1, max_length=80)
    ok: bool
    stage: Literal["token", "matrix", "done"]
    reason: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _consistent(self) -> AckResponse:
        if self.ok != (self.stage == "done") or self.ok != (self.reason == ""):
            raise ValueError("an acknowledgement is done with no reason, or stopped at a stage with a reason")
        return self


class LlmFailureBody(BaseModel):
    """POST /mentor/llm-failures: the switches to set (the gateway answers with the LlmFailureSwitches it sent)."""

    model_config = ConfigDict(extra="forbid", strict=True)
    llm_timeout: list[NodeId]
    broken_json: list[NodeId]
    silent_peer: list[NodeId]
    server_crash: list[NodeId]


class PttBody(BaseModel):
    """POST /voice/ptt: the website's talk button."""

    model_config = ConfigDict(extra="forbid", strict=True)
    pressed: bool


class SignUpBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)
    nickname: str = Field(max_length=64)
    password: str = Field(max_length=256)
    confirm: str = Field(max_length=256)
    mentor_key: str = Field(default="", max_length=256)


class SignInBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


def _client(request: Request | WebSocket) -> str:
    """Who is asking, for the try limits: the visitor address Cloudflare gives through the tunnel, else the socket's."""
    forwarded = request.headers.get("cf-connecting-ip", "")[:64]
    return forwarded or (request.client.host if request.client else "unknown")


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else None


def _person(session: Session) -> dict[str, str]:
    return {"user_id": session.user_id, "username": session.username, "nickname": session.nickname,
            "role": session.role}


class OkResponse(Contract):
    """A plain answer: done, or refused with a reason (POST /voice/ptt)."""

    type: Literal["ok_response"] = "ok_response"
    ok: bool
    reason: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _ok_or_reason(self) -> OkResponse:
        if self.ok != (self.reason == ""):
            raise ValueError("ok, or refused with a reason")
        return self


#: What the website receives (besides FarmSnapshot) or sends, for the JSON Schema export and the zod test.
#: Streams: /ws/chat (ChatAccepted, ChatChunk, ChatDone, ChatError, OwnerAlert), /ws/audit (AuditRecord),
#: /ws/voice (VoiceStatus). Routes: POST /faults/{scenario_id} → FaultResult; POST /alerts/{node}/{incident_id}/ack →
#: AckResponse; GET and POST
#: /mentor/llm-failures → LlmFailureSwitches; POST /voice/settings (VoiceSettings) → VoiceSettingsResult;
#: POST /voice/ptt → OkResponse. The HTTP bodies AckBody, LlmFailureBody and PttBody are plain JSON objects.
WEBSITE_MODELS: tuple[type[BaseModel], ...] = (ChatSend, ChatAccepted, ChatChunk, ChatDone, ChatError, OwnerAlert,
                                               AckResponse, AuditRecord, LlmFailureSwitches, VoiceStatus,
                                               VoiceSettings, VoiceSettingsResult, OkResponse, FaultResult)


# --- 6C: the gateway's own helpers for the chat, audit and acknowledgement routes (not on the wire) ---


class ReplyPiece(RootModel[Annotated[ChatChunk | ChatDone, Field(discriminator="type")]]):
    """What `jarvis.incubator-1.reply` carries: a ChatChunk or a ChatDone (one subscription for both)."""


class MatrixAck(BaseModel):
    """The Matrix API's answer to POST /alerts/{node}/{incident_id}/ack (P2 section 4, step 5)."""

    model_config = ConfigDict(extra="forbid", strict=True)
    node: NodeId
    incident_id: str = Field(min_length=1, max_length=80)
    acknowledged: int = Field(ge=1)
    farm_time: str = Field(pattern=FARM_TIME)


class Outbox:
    """One socket's bounded queue: when it is full, the oldest message is dropped and counted."""

    def __init__(self, size: int) -> None:
        self.items: deque[BaseModel] = deque(maxlen=size)
        self.dropped = 0
        self.sent = 0
        self._ready = asyncio.Event()

    def put(self, message: BaseModel) -> bool:
        """Queue one message; True when the oldest had to be dropped to make room."""
        dropped = len(self.items) == self.items.maxlen
        if dropped:
            self.dropped += 1
        self.items.append(message)
        self._ready.set()
        return dropped

    async def wait(self, timeout_s: float) -> bool:
        """Wait at most `timeout_s` for a message; True when one is queued."""
        if not self.items:
            self._ready.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._ready.wait(), timeout=timeout_s)
        return bool(self.items)


class ChatSession:
    """One open /ws/chat socket: its outbox and its one question in flight (request id, client message id, timer)."""

    def __init__(self) -> None:
        self.outbox = Outbox(SOCKET_QUEUE)
        self.request_id: str | None = None
        self.client_msg_id: str | None = None
        self.timer: asyncio.TimerHandle | None = None

    def finish(self) -> None:
        """The question in flight is over (answered, timed out, or the socket closed)."""
        if self.timer is not None:
            self.timer.cancel()
        self.request_id = self.client_msg_id = None
        self.timer = None


def _client_msg_id(raw: str) -> str | None:
    """The client_msg_id of an invalid ChatSend, when it has a valid one, so the website can match the error."""
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    found = value.get("client_msg_id") if isinstance(value, dict) else None
    return found if isinstance(found, str) and re.fullmatch(CLIENT_MSG_ID, found) else None


def _short(reason: str, limit: int = 300) -> str:
    """A reason that fits the contracts' 300 characters."""
    return reason if len(reason) <= limit else reason[:limit - 3] + "..."


class FarmView:
    """The gateway's picture of the farm, updated from NATS."""

    def __init__(self) -> None:
        self.sim: SimState | None = None
        self.links: LinkStatus | None = None
        self.status: dict[NodeId, FarmStatus] = {}
        self.assessment: dict[NodeId, FarmAssessment] = {}
        self.heartbeats: dict[NodeId, tuple[Heartbeat, float]] = {}
        self.capabilities: dict[NodeId, Capabilities] = {}
        self.events: deque[FarmEvent] = deque(maxlen=EVENTS_SHOWN)
        self.feed_id: str | None = None                      # the farm run shown (rule 7: never mix runs)
        self.retired_feeds: deque[str] = deque(maxlen=RETIRED_FEEDS)
        self.version = 0                                     # bumps on every change
        self.sequence = 0

    def changed(self) -> None:
        self.version += 1

    def follow(self, feed_id: str) -> bool:
        """Track the farm run, as node.py does. A message from a new run (a reset or a farm restart) clears the old
        run's states, assessments and events; a late message from a replaced run is dropped (False)."""
        if feed_id == self.feed_id:
            return True
        if feed_id in self.retired_feeds:
            return False
        if self.feed_id is not None:
            self.retired_feeds.append(self.feed_id)
        self.feed_id = feed_id
        self.status.clear()
        self.assessment.clear()
        self.events.clear()
        self.sim = None
        return True

    def alive_nodes(self) -> list[NodeId]:
        """Nodes whose heartbeat arrived within the silence limit."""
        now = time.monotonic()
        return [node for node in NODE_IDS
                if (beat := self.heartbeats.get(node)) is not None and now - beat[1] <= SILENT_AFTER_S]

    def snapshot(self) -> FarmSnapshot:
        now = time.monotonic()
        nodes: dict[NodeId, NodeView] = {}
        for node in NODE_IDS:
            beat = self.heartbeats.get(node)
            age = now - beat[1] if beat else None
            nodes[node] = NodeView(id=node, machine=MACHINES[node], alive=age is not None and age <= SILENT_AFTER_S,
                                   heartbeat_age_s=round(age, 3) if age is not None else None,
                                   known=list(beat[0].known) if beat else [],
                                   capabilities=self.capabilities.get(node), status=self.status.get(node),
                                   assessment=self.assessment.get(node))
        self.sequence += 1
        return FarmSnapshot(sent_utc=utc_now(), sequence=self.sequence, sim=self.sim, links=self.links, nodes=nodes,
                            events=list(self.events))


class Gateway:
    """The gateway's state: its NATS link, its farm view, its Matrix API client."""

    def __init__(self, link: NatsLink, matrix_url: str, *, logger: logging.Logger,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.link = link
        self.logger = logger
        self.view = FarmView()
        self.matrix = httpx.AsyncClient(base_url=matrix_url.rstrip("/"), timeout=MATRIX_TIMEOUT_S, transport=transport)
        # 6C
        self.chats: set[ChatSession] = set()
        self.pending: dict[str, ChatSession] = {}             # request id → the socket that asked
        self.audits: set[Outbox] = set()
        self.switches = LlmFailureSwitches(set_utc=utc_now())
        self.chat_dropped = 0                                 # messages dropped from full /ws/chat outboxes
        self.audit_dropped = 0                                # audit records dropped from full /ws/audit outboxes
        self.late_pieces = 0                                  # reply pieces of no request in flight (late or unknown)
        self._republisher: asyncio.Task[None] | None = None
        # 6E
        self.voices: set[Outbox] = set()
        self.voice_status: VoiceStatus | None = None

    async def start(self) -> None:
        """Subscribe to everything the website shows."""
        view = self.view

        async def on_status(message: FarmStatus, subject: str) -> None:
            if view.follow(message.feed_id):
                view.status[message.node] = message
                view.changed()

        async def on_assessment(message: FarmAssessment, subject: str) -> None:
            if view.follow(message.feed_id):
                view.assessment[message.node] = message
                view.changed()

        async def on_event(message: FarmEvent, subject: str) -> None:
            if view.follow(message.feed_id):
                view.events.append(message)
                view.changed()

        async def on_sim(message: SimState, subject: str) -> None:
            if view.follow(message.feed_id):
                view.sim = message
                view.changed()

        async def on_links(message: LinkStatus, subject: str) -> None:
            view.links = message
            view.changed()

        async def on_heartbeat(message: Heartbeat, subject: str) -> None:
            view.heartbeats[message.node] = (message, time.monotonic())
            view.changed()

        async def on_capabilities(message: Capabilities, subject: str) -> None:
            view.capabilities[message.node] = message
            view.changed()

        await self.link.subscribe(farm_subject("*", "status"), FarmStatus, on_status)
        await self.link.subscribe(farm_subject("*", "assessment"), FarmAssessment, on_assessment)
        await self.link.subscribe(farm_subject("*", "events"), FarmEvent, on_event)
        await self.link.subscribe(FARM_SIM, SimState, on_sim)
        await self.link.subscribe(FARM_LINKS, LinkStatus, on_links)
        await self.link.subscribe(jarvis_subject("*", "heartbeat"), Heartbeat, on_heartbeat)
        await self.link.subscribe(jarvis_subject("*", "capabilities"), Capabilities, on_capabilities)
        # 6C
        await self.link.subscribe(jarvis_subject("incubator-1", "reply"), ReplyPiece, self._on_reply)
        await self.link.subscribe(jarvis_subject("incubator-1", "alert"), OwnerAlert, self._on_alert)
        await self.link.subscribe(jarvis_subject("*", "audit"), AuditRecord, self._on_audit)
        # 6E
        await self.link.subscribe(jarvis_subject("incubator-1", "voice"), VoiceStatus, self._on_voice)
        self._republisher = asyncio.create_task(self._republish())

    async def close(self) -> None:
        if self._republisher is not None:
            self._republisher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._republisher
        for session in list(self.chats):
            session.finish()
        self.pending.clear()
        await self.matrix.aclose()
        await self.link.close()

    # --- 6C: chat ---

    def to_chat(self, session: ChatSession, message: BaseModel) -> None:
        if session.outbox.put(message):
            self.chat_dropped += 1

    async def chat_message(self, session: ChatSession, raw: str, *, priority: Literal["mentor", "user"] = "user") -> None:
        """One message from a /ws/chat socket: a ChatSend becomes a ChatRequest to incubator-1; anything else, a second
        question while one is in flight, or no NATS is a ChatError on that socket."""
        try:
            send = decode(ChatSend, raw)
        except ContractError as error:
            self.to_chat(session, ChatError(client_msg_id=_client_msg_id(raw), reason=_short(str(error))))
            return
        if session.request_id is not None:
            self.to_chat(session, ChatError(client_msg_id=send.client_msg_id, reason="one question at a time"))
            return
        if not self.link.connected:
            self.to_chat(session, ChatError(client_msg_id=send.client_msg_id,
                                            reason="incubator-1 is unreachable: the gateway is not connected to NATS"))
            return
        request_id = f"c-{secrets.token_hex(6)}"
        session.request_id, session.client_msg_id = request_id, send.client_msg_id
        session.timer = asyncio.get_running_loop().call_later(CHAT_DEADLINE_S, self._chat_expired, session,
                                                              request_id)
        self.pending[request_id] = session
        self.to_chat(session, ChatAccepted(client_msg_id=send.client_msg_id, request_id=request_id))
        await self.link.publish(jarvis_subject("incubator-1", "chat"),
                                ChatRequest(request_id=request_id, conversation_id=send.conversation_id, text=send.text,
                                            source="chat", sent_utc=utc_now(), priority=priority),
                                request_id=request_id)
        log(self.logger, logging.INFO, "chat question passed to incubator-1", request_id=request_id)

    def _chat_expired(self, session: ChatSession, request_id: str) -> None:
        """No ChatDone within CHAT_DEADLINE_S: the socket hears why, and later pieces of that request are dropped."""
        if self.pending.get(request_id) is not session:
            return
        del self.pending[request_id]
        client_msg_id = session.client_msg_id
        session.finish()
        self.to_chat(session, ChatError(client_msg_id=client_msg_id, request_id=request_id,
                                        reason=f"incubator-1 did not answer within {CHAT_DEADLINE_S:g} s"))
        log(self.logger, logging.WARNING, "chat question timed out", request_id=request_id, deadline_s=CHAT_DEADLINE_S)

    def chat_closed(self, session: ChatSession) -> None:
        """A /ws/chat socket closed: forget it and its question in flight."""
        self.chats.discard(session)
        if session.request_id is not None:
            self.pending.pop(session.request_id, None)
        session.finish()
        if session.outbox.dropped:
            log(self.logger, logging.WARNING, "chat socket dropped messages", dropped=session.outbox.dropped)

    async def _on_reply(self, message: ReplyPiece, subject: str) -> None:
        piece = message.root
        session = self.pending.get(piece.request_id)
        if session is None:                                   # too late, or another program's request
            self.late_pieces += 1
            return
        if isinstance(piece, ChatDone):
            del self.pending[piece.request_id]
            session.finish()
        self.to_chat(session, piece)

    async def _on_alert(self, message: OwnerAlert, subject: str) -> None:
        for session in list(self.chats):
            self.to_chat(session, message)
        log(self.logger, logging.INFO, "owner alert relayed", alert_id=message.alert_id, sockets=len(self.chats))

    # --- 6E: voice ---

    async def _on_voice(self, message: VoiceStatus, subject: str) -> None:
        self.voice_status = message
        for outbox in list(self.voices):
            outbox.put(message)

    async def voice_settings(self, settings: VoiceSettings) -> VoiceSettingsResult:
        """Pass the owner's choice to incubator-1; HTTPException(503) when it does not answer in time."""
        try:
            return await self.link.request(jarvis_subject("incubator-1", "voice_settings"), settings,
                                           VoiceSettingsResult, timeout_s=VOICE_TIMEOUT_S)
        except RequestFailed as error:
            raise HTTPException(503, f"incubator-1's voice did not answer ({error.kind}): {error.detail}"[:300]) \
                from error

    async def push_to_talk(self, pressed: bool) -> OkResponse:
        if not self.link.connected:
            return OkResponse(ok=False, reason="the gateway is not connected to NATS")
        await self.link.publish(jarvis_subject("incubator-1", "ptt"), PushToTalk(pressed=pressed, sent_utc=utc_now()))
        return OkResponse(ok=True)

    # --- 6C: audit ---

    async def _on_audit(self, message: AuditRecord, subject: str) -> None:
        for outbox in list(self.audits):
            if outbox.put(message):
                self.audit_dropped += 1

    # --- 6C: the mentor's LLM failure switches (test only) ---

    async def set_switches(self, body: LlmFailureBody) -> LlmFailureSwitches:
        """Publish new switches (ValidationError for a node named twice)."""
        switches = LlmFailureSwitches(**body.model_dump(), set_utc=utc_now())
        self.switches = switches
        await self.link.publish(MENTOR_LLM_FAILURES, switches)
        log(self.logger, logging.INFO, "LLM failure switches set", **body.model_dump())
        return switches

    async def _republish(self) -> None:
        """The current switches again every MENTOR_REPUBLISH_S, so a node that restarted learns them."""
        while True:
            await asyncio.sleep(MENTOR_REPUBLISH_S)
            if self.link.connected:
                await self.link.publish(MENTOR_LLM_FAILURES, self.switches)

    # --- 6C: acknowledging an alert (ground rule 3: the owner's words, then the owner's click) ---

    async def acknowledge(self, node: NodeId, incident_id: str, confirm_token: str, feed_id: str) -> AckResponse:
        """incubator-1 checks the token; only then is the Matrix API told. The answer says how far it got."""

        def stopped(stage: Literal["token", "matrix"], reason: str) -> AckResponse:
            log(self.logger, logging.WARNING, "acknowledgement stopped", node=node, incident_id=incident_id,
                stage=stage, reason=reason)
            return AckResponse(node=node, incident_id=incident_id, ok=False, stage=stage, reason=_short(reason))

        request_id = f"k-{secrets.token_hex(6)}"
        command = AckCommand(request_id=request_id, node=node, incident_id=incident_id, feed_id=feed_id,
                             confirm_token=confirm_token,
                             sent_utc=utc_now())
        try:
            result = await self.link.request(jarvis_subject("incubator-1", "ack"), command, AckResult,
                                             timeout_s=ACK_TIMEOUT_S, request_id=request_id)
        except RequestFailed as error:
            return stopped("token", f"incubator-1 did not confirm the token ({error.kind}): {error.detail}")
        if (result.request_id, result.node, result.incident_id) != (request_id, node, incident_id):
            return stopped("token", "incubator-1 answered for another acknowledgement")
        if not result.ok:
            return stopped("token", f"incubator-1 refused: {result.reason}")
        try:
            response = await self.matrix.post(f"/alerts/{node}/{quote(incident_id, safe='')}/ack",
                                              params={"feed_id": feed_id})
        except httpx.TimeoutException:
            return stopped("matrix", f"the Matrix API did not answer within {MATRIX_TIMEOUT_S:g} s")
        except httpx.HTTPError as error:
            return stopped("matrix", f"the Matrix API is unreachable: {type(error).__name__}")
        if response.status_code in (404, 409):
            return stopped("matrix", _matrix_reason(response))
        if response.status_code != 200:
            return stopped("matrix", f"the Matrix API answered HTTP {response.status_code}")
        try:
            marked = MatrixAck.model_validate_json(response.content)
        except ValidationError:
            return stopped("matrix", "the Matrix API's answer is not a valid acknowledgement")
        if (marked.node, marked.incident_id) != (node, incident_id):
            return stopped("matrix", "the Matrix API acknowledged another incident")
        log(self.logger, logging.INFO, "alert acknowledged", node=node, incident_id=incident_id, request_id=request_id,
            acknowledged=marked.acknowledged, farm_time=marked.farm_time)
        return AckResponse(node=node, incident_id=incident_id, ok=True, stage="done")

    async def matrix_health(self) -> str:
        try:
            response = await self.matrix.get("/health")
            return "ok" if response.status_code == 200 else f"HTTP {response.status_code}"
        except httpx.HTTPError as error:
            return f"unreachable ({type(error).__name__})"

    async def sim_action(self, action: str, speed: float | None) -> dict[str, Any]:
        """Forward one farm-clock action to the Matrix API; raises HTTPException with the reason."""
        body = {"speed": speed} if action == "speed" else None
        try:
            response = await self.matrix.post(f"/sim/{action}", json=body)
        except httpx.HTTPError as error:
            raise HTTPException(503, f"the Matrix API is unreachable: {type(error).__name__}") from error
        if response.status_code != 200:
            raise HTTPException(502, f"the Matrix API answered HTTP {response.status_code}")
        try:
            state: dict[str, Any] = SimState.model_validate_json(response.content).model_dump(mode="json")
        except ValidationError as error:
            raise HTTPException(502, "the Matrix API's answer is not a valid SimState") from error
        log(self.logger, logging.INFO, "farm clock action", action=action, speed=speed)
        return state

    async def inject_fault(self, scenario_id: ScenarioId) -> dict[str, Any]:
        """Forward one demo fault to the Matrix API (never NATS: ground rule 4). The Matrix's 404 and 409 are passed on
        with their reason; unreachable is 503; any other answer, or one that is not a FaultResult, is 502."""
        try:
            response = await self.matrix.post(f"/faults/{scenario_id}")
        except httpx.TimeoutException as error:
            raise HTTPException(504, "the Matrix API did not answer in time; the fault may have started") from error
        except httpx.HTTPError as error:
            raise HTTPException(503, f"the Matrix API is unreachable: {type(error).__name__}") from error
        if response.status_code in (404, 409):
            raise HTTPException(response.status_code, _matrix_reason(response))
        if response.status_code != 200:
            raise HTTPException(502, f"the Matrix API answered HTTP {response.status_code}")
        try:
            result: dict[str, Any] = FaultResult.model_validate_json(response.content).model_dump(mode="json")
        except ValidationError as error:
            raise HTTPException(502, "the Matrix API's answer is not a valid FaultResult") from error
        log(self.logger, logging.INFO, "demo fault forwarded", scenario_id=scenario_id, feed_id=result["feed_id"])
        return result


    async def cut_link(self, node: NodeId) -> dict[str, Any]:
        """A missing peer (the fault menu, decision 0023): the Matrix cuts that node's radio for MISSING_PEER_S farm
        seconds, in effect until the next reset. Answers as inject_fault does, with the Matrix's LinkStatus."""
        try:
            response = await self.matrix.post(f"/links/{node}/cut", json={"seconds": MISSING_PEER_S})
        except httpx.HTTPError as error:
            raise HTTPException(503, f"the Matrix API is unreachable: {type(error).__name__}") from error
        if response.status_code != 200:
            raise HTTPException(502, f"the Matrix API answered HTTP {response.status_code}")
        try:
            result: dict[str, Any] = LinkStatus.model_validate_json(response.content).model_dump(mode="json")
        except ValidationError as error:
            raise HTTPException(502, "the Matrix API's answer is not a valid LinkStatus") from error
        log(self.logger, logging.INFO, "missing peer forwarded", node=node)
        return result


def _matrix_reason(response: httpx.Response) -> str:
    """The Matrix API's own reason (FastAPI's {"detail": ...}), or its status."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return detail if isinstance(detail, str) and detail else f"the Matrix API answered HTTP {response.status_code}"


def create_app(gateway: Gateway, *, website_port: int = 5173, accounts: Accounts | None = None,
               extra_origins: tuple[str, ...] = ()) -> FastAPI:
    """The gateway's routes (see the module docstring). Without `accounts` every route is open (tests, programs)."""
    origins = [pattern.format(port=website_port) for pattern in WEBSITE_ORIGINS] + list(extra_origins)
    app = FastAPI(title="JARVIS gateway", version="0.6.0", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def seated_only(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """With accounts on, only a seated session reaches the farm, the chat and the voice."""
        if accounts is None or request.method == "OPTIONS" or request.url.path in OPEN_PATHS:
            return await call_next(request)
        try:
            session = accounts.check(_bearer(request), heartbeat=request.url.path == "/auth/heartbeat")
        except AccountError as error:
            return JSONResponse({"detail": error.message}, status_code=error.status)
        if request.url.path.startswith("/mentor/") and request.method == "POST" and session.role != "mentor":
            return JSONResponse({"detail": "only a mentor may break the language models"}, status_code=403)
        request.state.session = session
        if request.url.path.startswith("/faults/"):
            accounts.record(session.user_id, "fault", request.url.path.removeprefix("/faults/"))
        return await call_next(request)

    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "Authorization"])

    @app.middleware("http")
    async def same_site_only(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """A POST without a body is a CORS "simple request": a browser sends it from any page without asking, and
        CORS only hides the answer. So a request from a browser page that is not the website is refused here,
        before anything reaches the farm (Q7). Programs (no Origin header) are not browsers."""
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin is not None and origin not in origins:
            return JSONResponse({"detail": "only the JARVIS website may change the farm"}, status_code=403)
        return await call_next(request)

    # --- accounts ---

    @app.post("/auth/signup")
    async def sign_up(body: SignUpBody, request: Request) -> dict[str, Any]:
        if accounts is None:
            raise HTTPException(404, "accounts are off")
        try:
            await asyncio.to_thread(accounts.sign_up, body.username, body.nickname, body.password, body.confirm,
                                    body.mentor_key, _client(request))
            token, session = await asyncio.to_thread(accounts.sign_in, body.username, body.password, _client(request))
        except AccountError as error:
            raise HTTPException(error.status, error.message) from error
        return {"token": token, "user": _person(session)}

    @app.post("/auth/signin")
    async def sign_in(body: SignInBody, request: Request) -> dict[str, Any]:
        if accounts is None:
            raise HTTPException(404, "accounts are off")
        try:
            token, session = await asyncio.to_thread(accounts.sign_in, body.username, body.password, _client(request))
        except AccountError as error:
            raise HTTPException(error.status, error.message) from error
        return {"token": token, "user": _person(session)}

    @app.post("/auth/heartbeat")
    async def heartbeat(request: Request) -> dict[str, Any]:
        if accounts is None:
            raise HTTPException(404, "accounts are off")
        return {"user": _person(request.state.session), "seats_taken": accounts.seats_taken()}

    @app.get("/auth/me")
    async def me(request: Request) -> dict[str, Any]:
        if accounts is None:
            raise HTTPException(404, "accounts are off")
        session: Session = request.state.session
        return {"user": _person(session), "history": accounts.history(session.user_id, 20)}

    @app.post("/auth/signout")
    async def sign_out(request: Request) -> dict[str, Any]:
        if accounts is not None:
            accounts.sign_out(_bearer(request) or "")
        return {"ok": True}

    def socket_session(websocket: WebSocket) -> Session | None:
        """The socket's seated session (None with accounts off); AccountError when it has none."""
        return None if accounts is None else accounts.check(websocket.query_params.get("token"))

    @app.get("/health")
    async def health() -> dict[str, JsonValue]:
        alive: list[JsonValue] = list(gateway.view.alive_nodes())
        return {"status": "ok", "nats": gateway.link.connected, "matrix_api": await gateway.matrix_health(),
                "nodes_alive": alive, "dropped_messages": gateway.link.dropped}

    @app.post("/sim/{action}")
    async def sim(action: Literal["start", "pause", "reset", "speed"], body: SpeedBody | None = None) -> dict[str, Any]:
        speed = None
        if action == "speed":
            if body is None or body.speed not in ALLOWED_SPEEDS:
                raise HTTPException(422, f"speed must be one of {', '.join(f'{s:g}' for s in ALLOWED_SPEEDS)}")
            speed = body.speed
        return await gateway.sim_action(action, speed)

    # --- 6W routes ---

    @app.post("/faults/{scenario_id}")
    async def fault(scenario_id: ScenarioId) -> dict[str, Any]:
        return await gateway.inject_fault(scenario_id)

    @app.post("/links/{node}/cut")
    async def cut(node: NodeId) -> dict[str, Any]:
        return await gateway.cut_link(node)

    @app.websocket("/ws/farm")
    async def farm(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if origin is not None and origin not in origins:
            await websocket.close(code=1008)                  # policy violation: not the website
            return
        try:
            socket_session(websocket)
        except AccountError:
            await websocket.close(code=4401)                  # not signed in, or the seat is gone
            return
        await websocket.accept()
        sent = -1
        last_sent = float("-inf")
        try:
            while True:
                # on every change, and at least every KEEPALIVE_S: the website takes silence for a dead gateway
                if gateway.view.version != sent or time.monotonic() - last_sent >= KEEPALIVE_S:
                    if time.monotonic() - last_sent >= KEEPALIVE_S:
                        socket_session(websocket)              # the seat is still held, or AccountError ends this
                    sent = gateway.view.version
                    last_sent = time.monotonic()
                    await websocket.send_text(gateway.view.snapshot().model_dump_json())
                await asyncio.sleep(1.0 / MAX_SNAPSHOTS_PER_S)
        except (WebSocketDisconnect, RuntimeError):
            return
        except AccountError:
            with contextlib.suppress(Exception):
                await websocket.close(code=4401)

    # --- 6C routes ---

    async def website_socket(websocket: WebSocket) -> bool:
        origin = websocket.headers.get("origin")
        if origin is not None and origin not in origins:
            await websocket.close(code=1008)
            return False
        try:
            socket_session(websocket)
        except AccountError:
            await websocket.close(code=4401)
            return False
        await websocket.accept()
        return True

    async def pump(websocket: WebSocket, outbox: Outbox, gap_s: float = 0.0) -> None:
        """Send what the outbox holds, in order; at most one message every `gap_s`."""
        while True:
            if await outbox.wait(IDLE_CHECK_S):
                await websocket.send_text(outbox.items.popleft().model_dump_json())
                outbox.sent += 1
                if gap_s:
                    await asyncio.sleep(gap_s)

    async def both(*work: Awaitable[None]) -> None:
        """Run a socket's reader and sender until either ends (a closed socket ends both)."""
        tasks = [asyncio.ensure_future(job) for job in work]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            for outcome in await asyncio.gather(*tasks, return_exceptions=True):
                if isinstance(outcome, Exception) and not isinstance(outcome, (WebSocketDisconnect, RuntimeError)):
                    log(gateway.logger, logging.ERROR, "socket task failed", error=repr(outcome))

    @app.websocket("/ws/chat")
    async def chat(websocket: WebSocket) -> None:
        if not await website_socket(websocket):
            return
        session = ChatSession()
        gateway.chats.add(session)

        async def listen() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                text = message.get("text")
                if not isinstance(text, str) or len(text) > MAX_CHAT_MESSAGE:
                    gateway.to_chat(session, ChatError(reason="a chat message is one ChatSend as JSON text"))
                    continue
                try:
                    person = socket_session(websocket)
                except AccountError:
                    await websocket.close(code=4401)
                    return
                if person is not None and accounts is not None:
                    with contextlib.suppress(ValueError, AttributeError):
                        accounts.record(person.user_id, "chat", str(json.loads(text).get("text", "")))
                await gateway.chat_message(session, text,
                                           priority="mentor" if person is not None and person.role == "mentor" else "user")

        try:
            await both(listen(), pump(websocket, session.outbox))
        finally:
            gateway.chat_closed(session)

    @app.websocket("/ws/audit")
    async def audit(websocket: WebSocket) -> None:
        if not await website_socket(websocket):
            return
        outbox = Outbox(AUDIT_QUEUE)
        gateway.audits.add(outbox)

        async def listen() -> None:                           # the website sends nothing; this notices the close
            while (await websocket.receive())["type"] != "websocket.disconnect":
                pass

        try:
            await both(listen(), pump(websocket, outbox, 1.0 / AUDIT_PER_S))
        finally:
            gateway.audits.discard(outbox)

    @app.get("/mentor/llm-failures")
    async def current_failures() -> dict[str, Any]:
        return gateway.switches.model_dump(mode="json")

    @app.post("/mentor/llm-failures")
    async def set_failures(body: LlmFailureBody) -> dict[str, Any]:
        try:
            switches = await gateway.set_switches(body)
        except ValidationError as error:
            raise HTTPException(422, _short(str(error))) from error
        return switches.model_dump(mode="json")

    @app.post("/alerts/{node}/{incident_id}/ack")
    async def acknowledge(node: NodeId, body: AckBody,
                          incident_id: Annotated[str, PathParam(pattern=INCIDENT_ID)]) -> dict[str, Any]:
        return (await gateway.acknowledge(node, incident_id, body.confirm_token, body.feed_id)).model_dump(mode="json")

    # --- 6E routes ---

    @app.websocket("/ws/voice")
    async def voice(websocket: WebSocket) -> None:
        if not await website_socket(websocket):
            return
        outbox = Outbox(VOICE_QUEUE)
        if gateway.voice_status is not None:
            outbox.put(gateway.voice_status)
        gateway.voices.add(outbox)

        async def listen() -> None:
            while (await websocket.receive())["type"] != "websocket.disconnect":
                pass

        try:
            await both(listen(), pump(websocket, outbox))
        finally:
            gateway.voices.discard(outbox)

    @app.post("/voice/settings")
    async def voice_settings(body: VoiceSettings) -> dict[str, Any]:
        return (await gateway.voice_settings(body)).model_dump(mode="json")

    @app.post("/voice/ptt")
    async def voice_ptt(body: PttBody) -> dict[str, Any]:
        return (await gateway.push_to_talk(body.pressed)).model_dump(mode="json")

    return app


def read_ports(path: Path = ROOT / "launcher" / "ports.toml") -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


async def amain(args: argparse.Namespace) -> int:
    logger = program_logger("gateway", None if args.no_logs else args.logs, echo=args.echo)
    ports = read_ports()
    host = ports["host"]
    link = NatsLink("gateway", args.nats or f"nats://{host}:{ports['nats']}", logger=logger)
    try:
        await link.connect()
    except Exception as error:                                # NoServersError and friends
        log(logger, logging.ERROR, "NATS unreachable", error=str(error))
        return 2
    gateway = Gateway(link, args.matrix or f"http://{host}:{ports['matrix_api']}", logger=logger)
    await gateway.start()
    accounts = None if args.no_accounts else Accounts(args.accounts)
    app = create_app(gateway, website_port=int(ports["website"]), accounts=accounts, extra_origins=tuple(args.origin))
    server = uvicorn.Server(uvicorn.Config(app, host=host,
                                           port=args.port or int(ports["gateway"]), log_level="warning",
                                           lifespan="off"))
    try:
        await server.serve()
    finally:
        with contextlib.suppress(Exception):
            await gateway.close()
        log(logger, logging.INFO, "gateway stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nats", help="NATS URL (default: from launcher/ports.toml)")
    parser.add_argument("--matrix", help="Matrix API URL (default: from launcher/ports.toml)")
    parser.add_argument("--port", type=int, help="gateway port (default: from launcher/ports.toml)")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--logs", type=Path, default=ROOT / "logs", help="log folder (default: logs/)")
    where.add_argument("--no-logs", action="store_true", help="no log file")
    parser.add_argument("--echo", action="store_true", help="also print the log to stderr")
    data_root = os.environ.get("JARVIS_DATA_ROOT")                   # a test run's data stays in its own folder
    parser.add_argument("--accounts", type=Path,
                        default=Path(data_root) / "gateway" / "accounts.sqlite" if data_root else ACCOUNTS_DB,
                        help="the accounts database (default: interface/gateway/data/, or under JARVIS_DATA_ROOT)")
    parser.add_argument("--no-accounts", action="store_true", help="no sign-in: every route open (tests only)")
    parser.add_argument("--origin", action="append", default=[], help="another website origin (the public tunnel)")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
