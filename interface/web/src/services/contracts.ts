// Wire contracts of the gateway, checked with zod before the website uses them (plan Q2).
//
// They mirror the pydantic models in nodes/incubator-1/contracts.py and interface/gateway/gateway.py, defined once in
// Python and exported as JSON Schemas to interface/contracts/. tests/test_contracts_zod.py runs the same sample
// messages through pydantic and through these schemas, so the two sides cannot drift apart. Every object is strict:
// an unknown field, or a message of another schema_version, is rejected, never guessed at.

import { z } from "zod"

export const NODE_IDS = ["incubator-1", "incubator-2", "hvac", "animal-protection"] as const

export const NodeId = z.enum(NODE_IDS)
export const Machine = z.enum(["incubator", "hvac", "animal_protection"])
export const AssessmentName = z.enum(["NORMAL", "HANDLED_BY_MACHINE", "WATCHING", "ESCALATE_TO_USER", "DANGER"])
export const FeedLabel = z.enum(["live: simulated brains", "live: trained brains"])
export const Topic = z.enum(["status", "readings", "incident_history", "incubation", "hazard_check", "documents"])
export const FarmEventType = z.enum([
  "alarm_raised",
  "alarm_cleared",
  "safety_action",
  "safety_communication",
  "machine_offline",
  "machine_online",
])

const SchemaVersion = z.literal(1)
const FarmTime = z.string().regex(/^([01][0-9]|2[0-3]):[0-5][0-9]$/)
const Tick = z.number().int().min(0)
const Seconds = z.number().min(0)
const JsonRecord = z.record(z.string(), z.json())
/** One farm run: new at every farm-runtime start and every reset (ticks and sequences start again in a new run). */
const FeedId = z.string().regex(/^[0-9a-f]{12}$/)

const farmMessage = {
  schema_version: SchemaVersion,
  node: NodeId,
  tick: Tick,
  time_s: Seconds,
  farm_time: FarmTime,
  label: FeedLabel,
  feed_id: FeedId,
}

export const FarmStatus = z.strictObject({ ...farmMessage, online: z.boolean(), readings: JsonRecord })

export const GateDecision = z.strictObject({ proposed: z.string(), allowed: z.boolean(), reason: z.string() })

export const FarmAssessment = z.strictObject({
  ...farmMessage,
  assessment: AssessmentName,
  diagnosis: z.string().min(1),
  confidence: z.number().min(0).max(1),
  incident_kind: z.string().nullable(),
  incident_id: z.string().nullable(),
  recommendation: z.string(),
  evidence: z.array(z.string()),
  brain: z.enum(["rule", "ctm"]),
  action: GateDecision.nullable(),
  communication: GateDecision.nullable(),
})

export const FarmEvent = z.strictObject({
  ...farmMessage,
  sequence: z.number().int().min(0),
  type: FarmEventType,
  severity: z.enum(["info", "warning", "critical"]),
  data: JsonRecord,
})

export const DocumentInfo = z.strictObject({
  file: z.string().min(1),
  title: z.string().min(1),
  kind: z.enum(["machine manual", "domain guide", "shared"]),
  version: z.number().int().min(1),
  sha256: z.string().regex(/^[0-9a-fA-F]{64}$/),
})

export const LinkStatus = z.strictObject({
  schema_version: SchemaVersion,
  tick: Tick,
  time_s: Seconds,
  cut: z.array(NodeId),
  offline: z.array(NodeId),
})

export const SimState = z.strictObject({
  schema_version: SchemaVersion,
  tick: Tick,
  time_s: Seconds,
  farm_time: FarmTime,
  day: z.number().int().min(1),
  running: z.boolean(),
  target_speed: z.number().gt(0).max(1000),
  actual_speed: z.number().min(0),
  label: FeedLabel,
  episode_id: z.string(),
  feed_id: FeedId,
})

