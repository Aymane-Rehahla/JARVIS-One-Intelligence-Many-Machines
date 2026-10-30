"""Wire contracts of JARVIS: every message on NATS and the node's own `node.toml`, as pydantic v2 models (Q1, Q2).

Every message carries `schema_version`. `decode()` validates an incoming payload and raises ContractError for anything
invalid or of an unknown version; the NATS link logs and drops such messages, so a bad message never crashes a
program (Q2). The same models are exported as JSON Schemas to interface/contracts/ (tools/export_contracts.py), which
the website's zod schemas follow. This file is the same in all four nodes (tests/test_nodes_in_sync.py); programs
outside the nodes (the farm runtime, the gateway) import incubator-1's copy.

Subjects (plan section 4.3), built by the helpers below:
    farm.<node>.status | .events | .assessment | .incident    farm runtime → that node, gateway
    farm.links, farm.sim                                       farm runtime → all
    jarvis.<node>.heartbeat | .capabilities                    every node → all
    jarvis.<node>.ask                                          node → node, request-reply, through the ZigBee rules
    jarvis.<node>.probe                                        tests and diagnostics → node: "ask this peer" (opt-in)
    jarvis.<node>.audit                                        every node → gateway (6B)
    jarvis.incubator-1.chat                                    gateway → incubator-1: the owner's message (6C)
    jarvis.incubator-1.reply                                   incubator-1 → gateway: ChatChunk ..., then ChatDone (6C)
    jarvis.incubator-1.alert                                   incubator-1 → gateway: a proactive alert to the owner (6C)
    jarvis.incubator-1.ack                                     gateway → incubator-1, request-reply: a confirmed ack (6C)
    jarvis.<node>.notice                                       node → node through the ZigBee rules (6C, the agent's own)
    jarvis.incubator-1.voice                                   incubator-1 → gateway: the voice box's state (6E)
    jarvis.incubator-1.voice_settings | .ptt                   gateway → incubator-1: voice, speed, talk button (6E)
    mentor.llm_failures                                        gateway → every node: simulated LLM failures (test only)
"""
from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator, model_validator

SCHEMA_VERSION = 1

NodeId = Literal["incubator-1", "incubator-2", "hvac", "animal-protection"]
NODE_IDS: tuple[NodeId, ...] = ("incubator-1", "incubator-2", "hvac", "animal-protection")
Machine = Literal["incubator", "hvac", "animal_protection"]
MACHINES: dict[NodeId, Machine] = {"incubator-1": "incubator", "incubator-2": "incubator", "hvac": "hvac",
                                   "animal-protection": "animal_protection"}
Assessment = Literal["NORMAL", "HANDLED_BY_MACHINE", "WATCHING", "ESCALATE_TO_USER", "DANGER"]
Topic = Literal["status", "readings", "incident_history", "incubation", "hazard_check", "documents"]
TOPICS: tuple[Topic, ...] = ("status", "readings", "incident_history", "incubation", "hazard_check", "documents")
FeedLabel = Literal["live: simulated brains", "live: trained brains"]
AskError = Literal["timeout", "link_cut", "unavailable", "no_data", "bad_request", "lost"]
FARM_TIME = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
REQUEST_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$"
FEED_ID = r"^[0-9a-f]{12}$"                 # one farm run: new at every farm-runtime start and every reset
CONVERSATION_ID = r"^[A-Za-z0-9_-]{1,64}$"
CLIENT_MSG_ID = r"^[A-Za-z0-9_-]{1,64}$"
VOICE_ID = r"^[a-z]{2}_[a-z]+$"              # a Kokoro voice name, e.g. bm_george
CONTROL_CHARACTERS = r"[\x00-\x08\x0b\x0c\x0e-\x1f]"   # refused in free text; tab, newline and return are allowed
MAX_DEADLINE_MS = 30_000
SPEAKER_MODEL = "Qwen-3.5-2b-roleplay-tuned.Q8_0.gguf"


class ContractError(ValueError):
    """An incoming payload is not a valid message of the expected kind and version."""


