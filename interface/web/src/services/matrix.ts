// Demo data model for the JARVIS website.
//
// Everything in this file is demonstration data, scripted by hand. No value
// comes from the real Matrix, the CTM brains, the memory (CMM) or an LLM.
// The devices, thresholds, device codes and JARVIS assessments mirror the
// real farm simulation, so the demo shows what the system would
// actually do, and the shapes are ready to be fed by the live Matrix later.

export type ConnectionState = "connecting" | "connected" | "disconnected" | "demo"

export type DeviceCategory = "Incubator" | "HVAC" | "Animal protection"

export type OperationalState = "normal" | "warning" | "critical" | "unknown"

/** The real Matrix device IDs. */
export type DeviceId = "incubator-1" | "incubator-2" | "hvac" | "animal-protection"

export const DEVICE_IDS: DeviceId[] = ["incubator-1", "incubator-2", "hvac", "animal-protection"]

/** Named after the matching fault families in the real scenario library. */
export type ScenarioId = "heater_relay_stuck" | "supply_fan_failure" | "humidity_sensor_drift" | "supply_overvoltage"

export type Metric = {
  label: string
  value: string
  /** Marks a metric as the headline reading for cards and tables. */
  primary?: boolean
  /** Highlights a metric that the active scenario has pushed out of range. */
  flagged?: boolean
}

export type Device = {
  id: DeviceId
  name: string
  category: DeviceCategory
  connection: ConnectionState
  operational: OperationalState
  /** False while the machine's main function has stopped (for example the HVAC has no airflow). */
  running: boolean
  keyMeasurement: string
  lastUpdate: string
  metrics: Metric[]
  detail: string
}

export type EventSeverity = "Info" | "Warning" | "Critical"

export type EventSource = "Device" | "JARVIS" | "Operator"

export type EventRecord = {
  id: string
  severity: EventSeverity
  /** Simulated seconds since the demo started. */
  timeS: number
  timestamp: string
  type: string
  device: string
  deviceId: DeviceId | null
  description: string
  source: EventSource
  acknowledged: boolean
  evidence: string
}

/** The five conclusions a JARVIS node can reach (farm_poc/nodes/contracts.py). */
export const ASSESSMENTS = ["NORMAL", "HANDLED_BY_MACHINE", "WATCHING", "ESCALATE_TO_USER", "DANGER"] as const

export type Assessment = (typeof ASSESSMENTS)[number]

export const ASSESSMENT_LABELS: Record<Assessment, string> = {
  NORMAL: "Normal",
  HANDLED_BY_MACHINE: "Handled by machine",
  WATCHING: "Watching",
  ESCALATE_TO_USER: "Needs the owner",
  DANGER: "Danger",
}

export type GateVerdict = "approved" | "blocked"

/** One node of JARVIS: each machine carries one, and together they are one JARVIS. */
export type JarvisNode = {
  id: DeviceId
  assessment: Assessment
  diagnosis: string
  reason: string
  /** A protective action, only ever taken under DANGER and checked by the safety gate. */
  action: string | null
  gate: GateVerdict | null
  /** The last message this node sent to the owner. */
  ownerMessage: string | null
  sinceS: number
}

/** A broadcast over the peer link, sent whenever a node's conclusion changes. */
export type PeerMessage = {
  id: string
  timeS: number
  from: DeviceId
  assessment: Assessment
  diagnosis: string
  note: string
}

export type JarvisActivityEntry = {
  id: string
  timeS: number
  node: DeviceId | null
  message: string
  tone: "info" | "decision" | "warning"
}

export type JarvisState = {
  nodes: Record<DeviceId, JarvisNode>
  messages: PeerMessage[]
  /** How many messages JARVIS has sent to the owner, so the voice indicator can announce new ones. */
  announcements: number
  lastAnnouncement: string
}

export type ChatMessage = {
  id: string
  role: "operator" | "jarvis"
  text: string
  timeS: number
  /** Marks illustrative JARVIS replies as preview content, not real AI. */
  preview?: boolean
}

/** Simulated seconds that pass per real second. */
export const SIMULATION_SPEEDS = [10, 30, 60, 120] as const

export type SimulationSpeed = (typeof SIMULATION_SPEEDS)[number]

export type SimulationState = {
  running: boolean
  /** Simulated seconds since the demo started. */
  timeS: number
  speed: SimulationSpeed
  activeScenario: ScenarioId | null
  scenarioStartS: number | null
  /** Index of the next scripted moment of the active scenario. */
  nextBeat: number
}

/** What only a live farm has (src/services/live-map.ts): shown exactly as the gateway sent it. */
export type LiveInfo = {
  /** "live: simulated brains" or "live: trained brains". */
  label: string
  /** The farm's own clock, "HH:MM", and its day. */
  farmTime: string
  day: number
  tick: number
  /** One farm run; a reset starts a new one. */
  feedId: string
  /** The speed the website asked for and the speed the farm actually reaches (farm seconds per real second). */
  targetSpeed: number
  actualSpeed: number
  nodesAlive: DeviceId[]
  /** Snapshots refused by the zod check since the page opened (never shown as data). */
  invalidMessages: number
  /** The last refused farm action, with the gateway's reason. */
  lastError: string | null
}

