// The live farm, mapped onto the pages' FarmState (P1 section C2). Pure: no React, no network, no clock; Node runs it
// directly in scripts/check-live-map.ts (tests/test_live_map.py) and in the smoothness benchmark.
//
// Rules: values are the machine's own public readings, formatted, never invented; a missing reading is "No data".
// Farm time and the feed label are shown exactly as received. A new feed_id (a farm reset) starts the state afresh,
// so nothing of the previous run (events, history, incidents) is mixed in (rule 7). Live data never has `preview`.

import type { FarmEvent, FarmSnapshot, NodeView } from "./contracts.ts"
import {
  ASSESSMENT_LABELS,
  DEVICE_IDS,
  SIMULATION_SPEEDS,
  type Assessment,
  type Device,
  type DeviceId,
  type EventRecord,
  type EventSeverity,
  type FarmState,
  type JarvisActivityEntry,
  type JarvisNode,
  type LiveInfo,
  type Metric,
  type OperationalState,
  type ScenarioId,
  type SimulationSpeed,
} from "./matrix.ts"

/** At most this many conclusion changes are kept in the activity list. */
export const MAX_LIVE_ACTIVITY = 100
/** The gateway shows at most this many events; the website keeps the same bound itself. */
export const MAX_LIVE_EVENTS = 50

const NAMES: Record<DeviceId, { name: string; category: Device["category"]; detail: string }> = {
  "incubator-1": { name: "Incubator 1", category: "Incubator", detail: "Egg incubator. Live readings from its own controller." },
  "incubator-2": { name: "Incubator 2", category: "Incubator", detail: "Egg incubator. Live readings from its own controller." },
  hvac: { name: "HVAC", category: "HVAC", detail: "The central air handler for the barn, the storage room and the workspace. Live readings." },
  "animal-protection": {
    name: "Animal Protection",
    category: "Animal protection",
    detail: "Barn air quality, feed, water, windows and vents for the flock. Live readings.",
  },
}

const SEVERITY: Record<FarmEvent["severity"], EventSeverity> = { info: "Info", warning: "Warning", critical: "Critical" }

const EVENT_TYPES: Record<FarmEvent["type"], string> = {
  alarm_raised: "Alarm raised",
  alarm_cleared: "Alarm cleared",
  safety_action: "Safety gate: action",
  safety_communication: "Safety gate: call",
  machine_offline: "Machine offline",
  machine_online: "Machine back online",
}

type Readings = Record<string, unknown>

