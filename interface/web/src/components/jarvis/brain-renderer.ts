// Draws a BrainEngine on a canvas: neurons glowing as they fire, signals travelling along
// synapses, and links between neurons that fire in step (the synchrony a CTM reads its
// answer from). Plain canvas 2D, no dependencies; a few hundred draw calls per frame.

import { ASSESSMENTS, type Assessment } from "@/services/matrix"

import {
  BrainEngine,
  DECISION_CERTAINTY,
  PAIR_CROSS,
  PAIR_INPUT,
  PAIR_OUTPUT,
  ROLE_ASSOCIATION,
  ROLE_INPUT,
  TICKS_PER_THOUGHT,
} from "./brain-engine"

type RGB = readonly [number, number, number]

/** Conclusion colours on the dark scope (Tailwind 300/400 steps, chosen for contrast on navy). */
export const CONCLUSION_RGB: Record<Assessment, RGB> = {
  NORMAL: [52, 211, 153], // emerald-400
  HANDLED_BY_MACHINE: [94, 234, 212], // teal-300
  WATCHING: [252, 211, 77], // amber-300
  ESCALATE_TO_USER: [251, 146, 60], // orange-400
  DANGER: [248, 113, 113], // red-400
}
export const READING_RGB: RGB = [103, 232, 249] // cyan-300, the brand mark's colour
export const HOT_READING_RGB: RGB = [251, 191, 36] // amber-400: a reading out of range
const ASSOCIATION_RGB: RGB = [125, 211, 252] // sky-300
const IDLE_RGB: RGB = [71, 92, 122]

export const rgba = (rgb: RGB, alpha: number) => `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${Math.max(0, Math.min(1, alpha)).toFixed(3)})`

export type Point = { x: number; y: number }
export type BrainGeometry = {
  /** Canvas size in CSS pixels. */
  width: number
  height: number
  /** Where the neurons live, in CSS pixels. */
  field: { x: number; y: number; w: number; h: number }
  /** Where each reading's wiring starts (right edge of its row). */
  inputAnchors: Point[]
  /** Where each conclusion's wiring ends (left edge of its row). */
  outputAnchors: Point[]
}

type Pulse = { ax: number; ay: number; cx: number; cy: number; bx: number; by: number; t: number; duration: number; rgb: RGB; size: number; to: number }
/** A ripple where a strongly active neuron just fired. */
type Ripple = { x: number; y: number; t: number; rgb: RGB }

const sprites = new Map<string, HTMLCanvasElement>()
function glowSprite(rgb: RGB): HTMLCanvasElement {
  const key = rgb.join(",")
  let sprite = sprites.get(key)
  if (!sprite) {
    sprite = document.createElement("canvas")
    sprite.width = sprite.height = 64
    const ctx = sprite.getContext("2d")!
    const gradient = ctx.createRadialGradient(32, 32, 0, 32, 32, 32)
    gradient.addColorStop(0, rgba(rgb, 1))
    gradient.addColorStop(0.18, rgba(rgb, 0.55))
    gradient.addColorStop(0.45, rgba(rgb, 0.16))
    gradient.addColorStop(1, rgba(rgb, 0))
    ctx.fillStyle = gradient
    ctx.fillRect(0, 0, 64, 64)
    sprites.set(key, sprite)
  }
  return sprite
}

/** A gentle bow for a link between two points, stable for a given pair. */
function bow(ax: number, ay: number, bx: number, by: number, salt: number, amount: number): Point {
  const mx = (ax + bx) / 2
  const my = (ay + by) / 2
  const dx = bx - ax
  const dy = by - ay
  const s = (((salt * 2654435761) >>> 0) / 4294967296 - 0.5) * 2 * amount
  return { x: mx - dy * s, y: my + dx * s }
}