export type FarmState = {
  source: "live" | "demo" | "disconnected"
  live?: LiveInfo
  simulation: SimulationState
  devices: Device[]
  events: EventRecord[]
  jarvis: JarvisState
  activity: JarvisActivityEntry[]
  chat: ChatMessage[]
  nextIds: {
    event: number
    activity: number
    chat: number
    message: number
  }
}

export type Scenario = {
  id: ScenarioId
  label: string
  shortLabel: string
  description: string
  devices: DeviceId[]
  /** Simulated seconds from injection until the scripted repair is complete. */
  durationS: number
}

// ---------------------------------------------------------------------------
// Fixtures (defaults of the real Matrix devices)
// ---------------------------------------------------------------------------

export const SCENARIOS: Scenario[] = [
  {
    id: "heater_relay_stuck",
    label: "Incubator heater relay stuck on",
    shortLabel: "Heater relay stuck",
    description:
      "Incubator 1's heater relay sticks closed, so the heater stays on. The temperature climbs until the independent high-limit thermostat cuts the heater at 39.5 °C, and it keeps cycling there until the relay is replaced.",
    devices: ["incubator-1"],
    durationS: 2100,
  },
  {
    id: "supply_fan_failure",
    label: "HVAC supply fan failure",
    shortLabel: "Supply fan failure",
    description:
      "The central unit's supply fan stops. The airflow-proving interlock switches cooling and heating off, so the barn, storage room and workspace drift toward the outdoor temperature.",
    devices: ["hvac"],
    durationS: 2100,
  },
  {
    id: "humidity_sensor_drift",
    label: "Incubator humidity sensor drift",
    shortLabel: "Sensor drift",
    description:
      "One of Incubator 2's three humidity sensors drifts upward while the air itself doesn't change. The other two keep reading normal, so JARVIS treats it as a sensor fault, not a humidity problem.",
    devices: ["incubator-2"],
    durationS: 1500,
  },
  {
    id: "supply_overvoltage",
    label: "Farm supply overvoltage",
    shortLabel: "Overvoltage",
    description:
      "The farm's mains rise to 263 V. Both incubators' overvoltage relays trip above 260 V and switch their heaters off. The two incubator nodes compare notes over the peer link and conclude it's the farm supply, not one machine.",
    devices: ["incubator-1", "incubator-2"],
    durationS: 900,
  },
]

const INCUBATOR_DETAIL =
  "Egg incubator in the workspace. Its own controller holds 37.5 °C and 55 % humidity. An independent high-limit thermostat cuts the heater at 39.5 °C, and an overvoltage relay switches it off above 260 V. Water comes from the farm main through a hose and a float valve; a backup battery keeps the fans and electronics running in a power cut."

function incubatorMetrics(): Metric[] {
  return [
    { label: "Temperature", value: "37.5 °C", primary: true },
    { label: "Humidity sensors", value: "55 · 55 · 55 %" },
    { label: "Supply voltage", value: "230 V" },
    { label: "Fans", value: "1,200 · 1,180 rpm" },
    { label: "Water reservoir", value: "95 %" },
    { label: "Incubation", value: "Day 14 of 21 · 24 eggs" },
  ]
}

const baseDevices: Device[] = [
  {
    id: "incubator-1",
    name: "Incubator 1",
    category: "Incubator",
    connection: "demo",
    operational: "normal",
    running: true,
    keyMeasurement: "37.5 °C",
    lastUpdate: "Reference",
    detail: INCUBATOR_DETAIL,
    metrics: incubatorMetrics(),
  },
  {
    id: "incubator-2",
    name: "Incubator 2",
    category: "Incubator",
    connection: "demo",
    operational: "normal",
    running: true,
    keyMeasurement: "37.5 °C",
    lastUpdate: "Reference",
    detail: INCUBATOR_DETAIL,
    metrics: incubatorMetrics(),
  },
  {
    id: "hvac",
    name: "HVAC",
    category: "HVAC",
    connection: "demo",
    operational: "normal",
    running: true,
    keyMeasurement: "1,800 m³/h",
    lastUpdate: "Reference",
    detail:
      "One central unit (1,800 m³/h, 10 kW cooling, 9 kW electric heat) conditions the barn, the storage room and the workspace through a damper for each room. An airflow-proving interlock stops cooling and heating if the supply fan fails.",
    metrics: [
      { label: "Airflow", value: "1,800 m³/h", primary: true },
      { label: "Supply air", value: "16 °C" },
      { label: "Return air", value: "23 °C" },
      { label: "Supply fan", value: "58 %" },
      { label: "Power", value: "1.8 kW" },
      { label: "Mode", value: "Automatic" },
    ],
  },
  {
    id: "animal-protection",
    name: "Animal Protection",
    category: "Animal protection",
    connection: "demo",
    operational: "normal",
    running: true,
    keyMeasurement: "600 ppm",
    lastUpdate: "Reference",
    detail:
      "Looks after the 24 chickens in the barn: air quality (CO₂, O₂, ammonia, CO and smoke), feed and the 1,000 L drinker tank, the windows and vents, the storm shutters, the flood barrier and the north gate. A 500 Wh battery keeps it running in a power cut.",
    metrics: [
      { label: "Barn CO₂", value: "600 ppm", primary: true },
      { label: "Ammonia", value: "5 ppm" },
      { label: "Water tank", value: "75 % of 1,000 L" },
      { label: "Feed store", value: "75 % of 50 kg" },
      { label: "Windows · vents", value: "30 % · 40 % open" },
      { label: "Chickens", value: "24" },
    ],
  },
]

