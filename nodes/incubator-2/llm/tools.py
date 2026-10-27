"""Typed, permissioned read-only tools for farm context, approved documents and owner-confirmed alert delivery.

In: strict JSON arguments, the node's own published view, its approved-document index and peers. Out: bounded tool
outcomes carrying source farm time, audit rows and verify.py `ToolResult` values. Every handler is deadline-bound, and
this module has no machine-action path. Tests: `tests/llm/test_tools.py` and `tests/llm/safety/test_static_boundaries.py`.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import math
import re
import secrets
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from contracts import NODE_IDS, REQUEST_ID, FeedLabel, NodeConfig, NodeId, Topic
from llm.audit import Auditor
from llm.capabilities import classify_topic
from llm.conversation import LlmStore
from llm.documents import Documents
from llm.verify import ToolResult

Permission = Literal["read", "ask", "owner-confirmed"]
_FEED_LABELS = ("live: simulated brains", "live: trained brains")
PEER_DEADLINE_S = 3.0                    # ask_peer's radio deadline (plan section 6)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EmptyArgs(StrictModel):
    pass


class StatusArgs(StrictModel):
    fields: list[str] | None = None


class TrendArgs(StrictModel):
    signal: str = Field(min_length=1)
    minutes: int = Field(ge=1, le=120)


class EventsArgs(StrictModel):
    since_minutes: int = Field(ge=1, le=1440)
    severity: Literal["info", "warning", "critical"] | None = None
    limit: int = Field(default=20, ge=1, le=50)


class IncidentArgs(StrictModel):
    incident_id: str = Field(min_length=1)


class SearchArgs(StrictModel):
    query: str = Field(min_length=1, max_length=300)
    k: int = Field(default=4, ge=1, le=8)


class PeerArgs(StrictModel):
    node: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    topic: Topic | None = None


class RecallArgs(StrictModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=10)


class AcknowledgeArgs(StrictModel):
    incident_id: str = Field(min_length=1)
    confirm_token: str = Field(min_length=1)


class StatusOutput(StrictModel):
    online: bool
    farm_time: str | None
    tick: int | None
    label: FeedLabel | None
    readings: dict[str, JsonValue]


class AssessmentOutput(StrictModel):
    assessment: dict[str, JsonValue]
    farm_time: str | None
    tick: int | None
    label: FeedLabel | None


class TrendOutput(StrictModel):
    signal: str
    minimum: float
    maximum: float
    first: float
    last: float
    rate_per_min: float
    samples: int
    from_farm_time: str
    to_farm_time: str
    farm_time: str
    tick: int
    label: FeedLabel


class EventsOutput(StrictModel):
    events: list[dict[str, JsonValue]]
    farm_time: str | None
    tick: int | None
    label: FeedLabel | None


class IncidentOutput(StrictModel):
    incident: dict[str, JsonValue] | None                # None: no current incident (the normal state)
    farm_time: str
    tick: int
    label: FeedLabel


class DocumentsOutput(StrictModel):
    results: list[dict[str, JsonValue]]
    status: str
    farm_time: str | None
    tick: int | None
    label: FeedLabel | None


class PeerOutput(StrictModel):
    answer: dict[str, JsonValue]
    topic: Topic
    farm_time: str
    tick: int
    label: FeedLabel


class RecallOutput(StrictModel):
    turns: list[dict[str, JsonValue]]
    farm_time: str | None
    tick: int | None
    label: FeedLabel | None


class AcknowledgeOutput(StrictModel):
    acknowledged: bool
    incident_id: str
    farm_time: str | None
    tick: int | None
    label: FeedLabel | None


class ToolOutcome(StrictModel):
    """A tool's single success or failure result; tool errors are data, never uncaught exceptions."""

    ok: bool
    data: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    error: str | None = None
    farm_time: str | None = None
    tick: int | None = Field(default=None, ge=0)

    def tool_result(self, name: str) -> ToolResult:
        """Convert the outcome into verify.py's evidence contract."""
        return ToolResult(name=name, data=self.data)