export const Capabilities = z.strictObject({
  schema_version: SchemaVersion,
  node: NodeId,
  machine: Machine,
  speaks: z.boolean(),
  topics: z.array(Topic),
  documents: z.array(DocumentInfo),
  started_utc: z.string(),
  reason: z.enum(["start", "reply", "change"]),
})

export const NodeView = z.strictObject({
  id: NodeId,
  machine: Machine,
  alive: z.boolean(),
  heartbeat_age_s: z.number().min(0).nullable(),
  known: z.array(NodeId),
  capabilities: Capabilities.nullable(),
  status: FarmStatus.nullable(),
  assessment: FarmAssessment.nullable(),
})

/** `/ws/farm`: the whole farm as one message (at most five a second). */
export const FarmSnapshot = z.strictObject({
  schema_version: SchemaVersion,
  type: z.literal("farm_snapshot"),
  sent_utc: z.string(),
  sequence: z.number().int().min(0),
  sim: SimState.nullable(),
  links: LinkStatus.nullable(),
  nodes: z.record(NodeId, NodeView),
  events: z.array(FarmEvent).max(50),
})

export type FarmSnapshot = z.infer<typeof FarmSnapshot>
export type NodeView = z.infer<typeof NodeView>
export type SimState = z.infer<typeof SimState>
export type FarmEvent = z.infer<typeof FarmEvent>
export type FarmStatus = z.infer<typeof FarmStatus>
export type FarmAssessment = z.infer<typeof FarmAssessment>

// --- chat, acknowledgement, mentor and voice (6C, 6E; fixed by the senior before they were built, see contracts.py) ---

const RequestId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/)
const ConversationId = z.string().regex(/^[A-Za-z0-9_-]{1,64}$/)
const ClientMsgId = z.string().regex(/^[A-Za-z0-9_-]{1,64}$/)
const VoiceId = z.string().regex(/^[a-z]{2}_[a-z]+$/)
const IncidentId = z.string().min(1).max(80)
/** Free text may not carry control characters (tab, newline and return are fine), as on the Python side. */
// eslint-disable-next-line no-control-regex
const Printable = /^[^\x00-\x08\x0b\x0c\x0e-\x1f]*$/
const DurationMs = z.number().int().min(0)
const Speed = z.number().min(0.5).max(2)

export const Citation = z.strictObject({
  node: NodeId,
  file: z.string().min(1).max(200),
  section: z.string().max(200),
})

export const ToolUse = z.strictObject({
  node: NodeId,
  tool: z.string().min(1).max(64),
  ok: z.boolean(),
  duration_ms: DurationMs,
})

export const ReplyTimings = z.strictObject({
  first_chunk_ms: DurationMs.nullable(),
  total_ms: DurationMs,
  llm_ms: DurationMs.nullable(),
  tools_ms: DurationMs.nullable(),
})

export const AckOffer = z.strictObject({
  node: NodeId,
  incident_id: IncidentId,
  feed_id: FeedId, // the farm run: incident ids start again in every run
  token: z.string().min(1).max(256),
  expires_utc: z.string(),
  summary: z.string().min(1).max(300),
})

/** `/ws/chat`, website → gateway. */
export const ChatSend = z.strictObject({
  schema_version: SchemaVersion,
  type: z.literal("chat_send"),
  client_msg_id: ClientMsgId,
  conversation_id: ConversationId.nullable(),
  text: z.string().min(1).max(2000).regex(Printable),
})

export const ChatAccepted = z.strictObject({
  schema_version: SchemaVersion,
  type: z.literal("chat_accepted"),
  client_msg_id: ClientMsgId,
  request_id: RequestId,
})

/** A verified piece of a reply; the pieces of one request joined in `seq` order equal its ChatDone.text. */
export const ChatChunk = z.strictObject({
  schema_version: SchemaVersion,
  type: z.literal("chat_chunk"),
  request_id: RequestId,
  seq: z.number().int().min(0),
  text: z.string().min(1).max(2000),
})

