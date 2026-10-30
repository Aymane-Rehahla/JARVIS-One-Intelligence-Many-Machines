// Checks the live mapping (src/services/live-map.ts) and the gateway client's message handling on real snapshots
// made by the Python gateway (P1 section D; run by tests/test_live_map.py), and times the mapping (section E).
//
// Usage: node scripts/check-live-map.ts INPUT.json
// INPUT.json: {"runs": [[<FarmSnapshot JSON>, ...], ...], "invalid": [<anything>, ...], "bench": [<FarmSnapshot>, ...]}
// Each run is a sequence of snapshots of one session (a farm reset in the middle is a new feed_id). Prints one JSON
// object: per-run facts, the client's counts on the invalid messages, and the benchmark's mean and p95 milliseconds.

import { readFileSync } from "node:fs"
import { performance } from "node:perf_hooks"

import { FarmSnapshot } from "../src/services/contracts.ts"
import { LiveFarmClient } from "../src/services/live.ts"
import { mapSnapshot } from "../src/services/live-map.ts"
import type { FarmState } from "../src/services/matrix.ts"
import { worstAssessment } from "../src/lib/assessment.ts"

type Input = { runs: unknown[][]; invalid: unknown[]; bench: unknown[] }

const path = process.argv[2]
if (!path) {
  console.error("usage: node scripts/check-live-map.ts INPUT.json")
  process.exit(2)
}
const input = JSON.parse(readFileSync(path, "utf8")) as Input

function summary(state: FarmState) {
  return {
    source: state.source,
    label: state.live?.label,
    farmTime: state.live?.farmTime,
    feedId: state.live?.feedId,
    targetSpeed: state.live?.targetSpeed ?? null,
    day: state.live?.day ?? null,
    worst: worstAssessment(state),
    actualSpeed: state.live?.actualSpeed,
    running: state.simulation.running,
    timeS: state.simulation.timeS,
    devices: state.devices.map((device) => ({
      id: device.id,
      connection: device.connection,
      operational: device.operational,
      metrics: device.metrics.map((metric) => [metric.label, metric.value]),
      lastUpdate: device.lastUpdate,
    })),
    events: state.events.map((event) => ({ id: event.id, timestamp: event.timestamp, severity: event.severity, type: event.type, open: !event.acknowledged })),
    eventFeeds: [...new Set(state.events.map((event) => event.id.split("-")[0]))],
    activity: state.activity.length,
    nodes: Object.fromEntries(
      Object.entries(state.jarvis.nodes).map(([id, node]) => [id, [node.assessment, node.diagnosis, node.sinceS, node.noConclusion === true]]),
    ),
    preview: state.chat.some((message) => message.preview),
  }
}

const runs = input.runs.map((run) => {
  let state: FarmState | null = null
  const states = []
  for (const raw of run) {
    state = mapSnapshot(FarmSnapshot.parse(raw), state)
    states.push(summary(state))
  }
  return states
})

// The client: invalid messages are counted, never thrown, never delivered as data.
const delivered: unknown[] = []
const connections: string[] = []
let invalidCount = 0
const client = new LiveFarmClient("http://127.0.0.1:1", {
  onSnapshot: (snapshot) => delivered.push(snapshot.sequence),
  onConnection: (connection) => connections.push(connection),
  onInvalid: (count) => {
    invalidCount = count
  },
})
const receive = (client as unknown as { receive: (data: unknown) => void }).receive.bind(client)
let thrown = 0
const originalWarn = console.warn
let warnings = 0
console.warn = () => {
  warnings += 1
}
for (const message of input.invalid) {
  try {
    receive(typeof message === "string" ? message : JSON.stringify(message))
  } catch {
    thrown += 1
  }
}
if (input.bench.length > 0) receive(JSON.stringify(input.bench[0]))
console.warn = originalWarn

// The benchmark: map every recorded snapshot in order, as the hook does.
const parsed = input.bench.map((raw) => FarmSnapshot.parse(raw))
let state: FarmState | null = null
for (const snapshot of parsed.slice(0, 50)) state = mapSnapshot(snapshot, state)   // warm-up
state = null
const times: number[] = []
for (const snapshot of parsed) {
  const started = performance.now()
  state = mapSnapshot(snapshot, state)
  times.push(performance.now() - started)
}
times.sort((a, b) => a - b)
const mean = times.reduce((a, b) => a + b, 0) / times.length
const p95 = times[Math.min(times.length - 1, Math.ceil(0.95 * times.length) - 1)]

console.log(
  JSON.stringify({
    runs,
    client: { invalidCount, thrown, warnings, delivered: delivered.length, connections },
    bench: { snapshots: times.length, meanMs: mean, p95Ms: p95, maxMs: times[times.length - 1], finalEvents: state?.events.length ?? 0 },
  }),
)