function normalNode(id: DeviceId, sinceS = 0): JarvisNode {
  return {
    id,
    assessment: "NORMAL",
    diagnosis: "NONE",
    reason: "All readings are in range.",
    action: null,
    gate: null,
    ownerMessage: null,
    sinceS,
  }
}

const baseSimulation: SimulationState = {
  running: false,
  timeS: 0,
  speed: 60,
  activeScenario: null,
  scenarioStartS: null,
  nextBeat: 0,
}

// ---------------------------------------------------------------------------
// State factory + helpers
// ---------------------------------------------------------------------------

export function createInitialState(): FarmState {
  const devices = cloneDevices(baseDevices).map((device) => ({
    ...device,
    connection: "disconnected" as const,
    operational: "unknown" as const,
    keyMeasurement: "No data",
    lastUpdate: "No data",
    metrics: device.metrics.map((metric) => ({ ...metric, value: "No data", flagged: false })),
  }))
  return {
    source: "disconnected",
    simulation: { ...baseSimulation },
    devices,
    events: [],
    jarvis: {
      nodes: {
        "incubator-1": normalNode("incubator-1"),
        "incubator-2": normalNode("incubator-2"),
        hvac: normalNode("hvac"),
        "animal-protection": normalNode("animal-protection"),
      },
      messages: [],
      announcements: 0,
      lastAnnouncement: "",
    },
    activity: [],
    chat: [],
    nextIds: { event: 1, activity: 1, chat: 1, message: 1 },
  }
}

export function createDemoState(): FarmState {
  const state = createInitialState()
  state.source = "demo"
  state.devices = cloneDevices(baseDevices)
  return state
}

export function cloneDevices(devices: Device[]): Device[] {
  return devices.map((device) => ({
    ...device,
    metrics: device.metrics.map((metric) => ({ ...metric })),
  }))
}

export function cloneState(state: FarmState): FarmState {
  return {
    ...state,
    nextIds: { ...state.nextIds },
    devices: cloneDevices(state.devices),
    events: state.events.map((event) => ({ ...event })),
    jarvis: {
      ...state.jarvis,
      nodes: {
        "incubator-1": { ...state.jarvis.nodes["incubator-1"] },
        "incubator-2": { ...state.jarvis.nodes["incubator-2"] },
        hvac: { ...state.jarvis.nodes.hvac },
        "animal-protection": { ...state.jarvis.nodes["animal-protection"] },
      },
      messages: state.jarvis.messages.map((message) => ({ ...message })),
    },
    activity: state.activity.map((entry) => ({ ...entry })),
    chat: state.chat.map((message) => ({ ...message })),
    simulation: { ...state.simulation },
  }
}

export function findScenario(id: ScenarioId): Scenario | undefined {
  return SCENARIOS.find((scenario) => scenario.id === id)
}

export function deviceById(state: FarmState, id: DeviceId): Device | undefined {
  return state.devices.find((device) => device.id === id)
}

export function deviceName(id: DeviceId): string {
  return baseDevices.find((device) => device.id === id)?.name ?? id
}

/** Simulated time as "+mm:ss" or "+h:mm:ss". */
export function formatSimTime(timeS: number): string {
  const total = Math.max(0, Math.round(timeS))
  const hours = Math.floor(total / 3600)
  const minutes = String(Math.floor((total % 3600) / 60)).padStart(2, "0")
  const seconds = String(total % 60).padStart(2, "0")
  return hours > 0 ? `+${hours}:${minutes}:${seconds}` : `+${minutes}:${seconds}`
}