export class BrainRenderer {
  private readonly canvas: HTMLCanvasElement
  private readonly ctx: CanvasRenderingContext2D
  private staticLayer: HTMLCanvasElement
  private geometry: BrainGeometry | null = null
  private dpr = 1
  private staticKey = ""
  private pulses: Pulse[] = []
  private ripples: Ripple[] = []
  private glow = new Float32Array(0)
  private px = new Float32Array(0)
  private py = new Float32Array(0)

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    this.ctx = canvas.getContext("2d")!
    this.staticLayer = document.createElement("canvas")
  }

  resize(geometry: BrainGeometry, dpr: number) {
    this.geometry = geometry
    this.dpr = Math.min(2, Math.max(1, dpr))
    const width = Math.max(1, Math.round(geometry.width * this.dpr))
    const height = Math.max(1, Math.round(geometry.height * this.dpr))
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width
      this.canvas.height = height
    }
    this.staticLayer.width = width
    this.staticLayer.height = height
    this.staticKey = ""
    this.pulses = []
    this.ripples = []
  }

  /** Forget cached wiring and in-flight signals (for example after switching to another node's brain). */
  invalidate() {
    this.staticKey = ""
    this.pulses = []
    this.ripples = []
    this.glow.fill(0)
  }

  private positions(engine: BrainEngine) {
    const g = this.geometry!
    if (this.px.length !== engine.size) {
      this.px = new Float32Array(engine.size)
      this.py = new Float32Array(engine.size)
      this.glow = new Float32Array(engine.size)
    }
    for (let i = 0; i < engine.size; i += 1) {
      this.px[i] = g.field.x + engine.x[i] * g.field.w
      this.py[i] = g.field.y + engine.y[i] * g.field.h
    }
  }

  /** Background, grid, and the faint wiring; redrawn only when the layout or the readings change. */
  private drawStatic(engine: BrainEngine, salience: number[], target: Assessment) {
    const g = this.geometry!
    const ctx = this.staticLayer.getContext("2d")!
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0)
    ctx.clearRect(0, 0, g.width, g.height)

    // The panel colours are CSS variables (index.css), so the canvas matches the page around it.
    const styles = getComputedStyle(this.canvas)
    const background = ctx.createLinearGradient(0, 0, 0, g.height)
    background.addColorStop(0, styles.getPropertyValue("--scope").trim() || "#081322")
    background.addColorStop(1, styles.getPropertyValue("--scope-raised").trim() || "#0b1a2f")
    ctx.fillStyle = background
    ctx.fillRect(0, 0, g.width, g.height)
    const vignette = ctx.createRadialGradient(g.field.x + g.field.w / 2, g.field.y + g.field.h / 2, 10, g.field.x + g.field.w / 2, g.field.y + g.field.h / 2, Math.max(g.field.w, g.field.h) * 0.7)
    vignette.addColorStop(0, "rgba(56, 189, 248, 0.07)")
    vignette.addColorStop(1, "rgba(56, 189, 248, 0)")
    ctx.fillStyle = vignette
    ctx.fillRect(0, 0, g.width, g.height)

    ctx.fillStyle = "rgba(148, 163, 184, 0.09)"
    for (let x = 12; x < g.width; x += 24) {
      for (let y = 12; y < g.height; y += 24) ctx.fillRect(x, y, 1, 1)
    }

    // Synapses.
    ctx.lineWidth = 1
    ctx.strokeStyle = "rgba(125, 211, 252, 0.075)"
    ctx.beginPath()
    for (let l = 0; l < engine.links.length; l += 2) {
      const a = engine.links[l]
      const b = engine.links[l + 1]
      const c = bow(this.px[a], this.py[a], this.px[b], this.py[b], l, 0.12)
      ctx.moveTo(this.px[a], this.py[a])
      ctx.quadraticCurveTo(c.x, c.y, this.px[b], this.py[b])
    }
    ctx.stroke()

    // Dendrites from each reading's row, axons into each conclusion's row.
    for (let i = 0; i < engine.size; i += 1) {
      if (engine.role[i] === ROLE_INPUT) {
        const k = engine.group[i]
        const anchor = g.inputAnchors[k]
        if (!anchor) continue
        const hot = salience[k] > 0.5
        ctx.strokeStyle = rgba(hot ? HOT_READING_RGB : READING_RGB, hot ? 0.2 : 0.085)
        ctx.beginPath()
        ctx.moveTo(anchor.x, anchor.y)
        const midX = (anchor.x + this.px[i]) / 2
        ctx.bezierCurveTo(midX, anchor.y, midX, this.py[i], this.px[i], this.py[i])
        ctx.stroke()
      } else if (engine.role[i] !== ROLE_ASSOCIATION) {
        const c = engine.group[i]
        const anchor = g.outputAnchors[c]
        if (!anchor) continue
        const rgb = CONCLUSION_RGB[ASSESSMENTS[c]]
        ctx.strokeStyle = rgba(rgb, ASSESSMENTS[c] === target ? 0.22 : 0.07)
        ctx.beginPath()
        ctx.moveTo(this.px[i], this.py[i])
        const midX = (anchor.x + this.px[i]) / 2
        ctx.bezierCurveTo(midX, this.py[i], midX, anchor.y, anchor.x, anchor.y)
        ctx.stroke()
      }
    }
  }

  private spawn(ax: number, ay: number, bx: number, by: number, salt: number, rgb: RGB, size: number, to: number, speed = 240) {
    if (this.pulses.length > 280) this.pulses.splice(0, this.pulses.length - 280)
    const c = bow(ax, ay, bx, by, salt, 0.12)
    const duration = Math.min(0.7, Math.max(0.14, Math.hypot(bx - ax, by - ay) / speed))
    this.pulses.push({ ax, ay, cx: c.x, cy: c.y, bx, by, t: 0, duration, rgb, size, to })
  }

  draw(engine: BrainEngine, dt: number, salience: number[], target: Assessment) {
    const g = this.geometry
    if (!g) return
    this.positions(engine)
    const key = `${salience.map((s) => (s > 0.5 ? 1 : 0)).join("")}|${target}|${g.width}x${g.height}|${engine.x[0]}|${engine.y[0]}`
    if (key !== this.staticKey) {
      this.drawStatic(engine, salience, target)
      this.staticKey = key
    }
    const ctx = this.ctx
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.globalCompositeOperation = "source-over"
    ctx.drawImage(this.staticLayer, 0, 0)
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0)

    const targetIndex = ASSESSMENTS.indexOf(target)
    const neuronRgb = (i: number): RGB => {
      const role = engine.role[i]
      if (role === ROLE_INPUT) return salience[engine.group[i]] > 0.5 ? HOT_READING_RGB : READING_RGB
      if (role === ROLE_ASSOCIATION) return ASSOCIATION_RGB
      return CONCLUSION_RGB[ASSESSMENTS[engine.group[i]]]
    }

    // New signals: readings arriving, neurons firing.
    for (const i of engine.arrivals) {
      const anchor = g.inputAnchors[engine.group[i]]
      if (anchor) this.spawn(anchor.x, anchor.y, this.px[i], this.py[i], i * 7 + 3, neuronRgb(i), salience[engine.group[i]] > 0.5 ? 1.25 : 0.85, i, 300)
    }
    for (const i of engine.fired) {
      const out = engine.outLinks[i]
      const rgb = neuronRgb(i)
      if (engine.amp[i] > 0.45 && this.ripples.length < 60) this.ripples.push({ x: this.px[i], y: this.py[i], t: 0, rgb })
      const count = engine.role[i] === ROLE_INPUT && salience[engine.group[i]] < 0.5 ? 1 : 2
      for (let n = 0; n < Math.min(count, out.length); n += 1) {
        const link = out[(n + engine.thought + i) % out.length]
        const j = engine.links[link * 2 + 1]
        this.spawn(this.px[i], this.py[i], this.px[j], this.py[j], link * 2, rgb, 0.8 + engine.amp[i] * 0.6, j)
      }
      if (engine.role[i] !== ROLE_INPUT && engine.role[i] !== ROLE_ASSOCIATION && engine.amp[i] > 0.4) {
        const anchor = g.outputAnchors[engine.group[i]]
        if (anchor) this.spawn(this.px[i], this.py[i], anchor.x, anchor.y, i, rgb, 1, -1, 220)
      }
    }

    ctx.globalCompositeOperation = "lighter"

    // Firing ripples.
    ctx.lineWidth = 1
    this.ripples = this.ripples.filter((ripple) => {
      ripple.t += dt / 0.55
      if (ripple.t >= 1) return false
      ctx.strokeStyle = rgba(ripple.rgb, 0.5 * (1 - ripple.t) * (1 - ripple.t))
      ctx.beginPath()
      ctx.arc(ripple.x, ripple.y, 3 + 13 * ripple.t, 0, Math.PI * 2)
      ctx.stroke()
      return true
    })

    // Synchrony: pairs of neurons firing in step.
    ctx.lineCap = "round"
    for (let p = 0; p < engine.pairSync.length; p += 1) {
      const s = engine.pairSync[p]
      if (s < 0.4) continue
      const kind = engine.pairKind[p]
      const a = engine.pairs[p * 2]
      const b = engine.pairs[p * 2 + 1]
      let rgb: RGB
      let strength = Math.pow((s - 0.4) / 0.6, 1.4)
      if (kind === PAIR_INPUT) {
        rgb = salience[engine.group[a]] > 0.5 ? HOT_READING_RGB : READING_RGB
        strength *= 0.55
      } else {
        rgb = CONCLUSION_RGB[ASSESSMENTS[engine.pairGroup[p]]]
        if (kind === PAIR_CROSS) strength *= engine.pairGroup[p] === targetIndex ? 0.4 : 0.15
        else if (kind !== PAIR_OUTPUT) strength *= 0.45
      }
      if (strength < 0.02) continue
      const c = bow(this.px[a], this.py[a], this.px[b], this.py[b], p, kind === PAIR_OUTPUT || kind === PAIR_INPUT ? 0.1 : 0.08)
      ctx.strokeStyle = rgba(rgb, 0.85 * strength)
      ctx.lineWidth = 0.6 + 1.1 * strength
      ctx.beginPath()
      ctx.moveTo(this.px[a], this.py[a])
      ctx.quadraticCurveTo(c.x, c.y, this.px[b], this.py[b])
      ctx.stroke()
    }

    // Travelling signals.
    const alive: Pulse[] = []
    for (const pulse of this.pulses) {
      pulse.t += dt / pulse.duration
      if (pulse.t >= 1) {
        if (pulse.to >= 0 && pulse.to < this.glow.length) this.glow[pulse.to] = Math.min(1, this.glow[pulse.to] + 0.22)
        continue
      }
      alive.push(pulse)
      const t = pulse.t
      const u = 1 - t
      const x = u * u * pulse.ax + 2 * u * t * pulse.cx + t * t * pulse.bx
      const y = u * u * pulse.ay + 2 * u * t * pulse.cy + t * t * pulse.by
      const fade = Math.min(1, t * 6) * Math.min(1, u * 4)
      const size = 9 * pulse.size
      ctx.globalAlpha = 0.9 * fade
      ctx.drawImage(glowSprite(pulse.rgb), x - size / 2, y - size / 2, size, size)
    }
    this.pulses = alive
    ctx.globalAlpha = 1

    // Neurons.
    const fade = Math.exp(-dt * 5)
    for (let i = 0; i < engine.size; i += 1) {
      this.glow[i] *= fade
      const rgb = neuronRgb(i)
      const energy = Math.min(1.4, engine.z[i] * 0.9 + engine.flash[i] * 0.8 + this.glow[i] * 0.5)
      if (energy > 0.04) {
        const size = 8 + 30 * energy
        ctx.globalAlpha = Math.min(1, 0.25 + energy * 0.75)
        ctx.drawImage(glowSprite(rgb), this.px[i] - size / 2, this.py[i] - size / 2, size, size)
      }
    }
    ctx.globalAlpha = 1
    ctx.globalCompositeOperation = "source-over"
    for (let i = 0; i < engine.size; i += 1) {
      const rgb = neuronRgb(i)
      const energy = Math.min(1, engine.z[i] + engine.flash[i] * 0.6)
      const radius = 1.5 + 1.3 * engine.amp[i] + energy * 0.8
      const mix = Math.min(1, 0.15 + energy)
      ctx.fillStyle = `rgb(${Math.round(IDLE_RGB[0] + (rgb[0] - IDLE_RGB[0]) * mix)},${Math.round(IDLE_RGB[1] + (rgb[1] - IDLE_RGB[1]) * mix)},${Math.round(IDLE_RGB[2] + (rgb[2] - IDLE_RGB[2]) * mix)})`
      ctx.beginPath()
      ctx.arc(this.px[i], this.py[i], radius, 0, Math.PI * 2)
      ctx.fill()
      if (energy > 0.55) {
        ctx.fillStyle = `rgba(255,255,255,${((energy - 0.55) * 1.6).toFixed(3)})`
        ctx.beginPath()
        ctx.arc(this.px[i], this.py[i], radius * 0.55, 0, Math.PI * 2)
        ctx.fill()
      }
    }

    // Ports where the wiring meets the rows.
    g.inputAnchors.forEach((anchor, k) => {
      const hot = salience[k] > 0.5
      const level = Math.min(1, engine.inputActivity[k] * 1.6)
      ctx.fillStyle = rgba(hot ? HOT_READING_RGB : READING_RGB, 0.35 + 0.65 * level)
      ctx.beginPath()
      ctx.arc(anchor.x, anchor.y, 2.6, 0, Math.PI * 2)
      ctx.fill()
    })
    g.outputAnchors.forEach((anchor, c) => {
      const rgb = CONCLUSION_RGB[ASSESSMENTS[c]]
      ctx.fillStyle = rgba(rgb, 0.3 + 0.7 * Math.min(1, engine.probs[c] * 1.2))
      ctx.beginPath()
      ctx.arc(anchor.x, anchor.y, 2.6, 0, Math.PI * 2)
      ctx.fill()
    })
  }
}