class Contract(BaseModel):
    """Base of every wire message: strict (no type coercion, as in zod), immutable, versioned."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = SCHEMA_VERSION


M = TypeVar("M", bound=BaseModel)


def decode(model: type[M], raw: bytes | str) -> M:
    """Validate one incoming payload (JSON) as `model`. Raises ContractError with a one-line reason."""
    try:
        return model.model_validate_json(raw)
    except ValidationError as error:
        raise ContractError(f"invalid {model.__name__}: {_first_reason(error)}") from error


def _first_reason(error: ValidationError) -> str:
    """`where: what` of the first problem pydantic found."""
    errors = error.errors()
    if not errors:
        return f"message: {error}"
    where = ".".join(str(part) for part in errors[0]["loc"]) or "message"
    return f"{where}: {errors[0]['msg']}"


def _printable(value: str) -> str:
    """Free text from outside (the owner, a peer, a test) may not carry control characters (tab, newline, return
    are fine). The website's zod schemas refuse the same characters."""
    if re.search(CONTROL_CHARACTERS, value):
        raise ValueError("control characters are not allowed")
    return value


def encode(message: BaseModel) -> bytes:
    """The message as compact JSON bytes, for NATS."""
    return message.model_dump_json().encode("utf-8")


def utc_now() -> str:
    """The current UTC time, ISO 8601 with milliseconds."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --- node.toml -----------------------------------------------------------------------------------------------------


class LlmSlot(BaseModel):
    """Which language model a node uses, on which llama.cpp server and slot."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    model: str = Field(min_length=1)
    server: NodeId
    slot: Literal[0]


class EmbeddingsConfig(BaseModel):
    """The node's document-search embedding model (6B)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    model: str = Field(min_length=1)


class NodeConfig(BaseModel):
    """A node's identity (`node.toml`): who it is, what it runs, whether it speaks."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1]
    id: NodeId
    machine: Machine
    speaks: bool
    voice: bool
    llm: LlmSlot
    embeddings: EmbeddingsConfig

    @model_validator(mode="after")
    def _consistent(self) -> NodeConfig:
        if MACHINES[self.id] != self.machine:
            raise ValueError(f"{self.id} is a {MACHINES[self.id]}, not a {self.machine}")
        if self.llm.server != self.id:
            raise ValueError("each node uses its own LLM server")
        if self.speaks != (Path(self.llm.model).name == SPEAKER_MODEL):
            raise ValueError("the speaking node uses the 2B roleplay model and the silent nodes use Qwen3-0.6B")
        if self.speaks != (self.id == "incubator-1"):
            raise ValueError("only incubator-1 speaks")
        return self


def load_node_config(path: Path) -> NodeConfig:
    """Read and validate a `node.toml`. Raises ContractError naming the file."""
    try:
        return NodeConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except ValidationError as error:
        raise ContractError(f"{path}: {_first_reason(error)}") from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ContractError(f"{path}: {error}") from error


# --- subjects --------------------------------------------------------------------------------------------------------

FarmKind = Literal["status", "events", "assessment", "incident"]
JarvisKind = Literal["heartbeat", "capabilities", "ask", "probe", "audit", "chat", "reply", "alert", "ack", "notice",
                     "voice", "voice_settings", "ptt"]
FARM_LINKS = "farm.links"
FARM_SIM = "farm.sim"
MENTOR_LLM_FAILURES = "mentor.llm_failures"


def farm_subject(node: NodeId | Literal["*"], kind: FarmKind) -> str:
    """`farm.<node>.<kind>`; node "*" subscribes to all four."""
    return f"farm.{node}.{kind}"


def jarvis_subject(node: NodeId | Literal["*"], kind: JarvisKind) -> str:
    """`jarvis.<node>.<kind>`; node "*" subscribes to all four."""
    return f"jarvis.{node}.{kind}"


def node_of_subject(subject: str) -> NodeId | None:
    """The node named in a farm.* or jarvis.* subject, or None."""
    parts = subject.split(".")
    return next((node for node in NODE_IDS if len(parts) >= 2 and parts[1] == node), None)


# --- the farm feed ---------------------------------------------------------------------------------------------------


