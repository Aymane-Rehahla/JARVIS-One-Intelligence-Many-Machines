// An illustrative model of how one JARVIS node's brain, a small Continuous Thought
// Machine (CTM), reasons.
//
// Nothing here is the real CTM. The demo script decides the outcome (the node's
// assessment); this engine animates how a CTM would get there, following the ideas
// of the CTM paper (Sakana AI, 2025):
//
// - It "thinks" in internal ticks, decoupled from the incoming data. Each reading
//   starts a new thought of TICKS_PER_THOUGHT ticks.
// - Every neuron has its own rhythm. Its activation over the ticks is what the
//   traces at the bottom of the brain view show.
// - The answer is read from SYNCHRONISATION: which neurons fire in step with which.
//   Here each conclusion has an assembly of neurons; the assembly of the winning
//   conclusion locks into phase while the others drift apart, and the five
//   probabilities are computed from that synchrony.
// - Easy cases settle in a few ticks, hard ones take longer and end less certain
//   (the CTM's adaptive compute). A changed conclusion first scrambles the field,
//   then the new assembly forms: a visible "re-think".
//
// When the real nodes stream their neuron traces, a renderer can draw those instead.

import { ASSESSMENTS, type Assessment } from "@/services/matrix"

export const TICKS_PER_THOUGHT = 36
/** Wall-clock seconds per internal tick at normal speed. */
export const TICK_SECONDS = 0.085
/** Certainty at which a thought counts as decided. */
export const DECISION_CERTAINTY = 0.6

const INPUT_GROUP_SIZE = 6
const OUTPUT_GROUP_SIZE = 8
const ASSOCIATION_SIZE = 26
const TRACE_SAMPLES = 220
const TRACE_RATE = 55 // samples per second
const TWO_PI = Math.PI * 2

export type NeuronRole = 0 | 1 | 2 // input · association · output
export const ROLE_INPUT = 0
export const ROLE_ASSOCIATION = 1
export const ROLE_OUTPUT = 2

/** Kinds of neuron pairs whose synchrony the view can draw. */
export const PAIR_OUTPUT = 0
export const PAIR_INPUT = 1
export const PAIR_CROSS = 2
export const PAIR_RECRUIT = 3

export type BrainProfile = {
  /** The conclusion the node reached; the assembly that will synchronise. */
  target: Assessment
  /** A conclusion that competes for a while on hard cases. */
  competitor: Assessment | null
  /** How strongly each input channel excites the brain, 0..1. */
  salience: number[]
  /** 0 = obvious, 1 = hard: slower to settle and less certain. */
  difficulty: number
  /** Changes whenever the node changes its conclusion, which triggers a re-think. */
  revision: string
  /** No data source: the brain sits idle. */
  offline: boolean
}

export type TraceLane = { neuron: number; role: NeuronRole; group: number }