/** "SUPPLY_FAN_FAULT" → "Supply fan fault"; "NONE" → "None". */
export function diagnosisLabel(diagnosis: string): string {
  const words = diagnosis.toLowerCase().replace(/_/g, " ")
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function simulationLabel(state: FarmState): "Running" | "Paused" | "Stopped" {
  if (state.simulation.running) return "Running"
  return state.simulation.timeS > 0 ? "Paused" : "Stopped"
}

export function activeIncidentCount(state: FarmState): number {
  return state.events.filter((event) => !event.acknowledged && event.severity !== "Info").length
}

// ---------------------------------------------------------------------------
// Demo engine: scripted scenarios, telemetry, events, JARVIS nodes
// ---------------------------------------------------------------------------

export function startSimulation(state: FarmState): FarmState {
  const next = cloneState(state)
  if (next.source !== "demo") return next
  next.simulation.running = true
  return next
}

export function setSimulationSpeed(state: FarmState, speed: SimulationSpeed): FarmState {
  const next = cloneState(state)
  if (next.source !== "demo") return next
  next.simulation.speed = speed
  return next
}

export function pauseSimulation(state: FarmState): FarmState {
  const next = cloneState(state)
  if (next.source !== "demo") return next
  next.simulation.running = false
  return next
}

export function resetSimulation(): FarmState {
  return createDemoState()
}

const MAX_EVENTS = 200
const MAX_ACTIVITY = 100
const MAX_MESSAGES = 100
const MAX_CHAT = 50

type Beat = { atS: number; run: (state: FarmState) => void }

function pushEvent(
  state: FarmState,
  event: Omit<EventRecord, "id" | "timeS" | "timestamp" | "acknowledged">,
): void {
  state.events.unshift({
    id: `EVT-${String(state.nextIds.event++).padStart(6, "0")}`,
    timeS: state.simulation.timeS,
    timestamp: formatSimTime(state.simulation.timeS),
    acknowledged: false,
    ...event,
  })
  if (state.events.length > MAX_EVENTS) state.events.length = MAX_EVENTS
}

function pushActivity(state: FarmState, node: DeviceId | null, message: string, tone: JarvisActivityEntry["tone"]): void {
  state.activity.unshift({
    id: `ACT-${String(state.nextIds.activity++).padStart(6, "0")}`,
    timeS: state.simulation.timeS,
    node,
    message,
    tone,
  })
  if (state.activity.length > MAX_ACTIVITY) state.activity.length = MAX_ACTIVITY
}

function setMetric(state: FarmState, id: DeviceId, label: string, value: string, flagged = false): void {
  const device = deviceById(state, id)
  const metric = device?.metrics.find((item) => item.label === label)
  if (!device || !metric) return
  metric.value = value
  metric.flagged = flagged
  if (metric.primary) device.keyMeasurement = value
}

function setOperational(state: FarmState, id: DeviceId, operational: OperationalState, running?: boolean): void {
  const device = deviceById(state, id)
  if (!device) return
  device.operational = operational
  if (running !== undefined) device.running = running
}

function deviceEvent(state: FarmState, id: DeviceId, severity: EventSeverity, type: string, description: string, evidence: string): void {
  pushEvent(state, { severity, type, device: deviceName(id), deviceId: id, description, evidence, source: "Device" })
}

function operatorEvent(state: FarmState, id: DeviceId, type: string, description: string): void {
  pushEvent(state, { severity: "Info", type, device: deviceName(id), deviceId: id, description, evidence: "Operator action (demo)", source: "Operator" })
}

/** Change a node's conclusion; like the real nodes, it then broadcasts the change to its peers. */
function assess(state: FarmState, id: DeviceId, assessment: Assessment, diagnosis: string, reason: string): void {
  const node = state.jarvis.nodes[id]
  if (node.assessment === assessment && node.diagnosis === diagnosis && node.reason === reason) return
  node.assessment = assessment
  node.diagnosis = diagnosis
  node.reason = reason
  node.sinceS = state.simulation.timeS
  const tone = assessment === "ESCALATE_TO_USER" || assessment === "DANGER" ? "decision" : assessment === "WATCHING" ? "warning" : "info"
  pushActivity(state, id, `${ASSESSMENT_LABELS[assessment]}${diagnosis === "NONE" ? "" : ` · ${diagnosisLabel(diagnosis)}`}: ${reason}`, tone)
  state.jarvis.messages.unshift({
    id: `MSG-${String(state.nextIds.message++).padStart(6, "0")}`,
    timeS: state.simulation.timeS,
    from: id,
    assessment,
    diagnosis,
    note: reason,
  })
  if (state.jarvis.messages.length > MAX_MESSAGES) state.jarvis.messages.length = MAX_MESSAGES
}

function notifyOwner(state: FarmState, id: DeviceId, text: string): void {
  const node = state.jarvis.nodes[id]
  node.ownerMessage = text
  pushEvent(state, {
    severity: node.assessment === "DANGER" ? "Critical" : "Warning",
    type: "JARVIS notified the owner",
    device: deviceName(id),
    deviceId: id,
    description: text,
    evidence: `${ASSESSMENT_LABELS[node.assessment]} · ${diagnosisLabel(node.diagnosis)}`,
    source: "JARVIS",
  })
  pushActivity(state, id, `Notified the owner: ${text}`, "decision")
  state.jarvis.announcements += 1
  state.jarvis.lastAnnouncement = text
}

function restoreDevice(state: FarmState, id: DeviceId): void {
  const device = deviceById(state, id)
  const base = baseDevices.find((item) => item.id === id)
  if (!device || !base) return
  device.operational = base.operational
  device.running = base.running
  device.keyMeasurement = base.keyMeasurement
  device.metrics = base.metrics.map((metric) => ({ ...metric }))
}

/** End of the scripted repair: the machines are back to their defaults and the nodes back to NORMAL. */
function finishScenario(state: FarmState): void {
  const scenario = state.simulation.activeScenario ? findScenario(state.simulation.activeScenario) : undefined
  if (!scenario) return
  for (const id of scenario.devices) {
    restoreDevice(state, id)
    assess(state, id, "NORMAL", "NONE", "All readings are back in range.")
    state.jarvis.nodes[id].ownerMessage = null
  }
  pushEvent(state, {
    severity: "Info",
    type: "Scenario finished",
    device: scenario.devices.map(deviceName).join(", "),
    deviceId: scenario.devices.length === 1 ? scenario.devices[0] : null,
    description: `${scenario.shortLabel}: repaired and back to normal.`,
    evidence: "Demo script",
    source: "Operator",
  })
  state.simulation.activeScenario = null
  state.simulation.scenarioStartS = null
  state.simulation.nextBeat = 0
}

const SCRIPTS: Record<ScenarioId, Beat[]> = {
  heater_relay_stuck: [
    {
      atS: 30,
      run: (s) => {
        setMetric(s, "incubator-1", "Temperature", "37.7 °C", true)
        setOperational(s, "incubator-1", "warning")
        assess(s, "incubator-1", "WATCHING", "HEATER_FAULT", "The temperature is above the setpoint while the controller is asking for less heat.")
      },
    },
    {
      atS: 90,
      run: (s) => {
        setMetric(s, "incubator-1", "Temperature", "37.9 °C", true)
        deviceEvent(s, "incubator-1", "Warning", "Heater relay stuck", "Heater current keeps flowing while the controller commands the heater off: the relay is stuck closed.", "Device code HEATER_RELAY_STUCK · Temperature 37.9 °C")
        assess(s, "incubator-1", "ESCALATE_TO_USER", "HEATER_FAULT", "The incubator reports its heater relay stuck on. Only a repair fixes this.")
        notifyOwner(s, "incubator-1", "Incubator 1: the heater relay is stuck on and the temperature is rising (37.9 °C). The high-limit thermostat will cut the heater at 39.5 °C, but the relay needs replacing.")
      },
    },
    { atS: 360, run: (s) => setMetric(s, "incubator-1", "Temperature", "38.6 °C", true) },
    {
      atS: 780,
      run: (s) => {
        setMetric(s, "incubator-1", "Temperature", "39.5 °C", true)
        deviceEvent(s, "incubator-1", "Warning", "High-limit cutoff", "The independent high-limit thermostat cut the heater at 39.5 °C. It will keep cycling the heater until the relay is replaced.", "Local protection · Temperature 39.5 °C")
        pushActivity(s, "incubator-1", "Local protection is holding the temperature near 39.5 °C. Still escalated: the relay needs a repair.", "info")
      },
    },
    {
      atS: 1200,
      run: (s) => {
        operatorEvent(s, "incubator-1", "Repair done", "The heater relay was replaced. The temperature is coming back down.")
        setOperational(s, "incubator-1", "normal")
        assess(s, "incubator-1", "HANDLED_BY_MACHINE", "HEATER_FAULT", "Repaired; the controller is bringing the temperature back down.")
      },
    },
    { atS: 1500, run: (s) => setMetric(s, "incubator-1", "Temperature", "38.6 °C", true) },
    { atS: 1800, run: (s) => setMetric(s, "incubator-1", "Temperature", "37.9 °C") },
    { atS: 2100, run: finishScenario },
  ],
  supply_fan_failure: [
    {
      atS: 20,
      run: (s) => {
        setMetric(s, "hvac", "Airflow", "0 m³/h", true)
        setMetric(s, "hvac", "Supply fan", "0 %", true)
        setMetric(s, "hvac", "Power", "0.2 kW")
        setMetric(s, "hvac", "Mode", "Interlock: no cooling or heating", true)
        setOperational(s, "hvac", "warning", false)
        deviceEvent(s, "hvac", "Warning", "Supply fan failure", "The supply fan stopped and the airflow fell to zero. The airflow-proving interlock switched cooling and heating off.", "Device code SUPPLY_FAN_FAILURE · Airflow 0 m³/h")
      },
    },
    {
      atS: 30,
      run: (s) => {
        assess(s, "hvac", "ESCALATE_TO_USER", "SUPPLY_FAN_FAULT", "The unit reports a supply fan failure. It can't condition any room until the fan is repaired.")
        notifyOwner(s, "hvac", "HVAC: the supply fan has stopped, so the unit can't cool or heat. The barn, storage room and workspace will drift toward the outdoor temperature. The fan needs a repair.")
      },
    },
    { atS: 600, run: (s) => setMetric(s, "hvac", "Return air", "24 °C", true) },
    { atS: 1200, run: (s) => setMetric(s, "hvac", "Return air", "25 °C", true) },
    {
      atS: 1500,
      run: (s) => {
        operatorEvent(s, "hvac", "Repair done", "The supply fan was repaired. Airflow, cooling and heating are back.")
        restoreDevice(s, "hvac")
        setMetric(s, "hvac", "Return air", "24 °C", true)
        assess(s, "hvac", "HANDLED_BY_MACHINE", "SUPPLY_FAN_FAULT", "Repaired; the rooms are cooling back down.")
      },
    },
    { atS: 2100, run: finishScenario },
  ],
  humidity_sensor_drift: [
    { atS: 120, run: (s) => setMetric(s, "incubator-2", "Humidity sensors", "55 · 57 · 55 %") },
    {
      atS: 240,
      run: (s) => {
        setMetric(s, "incubator-2", "Humidity sensors", "55 · 59 · 55 %", true)
        setOperational(s, "incubator-2", "warning")
        assess(s, "incubator-2", "WATCHING", "SENSOR_FAULT", "Humidity sensor 2 disagrees with sensors 1 and 3, and nothing else in the incubator changed.")
      },
    },
    {
      atS: 540,
      run: (s) => {
        setMetric(s, "incubator-2", "Humidity sensors", "55 · 62 · 55 %", true)
        assess(s, "incubator-2", "ESCALATE_TO_USER", "SENSOR_FAULT", "The disagreement has lasted 5 minutes: sensor 2 is drifting.")
        notifyOwner(s, "incubator-2", "Incubator 2: humidity sensor 2 reads 62 % while the other two read 55 %. The air itself is fine; the sensor needs recalibrating or replacing.")
      },
    },
    {
      atS: 1200,
      run: (s) => {
        operatorEvent(s, "incubator-2", "Repair done", "Humidity sensor 2 was recalibrated.")
        setMetric(s, "incubator-2", "Humidity sensors", "55 · 55 · 55 %")
        setOperational(s, "incubator-2", "normal")
        assess(s, "incubator-2", "HANDLED_BY_MACHINE", "SENSOR_FAULT", "Recalibrated; checking that the three sensors agree.")
      },
    },
    { atS: 1500, run: finishScenario },
  ],
  supply_overvoltage: [
    {
      atS: 2,
      run: (s) => {
        for (const id of ["incubator-1", "incubator-2"] as const) {
          setMetric(s, id, "Supply voltage", "263 V", true)
          setOperational(s, id, "warning")
          deviceEvent(s, id, "Warning", "Overvoltage protection", "The mains are at 263 V, above the 260 V limit: the overvoltage relay switched the heater off.", "Device code OVERVOLTAGE_PROTECTION · Supply 263 V")
        }
      },
    },
    {
      atS: 5,
      run: (s) => {
        for (const id of ["incubator-1", "incubator-2"] as const) {
          assess(s, id, "HANDLED_BY_MACHINE", "SUPPLY_OVERVOLTAGE", "The overvoltage relay protected the machine.")
        }
      },
    },
    {
      atS: 10,
      run: (s) => {
        assess(s, "incubator-1", "ESCALATE_TO_USER", "SUPPLY_OVERVOLTAGE", "Incubator 2 reports the same 263 V, so this is the farm supply, not one machine.")
        notifyOwner(s, "incubator-1", "Both incubators measure 263 V and their protection relays have switched the heaters off. This looks like the farm's mains supply, not a machine fault. The relays reset by themselves 2 minutes after the voltage is normal again.")
        assess(s, "incubator-2", "ESCALATE_TO_USER", "SUPPLY_OVERVOLTAGE", "Same incident as Incubator 1, which has already told the owner.")
      },
    },
    {
      atS: 240,
      run: (s) => {
        for (const id of ["incubator-1", "incubator-2"] as const) {
          setMetric(s, id, "Supply voltage", "230 V")
          setMetric(s, id, "Temperature", "36.9 °C", true)
        }
        pushActivity(s, null, "The supply is back to 230 V. The relays wait 2 minutes of normal voltage before resetting.", "info")
      },
    },
    {
      atS: 360,
      run: (s) => {
        for (const id of ["incubator-1", "incubator-2"] as const) {
          setMetric(s, id, "Temperature", "36.8 °C", true)
          setOperational(s, id, "normal")
          deviceEvent(s, id, "Info", "Protection reset", "The supply has been normal for 2 minutes: the overvoltage relay reset and the heater is back on.", "Supply 230 V")
          assess(s, id, "HANDLED_BY_MACHINE", "SUPPLY_OVERVOLTAGE", "The supply is normal again; the heater is warming the eggs back up.")
        }
      },
    },
    {
      atS: 600,
      run: (s) => {
        for (const id of ["incubator-1", "incubator-2"] as const) setMetric(s, id, "Temperature", "37.2 °C", true)
      },
    },
    { atS: 900, run: finishScenario },
  ],
}

/**
 * Advance the demo by one step of `speed` simulated seconds and play every
 * scripted moment of the active scenario that is now due.
 */
export function advanceTick(state: FarmState): FarmState {
  if (state.source !== "demo" || !state.simulation.running) return state
  const next = cloneState(state)
  next.simulation.timeS += next.simulation.speed
  const stamp = formatSimTime(next.simulation.timeS)
  next.devices.forEach((device) => {
    device.lastUpdate = stamp
  })
  playDueBeats(next)
  return next
}

function playDueBeats(state: FarmState): void {
  const scenarioId = state.simulation.activeScenario
  if (!scenarioId || state.simulation.scenarioStartS === null) return
  const beats = SCRIPTS[scenarioId]
  const startS = state.simulation.scenarioStartS
  const stepEndS = state.simulation.timeS
  while (state.simulation.activeScenario === scenarioId && state.simulation.nextBeat < beats.length && startS + beats[state.simulation.nextBeat].atS <= stepEndS) {
    const beat = beats[state.simulation.nextBeat]
    state.simulation.nextBeat += 1
    // Stamp events and decisions with the moment they happen, not the end of the step.
    state.simulation.timeS = startS + beat.atS
    beat.run(state)
  }
  state.simulation.timeS = stepEndS
}

/** Start a scripted scenario. It plays as the simulated clock advances. */
export function injectScenario(state: FarmState, scenarioId: ScenarioId): FarmState {
  const next = cloneState(state)
  if (next.source !== "demo") return next
  const scenario = findScenario(scenarioId)
  if (!scenario || next.simulation.activeScenario) return next
  next.simulation.activeScenario = scenario.id
  next.simulation.scenarioStartS = next.simulation.timeS
  next.simulation.nextBeat = 0
  pushEvent(next, {
    severity: "Info",
    type: "Scenario injected",
    device: scenario.devices.map(deviceName).join(", "),
    deviceId: scenario.devices.length === 1 ? scenario.devices[0] : null,
    description: `Operator injected the "${scenario.label}" demo scenario.`,
    evidence: "Operator action (demo)",
    source: "Operator",
  })
  return next
}

export function acknowledgeEvent(state: FarmState, eventId: string): FarmState {
  const next = cloneState(state)
  const event = next.events.find((item) => item.id === eventId)
  if (event) event.acknowledged = true
  return next
}

export function acknowledgeAll(state: FarmState): FarmState {
  const next = cloneState(state)
  next.events.forEach((event) => {
    event.acknowledged = true
  })
  return next
}

export function clearResolvedEvents(state: FarmState): FarmState {
  // Only remove acknowledged Info entries (e.g. "Scenario finished" notes an
  // operator has already seen). Unacknowledged Info and all Warning/Critical
  // events remain so the incident history is never erased to reset counters.
  const next = cloneState(state)
  next.events = next.events.filter((event) => !(event.severity === "Info" && event.acknowledged))
  return next
}

// JARVIS chat is a deterministic, clearly-labeled preview. Replies are
// canned and explicitly marked so they are never mistaken for real AI.
// They read the current demo state so they agree with every other page.

function nodeSummary(state: FarmState): string {
  return DEVICE_IDS.map((id) => {
    const node = state.jarvis.nodes[id]
    const diagnosis = node.diagnosis === "NONE" ? "" : ` (${diagnosisLabel(node.diagnosis).toLowerCase()})`
    return `${deviceName(id)}: ${ASSESSMENT_LABELS[node.assessment].toLowerCase()}${diagnosis}`
  }).join(". ")
}

function metricValue(state: FarmState, id: DeviceId, label: string): string {
  return deviceById(state, id)?.metrics.find((metric) => metric.label === label)?.value ?? "no data"
}

function buildPreviewReply(state: FarmState, text: string): string {
  const normalized = text.toLowerCase()
  const scenario = state.simulation.activeScenario ? findScenario(state.simulation.activeScenario) : null
  const scenarioNote = scenario ? `Active scenario: ${scenario.label}.` : "No scenario is running."

  if (/status|state|overview|how is|how are/.test(normalized)) {
    return `Preview reply: ${nodeSummary(state)}. ${scenarioNote}`
  }
  if (/incubator|egg|temperature|humidity|heater/.test(normalized)) {
    return `Preview reply: Incubator 1 reads ${metricValue(state, "incubator-1", "Temperature")} and Incubator 2 reads ${metricValue(state, "incubator-2", "Temperature")}. Humidity sensors: ${metricValue(state, "incubator-1", "Humidity sensors")} and ${metricValue(state, "incubator-2", "Humidity sensors")}. ${scenarioNote}`
  }
  if (/hvac|fan|airflow|air flow|cooling/.test(normalized)) {
    return `Preview reply: the HVAC moves ${metricValue(state, "hvac", "Airflow")} with return air at ${metricValue(state, "hvac", "Return air")}. Its node says: ${ASSESSMENT_LABELS[state.jarvis.nodes.hvac.assessment].toLowerCase()}.`
  }
  if (/chicken|barn|co2|co₂|ammonia|water|feed|animal/.test(normalized)) {
    return `Preview reply: the barn has ${metricValue(state, "animal-protection", "Chickens")} chickens, CO₂ at ${metricValue(state, "animal-protection", "Barn CO₂")} and ammonia at ${metricValue(state, "animal-protection", "Ammonia")}. Water tank ${metricValue(state, "animal-protection", "Water tank")}, feed store ${metricValue(state, "animal-protection", "Feed store")}.`
  }
  if (/scenario|fault|inject/.test(normalized)) {
    return "Preview reply: open the Simulator page, pick a scenario and select Inject scenario. It plays a scripted incident while the simulated clock runs."
  }
  if (/jarvis|ctm|brain|memory|llm/.test(normalized)) {
    return "Preview reply: JARVIS is one intelligence with a node on every machine, and you talk to it through incubator-1. Here the nodes follow a demo script; the real nodes (CTM brain, memory and LLM) aren't connected to this website yet."
  }
  if (/hello|hi\b|hey|help/.test(normalized)) {
    return "Preview reply: this is a conversation preview. Ask about the status, the incubators, the HVAC, the barn or JARVIS to get canned demo replies."
  }
  return "Preview reply: I can talk about the demo devices, the scenarios and the JARVIS nodes. No real AI is connected yet."
}

export function sendChatMessage(state: FarmState, text: string): FarmState {
  const trimmed = text.trim()
  if (!trimmed) return state
  const next = cloneState(state)
  next.chat.push({
    id: `CHAT-${String(next.nextIds.chat++).padStart(6, "0")}`,
    role: "operator",
    text: trimmed,
    timeS: next.simulation.timeS,
  })
  next.chat.push({
    id: `CHAT-${String(next.nextIds.chat++).padStart(6, "0")}`,
    role: "jarvis",
    text: buildPreviewReply(next, trimmed),
    timeS: next.simulation.timeS,
    preview: true,
  })
  if (next.chat.length > MAX_CHAT) next.chat = next.chat.slice(-MAX_CHAT)
  return next
}

// ---------------------------------------------------------------------------
// Persistence (localStorage for demo state)
// ---------------------------------------------------------------------------

const PERSIST_KEY = "jarvis.demo.v3"
const LEGACY_PERSIST_KEYS = ["jarvis.demo.v1", "jarvis.demo.v2"]
const PERSIST_SCHEMA_VERSION = 3

export type PersistedDemo = {
  schemaVersion: number
  state: FarmState
  savedAt: number
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function isNode(value: unknown): boolean {
  return isRecord(value) && typeof value.id === "string" && typeof value.reason === "string" && typeof value.diagnosis === "string" &&
    typeof value.sinceS === "number" && ASSESSMENTS.includes(value.assessment as Assessment)
}

function isPersistedState(value: unknown): value is FarmState {
  if (!isRecord(value)) return false
  if (value.source !== "demo" || !isRecord(value.jarvis) || !isRecord(value.simulation)) return false
  if (!Array.isArray(value.devices) || !Array.isArray(value.events) || !Array.isArray(value.activity) || !Array.isArray(value.chat)) return false
  if (!isRecord(value.nextIds) || ["event", "activity", "chat", "message"].some((key) => {
    const id = (value.nextIds as Record<string, unknown>)[key]
    return typeof id !== "number" || !Number.isSafeInteger(id) || id < 1
  })) return false
  const simulation = value.simulation
  if (typeof simulation.running !== "boolean" || typeof simulation.timeS !== "number" || typeof simulation.nextBeat !== "number") return false
  if (!SIMULATION_SPEEDS.includes(simulation.speed as SimulationSpeed)) return false
  if (simulation.activeScenario !== null && !SCENARIOS.some((scenario) => scenario.id === simulation.activeScenario)) return false
  if (simulation.scenarioStartS !== null && typeof simulation.scenarioStartS !== "number") return false
  if (value.devices.length !== DEVICE_IDS.length || value.devices.some((device) => !isRecord(device) || !DEVICE_IDS.includes(device.id as DeviceId) ||
    typeof device.name !== "string" || typeof device.running !== "boolean" || !Array.isArray(device.metrics) ||
    device.metrics.some((metric) => !isRecord(metric) || typeof metric.label !== "string" || typeof metric.value !== "string"))) return false
  const jarvis = value.jarvis
  if (!isRecord(jarvis.nodes) || DEVICE_IDS.some((id) => !isNode((jarvis.nodes as Record<string, unknown>)[id]))) return false
  if (!Array.isArray(jarvis.messages) || typeof jarvis.announcements !== "number" || typeof jarvis.lastAnnouncement !== "string") return false
  if (value.events.some((event) => !isRecord(event) || typeof event.id !== "string" || typeof event.device !== "string" ||
    typeof event.timeS !== "number" || typeof event.acknowledged !== "boolean")) return false
  if (value.activity.some((entry) => !isRecord(entry) || typeof entry.id !== "string" || typeof entry.message !== "string" || typeof entry.timeS !== "number")) return false
  if (value.chat.some((message) => !isRecord(message) || typeof message.id !== "string" || typeof message.text !== "string")) return false
  return true
}

export function persistDemoState(state: FarmState): void {
  try {
    const payload: PersistedDemo = { schemaVersion: PERSIST_SCHEMA_VERSION, state, savedAt: Date.now() }
    localStorage.setItem(PERSIST_KEY, JSON.stringify(payload))
  } catch {
    // Ignore storage failures (quota, private mode).
  }
}

export function loadPersistedDemoState(): FarmState | null {
  try {
    LEGACY_PERSIST_KEYS.forEach((key) => localStorage.removeItem(key))
    const raw = localStorage.getItem(PERSIST_KEY)
    if (!raw) return null
    const payload: unknown = JSON.parse(raw)
    if (!isRecord(payload) || payload.schemaVersion !== PERSIST_SCHEMA_VERSION || typeof payload.savedAt !== "number" || !isPersistedState(payload.state)) return null
    const state = payload.state
    state.simulation.running = false
    if (state.events.length > MAX_EVENTS) state.events = state.events.slice(0, MAX_EVENTS)
    if (state.activity.length > MAX_ACTIVITY) state.activity = state.activity.slice(0, MAX_ACTIVITY)
    if (state.jarvis.messages.length > MAX_MESSAGES) state.jarvis.messages = state.jarvis.messages.slice(0, MAX_MESSAGES)
    if (state.chat.length > MAX_CHAT) state.chat = state.chat.slice(-MAX_CHAT)
    return state
  } catch {
    return null
  }
}

export function clearPersistedDemoState(): void {
  try {
    localStorage.removeItem(PERSIST_KEY)
    LEGACY_PERSIST_KEYS.forEach((key) => localStorage.removeItem(key))
  } catch {
    // Ignore.
  }
}

// ---------------------------------------------------------------------------
// Matrix connection probe (always offline in this phase)
// ---------------------------------------------------------------------------

const API_URL = import.meta.env?.VITE_MATRIX_API_URL

export async function checkMatrixHealth(signal?: AbortSignal): Promise<boolean> {
  if (!API_URL) return false
  const response = await fetch(`${API_URL}/health`, { signal })
  return response.ok
}

export async function connectToMatrix(): Promise<{ connected: boolean }> {
  // The Matrix is not integrated yet. A health check alone does not prove real
  // telemetry is available, so we never report connected. The probe stays so
  // the UI can show an honest "not connected" state.
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 2200)
  try {
    await checkMatrixHealth(controller.signal)
  } catch {
    // Network unreachable — expected in this environment.
  } finally {
    window.clearTimeout(timeout)
  }
  return { connected: false }
}