export const ChatDone = z
  .strictObject({
    schema_version: SchemaVersion,
    type: z.literal("chat_done"),
    request_id: RequestId,
    conversation_id: ConversationId,
    text: z.string().max(8000),
    chunks: z.number().int().min(0),
    outcome: z.enum(["answered", "fallback", "refused", "error"]),
    reason: z.string().max(300),
    numbers_checked: z.number().int().min(0),
    farm_time: FarmTime.nullable(),
    label: FeedLabel.nullable(),
    citations: z.array(Citation).max(10),
    tools: z.array(ToolUse).max(40),
    timings: ReplyTimings,
    confirm: AckOffer.nullable(),
  })
  .refine((done) => (done.outcome === "answered") === (done.reason === ""), {
    message: "an answered reply has no reason, and every other outcome says why",
  })
  .refine(
    (done) => !(done.outcome === "answered" || done.outcome === "fallback") || (done.farm_time !== null && done.label !== null),
    { message: "an answer states the farm time and the label of its data" },
  )
  .refine((done) => done.confirm === null || done.outcome === "answered", {
    message: "only an answered reply can offer a confirmation",
  })

export const ChatError = z.strictObject({
  schema_version: SchemaVersion,
  type: z.literal("chat_error"),
  client_msg_id: ClientMsgId.nullable(),
  request_id: RequestId.nullable(),
  reason: z.string().min(1).max(300),
})

/** A proactive alert: a machine reached ESCALATE_TO_USER or DANGER (a fixed template, maybe reworded and verified). */
export const OwnerAlert = z.strictObject({
  schema_version: SchemaVersion,
  type: z.literal("owner_alert"),
  alert_id: RequestId,
  node: NodeId,
  level: z.enum(["ESCALATE_TO_USER", "DANGER"]),
  incident_id: IncidentId.nullable(),
  text: z.string().min(1).max(1000),
  farm_time: FarmTime,
  feed_id: FeedId,
  label: FeedLabel,
  reworded: z.boolean(),
  sent_utc: z.string(),
})

export const AckResponse = z
  .strictObject({
    schema_version: SchemaVersion,
    type: z.literal("ack_response"),
    node: NodeId,
    incident_id: IncidentId,
    ok: z.boolean(),
    stage: z.enum(["token", "matrix", "done"]),
    reason: z.string().max(300),
  })
  .refine((r) => r.ok === (r.stage === "done") && r.ok === (r.reason === ""), {
    message: "an acknowledgement is done with no reason, or stopped at a stage with a reason",
  })

const Bounded = z.json()
export const AuditRecord = z
  .strictObject({
    schema_version: SchemaVersion,
    request_id: RequestId,
    node: NodeId,
    kind: z.enum(["tool_call", "peer_answer", "peer_question", "refusal", "answer"]),
    tool: z.string().nullable(),
    ok: z.boolean(),
    error: z.string().nullable(),
    duration_ms: DurationMs,
    farm_time: FarmTime.nullable(),
    tick: Tick.nullable(),
    inputs: z.record(z.string(), Bounded),
    outputs: z.record(z.string(), Bounded),
    truncated: z.boolean(),
    created_utc: z.string(),
  })
  .refine(
    (r) =>
      new TextEncoder().encode(JSON.stringify(r.inputs)).length <= 4096 &&
      new TextEncoder().encode(JSON.stringify(r.outputs)).length <= 4096,
    { message: "inputs and outputs are at most 4 KB each" },
  )

const unique = (nodes: readonly string[]) => new Set(nodes).size === nodes.length
export const LlmFailureSwitches = z
  .strictObject({
    schema_version: SchemaVersion,
    type: z.literal("llm_failures"),
    llm_timeout: z.array(NodeId),
    broken_json: z.array(NodeId),
    silent_peer: z.array(NodeId),
    server_crash: z.array(NodeId),
    set_utc: z.string(),
  })
  .refine((s) => [s.llm_timeout, s.broken_json, s.silent_peer, s.server_crash].every(unique), {
    message: "a switch names a node twice",
  })