class FarmMessage(Contract):
    """What every farm message has: the machine it is about, the farm time it is from, and the farm run (`feed_id`)
    it belongs to. A reset or a restart of the farm starts a new run whose ticks, event sequences and incident ids
    begin again, so they are only unique within one feed_id."""

    node: NodeId
    tick: int = Field(ge=0)
    time_s: float = Field(ge=0.0)
    farm_time: str = Field(pattern=FARM_TIME)
    label: FeedLabel
    feed_id: str = Field(pattern=FEED_ID)


class FarmStatus(FarmMessage):
    """`farm.<node>.status`: the machine's public readings this tick (its public telemetry)."""

    online: bool
    readings: dict[str, JsonValue]


FarmEventType = Literal["alarm_raised", "alarm_cleared", "safety_action", "safety_communication", "machine_offline",
                        "machine_online"]


class FarmEvent(FarmMessage):
    """`farm.<node>.events`: one public event of the machine: an alarm raised or cleared, a protective action the
    safety gate allowed or refused, the machine going offline or back online."""

    sequence: int = Field(ge=0)
    type: FarmEventType
    severity: Literal["info", "warning", "critical"]
    data: dict[str, JsonValue]


class DocumentInfo(BaseModel):
    """Public metadata for an indexed ROM document announced by a node."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    file: str = Field(min_length=1)
    title: str = Field(min_length=1)
    kind: Literal["machine manual", "domain guide", "shared"]
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class GateDecision(BaseModel):
    """What the safety gate did with a node's proposal (the only path from a brain to a machine)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    proposed: str
    allowed: bool
    reason: str = ""


class FarmAssessment(FarmMessage):
    """`farm.<node>.assessment`: the node's reasoning output this tick: what it concludes, not a command."""

    assessment: Assessment
    diagnosis: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    incident_kind: str | None = None
    incident_id: str | None = None
    recommendation: str = ""
    evidence: list[str] = Field(default_factory=list[str])
    brain: Literal["rule", "ctm"]
    action: GateDecision | None = None
    communication: GateDecision | None = None


class FarmIncident(FarmMessage):
    """`farm.<node>.incident`: an incident the node's reasoning opened, changed or closed, with its public summary."""

    incident_id: str = Field(min_length=1)
    state: Literal["opened", "changed", "closed"]
    kind: str | None = None
    summary: dict[str, JsonValue]


class LinkStatus(Contract):
    """`farm.links`: which machines' radio links are cut and which machines are offline."""

    tick: int = Field(ge=0)
    time_s: float = Field(ge=0.0)
    cut: list[NodeId]
    offline: list[NodeId]


class SimState(Contract):
    """`farm.sim`: the farm clock: time, running or paused, the speed asked for and the speed reached."""

    tick: int = Field(ge=0)
    time_s: float = Field(ge=0.0)
    farm_time: str = Field(pattern=FARM_TIME)
    day: int = Field(ge=1)
    running: bool
    target_speed: float = Field(gt=0.0, le=1000.0)
    actual_speed: float = Field(ge=0.0)
    label: FeedLabel
    episode_id: str
    feed_id: str = Field(pattern=FEED_ID)


# --- between nodes ---------------------------------------------------------------------------------------------------


class Heartbeat(Contract):
    """`jarvis.<node>.heartbeat`: "alive", every 10 s and whenever the node's view of its peers changes."""

    node: NodeId
    seq: int = Field(ge=0)
    sent_utc: str
    started_utc: str
    uptime_s: float = Field(ge=0.0)
    farm_tick: int | None = Field(default=None, ge=0)
    known: list[NodeId]                                # nodes whose capabilities this node has
    alive: list[NodeId]                                # nodes it heard from recently (itself included)


class Capabilities(Contract):
    """`jarvis.<node>.capabilities`: what a node can answer, sent at start, on change, and when a node joins."""

    node: NodeId
    machine: Machine
    speaks: bool
    topics: list[Topic]
    documents: list[DocumentInfo]
    started_utc: str
    reason: Literal["start", "reply", "change"]


class AskRequest(Contract):
    """`jarvis.<target>.ask`: one node's question to another; `request_id` follows it across nodes (Q5)."""

    request_id: str = Field(pattern=REQUEST_ID)
    sender: NodeId
    target: NodeId
    topic: Topic
    question: str = Field(default="", max_length=2000)
    sent_utc: str
    deadline_ms: int = Field(ge=1, le=MAX_DEADLINE_MS)

    @model_validator(mode="after")
    def _not_to_itself(self) -> AskRequest:
        if self.sender == self.target:
            raise ValueError("a node does not ask itself")
        return self