function mulberry32(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function hashSeed(text: string): number {
  let h = 2166136261
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

const smoothstep = (t: number) => {
  const x = Math.min(1, Math.max(0, t))
  return x * x * (3 - 2 * x)
}

/** Rises and falls inside the first part of a thought: how long a competing idea holds on. */
const bump = (t: number) => (t <= 0 || t >= 0.75 ? 0 : Math.sin((Math.PI * t) / 0.75))

export class BrainEngine {
  readonly inputCount: number
  readonly size: number
  readonly role: Uint8Array
  readonly group: Int16Array
  /** Position inside the field, 0..1 (set by layout()). */
  readonly x: Float32Array
  readonly y: Float32Array
  readonly phase: Float32Array
  readonly omega: Float32Array
  readonly amp: Float32Array
  /** Post-activation, 0..1: what the traces and the glow show. */
  readonly z: Float32Array
  /** Brief brightness after a neuron fires. */
  readonly flash: Float32Array
  /** Synapses as [from, to] pairs. */
  readonly links: Uint16Array
  /** Outgoing synapse indices per neuron. */
  readonly outLinks: number[][]
  /** Sampled neuron pairs whose synchrony is tracked, as [i, j]. */
  readonly pairs: Uint16Array
  readonly pairKind: Uint8Array
  readonly pairGroup: Int16Array
  readonly pairSync: Float32Array

  readonly probs = new Float32Array(ASSESSMENTS.length)
  certainty = 0
  /** Internal tick inside the current thought (fractional). */
  tick = 0
  thought = 1
  decidedAt: number | null = null
  /** Certainty at each tick of the current and the previous thought. */
  readonly certaintyByTick = new Float32Array(TICKS_PER_THOUGHT + 1)
  readonly previousCertainty = new Float32Array(TICKS_PER_THOUGHT + 1)
  hasPrevious = false
  /** Mean activation of each input channel's assembly, 0..1. */
  readonly inputActivity: Float32Array
  /** Mean activation of each conclusion's assembly, 0..1. */
  readonly outputActivity = new Float32Array(ASSESSMENTS.length)

  lanes: TraceLane[] = []
  readonly traces: Float32Array[] = []
  traceHead = 0

  /** Neurons that fired / inputs that received a reading during the last step. */
  readonly fired: number[] = []
  readonly arrivals: number[] = []

  private profile: BrainProfile
  private targetIndex = 0
  private competitorIndex = -1
  private priorIndex = -1
  private converge = 8
  /** How much of the last thought's belief a new thought starts from (none after a re-think). */
  private carry = 0
  private surprise = 0
  private traceClock = 0
  private readonly random: () => number
  private readonly jitterX: Float32Array
  private readonly jitterY: Float32Array
  /** Position of each neuron inside its assembly (assemblies are drawn as small rings). */
  private readonly member: Int16Array
  private readonly assocY: Float32Array
  private readonly members: number[][] // input groups first, then the association pool, then outputs
  private readonly groupCos: Float32Array
  private readonly groupSin: Float32Array
  private readonly groupR: Float32Array
  private readonly groupPsi: Float32Array
  private readonly logits = new Float32Array(ASSESSMENTS.length)

  constructor(seed: number, inputCount: number) {
    this.random = mulberry32(seed)
    const rand = this.random
    this.inputCount = inputCount
    this.size = inputCount * INPUT_GROUP_SIZE + ASSOCIATION_SIZE + ASSESSMENTS.length * OUTPUT_GROUP_SIZE
    const n = this.size
    this.role = new Uint8Array(n)
    this.group = new Int16Array(n)
    this.x = new Float32Array(n)
    this.y = new Float32Array(n)
    this.phase = new Float32Array(n)
    this.omega = new Float32Array(n)
    this.amp = new Float32Array(n)
    this.z = new Float32Array(n)
    this.flash = new Float32Array(n)
    this.jitterX = new Float32Array(n)
    this.jitterY = new Float32Array(n)
    this.member = new Int16Array(n)
    this.assocY = new Float32Array(n)
    this.inputActivity = new Float32Array(inputCount)

    const groupCount = inputCount + 1 + ASSESSMENTS.length
    this.members = Array.from({ length: groupCount }, () => [])
    this.groupCos = new Float32Array(groupCount)
    this.groupSin = new Float32Array(groupCount)
    this.groupR = new Float32Array(groupCount)
    this.groupPsi = new Float32Array(groupCount)

    let index = 0
    for (let k = 0; k < inputCount; k += 1) {
      for (let m = 0; m < INPUT_GROUP_SIZE; m += 1) {
        this.role[index] = ROLE_INPUT
        this.group[index] = k
        this.member[index] = m
        this.members[k].push(index)
        index += 1
      }
    }
    for (let m = 0; m < ASSOCIATION_SIZE; m += 1) {
      this.role[index] = ROLE_ASSOCIATION
      this.group[index] = -1
      this.members[inputCount].push(index)
      index += 1
    }
    for (let c = 0; c < ASSESSMENTS.length; c += 1) {
      for (let m = 0; m < OUTPUT_GROUP_SIZE; m += 1) {
        this.role[index] = ROLE_OUTPUT
        this.group[index] = c
        this.member[index] = m
        this.members[inputCount + 1 + c].push(index)
        index += 1
      }
    }

    // Each neuron keeps its own rhythm (1.25 Hz ± 0.45 Hz) and a fixed place in its cluster.
    for (let i = 0; i < n; i += 1) {
      this.omega[i] = TWO_PI * (1.25 + (rand() - 0.5) * 0.9)
      this.phase[i] = rand() * TWO_PI
      this.amp[i] = 0.1
      const angle = rand() * TWO_PI
      const radius = Math.sqrt(rand())
      this.jitterX[i] = Math.cos(angle) * radius
      this.jitterY[i] = Math.sin(angle) * radius
      this.assocY[i] = rand()
    }
    this.relaxAssociation()

    // Wiring: inputs feed the association pool, which feeds the conclusions; neighbours
    // inside an assembly are linked in a ring. Targets are picked by vertical proximity
    // so the wiring reads left to right.
    const links: number[] = []
    const assoc = this.members[inputCount]
    const defaultInputY = (k: number) => (k + 0.5) / inputCount
    const defaultOutputY = (c: number) => (c + 0.5) / ASSESSMENTS.length
    const nearestAssoc = (yy: number, count: number) =>
      assoc
        .map((neuron) => ({ neuron, key: Math.abs(this.assocY[neuron] - yy) + rand() * 0.12 }))
        .sort((a, b) => a.key - b.key)
        .slice(0, count)
        .map((entry) => entry.neuron)
    for (let k = 0; k < inputCount; k += 1) {
      const group = this.members[k]
      group.forEach((neuron, m) => {
        links.push(neuron, group[(m + 1) % group.length])
        for (const target of nearestAssoc(defaultInputY(k), 3).slice(0, 1 + Math.round(rand()))) links.push(neuron, target)
      })
    }
    assoc.forEach((neuron) => {
      for (const target of nearestAssoc(this.assocY[neuron], 4).slice(1, 3)) links.push(neuron, target)
      const order = [...Array(ASSESSMENTS.length).keys()].sort(
        (a, b) => Math.abs(defaultOutputY(a) - this.assocY[neuron]) - Math.abs(defaultOutputY(b) - this.assocY[neuron]),
      )
      for (const c of order.slice(0, 2)) {
        const group = this.members[inputCount + 1 + c]
        links.push(neuron, group[Math.floor(rand() * group.length)])
      }
    })
    for (let c = 0; c < ASSESSMENTS.length; c += 1) {
      const group = this.members[inputCount + 1 + c]
      group.forEach((neuron, m) => links.push(neuron, group[(m + 1) % group.length]))
    }
    this.links = Uint16Array.from(links)
    this.outLinks = Array.from({ length: n }, () => [])
    for (let l = 0; l < this.links.length; l += 2) this.outLinks[this.links[l]].push(l / 2)

    // Pairs whose synchrony is measured (the CTM also samples neuron pairs rather than
    // using all of them).
    const pairs: number[] = []
    const kinds: number[] = []
    const pairGroups: number[] = []
    const allPairs = (group: number[], kind: number, g: number) => {
      for (let a = 0; a < group.length; a += 1) {
        for (let b = a + 1; b < group.length; b += 1) {
          pairs.push(group[a], group[b])
          kinds.push(kind)
          pairGroups.push(g)
        }
      }
    }
    for (let c = 0; c < ASSESSMENTS.length; c += 1) allPairs(this.members[inputCount + 1 + c], PAIR_OUTPUT, c)
    for (let k = 0; k < inputCount; k += 1) allPairs(this.members[k], PAIR_INPUT, k)
    for (let k = 0; k < inputCount; k += 1) {
      for (let c = 0; c < ASSESSMENTS.length; c += 1) {
        const from = this.members[k]
        const to = this.members[inputCount + 1 + c]
        for (let s = 0; s < 2; s += 1) {
          pairs.push(from[Math.floor(rand() * from.length)], to[Math.floor(rand() * to.length)])
          kinds.push(PAIR_CROSS)
          pairGroups.push(c)
        }
      }
    }
    for (const neuron of assoc) {
      for (let c = 0; c < ASSESSMENTS.length; c += 1) {
        const to = this.members[inputCount + 1 + c]
        pairs.push(neuron, to[Math.floor(rand() * to.length)])
        kinds.push(PAIR_RECRUIT)
        pairGroups.push(c)
      }
    }
    this.pairs = Uint16Array.from(pairs)
    this.pairKind = Uint8Array.from(kinds)
    this.pairGroup = Int16Array.from(pairGroups)
    this.pairSync = new Float32Array(kinds.length)

    for (let t = 0; t < 6; t += 1) this.traces.push(new Float32Array(TRACE_SAMPLES))

    this.profile = {
      target: "NORMAL",
      competitor: null,
      salience: Array.from({ length: inputCount }, () => 0.15),
      difficulty: 0.2,
      revision: "",
      offline: false,
    }
    this.layout({ inputs: Array.from({ length: inputCount }, (_, k) => defaultInputY(k)), outputs: ASSESSMENTS.map((_, c) => defaultOutputY(c)) })
    this.applyProfile(this.profile, true)
  }

  /** Spread the association pool evenly over the height (a few rounds of 1-D repulsion). */
  private relaxAssociation() {
    const assoc: number[] = []
    for (let i = 0; i < this.size; i += 1) if (this.role[i] === ROLE_ASSOCIATION) assoc.push(i)
    assoc.sort((a, b) => this.assocY[a] - this.assocY[b])
    assoc.forEach((neuron, rank) => {
      this.assocY[neuron] = 0.06 + (0.88 * (rank + 0.5)) / assoc.length + (this.random() - 0.5) * 0.04
    })
  }

  /**
   * Place the clusters. `inputs` and `outputs` are the vertical centres (0..1 of the
   * field) of the rows that list the readings and the conclusions, so the wiring
   * meets them.
   */
  layout(anchors: { inputs: number[]; outputs: number[] }) {
    const inputRadius = Math.min(0.05, 0.4 / this.inputCount)
    const outputRadius = Math.min(0.095, 0.46 / ASSESSMENTS.length)
    for (let i = 0; i < this.size; i += 1) {
      const role = this.role[i]
      // Assemblies sit on small, slightly irregular rings; their chords light up as they synchronise.
      const wobble = 0.8 + 0.3 * Math.abs(this.jitterX[i])
      if (role === ROLE_INPUT) {
        const k = this.group[i]
        const angle = (TWO_PI * this.member[i]) / INPUT_GROUP_SIZE + k * 0.9 + this.jitterY[i] * 0.25
        this.x[i] = 0.1 + 0.03 * Math.sin(k * 1.7) + 0.045 * wobble * Math.cos(angle)
        this.y[i] = anchors.inputs[k] + inputRadius * wobble * Math.sin(angle)
      } else if (role === ROLE_OUTPUT) {
        const c = this.group[i]
        const angle = (TWO_PI * this.member[i]) / OUTPUT_GROUP_SIZE + c * 0.7 + this.jitterY[i] * 0.2
        this.x[i] = 0.84 - 0.03 * Math.cos(c * 2.1) + 0.075 * wobble * Math.cos(angle)
        this.y[i] = anchors.outputs[c] + outputRadius * wobble * Math.sin(angle)
      } else {
        this.x[i] = 0.5 + 0.2 * this.jitterX[i] + 0.06 * Math.sin(this.assocY[i] * 9)
        this.y[i] = this.assocY[i]
      }
      this.x[i] = Math.min(0.98, Math.max(0.02, this.x[i]))
      this.y[i] = Math.min(0.97, Math.max(0.03, this.y[i]))
    }
  }

  /** Start from a settled state for this profile (no re-think), e.g. when the view first opens. */
  reset(next: BrainProfile) {
    this.applyProfile(next, false)
    this.priorIndex = -1
    this.surprise = 0
    this.startThought(0.3, 0, 0.45)
    this.settle(4.6)
  }

  setProfile(next: BrainProfile) {
    const rethink = next.revision !== this.profile.revision && !next.offline && !this.profile.offline
    this.applyProfile(next, rethink)
  }

  private applyProfile(next: BrainProfile, rethink: boolean) {
    const previousTarget = this.targetIndex
    this.profile = { ...next, salience: next.salience.slice(0, this.inputCount) }
    while (this.profile.salience.length < this.inputCount) this.profile.salience.push(0.15)
    this.targetIndex = ASSESSMENTS.indexOf(next.target)
    this.competitorIndex = next.competitor ? ASSESSMENTS.indexOf(next.competitor) : -1
    this.pickLanes()
    if (rethink) {
      this.priorIndex = previousTarget !== this.targetIndex ? previousTarget : -1
      this.surprise = 1
      this.startThought(1, 6, 0)
    }
  }

  /** Which neurons the trace strip follows: the busiest reading, the association pool and the conclusions. */
  private pickLanes() {
    const salience = this.profile.salience
    let hot = 0
    for (let k = 1; k < this.inputCount; k += 1) if (salience[k] > salience[hot]) hot = k
    const output = (c: number, m: number) => this.members[this.inputCount + 1 + c][m]
    const other = this.competitorIndex >= 0 ? this.competitorIndex : (this.targetIndex + 2) % ASSESSMENTS.length
    const assoc = this.members[this.inputCount]
    this.lanes = [
      { neuron: this.members[hot][0], role: ROLE_INPUT, group: hot },
      { neuron: this.members[hot][3], role: ROLE_INPUT, group: hot },
      { neuron: assoc[Math.floor(assoc.length * this.random())], role: ROLE_ASSOCIATION, group: -1 },
      { neuron: output(this.targetIndex, 1), role: ROLE_OUTPUT, group: this.targetIndex },
      { neuron: output(this.targetIndex, 5), role: ROLE_OUTPUT, group: this.targetIndex },
      { neuron: output(other, 2), role: ROLE_OUTPUT, group: other },
    ]
  }

  private startThought(perturb: number, extraTicks: number, carry: number) {
    const rand = this.random
    this.carry = carry
    if (this.tick > 0) {
      this.previousCertainty.set(this.certaintyByTick)
      this.hasPrevious = true
    }
    this.certaintyByTick.fill(0)
    this.tick = 0
    this.decidedAt = null
    this.converge = 4 + this.profile.difficulty * 16 + extraTicks
    for (let i = 0; i < this.size; i += 1) {
      this.phase[i] = (this.phase[i] + (rand() - 0.5) * TWO_PI * perturb + TWO_PI) % TWO_PI
      if (this.role[i] === ROLE_OUTPUT) this.amp[i] *= 0.55 + 0.4 * (1 - perturb)
    }
  }

  /** Advance the brain by `dt` wall-clock seconds. */
  step(dt: number) {
    const rand = this.random
    const n = this.size
    const profile = this.profile
    const inputCount = this.inputCount
    this.fired.length = 0
    this.arrivals.length = 0
    // Frame timestamps can arrive slightly out of order; never step backwards.
    const h = Number.isFinite(dt) ? Math.min(Math.max(dt, 0), 0.05) : 0
    if (h === 0) return

    if (profile.offline) {
      for (let i = 0; i < n; i += 1) {
        this.amp[i] += (0.04 - this.amp[i]) * (1 - Math.exp(-h * 3))
        this.phase[i] = (this.phase[i] + this.omega[i] * h * 0.35) % TWO_PI
        this.z[i] = this.amp[i] * (0.5 + 0.5 * Math.cos(this.phase[i]))
        this.flash[i] *= Math.exp(-h * 6)
      }
      this.probs.fill(1 / ASSESSMENTS.length)
      this.certainty = 0
      this.sampleTraces(h)
      this.decaySync(h)
      return
    }

    // Internal clock.
    const before = this.tick
    this.tick += h / TICK_SECONDS
    if (this.tick >= TICKS_PER_THOUGHT) {
      this.thought += 1
      this.priorIndex = -1
      this.startThought(0.22 + 0.25 * profile.difficulty, 0, 0.45)
    }
    const tick = this.tick
    const progress = smoothstep((tick - 1) / this.converge)
    const wave = Math.exp(-tick / 5)
    this.surprise *= Math.exp(-h / 1.4)

    // Mean field of every assembly, weighted by activation.
    const groups = this.members.length
    let hotCos = 0
    let hotSin = 0
    let hotWeight = 0
    for (let g = 0; g < groups; g += 1) {
      let c = 0
      let s = 0
      let w = 0
      for (const i of this.members[g]) {
        c += this.amp[i] * Math.cos(this.phase[i])
        s += this.amp[i] * Math.sin(this.phase[i])
        w += this.amp[i]
      }
      this.groupCos[g] = w > 0 ? c / w : 0
      this.groupSin[g] = w > 0 ? s / w : 0
      this.groupR[g] = Math.hypot(this.groupCos[g], this.groupSin[g])
      this.groupPsi[g] = Math.atan2(this.groupSin[g], this.groupCos[g])
      if (g < inputCount) {
        const weight = profile.salience[g] * profile.salience[g]
        hotCos += c * weight
        hotSin += s * weight
        hotWeight += w * weight
      }
    }
    const hotR = hotWeight > 0 ? Math.hypot(hotCos, hotSin) / hotWeight : 0
    const hotPsi = Math.atan2(hotSin, hotCos)
    let meanHot = 0
    for (let k = 0; k < inputCount; k += 1) meanHot = Math.max(meanHot, profile.salience[k])

    const target = this.targetIndex
    const targetGroup = inputCount + 1 + target
    const targetR = this.groupR[targetGroup]
    const targetPsi = this.groupPsi[targetGroup]
    let targetOmega = 0
    for (const i of this.members[targetGroup]) targetOmega += this.omega[i]
    targetOmega /= OUTPUT_GROUP_SIZE

    // Belief: synchrony of each conclusion's assembly, steered by the demo script's outcome.
    const confidence = 1 - 0.45 * profile.difficulty
    let maxLogit = -Infinity
    const logits = this.logits
    for (let c = 0; c < ASSESSMENTS.length; c += 1) {
      const g = inputCount + 1 + c
      let meanAmp = 0
      for (const i of this.members[g]) meanAmp += this.amp[i]
      meanAmp /= OUTPUT_GROUP_SIZE
      const sync = this.groupR[g] * meanAmp
      let schedule = 0
      if (c === target) schedule = this.carry + (1 - this.carry) * progress
      else if (c === this.competitorIndex) schedule = 0.75 * profile.difficulty * bump(progress)
      if (c === this.priorIndex) schedule = Math.max(schedule, 0.85 * (1 - smoothstep(progress * 1.8)))
      logits[c] = confidence * (3.2 * sync + 3 * schedule)
      maxLogit = Math.max(maxLogit, logits[c])
    }
    let sum = 0
    for (let c = 0; c < ASSESSMENTS.length; c += 1) {
      this.probs[c] = Math.exp(logits[c] - maxLogit)
      sum += this.probs[c]
    }
    let entropy = 0
    for (let c = 0; c < ASSESSMENTS.length; c += 1) {
      this.probs[c] /= sum
      if (this.probs[c] > 1e-9) entropy -= this.probs[c] * Math.log(this.probs[c])
    }
    this.certainty = 1 - entropy / Math.log(ASSESSMENTS.length)
    if (Math.floor(tick) !== Math.floor(before) || before === 0) {
      const t = Math.min(TICKS_PER_THOUGHT, Math.floor(tick))
      this.certaintyByTick[t] = this.certainty
      if (this.decidedAt === null && this.certainty >= DECISION_CERTAINTY && this.probs[target] === Math.max(...this.probs)) {
        this.decidedAt = t
      }
    }

    // Neurons.
    const kTarget = 1 + 16 * progress
    const kCompetitor = 9 * profile.difficulty * bump(progress)
    const kPrior = 10 * (1 - smoothstep(progress * 1.8))
    const kRecruit = 7 * progress * meanHot
    const kCross = 6 * progress * meanHot
    const noise = 0.9 + 1.6 * this.surprise
    const sqrtH = Math.sqrt(h)
    for (let i = 0; i < n; i += 1) {
      const role = this.role[i]
      const g = role === ROLE_INPUT ? this.group[i] : role === ROLE_ASSOCIATION ? inputCount : inputCount + 1 + this.group[i]
      const R = this.groupR[g]
      const psi = this.groupPsi[g]
      let drive: number
      let k: number
      let omega = this.omega[i]
      let extra = 0
      if (role === ROLE_INPUT) {
        const sal = profile.salience[this.group[i]]
        drive = 0.08 + 0.9 * sal * (0.45 + 0.55 * wave) + 0.14 * wave
        k = 2 + 12 * sal * (0.35 + 0.65 * wave)
        if (rand() < h * (0.5 + 7 * sal * wave + 1.2 * sal)) this.arrivals.push(i)
      } else if (role === ROLE_ASSOCIATION) {
        drive = 0.14 + 0.45 * meanHot * (0.5 + 0.5 * wave) + 0.4 * this.surprise + 0.25 * progress * meanHot
        k = 1.5
        extra = kRecruit * targetR * Math.sin(targetPsi - this.phase[i])
      } else {
        const c = this.group[i]
        drive = 0.1 + 0.9 * this.probs[c]
        if (c === target) {
          k = kTarget
          omega += (targetOmega - omega) * 0.6 * progress
          extra = kCross * hotR * Math.sin(hotPsi - this.phase[i])
        } else if (c === this.competitorIndex) k = kCompetitor
        else if (c === this.priorIndex) k = kPrior
        else k = -2
      }
      this.amp[i] += (drive - this.amp[i]) * (1 - Math.exp(-h * 5))
      let phase = this.phase[i] + (omega + k * R * Math.sin(psi - this.phase[i]) + extra) * h + (rand() - 0.5) * noise * sqrtH
      if (phase >= TWO_PI) {
        phase -= TWO_PI
        if (this.amp[i] > 0.2) {
          this.fired.push(i)
          this.flash[i] = Math.min(1, this.flash[i] + 0.5 + 0.5 * this.amp[i])
        }
      } else if (phase < 0) phase += TWO_PI
      this.phase[i] = phase
      const cos = Math.cos(phase)
      this.z[i] = this.amp[i] * (0.62 * Math.exp(3.2 * (cos - 1)) + 0.38 * (0.5 + 0.5 * cos))
      this.flash[i] *= Math.exp(-h * 7)
    }

    // Assembly activity for the reading and conclusion rows.
    for (let k = 0; k < inputCount; k += 1) {
      let a = 0
      for (const i of this.members[k]) a += this.z[i] * 0.6 + this.amp[i] * 0.4
      this.inputActivity[k] += (a / INPUT_GROUP_SIZE - this.inputActivity[k]) * (1 - Math.exp(-h * 8))
    }
    for (let c = 0; c < ASSESSMENTS.length; c += 1) {
      let a = 0
      for (const i of this.members[inputCount + 1 + c]) a += this.z[i] * 0.6 + this.amp[i] * 0.4
      this.outputActivity[c] = a / OUTPUT_GROUP_SIZE
    }

    // Pairwise synchrony (smoothed).
    const blend = 1 - Math.exp(-h / 0.35)
    for (let p = 0; p < this.pairSync.length; p += 1) {
      const a = this.pairs[p * 2]
      const b = this.pairs[p * 2 + 1]
      const sync = 0.5 * (1 + Math.cos(this.phase[a] - this.phase[b])) * Math.sqrt(this.amp[a] * this.amp[b])
      this.pairSync[p] += (sync - this.pairSync[p]) * blend
    }
    this.sampleTraces(h)
  }

  private decaySync(h: number) {
    const fade = Math.exp(-h * 3)
    for (let p = 0; p < this.pairSync.length; p += 1) this.pairSync[p] *= fade
    this.inputActivity.forEach((value, k) => (this.inputActivity[k] = value * fade))
    this.outputActivity.fill(0)
  }

  private sampleTraces(h: number) {
    this.traceClock += h
    const interval = 1 / TRACE_RATE
    while (this.traceClock >= interval) {
      this.traceClock -= interval
      this.traceHead = (this.traceHead + 1) % TRACE_SAMPLES
      this.lanes.forEach((lane, index) => {
        this.traces[index][this.traceHead] = this.z[lane.neuron]
      })
    }
  }

  /** Run the brain without drawing, e.g. to show a settled frame when motion is reduced. */
  settle(seconds: number) {
    for (let t = 0; t < seconds; t += 1 / 30) this.step(1 / 30)
  }
}