export const VoiceStatus = z.strictObject({
  schema_version: SchemaVersion,
  type: z.literal("voice_status"),
  state: z.enum(["off", "idle", "wake", "listening", "thinking", "speaking", "error"]),
  mic: z.enum(["ok", "missing", "busy", "off"]),
  voice: VoiceId,
  speed: Speed,
  push_to_talk: z.boolean(),
  transcript: z.string().max(500).nullable(),
  request_id: RequestId.nullable(),
  detail: z.string().max(200),
  sent_utc: z.string(),
})

/** POST /voice/settings, website → gateway. */
export const VoiceSettings = z.strictObject({
  schema_version: SchemaVersion,
  type: z.literal("voice_settings"),
  voice: VoiceId,
  speed: Speed,
})

export const VoiceSettingsResult = z
  .strictObject({
    schema_version: SchemaVersion,
    type: z.literal("voice_settings_result"),
    ok: z.boolean(),
    reason: z.string().max(300),
    voice: VoiceId,
    speed: Speed,
    voices: z.array(z.string()).max(100),
  })
  .refine((r) => r.ok === (r.reason === ""), { message: "a result is ok, or refused with a reason" })

export const OkResponse = z
  .strictObject({
    schema_version: SchemaVersion,
    type: z.literal("ok_response"),
    ok: z.boolean(),
    reason: z.string().max(300),
  })
  .refine((r) => r.ok === (r.reason === ""), { message: "ok, or refused with a reason" })

/** Everything `/ws/chat` sends to the website. */
export const ScenarioId = z.enum(["heater_relay_stuck", "supply_fan_failure", "humidity_sensor_drift", "supply_overvoltage"])

/** POST /faults/{scenario_id}: which library fault started, on which machines, from which tick of which farm run. */
export const FaultResult = z.strictObject({
  schema_version: SchemaVersion,
  type: z.literal("fault_result"),
  scenario_id: ScenarioId,
  family: z.string().min(1).max(40),
  variant: z.number().int().min(0),
  devices: z.array(NodeId).min(1).max(4),
  tick: Tick,
  end_tick: z.number().int().min(1).nullable(),
  time_s: Seconds,
  farm_time: FarmTime,
  feed_id: FeedId,
})

export const ChatServerMessage = z.union([ChatAccepted, ChatChunk, ChatDone, ChatError, OwnerAlert])

export type ChatSend = z.infer<typeof ChatSend>
export type ChatChunk = z.infer<typeof ChatChunk>
export type ChatDone = z.infer<typeof ChatDone>
export type ChatServerMessage = z.infer<typeof ChatServerMessage>
export type OwnerAlert = z.infer<typeof OwnerAlert>
export type AckOffer = z.infer<typeof AckOffer>
export type AckResponse = z.infer<typeof AckResponse>
export type FaultResult = z.infer<typeof FaultResult>
export type AuditRecord = z.infer<typeof AuditRecord>
export type LlmFailureSwitches = z.infer<typeof LlmFailureSwitches>
export type VoiceStatus = z.infer<typeof VoiceStatus>
export type VoiceSettings = z.infer<typeof VoiceSettings>
export type VoiceSettingsResult = z.infer<typeof VoiceSettingsResult>
export type OkResponse = z.infer<typeof OkResponse>

/** Every schema the gateway sends or the website sends it, by the name the Python side uses. */
export const SCHEMAS = {
  FarmSnapshot,
  FarmStatus,
  FarmAssessment,
  FarmEvent,
  LinkStatus,
  SimState,
  Capabilities,
  DocumentInfo,
  NodeView,
  GateDecision,
  ChatSend,
  ChatAccepted,
  ChatChunk,
  ChatDone,
  ChatError,
  OwnerAlert,
  AckResponse,
  AuditRecord,
  LlmFailureSwitches,
  VoiceStatus,
  VoiceSettings,
  VoiceSettingsResult,
  OkResponse,
  FaultResult,
  Citation,
  ToolUse,
  ReplyTimings,
  AckOffer,
} as const

export type SchemaName = keyof typeof SCHEMAS
