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

const farmMessage = {
  schema_version: SchemaVersion,
  node: NodeId,
  tick: Tick,
  time_s: Seconds,
  farm_time: FarmTime,
  label: FeedLabel,
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
  data: JsonRecord,
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
})

export const Capabilities = z.strictObject({
  schema_version: SchemaVersion,
  node: NodeId,
  machine: Machine,
  speaks: z.boolean(),
  topics: z.array(Topic),
  documents: z.array(z.string()),
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
  events: z.array(FarmEvent),
})

export type FarmSnapshot = z.infer<typeof FarmSnapshot>
export type NodeView = z.infer<typeof NodeView>
export type SimState = z.infer<typeof SimState>

/** Every schema the gateway sends, by the name the Python side uses. */
export const SCHEMAS = {
  FarmSnapshot,
  FarmStatus,
  FarmAssessment,
  FarmEvent,
  LinkStatus,
  SimState,
  Capabilities,
  NodeView,
  GateDecision,
} as const

export type SchemaName = keyof typeof SCHEMAS