/** Neuron activation traces: one lane per followed neuron, newest on the right. */
export function drawTraces(canvas: HTMLCanvasElement, engine: BrainEngine, colors: RGB[], dpr: number) {
  const ctx = canvas.getContext("2d")
  if (!ctx) return
  const width = canvas.width / dpr
  const height = canvas.height / dpr
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, width, height)
  const lanes = engine.lanes.length
  const laneHeight = height / lanes
  const samples = engine.traces[0]?.length ?? 0
  if (!samples) return
  const step = width / (samples - 1)
  for (let lane = 0; lane < lanes; lane += 1) {
    const top = lane * laneHeight
    const base = top + laneHeight - 3
    ctx.strokeStyle = "rgba(148, 163, 184, 0.12)"
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(0, base + 0.5)
    ctx.lineTo(width, base + 0.5)
    ctx.stroke()
    const trace = engine.traces[lane]
    const rgb = colors[lane]
    ctx.beginPath()
    for (let s = 0; s < samples; s += 1) {
      const value = trace[(engine.traceHead + 1 + s) % samples]
      const x = s * step
      const y = base - value * (laneHeight - 6)
      if (s === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.lineJoin = "round"
    ctx.strokeStyle = rgba(rgb, 0.22)
    ctx.lineWidth = 3.5
    ctx.stroke()
    ctx.strokeStyle = rgba(rgb, 0.95)
    ctx.lineWidth = 1.25
    ctx.stroke()
  }
}

/** Certainty across the ticks of this thought (and, faintly, the last one). */
export function drawCertainty(canvas: HTMLCanvasElement, engine: BrainEngine, rgb: RGB, dpr: number) {
  const ctx = canvas.getContext("2d")
  if (!ctx) return
  const width = canvas.width / dpr
  const height = canvas.height / dpr
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, width, height)
  const pad = 3
  const x = (tick: number) => pad + (tick / TICKS_PER_THOUGHT) * (width - pad * 2)
  const y = (value: number) => height - pad - value * (height - pad * 2)

  // Tick ruler.
  for (let t = 0; t <= TICKS_PER_THOUGHT; t += 1) {
    const done = t <= engine.tick
    ctx.fillStyle = done ? "rgba(148, 163, 184, 0.5)" : "rgba(148, 163, 184, 0.18)"
    ctx.fillRect(Math.round(x(t)), height - 2, 1, t % 6 === 0 ? 2 : 1)
  }
  ctx.setLineDash([3, 3])
  ctx.strokeStyle = "rgba(148, 163, 184, 0.35)"
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(pad, Math.round(y(DECISION_CERTAINTY)) + 0.5)
  ctx.lineTo(width - pad, Math.round(y(DECISION_CERTAINTY)) + 0.5)
  ctx.stroke()
  ctx.setLineDash([])

  const line = (values: Float32Array, upTo: number, style: string, widthPx: number) => {
    ctx.beginPath()
    for (let t = 0; t <= upTo; t += 1) {
      if (t === 0) ctx.moveTo(x(t), y(values[t]))
      else ctx.lineTo(x(t), y(values[t]))
    }
    ctx.strokeStyle = style
    ctx.lineWidth = widthPx
    ctx.stroke()
  }
  if (engine.hasPrevious) line(engine.previousCertainty, TICKS_PER_THOUGHT - 1, "rgba(148, 163, 184, 0.25)", 1)
  const upTo = Math.min(TICKS_PER_THOUGHT - 1, Math.floor(engine.tick))
  line(engine.certaintyByTick, upTo, rgba(rgb, 0.95), 1.6)
  if (engine.decidedAt !== null) {
    ctx.fillStyle = rgba(rgb, 1)
    ctx.beginPath()
    ctx.arc(x(engine.decidedAt), y(engine.certaintyByTick[engine.decidedAt]), 2.6, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.fillStyle = "rgba(226, 232, 240, 0.8)"
  ctx.fillRect(Math.round(x(engine.tick)), pad, 1, height - pad * 2)
}
