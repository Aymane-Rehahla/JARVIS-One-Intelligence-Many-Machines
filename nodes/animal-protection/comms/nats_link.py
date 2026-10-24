"""A program's NATS connection: validated publish and subscribe, request-reply with deadlines, reconnects (S19).

Every incoming message is decoded with its contract (contracts.py); an invalid one, or one of an unknown version, is
logged and dropped, never raised (Q2). Every wait has a timeout and every subscription a pending limit (Q4). A request
id travels in the `Jarvis-Request-Id` header (Q5). Handlers run as tasks, at most `max_concurrent` at a time per
served subject, so a slow answer (the ZigBee delay) never holds up the next question.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

import nats
from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.errors import ConnectionClosedError, NoRespondersError, NoServersError
from nats.errors import TimeoutError as NatsTimeoutError
from pydantic import BaseModel

from comms.jsonlog import log
from contracts import ContractError, decode, encode

DEFAULT_URL = "nats://127.0.0.1:4222"
REQUEST_ID_HEADER = "Jarvis-Request-Id"
PENDING_MESSAGES = 1_000
PENDING_BYTES = 8 * 1024 * 1024
MAX_CONCURRENT = 16
CONNECT_ATTEMPTS = 30
FLUSH_TIMEOUT_S = 2

M = TypeVar("M", bound=BaseModel)
R = TypeVar("R", bound=BaseModel)


class RequestFailed(RuntimeError):
    """A request got no valid reply: `kind` is "timeout", "no_responders", "bad_reply" or "closed"."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