function num(readings: Readings, key: string): number | null {
  const value = readings[key]
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function nums(readings: Readings, key: string): number[] | null {
  const value = readings[key]
  if (!Array.isArray(value) || value.length === 0) return null
  return value.every((item) => typeof item === "number" && Number.isFinite(item)) ? (value as number[]) : null
}

function fixed(value: number, digits: number): string {
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function show(value: number | null, digits: number, unit: string): string {
  return value === null ? "No data" : `${fixed(value, digits)} ${unit}`
}

function incubatorMetrics(r: Readings): Metric[] {
  const humidities = nums(r, "humidities_pct")
  const fans = nums(r, "fan_rpm")
  const voltage = num(r, "ac_voltage_v")
  const day = num(r, "incubation_day")
  const remaining = num(r, "days_remaining")
  const eggs = num(r, "eggs")
  return [
    { label: "Temperature", value: show(num(r, "temperature_mean_c"), 1, "°C"), primary: true },
    { label: "Humidity sensors", value: humidities ? `${humidities.map((h) => fixed(h, 1)).join(" · ")} %` : "No data" },
    { label: "Supply voltage", value: show(voltage, 0, "V"), flagged: r.power_status === "overvoltage" },
    { label: "Fans", value: fans ? `${fans.map((f) => fixed(f, 0)).join(" · ")} rpm` : "No data" },
    { label: "Water reservoir", value: show(num(r, "reservoir_pct"), 0, "%") },
    {
      label: "Incubation",
      value: day !== null && remaining !== null ? `Day ${day} of ${day + remaining}${eggs !== null ? ` · ${eggs} eggs` : ""}` : "No data",
    },
  ]
}

function hvacMetrics(r: Readings): Metric[] {
  const power = num(r, "power_w")
  const mode = r.mode
  return [
    { label: "Airflow", value: show(num(r, "airflow_m3_h"), 0, "m³/h"), primary: true },
    { label: "Supply air", value: show(num(r, "supply_air_temperature_c"), 1, "°C") },
    { label: "Return air", value: show(num(r, "return_air_temperature_c"), 1, "°C") },
    { label: "Supply fan", value: show(num(r, "fan_speed_pct"), 0, "%") },
    { label: "Power", value: power === null ? "No data" : `${fixed(power / 1000, 1)} kW` },
    { label: "Mode", value: typeof mode === "string" && mode ? mode.charAt(0).toUpperCase() + mode.slice(1) : "No data" },
  ]
}

function protectionMetrics(r: Readings): Metric[] {
  const tankPct = num(r, "water_tank_level_pct")
  const tankL = num(r, "water_tank_volume_l")
  const feedPct = num(r, "feed_storage_level_pct")
  const feedKg = num(r, "feed_storage_remaining_kg")
  const windows = num(r, "windows_pct")
  const vents = num(r, "vents_pct")
  const capacity = (part: number | null, pct: number | null): number | null =>
    part !== null && pct !== null && pct > 0 ? (part / pct) * 100 : null
  const tankCapacity = capacity(tankL, tankPct)
  const feedCapacity = capacity(feedKg, feedPct)
  return [
    { label: "Barn CO₂", value: show(num(r, "co2_ppm"), 0, "ppm"), primary: true },
    { label: "Ammonia", value: show(num(r, "ammonia_ppm"), 1, "ppm") },
    {
      label: "Water tank",
      value: tankPct === null ? "No data" : `${fixed(tankPct, 0)} %${tankCapacity !== null ? ` of ${fixed(tankCapacity, 0)} L` : ""}`,
    },
    {
      label: "Feed store",
      value: feedPct === null ? "No data" : `${fixed(feedPct, 0)} %${feedCapacity !== null ? ` of ${fixed(feedCapacity, 0)} kg` : ""}`,
    },
    {
      label: "Windows · vents",
      value: windows === null || vents === null ? "No data" : `${fixed(windows, 0)} % · ${fixed(vents, 0)} % open`,
    },
    { label: "Chickens", value: show(num(r, "population_count"), 0, "").trim() },
  ]
}

function metricsFor(id: DeviceId, readings: Readings): Metric[] {
  if (id === "hvac") return hvacMetrics(readings)
  if (id === "animal-protection") return protectionMetrics(readings)
  return incubatorMetrics(readings)
}

function listed(readings: Readings, ...keys: string[]): boolean {
  return keys.some((key) => Array.isArray(readings[key]) && (readings[key] as unknown[]).length > 0)
}

/** The machine's own state: its faults, alarms and health, never the brain's conclusion (that is shown apart). */
function operational(readings: Readings): OperationalState {
  if (listed(readings, "faults", "active_faults")) return "critical"
  if (listed(readings, "alarms", "active_alarms")) return "warning"
  const health = readings.health
  return typeof health === "string" && health !== "normal" ? "warning" : "normal"
}

function running(id: DeviceId, readings: Readings): boolean {
  if (id === "hvac") return readings.supply_fan_state === "on" && (num(readings, "airflow_m3_h") ?? 0) > 0
  return readings.online !== false
}

function mapDevice(id: DeviceId, view: NodeView | undefined): Device {
  const status = view?.status ?? null
  const base = NAMES[id]
  if (!status || !status.online) {
    const metrics = metricsFor(id, {}).map((metric) => ({ ...metric, flagged: false }))
    return {
      id,
      ...base,
      connection: "disconnected",
      operational: "unknown",
      running: false,
      keyMeasurement: "No data",
      lastUpdate: status ? `farm ${status.farm_time} · offline` : "No data",
      metrics,
    }
  }
  const readings = status.readings as Readings
  const metrics = metricsFor(id, readings)
  return {
    id,
    ...base,
    connection: "connected",
    operational: operational(readings),
    running: running(id, readings),
    keyMeasurement: metrics.find((metric) => metric.primary)?.value ?? "No data",
    lastUpdate: `farm ${status.farm_time}`,
    metrics,
  }
}

function eventDescription(event: FarmEvent): string {
  const data = event.data as Record<string, unknown>
  switch (event.type) {
    case "alarm_raised":
    case "alarm_cleared":
      return `${String(data.code ?? "")} (${String(data.source ?? "alarm")})`
    case "safety_action":
    case "safety_communication":
      return `${String(data.proposed ?? "")}: ${data.allowed ? "allowed" : "blocked"}${data.reason ? `, ${String(data.reason)}` : ""}`
    default:
      return EVENT_TYPES[event.type]
  }
}

/** In live mode `acknowledged` means "no longer open": owners cannot acknowledge before 6C, so an alarm stays open
 *  until its machine clears it, and going offline until it is back online. Other events are records, never open. */
function stillOpen(events: FarmEvent[]): Set<FarmEvent> {
  // every raise of a still-open alarm stays open (a rise from warning to critical is raised again, not cleared)
  const open = new Map<string, FarmEvent[]>()
  for (const event of events) {
    const data = event.data as Record<string, unknown>
    const key = event.type === "alarm_raised" || event.type === "alarm_cleared" ? `${event.node}/alarm/${String(data.code)}` : `${event.node}/online`
    if (event.type === "alarm_raised" || event.type === "machine_offline") open.set(key, [...(open.get(key) ?? []), event])
    else if (event.type === "alarm_cleared" || event.type === "machine_online") open.delete(key)
  }
  return new Set([...open.values()].flat())
}

function mapEvent(event: FarmEvent, open: boolean): EventRecord {
  const gate = event.type === "safety_action" || event.type === "safety_communication"
  return {
    id: `${event.feed_id}-${event.sequence}`,
    severity: SEVERITY[event.severity],
    timeS: event.time_s,
    timestamp: event.farm_time,
    type: EVENT_TYPES[event.type],
    device: NAMES[event.node].name,
    deviceId: event.node,
    description: eventDescription(event),
    source: gate ? "JARVIS" : "Device",
    acknowledged: !open,
    evidence: `${event.label} · tick ${event.tick}`,
  }
}

function mapNode(id: DeviceId, view: NodeView | undefined, before: JarvisNode | undefined): JarvisNode {
  const assessment = view?.assessment ?? null
  if (!assessment) {
    return {
      id,
      assessment: "NORMAL",
      diagnosis: "NONE",
      reason: "No conclusion received yet in this farm run.",
      action: null,
      gate: null,
      ownerMessage: null,
      sinceS: 0,
      noConclusion: true,
    }
  }
  const level = assessment.assessment as Assessment
  const reason = assessment.evidence.length > 0 ? assessment.evidence.join("; ") : assessment.recommendation || "No evidence given."
  const changed = !before || before.assessment !== level || before.diagnosis !== assessment.diagnosis
  return {
    id,
    assessment: level,
    diagnosis: assessment.diagnosis,
    reason,
    action: assessment.action?.proposed ?? null,
    gate: assessment.action ? (assessment.action.allowed ? "approved" : "blocked") : null,
    ownerMessage: before?.ownerMessage ?? null,
    sinceS: changed || !before ? assessment.time_s : before.sinceS,
  }
}

function speedOf(target: number, before: SimulationSpeed | undefined): SimulationSpeed {
  return (SIMULATION_SPEEDS as readonly number[]).includes(target) ? (target as SimulationSpeed) : before ?? 60
}

/**
 * The pages' FarmState for one gateway snapshot. `previous` is the state made from the previous snapshot (or null);
 * it is used only within the same farm run: for when each conclusion started, the activity list and the chat.
 * `injected` is the demo fault the website started in this run, which the farm never publishes (ground rule 4).
 */
export function mapSnapshot(
  snapshot: FarmSnapshot,
  previous: FarmState | null,
  options: {
    invalidMessages?: number
    lastError?: string | null
    injected?: { scenarioId: ScenarioId; feedId: string; startS: number; endTick: number | null } | null
  } = {},
): FarmState {
  const sim = snapshot.sim
  const anyStatus = DEVICE_IDS.map((id) => snapshot.nodes[id]?.status).find((status) => status != null) ?? null
  const feedId = sim?.feed_id ?? anyStatus?.feed_id ?? ""
  const sameRun = previous?.source === "live" && previous.live?.feedId === feedId
  const before = sameRun ? previous : null
  const timeS = sim?.time_s ?? anyStatus?.time_s ?? 0

  const live: LiveInfo = {
    label: sim?.label ?? anyStatus?.label ?? null,
    farmTime: sim?.farm_time ?? anyStatus?.farm_time ?? null,
    day: sim?.day ?? null,
    tick: sim?.tick ?? anyStatus?.tick ?? null,
    feedId,
    targetSpeed: sim?.target_speed ?? null,
    actualSpeed: sim?.actual_speed ?? null,
    nodesAlive: DEVICE_IDS.filter((id) => snapshot.nodes[id]?.alive),
    invalidMessages: options.invalidMessages ?? previous?.live?.invalidMessages ?? 0,
    lastError: options.lastError ?? null,
    faultEndTick: null,
  }

  const nodes = {} as Record<DeviceId, JarvisNode>
  const activity: JarvisActivityEntry[] = before ? before.activity.slice() : []
  let nextActivity = before?.nextIds.activity ?? 1
  for (const id of DEVICE_IDS) {
    const node = mapNode(id, snapshot.nodes[id], before?.jarvis.nodes[id])
    const old = before?.jarvis.nodes[id]
    if (snapshot.nodes[id]?.assessment && (!old || old.noConclusion || old.assessment !== node.assessment || old.diagnosis !== node.diagnosis)) {
      activity.unshift({
        id: `ACT-${String(nextActivity++).padStart(6, "0")}`,
        timeS: node.sinceS,
        node: id,
        message: `${ASSESSMENT_LABELS[node.assessment]}${node.diagnosis === "NONE" ? "" : ` · ${node.diagnosis}`}: ${node.reason}`,
        tone: node.assessment === "ESCALATE_TO_USER" || node.assessment === "DANGER" ? "decision" : node.assessment === "WATCHING" ? "warning" : "info",
      })
    }
    nodes[id] = node
  }
  if (activity.length > MAX_LIVE_ACTIVITY) activity.length = MAX_LIVE_ACTIVITY

  const injected = options.injected && options.injected.feedId === feedId ? options.injected : null
  live.faultEndTick = injected?.endTick ?? null
  const runEvents = snapshot.events.filter((event) => event.feed_id === feedId).slice(-MAX_LIVE_EVENTS)
  const open = stillOpen(runEvents)
  return {
    source: "live",
    live,
    simulation: {
      running: sim?.running ?? false,
      timeS,
      // the pages' speed menu reads live.targetSpeed; this field only keeps the demo's type
      speed: speedOf(live.targetSpeed ?? 0, before?.simulation.speed),
      activeScenario: injected?.scenarioId ?? null,
      scenarioStartS: injected?.startS ?? null,
      nextBeat: 0,
    },
    devices: DEVICE_IDS.map((id) => mapDevice(id, snapshot.nodes[id])),
    events: runEvents.map((event) => mapEvent(event, open.has(event))).reverse(),
    jarvis: { nodes, messages: [], announcements: before?.jarvis.announcements ?? 0, lastAnnouncement: before?.jarvis.lastAnnouncement ?? "" },
    activity,
    chat: before ? before.chat : [],
    nextIds: { event: 1, activity: nextActivity, chat: before?.nextIds.chat ?? 1, message: 1 },
  }
}