class AskReply(Contract):
    """The answer to an AskRequest: `ok` with an answer, or an error that says why (never a hang, Q6)."""

    request_id: str = Field(pattern=REQUEST_ID)
    sender: NodeId                                     # the node that answered (or should have)
    target: NodeId                                     # the node that asked
    topic: Topic
    ok: bool
    answer: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    farm_time: str | None = Field(default=None, pattern=FARM_TIME)
    tick: int | None = Field(default=None, ge=0)
    error: AskError | None = None
    detail: str = Field(default="", max_length=500)
    elapsed_ms: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _ok_or_error(self) -> AskReply:
        if self.ok == (self.error is not None):
            raise ValueError("a reply is either ok or has an error")
        return self


class ProbeRequest(Contract):
    """`jarvis.<node>.probe` (tests and diagnostics only, opt-in): make this node ask a peer, through its own peer
    network, and return the reply. Read-only: it can only trigger a question."""

    target: NodeId
    topic: Topic
    question: str = Field(default="", max_length=2000)
    deadline_ms: int = Field(default=3000, ge=1, le=MAX_DEADLINE_MS)

    @field_validator("question")
    @classmethod
    def _text_is_printable(cls, value: str) -> str:
        return _printable(value)


class AuditRecord(Contract):
    """A bounded record of one tool or peer interaction, published on `jarvis.<node>.audit`."""

    request_id: str = Field(pattern=REQUEST_ID)
    node: NodeId
    kind: Literal["tool_call", "peer_answer", "peer_question", "refusal", "answer"]
    tool: str | None
    ok: bool
    error: str | None
    duration_ms: int = Field(ge=0)
    farm_time: str | None = Field(default=None, pattern=FARM_TIME)
    tick: int | None = Field(default=None, ge=0)
    inputs: dict[str, JsonValue]
    outputs: dict[str, JsonValue]
    truncated: bool
    created_utc: str

    @model_validator(mode="after")
    def _bounded_payloads(self) -> AuditRecord:
        for name, value in (("inputs", self.inputs), ("outputs", self.outputs)):
            if len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > 4096:
                raise ValueError(f"{name} exceeds 4 KB")
        return self


# --- the owner and JARVIS (6C, 6E): chat, alerts, acknowledgement, voice; the mentor's LLM failure switches ---------
#
# Fixed by the senior before 6C and 6E were built in parallel (docs/handoff/README.md): the gateway, incubator-1's
# agent and voice box, and the website's zod schemas all follow these. A change goes through the senior.


class Citation(BaseModel):
    """One approved document passage a reply used (from `search_documents` of that turn)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    node: NodeId                                       # the node whose index held the passage
    file: str = Field(min_length=1, max_length=200)
    section: str = Field(max_length=200)


class ToolUse(BaseModel):
    """One tool call a reply made, with its own node, outcome and duration (the audit has the details)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    node: NodeId
    tool: str = Field(min_length=1, max_length=64)
    ok: bool
    duration_ms: int = Field(ge=0)


class ReplyTimings(BaseModel):
    """How long a reply took, measured by incubator-1 from the moment the question arrived (monotonic clock)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    first_chunk_ms: int | None = Field(default=None, ge=0)      # None: no chunk was sent (a refusal or an error)
    total_ms: int = Field(ge=0)
    llm_ms: int | None = Field(default=None, ge=0)              # None: no language model was used
    tools_ms: int | None = Field(default=None, ge=0)            # None: no tool was called


class AckOffer(BaseModel):
    """The owner asked, in their own words, to acknowledge an alert: the website shows a confirm button for it. The
    token is single-use and expires (ConfirmTokens); only the owner's click sends it back (ground rule 3)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    node: NodeId                                       # the machine whose incident it is
    incident_id: str = Field(min_length=1, max_length=80)
    token: str = Field(min_length=1, max_length=256)
    expires_utc: str
    summary: str = Field(min_length=1, max_length=300)   # what a click acknowledges, in words


ReplyOutcome = Literal["answered", "fallback", "refused", "error"]


