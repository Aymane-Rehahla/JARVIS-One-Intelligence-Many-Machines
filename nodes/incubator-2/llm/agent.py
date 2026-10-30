"""incubator-1's agent (6C, decision 0018): one owner message in, verified chunks and exactly one ChatDone out.

One turn (P2 section 3):
1. the owner's text is stored as a turn and an audit `answer` step opens with the request id;
2. requests to act on a machine, call emergency services, read the CMM or change the rules are refused in code with a
   fixed reply (JARVIS talks; the machines act through their safety gate);
3. "acknowledge the HVAC alert" finds that machine's current incident in this turn's tool data and offers a single-use
   confirm button (AckOffer); only the owner's click, through the gateway, acknowledges (ground rule 3);
4. context first: incubator-1's own status and assessment are always gathered; code routes the question to the
   machines it is about (`llm/routing.py`) and asks those peers in parallel (S17);
5. the 2B model streams the reply and may call read-only tools (at most two rounds); each finished sentence is checked
   with verify.py against ALL tool results of the turn before it is sent. No unverified number or time is ever sent.
   A failing sentence stops the stream; one regeneration names the unmatched numbers; if that fails too, the reply
   ends with a fixed sentence built from the tool data (outcome "fallback");
6. every answered or fallback reply states the farm time of its newest data (one short fixed sentence is appended
   when the model left it out); passages from document searches become citations; one ChatDone ends the turn.

Deadlines nest strictly (rule 9; `test_agent::test_deadlines_nest`): the radio 3.0 s < ask_peer 3.5 s < one 2B call
10 s (first token within 4 s) < the turn 25 s < the gateway's chat deadline 30 s. The turn runs as a task feeding a
bounded queue, so a slow consumer or a stuck call never stretches the turn past its deadline: at the deadline the task
is cancelled and the reply ends with a fallback from the data gathered so far. Every failure ends in a ChatDone.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from comms.jsonlog import log
from contracts import (NODE_IDS, AckOffer, ChatChunk, ChatDone, Citation, FeedLabel, NodeId, OwnerAlert,
                       ReplyTimings, ToolUse)
from llm.engines import EngineError, assemble_tool_calls
from llm.jarvis_speaker import Persona, fixed_start, history_messages, turn_message
from llm.personality import Style, style_for
from llm.recovery import NAMES, MachineData, PeerMemory, describe, fallback_text, words
from llm.routing import ALIASES, Route, route
from llm.tools import PEER_DEADLINE_S
from llm.verify import states_farm_time, verify_reply

RADIO_S = PEER_DEADLINE_S                     # 3.0: the radio's own deadline for one question
ASK_PEER_S = PEER_DEADLINE_S + 0.5            # 3.5: the ask_peer tool's deadline (tools.py)
GATHER_S = ASK_PEER_S + 0.5                   # 4.0: all the turn's parallel questions
LLM_CALL_S = 10.0                             # one call to the 2B model
FIRST_TOKEN_S = 4.0                           # its first token
TURN_S = 25.0                                 # the whole turn
GATEWAY_CHAT_S = 30.0                         # the gateway's deadline for a ChatDone (interface/gateway)
ALERT_TEMPLATE_S = 0.5                        # an alert's template is sent within this of hearing it
ALERT_REWORD_S = 2.0                          # the model may reword it only if verified within this in total
MAX_TOOL_ROUNDS = 2
WARM_S = 30.0                    # the warm-up's own deadline (cold prefill 6.5 s, or a server still loading)
MAX_CITATIONS = 3
QUEUE_SIZE = 64
CONFIRM_SECONDS = 60

_I = re.IGNORECASE
_ACT = re.compile(r"\b(?:turn|switch|shut|power)\s+(?:it\s+|that\s+|the\s+\w+(?:\s+\w+)?\s+)?(?:off|on|down)\b|"
                  r"\b(?:stop|start|restart|reboot|disable|enable|open|close|lower|raise|increase|decrease|"
                  r"set|adjust|reset)\s+(?:the|its|that|my|all)\b|\bcut (?:the )?power\b|\bunlock\b", _I)
_EMERGENCY = re.compile(r"\bcall\s+(?:the\s+)?(?:fire\s*(?:brigade|department|service)|police|ambulance|"
                        r"civil\s+protection|emergency|112|911|999|firefighters)\b|\bdial\s+(?:112|911|999)\b", _I)
_CMM = re.compile(r"\bcmm\b|\bcognitive memory\b|\bworking memory\b|\bcmm\.sqlite\b|\byour (?:memory )?database\b", _I)
_RULES = re.compile(r"\bignore (?:all |your |the |previous |prior )*(?:rules|instructions)\b|"
                    r"\b(?:change|override|disable|bypass|forget)\s+(?:your |the )?(?:rules|safety|instructions|"
                    r"gate|guardrails)\b|\bnew (?:rules|instructions)\b|\bdeveloper mode\b|\bjailbreak\b", _I)
_ACK = re.compile(r"\backnowledge\b|\back\s+(?:the|that|it)\b|\bmark (?:it|that|the alert) (?:as )?(?:seen|read)\b|"
                  r"\bi(?:'ve| have) seen (?:the|that) alert\b", _I)
REFUSALS: dict[str, str] = {
    "act": "JARVIS talks; the machines act through their safety gate, sir. I can tell you what the machine reports, "
           "but I cannot operate it.",
    "emergency": "I cannot call anyone, sir: emergency calls come only from a machine's reasoning, through its safety "
                 "gate. If you are in danger, call the emergency services yourself now.",
    "cmm": "The machines' memories belong to their reasoning, sir; I only see what they publish, and I cannot open "
           "them.",
    "rules": "My rules are not mine to change, sir; they are kept in code, not in conversation.",
}
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "approx.", "vs.", "no.", "dr.", "mr.", "mrs.", "st.", "a.m.", "p.m.",
                  "fig.", "min.", "max.", "approx.")
_BAD_SENTENCE = re.compile(r"\[|\]|<\s*/?\s*(?:data|owner|tool)|tool results|```", _I)
# JARVIS talks and never acts (ground rule 3): a sentence in which it says it did something is never sent.
_CLAIMED_ACTION = re.compile(
    r"\b(?:I|we)(?:'ve|'d|\s+have|\s+had)?\s+(?:just\s+|already\s+|now\s+|also\s+)?"
    r"(?:stopped|switched|turned|shut|disabled|started|restarted|rebooted|opened|closed|called|phoned|rang|dialled|"
    r"dialed|contacted|notified|alerted|reset|cleared|silenced|acknowledged|adjusted|lowered|raised|activated|"
    r"deactivated|deployed|sent|ordered|triggered|isolated|unlocked|locked)\b", _I)
LLM_TOOLS = ("get_trend", "list_events", "get_incident", "search_documents", "ask_peer", "recall_conversation")


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """(finished sentences, the unfinished rest). A sentence ends at . ! ? or a blank line, followed by white space,
    and not inside a number ("37.5") or after an abbreviation ("e.g."). Each sentence keeps its trailing space."""
    done: list[str] = []
    start = 0
    index = 0
    while index < len(buffer):
        char = buffer[index]
        end: int | None = None
        if char in ".!?" and index + 1 < len(buffer) and buffer[index + 1] in " \n\t":
            word = buffer[start:index + 1].split()[-1].lower() if buffer[start:index + 1].split() else ""
            if word not in _ABBREVIATIONS and not re.fullmatch(r"\d{1,2}\.", word):
                end = index + 1
        elif char == "\n" and index + 1 < len(buffer) and buffer[index + 1] == "\n":
            end = index + 1
        if end is not None:
            stop = end
            while stop < len(buffer) and buffer[stop] in " \n\t":
                stop += 1
            if stop == len(buffer):
                break                                  # the next sentence has not started: wait for more text
            sentence = buffer[start:end].strip()
            if sentence:
                done.append(sentence + " ")
            start = stop
            index = stop
            continue
        index += 1
    return done, buffer[start:]


@dataclass
class Turn:
    """Everything one turn has gathered, sent and decided (the ChatDone is built from it)."""

    request_id: str
    started: float
    conversation_id: str = ""
    pieces: list[str] = field(default_factory=list[str])            # what the producer put on the queue
    evidence: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    machines: dict[NodeId, MachineData] = field(default_factory=dict[NodeId, MachineData])
    tools: list[ToolUse] = field(default_factory=list[ToolUse])
    citations: list[Citation] = field(default_factory=list[Citation])
    outcome: Literal["answered", "fallback", "refused", "error"] = "answered"
    reason: str = ""
    numbers_checked: int = 0
    confirm: AckOffer | None = None
    llm_ms: float = 0.0
    llm_calls: int = 0
    tools_ms: float = 0.0
    first_chunk_at: float | None = None
    tool_calls_valid: int = 0
    tool_calls_invalid: int = 0
    unverified_sentences: int = 0                                   # sentences the gate stopped (E1's "before")
    regenerated: bool = False

    def newest(self) -> tuple[str | None, FeedLabel | None]:
        """The farm time and label of the newest data of this turn (the highest tick). A silent peer's remembered
        answer is an older report, quoted with its own time, so it counts only when nothing fresh came back."""
        best: tuple[int, str, FeedLabel | None] | None = None
        fresh = [m for m in self.machines.values() if not m.remembered]
        for machine in fresh if any(m.farm_time is not None for m in fresh) else self.machines.values():
            if machine.farm_time is not None and machine.tick is not None:
                label = machine.label if machine.label in ("live: simulated brains", "live: trained brains") else None
                if best is None or machine.tick > best[0]:
                    best = (machine.tick, machine.farm_time, label)  # type: ignore[assignment]
        return (best[1], best[2]) if best else (None, None)


class Agent:
    """The `Agent` interface of P4: `reply()` and `add_alert_listener()`."""

    def __init__(self, node: Any, engine: Any, persona: Persona, *, logger: logging.Logger | None = None,
                 turn_s: float = TURN_S, llm_call_s: float = LLM_CALL_S, first_token_s: float = FIRST_TOKEN_S) -> None:
        self.node = node                              # the incubator-1 Node: tools, store, auditor, peers, feed
        self.engine = engine                          # an EngineClient (wrapped by the failure injector)
        self.persona = persona
        self.logger = logger or logging.getLogger("jarvis.agent")
        self.turn_s, self.llm_call_s, self.first_token_s = turn_s, llm_call_s, first_token_s
        self.peer_memory = PeerMemory()
        self.alert_levels: dict[tuple[str, NodeId], str] = {}         # (feed, node) -> last alert level heard
        self._listeners: list[Callable[[OwnerAlert], Awaitable[None]]] = []
        self.tool_schemas = self._tool_schemas()
        self._warming: asyncio.Future[float | None] | None = None
        self.warm_ms: float | None = None                              # the last successful warm-up, measured

    # --- the server's prompt cache ----------------------------------------------------------------------------

    async def warm(self) -> float | None:
        """Prefill the fixed start of every prompt (the persona and the tool schemas) into the server's cache, so a
        turn pays only for its own data. Measured on the RX 580 (31 Oct): 6.5 s to the first token with a cold
        cache, 2.1 s with the fixed start cached; the first-token watchdog is FIRST_TOKEN_S. None on failure."""
        started = time.monotonic()
        try:
            async for _ in self.engine.chat_stream(
                    [*fixed_start(self.persona), {"role": "user", "content": "<data>ready?</data>"}], max_tokens=1,
                    temperature=0.0, tools=self.tool_schemas, deadline_s=WARM_S, first_token_s=WARM_S):
                pass
        except EngineError as error:
            log(self.logger, logging.WARNING, "prompt cache warm-up failed", error=str(error)[:200])
            return None
        self.warm_ms = (time.monotonic() - started) * 1000
        log(self.logger, logging.INFO, "prompt cache warmed", ms=round(self.warm_ms))
        return self.warm_ms

    def rewarm(self) -> None:
        """Warm the cache in the background (at start, and after the server failed: a restarted server is cold)."""
        if self._warming is None or self._warming.done():
            self._warming = asyncio.ensure_future(self.warm())

    async def close(self) -> None:
        if self._warming is not None and not self._warming.done():
            self._warming.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._warming

    # --- the interface -----------------------------------------------------------------------------------------

    def add_alert_listener(self, listener: Callable[[OwnerAlert], Awaitable[None]]) -> None:
        """Called with every OwnerAlert incubator-1 sends (the voice box speaks them)."""
        self._listeners.append(listener)

    async def alert(self, alert: OwnerAlert) -> None:
        """Hand one OwnerAlert to every listener (the gateway's publication is the node's; see alerts.py)."""
        self.alert_levels[(alert.feed_id, alert.node)] = alert.level
        for listener in list(self._listeners):
            try:
                await listener(alert)
            except Exception:                                  # a listener never breaks the alert path
                self.logger.exception("alert listener failed")

    async def reply(self, text: str, *, request_id: str, conversation_id: str | None,
                    source: Literal["chat", "voice"]) -> AsyncIterator[ChatChunk | ChatDone]:
        """Zero or more verified ChatChunks, then exactly one ChatDone, within the turn's deadline. Never raises for an
        ordinary failure."""
        turn = Turn(request_id=request_id, started=time.monotonic())
        async with self.node.auditor.step(request_id, "answer", None, {"text": text[:500], "source": source}) as step:
            async for item in self._reply(text, conversation_id, source, turn):
                if isinstance(item, ChatDone):
                    step.done({"outcome": item.outcome, "reason": item.reason, "numbers_checked": item.numbers_checked,
                               "chunks": item.chunks, "timings": item.timings.model_dump(mode="json")},
                              item.farm_time)
                yield item

    async def _reply(self, text: str, conversation_id: str | None, source: str,
                     turn: Turn) -> AsyncIterator[ChatChunk | ChatDone]:
        request_id = turn.request_id
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=QUEUE_SIZE)
        task = asyncio.create_task(self._run(text, conversation_id, source, turn, queue))
        sent: list[str] = []
        deadline = turn.started + self.turn_s
        timed_out = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                piece = await asyncio.wait_for(queue.get(), remaining)
            except TimeoutError:
                timed_out = True
                break
            if piece is None:
                break
            if turn.first_chunk_at is None:
                turn.first_chunk_at = time.monotonic()
            sent.append(piece)
            yield ChatChunk(request_id=request_id, seq=len(sent) - 1, text=piece)
        if not task.done():
            task.cancel()
        results = await asyncio.gather(task, return_exceptions=True)
        if timed_out:
            turn.outcome, turn.reason = "fallback", f"the turn's deadline ({self.turn_s:g} s) passed"
            closing = fallback_text(list(turn.machines.values()), "deadline") if turn.machines else ""
            if closing:
                piece = ("" if not sent or sent[-1].endswith(" ") else " ") + closing
                sent.append(piece)
                yield ChatChunk(request_id=request_id, seq=len(sent) - 1, text=piece)
        elif isinstance(results[0], BaseException) and not isinstance(results[0], asyncio.CancelledError):
            self.logger.error("agent turn failed", exc_info=results[0])
            turn.outcome, turn.reason = "error", f"internal error: {type(results[0]).__name__}"
        yield await self._finish(turn, sent)

    # --- one turn ----------------------------------------------------------------------------------------------

    async def _run(self, text: str, conversation_id: str | None, source: str, turn: Turn,
                   queue: asyncio.Queue[str | None]) -> None:
        async def send(piece: str) -> None:
            turn.pieces.append(piece)
            await queue.put(piece)

        try:
            store = self.node.store
            turn.conversation_id = conversation_id or await store.new_conversation(text[:60])
            try:
                await store.add_turn(turn.conversation_id, turn.request_id, "owner", text)
            except Exception:                                  # an unknown conversation id: start a new one
                turn.conversation_id = await store.new_conversation(text[:60])
                await store.add_turn(turn.conversation_id, turn.request_id, "owner", text)
            refusal = self._refusal(text)
            if refusal is not None:
                turn.outcome, turn.reason = "refused", f"asked {refusal}"
                await send(REFUSALS[refusal])
                return
            where = route(text, self_node=self.node.id)
            if _ACK.search(text):
                await self._offer_ack(text, where, turn, send)
                return
            await self._gather(text, where, turn)
            if not turn.machines or all(m.farm_time is None for m in turn.machines.values()):
                turn.outcome, turn.reason = "error", "no farm data has arrived yet"
                await send("I have no farm data yet, sir: the farm is not publishing anything I can read.")
                return
            levels = [str(m.assessment.get("assessment")) for m in turn.machines.values() if m.assessment]
            levels += [level for (feed, _), level in self.alert_levels.items() if feed == self.node.feed_id]
            style = style_for(levels)
            await self._speak(text, turn, style, send)
            if turn.outcome in ("answered", "fallback"):
                await self._state_farm_time(turn, send)
        finally:
            await queue.put(None)

    @staticmethod
    def _refusal(text: str) -> str | None:
        for name, pattern in (("emergency", _EMERGENCY), ("cmm", _CMM), ("rules", _RULES), ("act", _ACT)):
            if pattern.search(text):
                return {"emergency": "emergency", "cmm": "cmm", "rules": "rules", "act": "act"}[name]
        return None

    # --- gathering ---------------------------------------------------------------------------------------------

    async def _own(self, name: str, args: dict[str, Any], turn: Turn) -> Any:
        started = time.monotonic()
        outcome = await self.node.tools.call(name, args, request_id=turn.request_id, caller="llm")
        elapsed = time.monotonic() - started
        turn.tools.append(ToolUse(node=self.node.id, tool=name, ok=outcome.ok, duration_ms=int(elapsed * 1000)))
        if outcome.ok:
            turn.evidence.append({"name": f"{self.node.id}.{name}", "data": outcome.data})
        return outcome

    async def _peer(self, node: NodeId, topic: str, text: str, turn: Turn) -> Any:
        started = time.monotonic()
        outcome = await self.node.tools.call("ask_peer", {"node": node, "question": text[:2000], "topic": topic},
                                             request_id=turn.request_id, caller="llm")
        elapsed = time.monotonic() - started
        turn.tools.append(ToolUse(node=node, tool=f"ask_peer:{topic}", ok=outcome.ok,
                                  duration_ms=int(elapsed * 1000)))
        if outcome.ok:
            turn.evidence.append({"name": f"{node}.{topic}", "data": outcome.data})
            if self.node.feed_id:
                self.peer_memory.remember(self.node.feed_id, node, topic, dict(outcome.data["answer"]),
                                          outcome.farm_time, outcome.tick)
        return outcome

    async def _gather(self, text: str, where: Route, turn: Turn) -> None:
        """incubator-1's own data always, and every routed peer's topics, all in parallel within GATHER_S."""
        started = time.monotonic()
        me: NodeId = self.node.id
        jobs: list[tuple[NodeId, str, Awaitable[Any]]] = [
            (me, "assessment", self._own("get_assessment", {}, turn)),
            (me, "readings", self._own("get_status", {}, turn))]
        if where.needs_manual and me in where.machines:
            jobs.append((me, "documents", self._own("search_documents", {"query": text[:300], "k": 4}, turn)))
        if "incident_history" in where.topics and me in where.machines:
            jobs.append((me, "incident", self._own("get_incident", {"incident_id": "current"}, turn)))
        for node in where.machines:
            if node == me:
                continue
            capabilities = self.node.capability_registry.capabilities_of(node)
            announced = set(capabilities.topics) if capabilities else set()
            for topic in where.topics:
                if not announced or topic in announced:
                    jobs.append((node, topic, self._peer(node, topic, text, turn)))
        try:
            async with asyncio.timeout(GATHER_S):
                results = await asyncio.gather(*(job for _, _, job in jobs), return_exceptions=True)
        except TimeoutError:
            results = [TimeoutError("gather deadline")] * len(jobs)
        turn.tools_ms += (time.monotonic() - started) * 1000
        for (node, topic, _), result in zip(jobs, results, strict=True):
            data = turn.machines.setdefault(node, MachineData(node=node))
            if isinstance(result, BaseException) or not result.ok:
                data.error = data.error or (str(result) if isinstance(result, BaseException) else result.error)
                continue
            self._absorb(node, topic, result.data, result.farm_time, result.tick, data)
        for node, data in turn.machines.items():                  # silent peers: their last good answer of this run
            if node != me and data.assessment is None and data.readings is None and self.node.feed_id:
                for topic in ("status", "readings"):
                    remembered = self.peer_memory.last(self.node.feed_id, node, topic)
                    if remembered is not None:
                        answer, farm_time, tick = remembered
                        self._absorb(node, topic, {"answer": answer}, farm_time, tick, data)
                        data.remembered = True
                        turn.evidence.append({"name": f"{node}.{topic}.remembered", "data": {"answer": answer,
                                                                                          "farm_time": farm_time}})

    @staticmethod
    def _absorb(node: NodeId, topic: str, payload: dict[str, Any], farm_time: str | None, tick: int | None,
                data: MachineData) -> None:
        """Fold one tool result (own or a peer's answer) into what the turn knows about that machine."""
        answer: dict[str, Any] = payload.get("answer", payload)
        assessment = answer.get("get_assessment", {}).get("assessment") if topic == "status" else None
        if topic == "assessment":
            assessment = answer.get("assessment")
        if isinstance(assessment, dict):
            data.assessment = assessment
        status = answer.get("get_status") if "get_status" in answer else (answer if "readings" in answer else None)
        if isinstance(status, dict) and isinstance(status.get("readings"), dict) and topic in ("readings", "status"):
            data.readings = {**(data.readings or {}), **status["readings"]}
        search = answer.get("search_documents") if "search_documents" in answer else (
            answer if topic == "documents" else None)
        if isinstance(search, dict):
            data.documents += [r for r in search.get("results", []) if isinstance(r, dict)]
        incident = answer.get("get_incident") if "get_incident" in answer else (answer if topic == "incident" else None)
        if isinstance(incident, dict) and "incident" in incident:
            data.incident = incident.get("incident")
        label = payload.get("label") or answer.get("label")
        if farm_time is not None and tick is not None and (data.tick is None or tick >= data.tick):
            data.farm_time, data.tick = farm_time, tick
            if isinstance(label, str):
                data.label = label

    # --- speaking ----------------------------------------------------------------------------------------------

    def _tool_schemas(self) -> list[dict[str, Any]]:
        """The read-only tools the 2B model may call, JSON-schema'd from their input models (fixed: cached prompt)."""
        registry = self.node.tools
        schemas = []
        for name in LLM_TOOLS:
            model = registry.tools[name].input_model
            schemas.append({"type": "function", "function": {
                "name": name, "description": f"JARVIS read-only tool {name}",
                "parameters": model.model_json_schema()}})
        return schemas

    def _data_block(self, turn: Turn) -> dict[str, Any]:
        block: dict[str, Any] = {}
        for node, data in turn.machines.items():
            entry: dict[str, Any] = {"machine": NAMES[node], "farm_time": data.farm_time}
            if data.assessment:
                entry["conclusion"] = {k: data.assessment.get(k) for k in
                                       ("assessment", "diagnosis", "confidence", "evidence", "recommendation", "brain")}
            if data.readings:
                entry["readings"] = data.readings
            if data.incident:
                entry["incident"] = data.incident
            if data.documents:
                entry["manual"] = [{"file": d.get("file"), "section": d.get("section"), "text": d.get("text")}
                                   for d in data.documents[:2]]
            if data.error and not data.assessment and not data.readings:
                entry["not_answering"] = data.error
            if data.remembered:
                entry["note"] = "not answering now; this is its last report of this run"
            block[node] = entry
        return block

    async def _speak(self, text: str, turn: Turn, style: Style, send: Callable[[str], Awaitable[None]]) -> None:
        farm_time, label = turn.newest()
        try:
            history = await self.node.store.recent_turns(turn.conversation_id, 8, exclude_request=turn.request_id)
        except Exception:                                           # no history is a smaller prompt, not a failure
            history = []
        messages = [*fixed_start(self.persona), *history_messages(history),
                    turn_message(text, self._data_block(turn), style=style.instruction, farm_time=farm_time,
                                 label=label)]
        said: list[str] = []
        try:
            failed = await self._stream(messages, turn, style, said, send, tools=True)
            if failed is not None:
                turn.regenerated = True
                note = (f"Your last sentence was not sent: {failed}. Continue your reply after what you already said, "
                        "in one or two sentences, using only numbers that appear in the data.")
                retry = [*messages]
                if said:
                    retry.append({"role": "assistant", "content": "".join(said)})
                retry.append({"role": "user", "content": note})
                failed = await self._stream(retry, turn, style, said, send, tools=False)
                if failed is not None:
                    turn.outcome, turn.reason = "fallback", f"a sentence failed verification twice ({failed})"
                    await send(self._join(said, self._template_sentence(turn)))
        except EngineError as error:
            turn.outcome, turn.reason = "fallback", f"language model unavailable: {str(error)[:200]}"
            await send(self._join(said, fallback_text(list(turn.machines.values()), "model")))
            self.rewarm()
        if turn.outcome == "answered" and not said:
            turn.outcome, turn.reason = "fallback", "the language model returned no text"
            await send(fallback_text(list(turn.machines.values()), "verify"))
        turn.citations = self._citations(turn) if turn.outcome in ("answered", "fallback") else []

    @staticmethod
    def _join(said: list[str], text: str) -> str:
        return text if not said or said[-1].endswith((" ", "\n")) else " " + text

    def _template_sentence(self, turn: Turn) -> str:
        return " ".join(describe(machine) for machine in turn.machines.values())

    async def _stream(self, messages: list[dict[str, Any]], turn: Turn, style: Style, said: list[str],
                      send: Callable[[str], Awaitable[None]], *, tools: bool) -> str | None:
        """Stream one reply (with up to MAX_TOOL_ROUNDS of tool calls), gating each sentence. None when everything
        went out verified; otherwise why the first failing sentence was stopped."""
        sampling = self.persona.sampling
        for round_ in range(MAX_TOOL_ROUNDS + 1):
            # the schemas go with every call, even when no tool may be called, so the cached prompt start stays the same
            allowed = tools and round_ < MAX_TOOL_ROUNDS
            fragments: list[dict[str, Any]] = []
            buffer = ""
            started = time.monotonic()
            turn.llm_calls += 1
            stream = self.engine.chat_stream(
                messages, max_tokens=style.max_tokens, temperature=min(style.temperature, sampling.temperature),
                top_p=sampling.top_p, min_p=sampling.min_p, top_k=sampling.top_k,
                repeat_penalty=sampling.repeat_penalty, tools=self.tool_schemas,
                deadline_s=max(0.5, min(self.llm_call_s, self._remaining(turn) - 1.0)),
                first_token_s=self.first_token_s)
            try:
                async for delta in stream:
                    fragments += delta.tool_calls
                    if fragments:
                        continue
                    buffer += delta.text
                    sentences, buffer = split_sentences(buffer)
                    for sentence in sentences:
                        problem = await self._gate(sentence, turn, said, send)
                        if problem is not None:
                            return problem
            finally:
                await stream.aclose()
                turn.llm_ms += (time.monotonic() - started) * 1000
            if fragments and not allowed:
                turn.tool_calls_invalid += 1
                return "it asked for a tool after its last tool round"
            if fragments:
                calls = assemble_tool_calls(fragments)
                messages = [*messages, {"role": "assistant", "content": "", "tool_calls": calls}]
                for call in calls:
                    messages.append(await self._tool_message(call, turn))
                continue
            rest = buffer.strip()
            if rest:
                return await self._gate(rest + " ", turn, said, send)
            return None
        return None

    async def _gate(self, sentence: str, turn: Turn, said: list[str],
                    send: Callable[[str], Awaitable[None]]) -> str | None:
        """Send one sentence if every number and time in it matches this turn's tool results; else say why not."""
        if _BAD_SENTENCE.search(sentence):
            turn.unverified_sentences += 1
            return "it contained brackets or tags"
        if _CLAIMED_ACTION.search(sentence):
            turn.unverified_sentences += 1
            return "it said JARVIS did something, and JARVIS never acts"
        report = verify_reply(sentence, turn.evidence)
        if not report.ok:
            turn.unverified_sentences += 1
            unmatched = [n.text for n in report.unmatched] + [t.text for t in report.unmatched_times]
            return "these numbers are not in the data: " + ", ".join(unmatched[:6])
        turn.numbers_checked += report.checked + report.times_checked
        piece = self._join(said, sentence.strip()) + " "
        said.append(piece)
        await send(piece)
        return None

    async def _tool_message(self, call: dict[str, Any], turn: Turn) -> dict[str, Any]:
        """Run one tool call of the model (read-only tools only), and answer it as a tool message."""
        function = call.get("function", {})
        name = str(function.get("name", ""))
        try:
            args = json.loads(function.get("arguments") or "{}")
            if not isinstance(args, dict) or name not in LLM_TOOLS:
                raise ValueError("not an allowed tool or not a JSON object")
        except (ValueError, TypeError) as error:
            turn.tool_calls_invalid += 1
            return {"role": "tool", "tool_call_id": call.get("id", ""), "content": json.dumps(
                {"error": f"invalid tool call: {error}"})}
        started = time.monotonic()
        outcome = await self.node.tools.call(name, args, request_id=turn.request_id, caller="llm")
        turn.tools_ms += (time.monotonic() - started) * 1000
        if outcome.error and outcome.error.startswith("invalid arguments"):
            turn.tool_calls_invalid += 1
        else:
            turn.tool_calls_valid += 1
        turn.tools.append(ToolUse(node=self.node.id, tool=name, ok=outcome.ok,
                                  duration_ms=int((time.monotonic() - started) * 1000)))
        if outcome.ok:
            turn.evidence.append({"name": f"{self.node.id}.{name}", "data": outcome.data})
            if name == "search_documents":
                machine = turn.machines.setdefault(self.node.id, MachineData(node=self.node.id))
                machine.documents += [r for r in outcome.data.get("results", []) if isinstance(r, dict)]
        content = outcome.data if outcome.ok else {"error": outcome.error}
        return {"role": "tool", "tool_call_id": call.get("id", ""),
                "content": json.dumps(content, ensure_ascii=False)[:4000]}

    def _remaining(self, turn: Turn) -> float:
        return turn.started + self.turn_s - time.monotonic()

    async def _state_farm_time(self, turn: Turn, send: Callable[[str], Awaitable[None]]) -> None:
        """Ground rule 6: the reply states the farm time of its newest data; if the model left it out, say it."""
        farm_time, _ = turn.newest()
        text = "".join(turn.pieces)
        if farm_time is None or states_farm_time(text, {"farm_time": farm_time}):
            return
        await send(self._join(turn.pieces, f"That is as of farm time {farm_time}."))

    def _citations(self, turn: Turn) -> list[Citation]:
        cited: list[Citation] = []
        for node, data in turn.machines.items():
            for passage in data.documents[:2]:
                file, section = passage.get("file"), passage.get("section")
                if isinstance(file, str) and file:
                    citation = Citation(node=node, file=file[:200], section=str(section or "")[:200])
                    if citation not in cited:
                        cited.append(citation)
        return cited[:MAX_CITATIONS]

    # --- acknowledging an alert (ground rule 3: the owner's words, then a confirm click) -------------------------

    async def _offer_ack(self, text: str, where: Route, turn: Turn, send: Callable[[str], Awaitable[None]]) -> None:
        named: list[NodeId] = [node for node in NODE_IDS if any(p.search(text) for p in ALIASES[node])]
        if not named:
            alerted: list[NodeId] = [n for n in NODE_IDS if (self.node.feed_id, n) in self.alert_levels]
            named = alerted if len(alerted) == 1 else []
        if len(named) != 1:
            turn.outcome, turn.reason = "error", "the alert's machine is not clear"
            await send("Which machine's alert should I acknowledge, sir? Name it, for example the HVAC.")
            return
        node = named[0]
        incident_where = Route(machines=(node,), topics=("incident_history",), farm_wide=False, needs_manual=False)
        await self._gather(text, incident_where if node != self.node.id else
                           Route(machines=(node,), topics=("status", "incident_history"), farm_wide=False,
                                 needs_manual=False), turn)
        data = turn.machines.get(node)
        farm_time, label = turn.newest()
        if data is None or farm_time is None:
            turn.outcome, turn.reason = "error", f"{node} did not answer"
            await send(f"{NAMES[node]} isn't answering, sir, so I cannot find its alert right now.")
            return
        incident = data.incident
        if not incident or incident.get("state") == "closed" or not incident.get("incident_id"):
            await send(f"There's no active alert on {NAMES[node].lower() if node != 'hvac' else 'the HVAC'} "
                       f"at farm time {data.farm_time or farm_time}, sir.")
            return
        incident_id = str(incident["incident_id"])[:80]
        token = self.node.confirm_tokens.issue(node, incident_id)
        expires = (datetime.now(timezone.utc) + timedelta(seconds=CONFIRM_SECONDS)).isoformat(timespec="seconds")
        kind = words(str(incident.get("kind") or (data.assessment or {}).get("diagnosis") or "alert"))
        summary = f"{NAMES[node]}: {kind} (incident {incident_id}), as of farm time {data.farm_time}"[:300]
        turn.confirm = AckOffer(node=node, incident_id=incident_id, token=token, expires_utc=expires, summary=summary)
        await send(f"To acknowledge the {NAMES[node]} alert, {kind}, press Confirm within {CONFIRM_SECONDS} seconds, "
                   f"sir. It tells the machine you have seen it; it changes nothing on the machine. "
                   f"Its data is as of farm time {data.farm_time}.")

    # --- the end of a turn -------------------------------------------------------------------------------------

    async def _finish(self, turn: Turn, sent: list[str]) -> ChatDone:
        text = "".join(sent)
        farm_time, label = turn.newest()
        outcome, reason = turn.outcome, turn.reason
        if outcome in ("answered", "fallback") and (farm_time is None or label is None):
            outcome, reason = "error", reason or "no farm data with a farm time"
        if outcome == "answered":
            reason = ""
        elif not reason:
            reason = "no reason recorded"
        now = time.monotonic()
        timings = ReplyTimings(
            first_chunk_ms=int((turn.first_chunk_at - turn.started) * 1000) if turn.first_chunk_at else None,
            total_ms=int((now - turn.started) * 1000), llm_ms=int(turn.llm_ms) if turn.llm_calls else None,
            tools_ms=int(turn.tools_ms) if turn.tools else None)
        done = ChatDone(request_id=turn.request_id, conversation_id=turn.conversation_id or "unknown",
                        text=text[:8000], chunks=len(sent), outcome=outcome, reason=reason[:300],
                        numbers_checked=turn.numbers_checked, farm_time=farm_time, label=label,
                        citations=turn.citations, tools=turn.tools[:40], timings=timings,
                        confirm=turn.confirm if outcome == "answered" else None)
        try:
            if turn.conversation_id and text:
                await self.node.store.add_turn(turn.conversation_id, turn.request_id, "jarvis", text)
        except Exception:
            self.logger.exception("could not store the reply")
        log(self.logger, logging.INFO, "reply", request_id=turn.request_id, outcome=outcome, reason=reason,
            numbers_checked=turn.numbers_checked, total_ms=timings.total_ms, first_chunk_ms=timings.first_chunk_ms,
            unverified_sentences=turn.unverified_sentences, regenerated=turn.regenerated,
            tool_calls_valid=turn.tool_calls_valid, tool_calls_invalid=turn.tool_calls_invalid)
        return done


def new_request_id() -> str:
    return f"r-{uuid.uuid4().hex[:12]}"
