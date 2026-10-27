"""Simulated ZigBee for the questions between nodes (plan 4.2, S20): delay, loss and cut-offs, then NATS.

Every question one node asks another, and every answer, passes here first. The rules are the farm's own radio rules
(the Matrix's peer bus): a latency in farm ticks, a loss probability, and links the Matrix cuts. A tick of latency lasts
`step_seconds / speed` real seconds on the farm clock (`farm.sim`), so at 1x a one-tick hop takes 1 s and a question
with its answer about 2 s. While either end's link is cut (`farm.links`), messages are lost in the air: the asker gets
no answer and its question times out cleanly at its deadline. Observability and website traffic never comes here.

`ask()` never raises: every outcome is an AskReply, `ok` or with an error ("timeout", "link_cut" when this node's own
link is cut, "lost", "unavailable" when no program answers, "bad_request").
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import ValidationError

from comms.jsonlog import log
from comms.nats_link import NatsLink, RequestFailed
from contracts import (REQUEST_ID, AskError, AskReply, AskRequest, LinkStatus, NodeId, SimState, Topic,
                       jarvis_subject, utc_now)

DEFAULT_DEADLINE_S = 3.0                          # tool 7, ask_peer: a 3 s deadline (plan section 6)


@dataclass(frozen=True)
class RadioRules:
    """The ZigBee rules: latency in farm ticks, the chance a message is lost, the seed of that chance."""

    latency_ticks: int = 1
    loss: float = 0.0
    seed: str = "radio"

    def __post_init__(self) -> None:
        if self.latency_ticks < 0 or not 0.0 <= self.loss < 1.0:
            raise ValueError("latency must be 0 or more ticks and loss between 0 and 1")


class PeerNetwork:
    """One node's radio: it asks the other nodes and answers them, under the ZigBee rules."""

    def __init__(self, node: NodeId, link: NatsLink, *, rules: RadioRules | None = None, logger: logging.Logger,
                 step_seconds: float = 1.0, speed: float = 1.0) -> None:
        self.node: NodeId = node
        self.link = link
        self.rules = rules or RadioRules()
        self.logger = logger
        self._rng = random.Random(f"{self.rules.seed}:{node}")
        self._step_seconds = step_seconds
        self._speed = speed
        self._cut: frozenset[str] = frozenset()

    # --- the farm's view of the radio ---------------------------------------------------------------------------

    def update_links(self, status: LinkStatus) -> None:
        """Which links the Matrix has cut (`farm.links`)."""
        cut = frozenset(status.cut)
        if cut != self._cut:
            log(self.logger, logging.INFO, "radio links changed", cut=sorted(cut))
        self._cut = cut

    def update_sim(self, sim: SimState) -> None:
        """The farm clock's speed (`farm.sim`): it sets how long a tick of latency lasts in real time. The measured
        speed is used once there is one; until then (the first seconds of a run, when the farm reports 0) and while
        paused, the speed asked for. Keeping the 1x default instead made hops ten times too slow at 10x."""
        self._speed = sim.actual_speed if sim.running and sim.actual_speed > 0 else sim.target_speed

    def is_cut(self, node: str) -> bool:
        """True while that node's radio link is cut."""
        return node in self._cut

    @property
    def latency_s(self) -> float:
        """One hop's delay in real seconds."""
        return self.rules.latency_ticks * self._step_seconds / max(self._speed, 1e-6)

    def _lost(self) -> bool:
        return self.rules.loss > 0.0 and self._rng.random() < self.rules.loss

    # --- asking -------------------------------------------------------------------------------------------------

    async def ask(self, target: NodeId, topic: Topic, question: str = "", *, deadline_s: float = DEFAULT_DEADLINE_S,
                  request_id: str | None = None) -> AskReply:
        """Ask `target` one question and wait for its answer until the deadline (see the module docstring)."""
        started = time.monotonic()
        given_id = request_id
        if not request_id or not re.fullmatch(REQUEST_ID, request_id):
            request_id = f"q-{uuid.uuid4().hex[:12]}"             # the reply needs a valid id, even a refusal

        def failed(error: AskError, detail: str) -> AskReply:
            elapsed = (time.monotonic() - started) * 1000
            log(self.logger, logging.INFO, "question failed", request_id=request_id, target=target, topic=topic,
                error=error, detail=detail, elapsed_ms=round(elapsed, 1))
            return AskReply(request_id=request_id, sender=target, target=self.node, topic=topic, ok=False,
                            error=error, detail=detail[:500], elapsed_ms=elapsed)

        if target == self.node:
            return failed("bad_request", "a node does not ask itself")
        if given_id is not None and given_id != request_id:
            return failed("bad_request", f"not a valid question: the request id {given_id[:40]!r} is not valid")
        try:
            request = AskRequest(request_id=request_id, sender=self.node, target=target, topic=topic,
                                 question=question, sent_utc=utc_now(), deadline_ms=max(1, int(deadline_s * 1000)))
        except ValidationError as error:
            return failed("bad_request", f"not a valid question: {error.errors()[0]['msg']}")
        if self.is_cut(self.node):
            return failed("link_cut", f"{self.node}'s own radio link is cut")
        await asyncio.sleep(min(self.latency_s, deadline_s))           # the question in the air
        remaining = deadline_s - (time.monotonic() - started)
        if self.is_cut(target) or self._lost():
            why = f"{target}'s link is cut" if self.is_cut(target) else "the question was lost on the radio"
            await asyncio.sleep(max(0.0, remaining))
            return failed("timeout", f"no answer from {target} within {deadline_s:.1f} s ({why})")
        if remaining <= 0:
            return failed("timeout", f"no answer from {target} within {deadline_s:.1f} s")
        try:
            reply = await self.link.request(jarvis_subject(target, "ask"), request, AskReply, timeout_s=remaining,
                                            request_id=request_id)
        except RequestFailed as error:
            if error.kind == "no_responders":
                return failed("unavailable", f"no {target} program is running ({error.detail})")
            if error.kind == "timeout":
                return failed("timeout", f"no answer from {target} within {deadline_s:.1f} s")
            return failed("unavailable", error.detail)
        elapsed = (time.monotonic() - started) * 1000
        log(self.logger, logging.INFO, "question answered", request_id=request_id, target=target, topic=topic,
            ok=reply.ok, elapsed_ms=round(elapsed, 1))
        return reply.model_copy(update={"elapsed_ms": elapsed})

    # --- answering ----------------------------------------------------------------------------------------------

    async def serve(self, answer: Callable[[AskRequest], Awaitable[AskReply]]) -> None:
        """Answer the questions addressed to this node: the answer travels back under the same rules."""

        async def handle(request: AskRequest, subject: str) -> AskReply | None:
            if request.target != self.node:
                return None
            if self.is_cut(self.node) or self.is_cut(request.sender):
                log(self.logger, logging.INFO, "question not heard: a link is cut", request_id=request.request_id,
                    sender=request.sender)
                return None
            reply = await answer(request)
            await asyncio.sleep(self.latency_s)                         # the answer in the air
            if self.is_cut(self.node) or self.is_cut(request.sender) or self._lost():
                log(self.logger, logging.INFO, "answer lost on the radio", request_id=request.request_id)
                return None
            return reply

        await self.link.serve(jarvis_subject(self.node, "ask"), AskRequest, handle)