class ChatRequest(Contract):
    """`jarvis.incubator-1.chat`: one message from the owner. The gateway gives it its request_id; a null
    conversation_id starts a new conversation."""

    type: Literal["chat_request"] = "chat_request"
    request_id: str = Field(pattern=REQUEST_ID)
    conversation_id: str | None = Field(default=None, pattern=CONVERSATION_ID)
    text: str = Field(min_length=1, max_length=2000)
    source: Literal["chat", "voice"]
    sent_utc: str

    @field_validator("text")
    @classmethod
    def _text_is_printable(cls, value: str) -> str:
        return _printable(value)


class ChatChunk(Contract):
    """`jarvis.incubator-1.reply`: the next piece of a reply, sent only after verify.py passed it. The pieces of one
    request, joined in `seq` order with nothing between them, equal its ChatDone.text."""

    type: Literal["chat_chunk"] = "chat_chunk"
    request_id: str = Field(pattern=REQUEST_ID)
    seq: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=2000)


class ChatDone(Contract):
    """`jarvis.incubator-1.reply`: the end of a reply, always exactly one per request.

    `answered`: the language model's verified reply. `fallback`: a reply built from tool results with a fixed
    template, because the model, a peer or a deadline failed (`reason` says which). `refused`: the request asked for
    something JARVIS does not do (it talks, it never acts). `error`: no answer could be given. An answered or
    fallback reply states the farm time its data comes from (ground rule 6), so `farm_time` and `label` are set."""

    type: Literal["chat_done"] = "chat_done"
    request_id: str = Field(pattern=REQUEST_ID)
    conversation_id: str = Field(pattern=CONVERSATION_ID)
    text: str = Field(max_length=8000)
    chunks: int = Field(ge=0)                          # how many ChatChunks were sent before this, so a gap shows
    outcome: ReplyOutcome
    reason: str = Field(default="", max_length=300)
    numbers_checked: int = Field(ge=0)                 # numbers and times verify.py matched to this turn's tool results
    farm_time: str | None = Field(default=None, pattern=FARM_TIME)
    label: FeedLabel | None = None
    citations: list[Citation] = Field(default_factory=list[Citation], max_length=10)
    tools: list[ToolUse] = Field(default_factory=list[ToolUse], max_length=40)
    timings: ReplyTimings
    confirm: AckOffer | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ChatDone:
        if (self.outcome == "answered") != (self.reason == ""):
            raise ValueError("an answered reply has no reason, and every other outcome says why")
        if self.outcome in ("answered", "fallback") and (self.farm_time is None or self.label is None):
            raise ValueError("an answer states the farm time and the label of its data")
        if self.confirm is not None and self.outcome != "answered":
            raise ValueError("only an answered reply can offer a confirmation")
        return self


class OwnerAlert(Contract):
    """`jarvis.incubator-1.alert`: JARVIS tells the owner at once that a machine reached ESCALATE_TO_USER or DANGER.
    The text comes from a fixed template filled with that node's published output; `reworded` is true only when
    the language model's wording passed verify.py within the alert deadline."""

    type: Literal["owner_alert"] = "owner_alert"
    alert_id: str = Field(pattern=REQUEST_ID)
    node: NodeId
    level: Literal["ESCALATE_TO_USER", "DANGER"]
    incident_id: str | None = Field(default=None, min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1000)
    farm_time: str = Field(pattern=FARM_TIME)
    feed_id: str = Field(pattern=FEED_ID)
    label: FeedLabel
    reworded: bool
    sent_utc: str


class AckCommand(Contract):
    """`jarvis.incubator-1.ack` (request-reply, gateway → incubator-1): the owner clicked "confirm" on an AckOffer.
    incubator-1 checks the token (single-use, bound to this node and incident) and audits the decision; only then
    does the gateway pass the acknowledgement to the Matrix API."""

    type: Literal["ack_command"] = "ack_command"
    request_id: str = Field(pattern=REQUEST_ID)
    node: NodeId
    incident_id: str = Field(min_length=1, max_length=80)
    confirm_token: str = Field(min_length=1, max_length=256)
    sent_utc: str