class NatsLink:
    """One program's connection to nats-server, with the contracts applied at the border."""

    def __init__(self, name: str, url: str = DEFAULT_URL, *, logger: logging.Logger, connect_timeout_s: float = 2.0,
                 reconnect_wait_s: float = 1.0, max_concurrent: int = MAX_CONCURRENT) -> None:
        self.name = name
        self.url = url
        self.logger = logger
        self._connect_timeout_s = connect_timeout_s
        self._reconnect_wait_s = reconnect_wait_s
        self._max_concurrent = max_concurrent
        self._client: Client | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self.dropped = 0                                  # invalid or over-limit messages, for health checks

    @property
    def connected(self) -> bool:
        """True while the connection is up."""
        return self._client is not None and self._client.is_connected

    @property
    def client(self) -> Client:
        if self._client is None:
            raise ConnectionClosedError()
        return self._client

    async def connect(self, *, attempts: int = CONNECT_ATTEMPTS) -> None:
        """Connect, retrying while nats-server starts (at most `attempts` tries). Raises NoServersError after that.

        Each try has its own time limit: nats-py retries a first connection forever when reconnects are unlimited,
        and unlimited reconnects are wanted once connected."""
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                self._client = await asyncio.wait_for(nats.connect(
                    self.url, name=self.name, connect_timeout=int(max(1, self._connect_timeout_s)),
                    reconnect_time_wait=int(max(1, self._reconnect_wait_s)), max_reconnect_attempts=-1,
                    allow_reconnect=True, error_cb=self._on_error, disconnected_cb=self._on_disconnected,
                    reconnected_cb=self._on_reconnected), timeout=self._connect_timeout_s + 1.0)
                log(self.logger, logging.INFO, "connected to NATS", url=self.url, attempt=attempt)
                return
            except (NoServersError, OSError, asyncio.TimeoutError) as error:
                last = error
                await asyncio.sleep(min(0.25 * attempt, 2.0))
        log(self.logger, logging.ERROR, "could not connect to NATS", url=self.url, attempts=attempts, error=str(last))
        raise NoServersError()

    async def close(self) -> None:
        """Drain and close the connection; cancel handlers still running."""
        for task in list(self._tasks):
            task.cancel()
        if self._client is not None and not self._client.is_closed:
            try:
                await asyncio.wait_for(self._client.drain(), timeout=2.0)
            except (asyncio.TimeoutError, ConnectionClosedError, OSError):
                await self._client.close()
        self._client = None

    async def publish(self, subject: str, message: BaseModel, *, request_id: str | None = None) -> None:
        """Publish one contract message. A lost connection is logged, not raised (the next message retries)."""
        headers = {REQUEST_ID_HEADER: request_id} if request_id else None
        try:
            await self.client.publish(subject, encode(message), headers=headers)
        except (ConnectionClosedError, OSError) as error:
            log(self.logger, logging.WARNING, "publish failed", subject=subject, error=str(error))

    async def subscribe(self, subject: str, model: type[M], handler: Callable[[M, str], Awaitable[None]]) -> None:
        """Call `handler(message, subject)` for every valid `model` on `subject` (wildcards allowed)."""

        async def on_message(msg: Msg) -> None:
            message = self._decode(model, msg)
            if message is not None:
                await self._guarded(handler(message, msg.subject), msg.subject)

        await self.client.subscribe(subject, cb=on_message, pending_msgs_limit=PENDING_MESSAGES,
                                    pending_bytes_limit=PENDING_BYTES)
        await self._registered(subject)

    async def serve(self, subject: str, model: type[M], handler: Callable[[M, str], Awaitable[BaseModel | None]]) -> None:
        """Answer requests on `subject`: each valid request runs `handler` in its own task; a returned message is
        the reply, None sends nothing (the asker then times out, as with a lost radio message)."""
        limit = asyncio.Semaphore(self._max_concurrent)

        async def respond(msg: Msg, message: M) -> None:
            async with limit:
                reply = await handler(message, msg.subject)
                if reply is not None and msg.reply:
                    try:
                        await msg.respond(encode(reply))
                    except (ConnectionClosedError, OSError) as error:
                        log(self.logger, logging.WARNING, "reply failed", subject=msg.subject, error=str(error))

        async def on_request(msg: Msg) -> None:
            message = self._decode(model, msg)
            if message is None:
                return
            if len(self._tasks) >= 4 * self._max_concurrent:
                self.dropped += 1
                log(self.logger, logging.WARNING, "too many requests in flight; dropped one", subject=msg.subject)
                return
            task = asyncio.create_task(self._guarded(respond(msg, message), msg.subject))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        await self.client.subscribe(subject, cb=on_request, pending_msgs_limit=PENDING_MESSAGES,
                                    pending_bytes_limit=PENDING_BYTES)
        await self._registered(subject)

    async def _registered(self, subject: str) -> None:
        """Wait until nats-server has the new subscription, so a message sent right after this returns reaches it
        (a peer may ask as soon as it has our capabilities). A slow flush is logged, not raised."""
        try:
            await self.client.flush(timeout=FLUSH_TIMEOUT_S)
        except (NatsTimeoutError, asyncio.TimeoutError, ConnectionClosedError, OSError) as error:
            log(self.logger, logging.WARNING, "subscription not confirmed yet", subject=subject, error=str(error))

    async def request(self, subject: str, message: BaseModel, reply_model: type[R], *, timeout_s: float,
                      request_id: str | None = None) -> R:
        """Send a request and wait at most `timeout_s` for a valid reply. Raises RequestFailed."""
        headers = {REQUEST_ID_HEADER: request_id} if request_id else None
        try:
            msg = await self.client.request(subject, encode(message), timeout=max(0.001, timeout_s), headers=headers)
        except (NatsTimeoutError, asyncio.TimeoutError) as error:
            raise RequestFailed("timeout", f"no reply on {subject} within {timeout_s:.2f} s") from error
        except NoRespondersError as error:
            raise RequestFailed("no_responders", f"no program answers on {subject}") from error
        except (ConnectionClosedError, OSError) as error:
            raise RequestFailed("closed", str(error)) from error
        reply = self._decode(reply_model, msg)
        if reply is None:
            raise RequestFailed("bad_reply", f"the reply on {subject} is not a valid {reply_model.__name__}")
        return reply

    def _decode(self, model: type[M], msg: Msg) -> M | None:
        try:
            return decode(model, msg.data)
        except ContractError as error:
            self.dropped += 1
            log(self.logger, logging.WARNING, "dropped an invalid message", subject=msg.subject, error=str(error))
            return None

    async def _guarded(self, work: Awaitable[None], subject: str) -> None:
        try:
            await work
        except asyncio.CancelledError:
            raise
        except Exception as error:                        # a handler bug must not stop the subscription (Q6)
            log(self.logger, logging.ERROR, "message handler failed", subject=subject,
                error=f"{type(error).__name__}: {error}")

    async def _on_error(self, error: Exception) -> None:
        log(self.logger, logging.WARNING, "NATS error", error=f"{type(error).__name__}: {error}")

    async def _on_disconnected(self) -> None:
        log(self.logger, logging.WARNING, "disconnected from NATS; reconnecting")

    async def _on_reconnected(self) -> None:
        log(self.logger, logging.INFO, "reconnected to NATS")
