// Turns the shared demo state into what one node's brain "sees" (its input channels) and
// how its thinking should unfold (the BrainProfile the engine animates).

import {
  ASSESSMENTS,
  ASSESSMENT_LABELS,
  DEVICE_IDS,
  deviceById,
  deviceName,
  diagnosisLabel,
  type Assessment,
  type DeviceId,
  type FarmState,
} from "@/services/matrix"

import type { BrainProfile } from "./brain-engine"

export type ReadingChannel = {
  key: string
  label: string
  value: string
  /** How strongly this reading excites the brain, 0..1. Above 0.5 counts as out of the ordinary. */
  salience: number
  hot: boolean
  /** Measurements read best in a monospaced face; words don't. */
  mono: boolean
}

const SEVERITY: Record<Assessment, number> = {
  NORMAL: 0.12,
  HANDLED_BY_MACHINE: 0.35,
  WATCHING: 0.55,
  ESCALATE_TO_USER: 0.8,
  DANGER: 0.95,
}

const COMPETITOR: Record<Assessment, Assessment | null> = {
  NORMAL: null,
  HANDLED_BY_MACHINE: "ESCALATE_TO_USER",
  WATCHING: "NORMAL",
  ESCALATE_TO_USER: "WATCHING",
  DANGER: "ESCALATE_TO_USER",
}

const DIFFICULTY: Record<Assessment, number> = {
  NORMAL: 0.12,
  HANDLED_BY_MACHINE: 0.35,
  WATCHING: 0.75,
  ESCALATE_TO_USER: 0.45,
  DANGER: 0.3,
}

const QUIET = 0.15

/** What a node reads each second: its machine's readings, the device code it reports, and its peers. */
export function readingChannels(state: FarmState, id: DeviceId): ReadingChannel[] {
  const live = state.source === "demo"
  const device = deviceById(state, id)
  const node = state.jarvis.nodes[id]
  const channels: ReadingChannel[] = (device?.metrics ?? []).map((metric) => {
    const salience = !live ? 0 : metric.flagged ? 1 : QUIET
    return { key: metric.label, label: metric.label, value: metric.value, salience, hot: salience > 0.5, mono: true }
  })

  // A device code counts while the node is still dealing with the incident it belongs to.
  const concerned = node.assessment === "WATCHING" || node.assessment === "ESCALATE_TO_USER" || node.assessment === "DANGER"
  const since = state.simulation.scenarioStartS
  const codeEvent =
    live && concerned && since !== null
      ? state.events.find((event) => event.deviceId === id && event.timeS >= since && /Device code [A-Z_]+/.test(event.evidence))
      : undefined
  const code = codeEvent?.evidence.match(/Device code ([A-Z_]+)/)?.[1]
  channels.push({
    key: "device-code",
    label: "Device code",
    value: !live ? "No data" : code ? diagnosisLabel(code) : "None",
    salience: !live ? 0 : code ? 0.9 : 0.1,
    hot: Boolean(live && code),
    mono: false,
  })

  // Peer link: what the other three nodes last broadcast. It matters most when a peer
  // reports the same problem (the overvoltage case), less when it's about something else.
  const others = DEVICE_IDS.filter((other) => other !== id).map((other) => state.jarvis.nodes[other])
  const loudest = others.reduce((worst, other) => (SEVERITY[other.assessment] > SEVERITY[worst.assessment] ? other : worst), others[0])
  const corroborates = loudest.assessment !== "NORMAL" && loudest.diagnosis === node.diagnosis && node.assessment !== "NORMAL"
  const peerSalience = !live ? 0 : corroborates ? SEVERITY[loudest.assessment] : QUIET + 0.3 * (SEVERITY[loudest.assessment] - SEVERITY.NORMAL)
  channels.push({
    key: "peer-link",
    label: "Peer link",
    value: !live
      ? "No data"
      : loudest.assessment === "NORMAL"
        ? "All peers normal"
        : `${deviceName(loudest.id)}: ${ASSESSMENT_LABELS[loudest.assessment].toLowerCase()}`,
    salience: peerSalience,
    hot: peerSalience > 0.5,
    mono: false,
  })
  return channels
}

export function brainProfile(state: FarmState, id: DeviceId, channels: ReadingChannel[]): BrainProfile {
  const node = state.jarvis.nodes[id]
  const sensorDisagreement = node.diagnosis === "SENSOR_FAULT" ? 0.15 : 0
  return {
    target: node.assessment,
    competitor: COMPETITOR[node.assessment],
    salience: channels.map((channel) => channel.salience),
    difficulty: Math.min(0.95, DIFFICULTY[node.assessment] + sensorDisagreement),
    revision: `${node.assessment}|${node.diagnosis}|${node.sinceS}`,
    offline: state.source !== "demo",
  }
}

/** The node with the most serious conclusion (ties: the one that changed its mind last). */
export function mostSeriousNode(state: FarmState): DeviceId {
  let best: DeviceId = DEVICE_IDS[0]
  for (const id of DEVICE_IDS) {
    const node = state.jarvis.nodes[id]
    const current = state.jarvis.nodes[best]
    const rank = ASSESSMENTS.indexOf(node.assessment)
    const bestRank = ASSESSMENTS.indexOf(current.assessment)
    if (rank > bestRank || (rank === bestRank && rank > 0 && node.sinceS > current.sinceS)) best = id
  }
  return best
}