class AckResult(Contract):
    """The answer to an AckCommand: ok, or the reason it was refused (a bad, used or expired token...)."""

    type: Literal["ack_result"] = "ack_result"
    request_id: str = Field(pattern=REQUEST_ID)
    node: NodeId
    incident_id: str = Field(min_length=1, max_length=80)
    ok: bool
    reason: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _ok_or_reason(self) -> AckResult:
        if self.ok != (self.reason == ""):
            raise ValueError("a result is ok, or refused with a reason")
        return self


class LlmFailureSwitches(Contract):
    """`mentor.llm_failures` (gateway → every node, test only): the simulated LLM failures that are on, by node.
    Only a node's failure-injection wrappers read it; its agent, prompts and tools never see it (E7). It never
    reaches the farm or the reasoning (ground rule 4)."""

    type: Literal["llm_failures"] = "llm_failures"
    llm_timeout: list[NodeId] = Field(default_factory=list[NodeId])   # the node's LLM calls never answer in time
    broken_json: list[NodeId] = Field(default_factory=list[NodeId])   # its LLM output is corrupted
    silent_peer: list[NodeId] = Field(default_factory=list[NodeId])   # it stops answering other nodes
    server_crash: list[NodeId] = Field(default_factory=list[NodeId])  # its LLM server is treated as down
    set_utc: str

    @model_validator(mode="after")
    def _no_repeats(self) -> LlmFailureSwitches:
        for name in ("llm_timeout", "broken_json", "silent_peer", "server_crash"):
            nodes = getattr(self, name)
            if len(set(nodes)) != len(nodes):
                raise ValueError(f"{name} names a node twice")
        return self


VoiceStateName = Literal["off", "idle", "wake", "listening", "thinking", "speaking", "error"]


class VoiceStatus(Contract):
    """`jarvis.incubator-1.voice`: what the voice box is doing, sent on every change and every 5 s. The website's
    voice indicator shows this, never a guess."""

    type: Literal["voice_status"] = "voice_status"
    state: VoiceStateName
    mic: Literal["ok", "missing", "busy", "off"]
    voice: str = Field(pattern=VOICE_ID)
    speed: float = Field(ge=0.5, le=2.0)
    push_to_talk: bool                                 # true while the owner holds the website's talk button
    transcript: str | None = Field(default=None, max_length=500)
    request_id: str | None = Field(default=None, pattern=REQUEST_ID)
    detail: str = Field(default="", max_length=200)
    sent_utc: str


class VoiceSettings(Contract):
    """`jarvis.incubator-1.voice_settings` (request-reply): the voice and speed the owner chose; incubator-1 checks
    them, saves them in llm.sqlite and uses them from the next sentence on."""

    type: Literal["voice_settings"] = "voice_settings"
    voice: str = Field(pattern=VOICE_ID)
    speed: float = Field(ge=0.5, le=2.0)


class VoiceSettingsResult(Contract):
    """The answer to VoiceSettings: the settings now in force and every voice the installed model has."""

    type: Literal["voice_settings_result"] = "voice_settings_result"
    ok: bool
    reason: str = Field(default="", max_length=300)
    voice: str = Field(pattern=VOICE_ID)
    speed: float = Field(ge=0.5, le=2.0)
    voices: list[str] = Field(max_length=100)

    @model_validator(mode="after")
    def _ok_or_reason(self) -> VoiceSettingsResult:
        if self.ok != (self.reason == ""):
            raise ValueError("a result is ok, or refused with a reason")
        return self


class PushToTalk(Contract):
    """`jarvis.incubator-1.ptt`: the owner pressed or released the website's talk button."""

    type: Literal["push_to_talk"] = "push_to_talk"
    pressed: bool
    sent_utc: str


#: Every wire message, for the JSON Schema export.
WIRE_MODELS: tuple[type[Contract], ...] = (FarmStatus, FarmEvent, FarmAssessment, FarmIncident, LinkStatus, SimState,
                                           Heartbeat, Capabilities, AskRequest, AskReply, ProbeRequest, AuditRecord,
                                           ChatRequest, ChatChunk, ChatDone, OwnerAlert, AckCommand, AckResult,
                                           LlmFailureSwitches, VoiceStatus, VoiceSettings, VoiceSettingsResult,
                                           PushToTalk)