@dataclass(frozen=True)
class Tool:
    """One strict, permissioned handler and its execution deadline."""

    name: str
    permission: Permission
    input_model: type[StrictModel]
    output_model: type[StrictModel]
    deadline_s: float
    handler: Callable[[BaseModel, str, str], Awaitable[dict[str, Any]]]


class _ToolFailure(Exception):
    pass


Caller = Literal["owner", "llm", "peer", "mcp"]


def _valid_request_id(request_id: str | None) -> tuple[str, str | None]:
    """(usable id, rejected text): the given id when it matches REQUEST_ID, otherwise a fresh `r-<12 hex>` plus the
    rejected text (cut to 80 characters), which the audit keeps as "given_request_id" so nothing is hidden."""
    if request_id is not None and re.fullmatch(REQUEST_ID, request_id):
        return request_id, None
    return f"r-{secrets.token_hex(6)}", None if request_id is None else request_id[:80]


def _audited(inputs: Mapping[str, Any], given: str | None) -> dict[str, Any]:
    """The inputs as audited: with the rejected request id, when there was one."""
    return {**inputs, "given_request_id": given} if given is not None else dict(inputs)


class ConfirmTokens:
    """Issue and verify short-lived, single-use HMAC confirmations without an unbounded replay cache."""

    def __init__(self, secret: bytes | str) -> None:
        self._secret = secret.encode("utf-8") if isinstance(secret, str) else secret
        if not self._secret:
            raise ValueError("confirmation secret must not be empty")
        self._used: OrderedDict[str, int] = OrderedDict()

    def issue(self, incident_id: str, *, now_s: int | None = None) -> str:
        """Create a testable token expiring 60 seconds from the supplied or current Unix time."""
        expiry = (int(time.time()) if now_s is None else now_s) + 60
        message = f"{incident_id}:{expiry}"
        signature = hmac.new(self._secret, message.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{expiry}.{signature}"

    def verify(self, incident_id: str, token: str, *, now_s: int | None = None) -> bool:
        """Check signature, incident binding, expiry and single use; reject after the bounded replay set fills."""
        now = int(time.time()) if now_s is None else now_s
        for old_token, expiry in list(self._used.items()):
            if expiry < now:
                self._used.pop(old_token, None)
        if token in self._used or len(self._used) >= 1000:
            return False
        try:
            expiry_text, signature = token.split(".", 1)
            expiry = int(expiry_text)
        except (ValueError, TypeError):
            return False
        if expiry <= now or expiry > now + 60:
            return False
        expected = hmac.new(self._secret, f"{incident_id}:{expiry}".encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        self._used[token] = expiry
        return True


class ToolRegistry:
    """Validate and execute the nine fixed tools against the caller's node-local public data."""

    def __init__(self, node_config: NodeConfig, view: Any, store: LlmStore, documents: Documents,
                 peers: Any, auditor: Auditor, confirm: ConfirmTokens,
                 deliver: Callable[[str], Awaitable[None]] | None = None,
                 *, logger: logging.Logger | None = None) -> None:
        self.node_config = node_config
        self.view = view
        self.store = store
        self.documents = documents
        self.peers = peers
        self.auditor = auditor
        self.confirm = confirm
        self.deliver = deliver
        self.logger = logger or logging.getLogger(f"jarvis.tools.{node_config.id}")
        self.tools: dict[str, Tool] = {
            "get_status": Tool("get_status", "read", StatusArgs, StatusOutput, 1.0, self._get_status),
            "get_assessment": Tool("get_assessment", "read", EmptyArgs, AssessmentOutput, 1.0, self._get_assessment),
            "get_trend": Tool("get_trend", "read", TrendArgs, TrendOutput, 2.0, self._get_trend),
            "list_events": Tool("list_events", "read", EventsArgs, EventsOutput, 2.0, self._list_events),
            "get_incident": Tool("get_incident", "read", IncidentArgs, IncidentOutput, 2.0, self._get_incident),
            "search_documents": Tool("search_documents", "read", SearchArgs, DocumentsOutput, 5.0,
                                     self._search_documents),
            # the radio's own deadline is PEER_DEADLINE_S; the tool's must be longer, or its timeout races the radio's
            # and replaces the peer's clear reason ("hvac's link is cut") with a bare "timeout"
            "ask_peer": Tool("ask_peer", "ask", PeerArgs, PeerOutput, PEER_DEADLINE_S + 0.5, self._ask_peer),
            "recall_conversation": Tool("recall_conversation", "read", RecallArgs, RecallOutput, 2.0,
                                        self._recall_conversation),
            "acknowledge_alert": Tool("acknowledge_alert", "owner-confirmed", AcknowledgeArgs, AcknowledgeOutput,
                                      2.0, self._acknowledge_alert),
        }

    def _source(self) -> tuple[str | None, int | None, FeedLabel | None]:
        status = getattr(self.view, "status", None)
        if status is not None:
            return status.farm_time, status.tick, status.label
        sim = getattr(self.view, "sim", None)
        if sim is not None:
            return sim.farm_time, sim.tick, sim.label
        return None, None, None

    def _feed(self) -> tuple[str | None, float | None]:
        """(feed_id, time_s) of the node's latest farm data: which farm run "now" is, and when on its clock."""
        for source in (getattr(self.view, "status", None), getattr(self.view, "sim", None)):
            if source is not None:
                return source.feed_id, source.time_s
        return None, None

    def _farm_fields(self) -> dict[str, Any]:
        farm_time, tick, label = self._source()
        return {"farm_time": farm_time, "tick": tick, "label": label}

    def _outcome(self, *, data: Mapping[str, Any], error: str | None = None,
                 ok: bool = True, farm_time: str | None = None, tick: int | None = None) -> ToolOutcome:
        source_time, source_tick, _ = self._source()
        return ToolOutcome(ok=ok, data=dict(data), error=error,
                           farm_time=farm_time if farm_time is not None else source_time,
                           tick=tick if tick is not None else source_tick)

    async def _refusal(self, name: str, request_id: str, args: Any, reason: str, given: str | None) -> ToolOutcome:
        """Audit a denied or invalid call exactly once as a refusal and return its clear reason."""
        try:
            async with self.auditor.step(request_id, "refusal", name,
                                         _audited(args if isinstance(args, Mapping) else {}, given)) as _:
                raise _ToolFailure(reason)
        except _ToolFailure:
            pass
        return self._outcome(data={}, error=reason, ok=False)

    async def call(self, name: str, args: Any, *, request_id: str | None, caller: Caller) -> ToolOutcome:
        """Validate permission, arguments, output and deadline; convert every ordinary failure to a ToolOutcome.

        `caller` says who is asking: "owner" ONLY on the gateway's owner-confirmed path (6W), "llm" for the language
        model's own tool choices (6C), "peer" for another node's question, "mcp" for a local MCP client. The
        owner-confirmed tool also needs a valid single-use confirm token, so a wrong caller alone cannot act.
        A request id that is not a valid REQUEST_ID is replaced by a fresh one; the given text is kept in the audit."""
        request_id, given = _valid_request_id(request_id)
        tool = self.tools.get(name)
        if tool is None:
            return await self._refusal(name, request_id, args, f"unknown tool: {name}", given)
        if caller not in get_args(Caller):
            return await self._refusal(name, request_id, args, f"permission denied: unknown caller {caller!r}", given)
        try:
            parsed = tool.input_model.model_validate(args)
        except ValidationError as error:
            reason = f"invalid arguments: {error.errors()[0]['msg']}"
            return await self._refusal(name, request_id, args, reason, given)
        inputs = parsed.model_dump(mode="json")
        if tool.permission == "owner-confirmed" and caller != "owner":
            return await self._refusal(name, request_id, inputs, "permission denied: owner confirmation required", given)
        if name in {"ask_peer", "recall_conversation", "acknowledge_alert"} and not self.node_config.speaks:
            return await self._refusal(name, request_id, inputs,
                                       f"unavailable: {self.node_config.id} is not the speaking node", given)
        if name == "acknowledge_alert":
            acknowledge = cast(AcknowledgeArgs, parsed)
            if not self.confirm.verify(acknowledge.incident_id, acknowledge.confirm_token):
                return await self._refusal(name, request_id, inputs, "invalid confirmation token", given)
        try:
            async with self.auditor.step(request_id, "tool_call", name, _audited(inputs, given)) as step:
                async with asyncio.timeout(tool.deadline_s):
                    raw = await tool.handler(parsed, request_id or "", caller)
                output = tool.output_model.model_validate(raw)
                data = output.model_dump(mode="json")
                step.done(data, raw.get("farm_time"), raw.get("tick"))
            return self._outcome(data=data, farm_time=raw.get("farm_time"), tick=raw.get("tick"))
        except TimeoutError:
            return self._outcome(data={}, error="timeout", ok=False)
        except _ToolFailure as error:
            return self._outcome(data={}, error=str(error), ok=False)
        except Exception as error:
            self.logger.exception("tool call failed", extra={"tool": name, "request_id": request_id})
            return self._outcome(data={}, error=f"tool failed: {type(error).__name__}", ok=False)

    async def _get_status(self, raw: BaseModel, request_id: str, caller: str) -> dict[str, Any]:
        args = cast(StatusArgs, raw)
        status = getattr(self.view, "status", None)
        if status is None:
            raise _ToolFailure("no status data yet")
        readings: Mapping[str, Any] = status.readings
        allowed_fields = set(readings) | set(getattr(self.view, "allowed_reading_keys", ())) | {"online", "farm_time", "health"}
        unknown = sorted(set(args.fields or ()) - allowed_fields)
        if unknown:
            raise _ToolFailure(f"unknown fields: {', '.join(unknown)}; allowed: {', '.join(sorted(allowed_fields))}")
        selected = dict(readings) if args.fields is None else {key: readings[key] for key in args.fields if key in readings}
        # online and the farm time are always included, whatever fields were asked for
        return {"online": bool(status.online), "farm_time": status.farm_time, "tick": status.tick,
                "label": status.label, "readings": selected}

    async def _get_assessment(self, raw: BaseModel, request_id: str, caller: str) -> dict[str, Any]:
        assessment = getattr(self.view, "assessment", None)
        if assessment is None:
            raise _ToolFailure("no assessment data yet")
        return {"assessment": assessment.model_dump(mode="json"), "farm_time": assessment.farm_time,
                "tick": assessment.tick, "label": assessment.label}

    async def _get_trend(self, raw: BaseModel, request_id: str, caller: str) -> dict[str, Any]:
        args = cast(TrendArgs, raw)
        history: Sequence[Any] = getattr(self.view, "trend_buffer", ())
        statuses = [item for item in history if getattr(item, "readings", {}).get(args.signal) is not None
                    and type(item.readings.get(args.signal)) in (int, float)
                    and math.isfinite(float(item.readings[args.signal]))]
        latest = getattr(self.view, "status", None)
        if latest is not None and all(item.tick != latest.tick for item in statuses):
            value = latest.readings.get(args.signal)
            if type(value) in (int, float) and math.isfinite(float(value)):
                statuses.append(latest)
        if latest is not None:
            statuses = [item for item in statuses if latest.time_s - item.time_s <= args.minutes * 60]
        statuses.sort(key=lambda item: item.time_s)
        if len(statuses) < 2:
            raise _ToolFailure("not enough data yet")
        values = [float(item.readings[args.signal]) for item in statuses]
        times = [(item.time_s - statuses[0].time_s) / 60.0 for item in statuses]
        mean_x = sum(times) / len(times)
        mean_y = sum(values) / len(values)
        denominator = sum((x - mean_x) ** 2 for x in times)
        rate = sum((x - mean_x) * (y - mean_y) for x, y in zip(times, values)) / denominator if denominator else 0.0
        source = statuses[-1]
        return {"signal": args.signal, "minimum": min(values), "maximum": max(values), "first": values[0],
                "last": values[-1], "rate_per_min": rate, "samples": len(values),
                "from_farm_time": statuses[0].farm_time, "to_farm_time": source.farm_time,
                "farm_time": source.farm_time, "tick": source.tick, "label": source.label}

    async def _list_events(self, raw: BaseModel, request_id: str, caller: str) -> dict[str, Any]:
        args = cast(EventsArgs, raw)
        feed_id, now_s = self._feed()
        if feed_id is None or now_s is None:                       # no farm data yet: nothing can be recent
            return {"events": [], **self._farm_fields()}
        events = await self.store.events(args.since_minutes, args.severity, args.limit, feed_id=feed_id, now_s=now_s)
        return {"events": events, **self._farm_fields()}

    async def _get_incident(self, raw: BaseModel, request_id: str, caller: str) -> dict[str, Any]:
        """The current incident, or one by id, of the farm run the node is in. No current incident is a normal,
        successful answer (incident = null, with the node's farm time); an unknown id is a failure."""
        args = cast(IncidentArgs, raw)
        feed_id, _ = self._feed()
        farm_time, tick, label = self._source()
        if feed_id is None or farm_time is None or tick is None or label is None:
            raise _ToolFailure("no farm data yet")
        incident = await self.store.incident(args.incident_id, feed_id=feed_id)
        if incident is None:
            if args.incident_id == "current":
                return {"incident": None, "farm_time": farm_time, "tick": tick, "label": label}
            raise _ToolFailure(f"unknown incident: {args.incident_id}")
        return {"incident": incident, "farm_time": incident["farm_time"], "tick": incident["tick"], "label": label}

    async def _search_documents(self, raw: BaseModel, request_id: str, caller: str) -> dict[str, Any]:
        args = cast(SearchArgs, raw)
        results = await asyncio.to_thread(self.documents.search, args.query, args.k)
        return {"results": results, "status": self.documents.status(), **self._farm_fields()}

    async def _ask_peer(self, raw: BaseModel, request_id: str, caller: str) -> dict[str, Any]:
        args = cast(PeerArgs, raw)
        target = args.node
        if target not in NODE_IDS or target == self.node_config.id:
            raise _ToolFailure("unavailable: unknown peer")
        capabilities = self.peers.capabilities_of(target)
        if capabilities is None:
            raise _ToolFailure("unavailable: peer is unknown")
        if not self.peers.is_alive(target):
            raise _ToolFailure("unavailable: peer is not alive")
        topic = args.topic or classify_topic(args.question)
        if topic not in capabilities.topics:
            raise _ToolFailure(f"unavailable: {target} does not announce topic {topic}")
        async with self.auditor.step(request_id, "peer_question", "ask_peer",
                                     {"node": target, "topic": topic, "question": args.question}) as question_step:
            reply = await self.peers.ask(target, topic, args.question, deadline_s=PEER_DEADLINE_S,
                                         request_id=request_id)
            if not reply.ok:
                raise _ToolFailure(f"unavailable: {reply.detail or reply.error or 'peer did not answer'}")
            question_step.done(reply.answer, reply.farm_time, reply.tick)
        label = reply.answer.get("label")
        if label not in _FEED_LABELS:
            _, _, label = self._source()
        farm_time = reply.farm_time or self._source()[0]
        tick = reply.tick if reply.tick is not None else self._source()[1]
        if farm_time is None or tick is None or label not in _FEED_LABELS:
            raise _ToolFailure("unavailable: peer answer has no farm coordinates")
        return {"answer": reply.answer, "topic": topic, "farm_time": farm_time, "tick": tick, "label": label}

    async def _recall_conversation(self, raw: BaseModel, request_id: str, caller: str) -> dict[str, Any]:
        args = cast(RecallArgs, raw)
        turns = await self.store.search_turns(args.query, args.limit)
        return {"turns": turns, **self._farm_fields()}

    async def _acknowledge_alert(self, raw: BaseModel, request_id: str, caller: str) -> dict[str, Any]:
        args = cast(AcknowledgeArgs, raw)
        feed_id, _ = self._feed()
        incident = await self.store.incident(args.incident_id, feed_id=feed_id) if feed_id is not None else None
        if incident is None or incident.get("state") == "closed":
            raise _ToolFailure("no active incident to acknowledge")
        if self.deliver is None:
            raise _ToolFailure("alert delivery is unavailable")
        await self.deliver(args.incident_id)
        return {"acknowledged": True, "incident_id": args.incident_id, **self._farm_fields()}
