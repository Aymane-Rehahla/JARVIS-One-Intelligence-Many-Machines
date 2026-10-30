import { useEffect, useState } from "react"

/**
 * The recorded-mind adapter. Everything the JARVIS mind views draw comes through here, from the export in
 * public/mind (release v3.1: one recorded episode, every kept thought replayed step by step). Nothing in this
 * file computes or invents model values; a live adapter can later return the same shapes.
 */

export const MIND_NODES = ["incubator-1", "incubator-2", "hvac", "animal-protection"] as const
export type MindNode = (typeof MIND_NODES)[number]
export type BrainKey = "incubator" | "hvac" | "animal_protection"
export type MomentKey = "calm" | "first_alarm" | "peak"

export const NODE_LABEL: Record<MindNode, string> = {
  "incubator-1": "Incubator 1",
  "incubator-2": "Incubator 2",
  hvac: "HVAC",
  "animal-protection": "Animal protection",
}
export const NODE_BRAIN: Record<MindNode, BrainKey> = {
  "incubator-1": "incubator",
  "incubator-2": "incubator",
  hvac: "hvac",
  "animal-protection": "animal_protection",
}
export const MOMENT_LABEL: Record<MomentKey, string> = { calm: "Calm", first_alarm: "First alarm", peak: "Peak" }
export const MOMENT_MEANING: Record<MomentKey, string> = {
  calm: "30 s before the first fault",
  first_alarm: "the first second the node left NORMAL",
  peak: "the last time its level rose",
}
/** The label every recorded view carries. */
export const RECORDED_LABEL = "Recorded from the real trained brain (release v3.1), replayed"

export type Manifest = {
  generated_utc: string
  release: string
  episode: { family: string; variant: number; duration_ticks: number; start_hour: number; faults: { kind: string; inject_tick: number; end_tick: number; voltage_v?: number; shared?: boolean }[]; calm_tick: number }
}

export type Brain = {
  machine: string
  neurons: number
  internal_steps_per_tick: number
  adaptive_thinking: boolean
  memory_length: number
  memory_meaning: string
  inputs: { features: number; numeric: number; flags: number; notes_hidden: number; perception_width: number; backbone_output: number }
  parameters: { total: number; perception_backbone: number; ctm_core: number; ctm_synapses: number; ctm_neuron_level_models: number; ctm_learned_start_and_decay: number; heads: Record<string, number> }
  synapses: { kind: string; input: string }
  neuron_level_models: { kind: string; per_neuron_weights: number; shape: string }
  synchronisation: { neurons: number[]; pairs: number; pair_list: [number, number][]; decay_rate: number[]; meaning: string }
  heads: { assessment: string[]; diagnosis: string[]; action: string[]; communication: string[]; source: string[] }
  decision_layer: { tau: number; theta: number; k_watch: number; k_escalate: number; hold: boolean }
}
export type Architecture = Record<BrainKey, Brain>

export type Timeline = {
  assessments: string[]
  episode: Manifest["episode"]
  /** Per node, per farm second: [assessment index, confidence] as the node stated it. */
  nodes: Record<MindNode, [number, number][]>
}

export type Thought = {
  tick: number
  farm_time_s: number
  hour_of_day: number
  decided: { assessment: string; diagnosis: string; confidence: number; action: string; communication: string; evidence: string[] }
  assessment_probabilities: number[]
  per_step: {
    assessment_probabilities: number[][]
    certainty: number[]
    rms_change: number[]
    recurrent_drive: number[][]
    sensory_drive: number[][]
    edge_weight: number[][]
  }
  /** 256 × 32: columns 0–15 are the previous second's steps, 16–31 this thought's steps 1–16. */
  history_pre: number[][]
  history_post: number[][]
  synchronisation: number[]
  top_diagnoses: [string, number][]
  top_sources: [string, number][]
  unusual_inputs: [string, number][]
  replay_max_difference: number
}

export type CtmRecord = {
  node: MindNode
  machine: string
  /** [source neuron, target neuron]: for each target, its 6 strongest incoming influences at the kept moments. */
  edges: [number, number][]
  edges_meaning: string
  latency_ms: { n: number; p50: number; p95: number; mean: number }
  thoughts: Partial<Record<MomentKey, Thought>>
}

export type CmmRecord = {
  node: MindNode
  tables: Record<string, number>
  episodes: {
    id: number
    opened_s: number
    closed_s: number | null
    trigger: string
    kind: string | null
    diagnosis: string
    peak: string
    ticks: number
    summary: string
    source: string | null
    peers: string
    /** [tick, stated assessment, diagnosis, cause] for every kept second. */
    ticks_kept: [number, string, string, string | null][]
    example_readings: Record<string, string | number | boolean>
  }[]
  facts: unknown[]
  hypotheses: unknown[]
  procedures: { id: number; time_s: number; situation: string; action: string; result: string }[]
  profile_source: string
  profile: { hour: number; key: string; n: number; mean: number; spread: number }[]
  rom: { key: string; kind: string; text: string }[]
}

const cache = new Map<string, Promise<unknown>>()
function load<T>(path: string): Promise<T> {
  if (!cache.has(path)) {
    cache.set(
      path,
      fetch(`/mind/${path}`).then((response) => {
        if (!response.ok) throw new Error(`${path}: ${response.status}`)
        return response.json()
      }),
    )
  }
  return cache.get(path) as Promise<T>
}

export type Loaded<T> = { data: T | null; error: string | null }

function useMindFile<T>(path: string | null): Loaded<T> {
  const [state, setState] = useState<{ path: string | null } & Loaded<T>>({ path: null, data: null, error: null })
  useEffect(() => {
    if (!path) return
    let alive = true
    load<T>(path).then(
      (data) => alive && setState({ path, data, error: null }),
      (error: unknown) => alive && setState({ path, data: null, error: error instanceof Error ? error.message : String(error) }),
    )
    return () => {
      alive = false
    }
  }, [path])
  return state.path === path ? { data: state.data, error: state.error } : { data: null, error: null }
}

export const useManifest = () => useMindFile<Manifest>("manifest.json")
export const useArchitecture = () => useMindFile<Architecture>("architecture.json")
export const useTimeline = () => useMindFile<Timeline>("timeline.json")
export const useCtm = (node: MindNode) => useMindFile<CtmRecord>(`ctm/${node}.json`)
export const useCmm = (node: MindNode) => useMindFile<CmmRecord>(`cmm/${node}.json`)

/** Kept moments in time order. */
export function momentsOf(record: CtmRecord): MomentKey[] {
  return (Object.keys(record.thoughts) as MomentKey[]).sort((a, b) => (record.thoughts[a]?.tick ?? 0) - (record.thoughts[b]?.tick ?? 0))
}

/** The one recorded false alert (animal protection at tick 13, the release's known open issue). */
export function isKnownFalseAlert(node: MindNode, moment: MomentKey) {
  return node === "animal-protection" && moment === "first_alarm"
}

export function farmClock(startHour: number, tick: number) {
  const seconds = Math.round(startHour * 3600 + tick)
  const h = Math.floor(seconds / 3600) % 24, m = Math.floor((seconds % 3600) / 60), s = seconds % 60
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
}

export function humanize(code: string) {
  return code.replace(/_/g, " ").toLowerCase().replace(/^\w/, (letter) => letter.toUpperCase())
}
