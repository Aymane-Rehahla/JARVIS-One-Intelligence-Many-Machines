import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent as ReactWheelEvent } from "react"
import { ChevronFirst, ChevronLast, ChevronLeft, ChevronRight, Minus, Pause, Play, Plus, RotateCcw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useReducedMotion } from "@/hooks/use-reduced-motion"
import {
  MOMENT_LABEL,
  MOMENT_MEANING,
  NODE_BRAIN,
  NODE_LABEL,
  RECORDED_LABEL,
  farmClock,
  humanize,
  isKnownFalseAlert,
  momentsOf,
  useArchitecture,
  useCtm,
  useManifest,
  useTimeline,
  type Brain,
  type CtmRecord,
  type MindNode,
  type MomentKey,
  type Thought,
  type Timeline,
} from "./mind-data"
import { layoutNeurons } from "./neuron-layout"

const STEPS = 16
const W = 1000, H = 640
/** One colour per assessment level, the same mapping the rest of the site uses. */
const LEVEL_COLOR = ["#1f8a4c", "#2f5d8a", "#e09500", "#ff4500", "#c8102e"]

function mix(a: [number, number, number], b: [number, number, number], t: number) {
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * t)} ${Math.round(a[1] + (b[1] - a[1]) * t)} ${Math.round(a[2] + (b[2] - a[2]) * t)})`
}
const NEUTRAL: [number, number, number] = [214, 214, 214], POSITIVE: [number, number, number] = [255, 69, 0], NEGATIVE: [number, number, number] = [10, 10, 10]
/** Diverging scale: orange for positive output, ink for negative, grey near zero. */
function activityColor(t: number) {
  const m = Math.min(1, Math.abs(t)) ** 0.8
  return t >= 0 ? mix(NEUTRAL, POSITIVE, m) : mix(NEUTRAL, NEGATIVE, m)
}
function percentile(values: number[], q: number) {
  const sorted = [...values].sort((a, b) => a - b)
  return Math.max(1e-6, sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))] ?? 0)
}
const fmt = (value: number, digits = 3) => (value >= 0 ? "+" : "−") + Math.abs(value).toFixed(digits)
const pct = (value: number) => `${Math.round(value * 100)} %`

export function CtmExplorer({ node }: { node: MindNode }) {
  const { data: record, error } = useCtm(node)
  const { data: architecture } = useArchitecture()
  const { data: timeline } = useTimeline()
  const { data: manifest } = useManifest()
  const brain = architecture?.[NODE_BRAIN[node]] ?? null

  if (error) return <p className="text-[14px] text-danger">The recorded thoughts for {NODE_LABEL[node]} could not be loaded ({error}).</p>
  if (!record) return <div className="hatch grid h-[420px] place-items-center border"><span role="status" className="label-mono bg-background px-3 py-2">Loading {NODE_LABEL[node]}'s recorded thoughts…</span></div>
  return <CtmRecordView key={node} node={node} record={record} brain={brain} timeline={timeline} startHour={manifest?.episode.start_hour ?? timeline?.episode.start_hour ?? 0} />
}

function CtmRecordView({ node, record, brain, timeline, startHour }: { node: MindNode; record: CtmRecord; brain: Brain | null; timeline: Timeline | null; startHour: number }) {
  const moments = momentsOf(record)
  const [moment, setMoment] = useState<MomentKey>(moments.includes("first_alarm") ? "first_alarm" : moments[0])
  const thought = record.thoughts[moment] as Thought
  const [step, setStep] = useState(1)
  const [playing, setPlaying] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const reduced = useReducedMotion()
  const syncNeurons = useMemo(() => new Set(brain?.synchronisation.neurons ?? Array.from({ length: 32 }, (_, i) => 224 + i)), [brain])

  useEffect(() => {
    if (!playing) return
    if (step >= STEPS) { setPlaying(false); return }
    const timer = window.setTimeout(() => setStep((value) => Math.min(STEPS, value + 1)), reduced ? 900 : 650)
    return () => window.clearTimeout(timer)
  }, [playing, step, reduced])

  const pickMoment = (next: MomentKey) => { setMoment(next); setStep(1); setPlaying(false) }

  return (
    <div className="space-y-8">
      <EpisodeStrip node={node} record={record} timeline={timeline} moment={moment} onMoment={pickMoment} startHour={startHour} />
      <div className="grid gap-6 2xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-4">
          <NetworkView node={node} record={record} thought={thought} step={step} selected={selected} onSelect={setSelected} syncNeurons={syncNeurons} />
          <StepPlayer step={step} certainty={thought.per_step.certainty} playing={playing} onStep={(value) => { setPlaying(false); setStep(value) }} onPlay={() => { if (step >= STEPS) setStep(1); setPlaying((value) => !value) }} />
        </div>
        <NeuronInspector thought={thought} step={step} neuron={selected} edges={record.edges} syncNeurons={syncNeurons} onSelect={setSelected} />
      </div>
      <EffortView node={node} moment={moment} thought={thought} step={step} brain={brain} latency={record.latency_ms} />
      <SeenAndConcluded thought={thought} />
      <HistoryExplorer thought={thought} step={step} selected={selected} onSelect={setSelected} syncNeurons={syncNeurons} />
    </div>
  )
}

/* ------------------------------------------------------------------ episode */

function EpisodeStrip({ node, record, timeline, moment, onMoment, startHour }: { node: MindNode; record: CtmRecord; timeline: Timeline | null; moment: MomentKey; onMoment: (moment: MomentKey) => void; startHour: number }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const series = timeline?.nodes[node] ?? null
  const duration = timeline?.episode.duration_ticks ?? 1800
  useEffect(() => {
    const element = canvas.current
    if (!element || !series) return
    element.width = series.length
    element.height = 1
    const context = element.getContext("2d")
    if (!context) return
    series.forEach(([level], tick) => { context.fillStyle = LEVEL_COLOR[level] ?? "#8c8c8c"; context.fillRect(tick, 0, 1, 1) })
  }, [series])
  const moments = momentsOf(record)
  return (
    <section aria-labelledby="episode-title">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h3 id="episode-title" className="display-sm text-[13px]">The recorded episode</h3>
        <span className="label-mono text-muted-foreground">{duration} farm seconds // from {farmClock(startHour, 0)}</span>
      </div>
      <p className="mt-2 max-w-3xl text-[14px] leading-6 text-muted-foreground">
        What {NODE_LABEL[node]} said every second. Three moments were kept with every internal step: pick one to replay it.
      </p>
      <div className="relative mt-4 h-10 border bg-panel">
        {series ? <canvas ref={canvas} aria-hidden className="absolute inset-0 h-full w-full [image-rendering:pixelated]" /> : <div className="hatch absolute inset-0" />}
        {timeline?.episode.faults.map((fault) => (
          <div key={fault.inject_tick} aria-hidden className="absolute inset-y-0 border-x border-foreground" style={{ left: `${(fault.inject_tick / duration) * 100}%`, width: `${((fault.end_tick - fault.inject_tick) / duration) * 100}%`, backgroundImage: "repeating-linear-gradient(130deg, transparent 0 5px, rgb(10 10 10 / 0.35) 5px 6px)" }} />
        ))}
        {moments.map((key) => {
          const tick = record.thoughts[key]?.tick ?? 0
          return <span key={key} aria-hidden className={cn("absolute -top-1.5 -bottom-1.5 w-[3px] -translate-x-1/2", key === moment ? "bg-foreground" : "bg-foreground/40")} style={{ left: `${(tick / duration) * 100}%` }} />
        })}
      </div>
      <div className="mt-2 flex flex-wrap justify-between gap-2">
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {(timeline?.assessments ?? []).map((label, index) => <li key={label} className="label-mono flex items-center gap-1.5 text-muted-foreground"><span aria-hidden className="size-2" style={{ background: LEVEL_COLOR[index] }} />{humanize(label)}</li>)}
          {timeline?.episode.faults.map((fault) => <li key={fault.inject_tick} className="label-mono flex items-center gap-1.5 text-muted-foreground"><span aria-hidden className="size-2 border border-foreground" style={{ backgroundImage: "repeating-linear-gradient(130deg, transparent 0 2px, #0a0a0a 2px 3px)" }} />Fault: {fault.kind} {fault.voltage_v ? `${fault.voltage_v} V` : ""}, ticks {fault.inject_tick}–{fault.end_tick}</li>)}
        </ul>
      </div>
      <div role="radiogroup" aria-label="Recorded moment" className="mt-4 grid border-l border-t sm:grid-cols-3">
        {moments.map((key) => {
          const kept = record.thoughts[key] as Thought
          const falseAlert = isKnownFalseAlert(node, key)
          return (
            <button
              key={key}
              type="button"
              role="radio"
              aria-checked={moment === key}
              onClick={() => onMoment(key)}
              className={cn("border-b border-r border-l-[3px] p-4 text-left transition-colors duration-150", moment === key ? "border-l-signal bg-foreground text-background" : "border-l-transparent hover:bg-secondary")}
            >
              <span className="label-mono block opacity-70">Tick {kept.tick} // {farmClock(startHour, kept.tick)}</span>
              <span className="display-sm mt-2 block text-[14px]">{MOMENT_LABEL[key]}</span>
              <span className="mt-1 block text-[13px] leading-5 opacity-80">{falseAlert ? "A false alert: the release's known open issue, before any fault." : MOMENT_MEANING[key]}</span>
              <span className="label-mono mt-2 block">Said // {humanize(kept.decided.assessment)}</span>
            </button>
          )
        })}
        {moments.length === 1 && (
          <div className="border-b border-r p-4 text-[13px] leading-5 text-muted-foreground sm:col-span-2">
            This node stayed calm for the whole episode, which is correct: the fault was not on its machine. Only its calm thought was kept.
          </div>
        )}
      </div>
    </section>
  )
}

/* ------------------------------------------------------------------ network */

function NetworkView({ node, record, thought, step, selected, onSelect, syncNeurons }: { node: MindNode; record: CtmRecord; thought: Thought; step: number; selected: number | null; onSelect: (neuron: number | null) => void; syncNeurons: Set<number> }) {
  const points = useMemo(() => layoutNeurons(node, 256, record.edges, W, H), [node, record.edges])
  const [view, setView] = useState({ x: 0, y: 0, k: 1 })
  const svg = useRef<SVGSVGElement>(null)
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean } | null>(null)

  // Scales are fixed per thought so a change between steps is a real change, not a re-normalisation.
  // The 95th percentile, not the maximum, so one extreme neuron does not wash the rest out to grey.
  const postMax = useMemo(() => percentile(thought.history_post.flatMap((row) => row.slice(16).map(Math.abs)), 0.95), [thought])
  const edgeMax = useMemo(() => percentile(thought.per_step.edge_weight.flatMap((row) => row.map(Math.abs)), 0.95), [thought])
  const weights = thought.per_step.edge_weight[step - 1]

  const zoomAt = (factor: number, cx = W / 2, cy = H / 2) => setView((current) => {
    const k = Math.min(6, Math.max(1, current.k * factor))
    const wx = (cx - current.x) / current.k, wy = (cy - current.y) / current.k
    return { k, x: k === 1 ? 0 : cx - wx * k, y: k === 1 ? 0 : cy - wy * k }
  })
  const toView = (clientX: number, clientY: number) => {
    const box = svg.current?.getBoundingClientRect()
    if (!box) return { x: W / 2, y: H / 2 }
    return { x: ((clientX - box.left) / box.width) * W, y: ((clientY - box.top) / box.height) * H }
  }
  const onWheel = (event: ReactWheelEvent<SVGSVGElement>) => {
    if (!event.ctrlKey && !event.metaKey && Math.abs(event.deltaY) < 1) return
    const p = toView(event.clientX, event.clientY)
    zoomAt(event.deltaY < 0 ? 1.15 : 1 / 1.15, p.x, p.y)
  }
  const onPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    drag.current = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y, moved: false }
  }
  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const current = drag.current
    if (!current) return
    const box = svg.current?.getBoundingClientRect()
    if (!box) return
    const dx = ((event.clientX - current.x) / box.width) * W, dy = ((event.clientY - current.y) / box.height) * H
    if (Math.abs(dx) + Math.abs(dy) > 3) current.moved = true
    if (current.moved && view.k > 1) {
      event.currentTarget.setPointerCapture(event.pointerId)
      setView((value) => ({ ...value, x: current.vx + dx, y: current.vy + dy }))
    }
  }
  const onPointerUp = () => { drag.current = null }
  const pick = (neuron: number) => { if (!drag.current?.moved) onSelect(selected === neuron ? null : neuron) }

  return (
    <section aria-labelledby="network-title" className="border bg-background">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <div>
          <h3 id="network-title" className="display-sm text-[13px]">256 neurons, step {step} of 16</h3>
          <p className="label-mono mt-1 text-muted-foreground">{RECORDED_LABEL}</p>
        </div>
        <div className="flex items-center gap-1">
          <Button size="icon-sm" variant="outline" aria-label="Zoom in" onClick={() => zoomAt(1.4)}><Plus /></Button>
          <Button size="icon-sm" variant="outline" aria-label="Zoom out" onClick={() => zoomAt(1 / 1.4)}><Minus /></Button>
          <Button size="icon-sm" variant="outline" aria-label="Reset zoom" onClick={() => setView({ x: 0, y: 0, k: 1 })}><RotateCcw /></Button>
        </div>
      </div>
      <div className="relative bg-panel" style={{ backgroundImage: "linear-gradient(#ececec 1px, transparent 1px), linear-gradient(90deg, #ececec 1px, transparent 1px)", backgroundSize: "40px 40px" }}>
        <svg
          ref={svg}
          viewBox={`0 0 ${W} ${H}`}
          className={cn("block h-auto w-full touch-pan-y select-none", view.k > 1 && "cursor-grab active:cursor-grabbing")}
          onWheel={onWheel}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
          role="group"
          aria-label={`Neuron layout for ${NODE_LABEL[node]}. Positions are a layout; the links and the activity are real.`}
        >
          <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
            <g strokeLinecap="round">
              {record.edges.map(([source, target], index) => {
                const t = weights[index] / edgeMax
                const focused = selected === null || selected === target || selected === source
                const strength = Math.abs(t)
                const alpha = selected === null ? 0.05 + 0.55 * strength : focused ? 0.25 + 0.7 * strength : 0.02
                return (
                  <line
                    key={index}
                    x1={points[source].x}
                    y1={points[source].y}
                    x2={points[target].x}
                    y2={points[target].y}
                    stroke={t >= 0 ? `rgb(255 69 0 / ${alpha})` : `rgb(10 10 10 / ${alpha})`}
                    strokeWidth={(focused && selected !== null ? 1.6 : 0.7) / view.k + strength * 1.2 / view.k}
                  />
                )
              })}
            </g>
            {points.map((point, neuron) => {
              const value = thought.history_post[neuron][15 + step] / postMax
              const fill = activityColor(value)
              const isSync = syncNeurons.has(neuron)
              const size = (isSync ? 11 : 9) / Math.sqrt(view.k)
              return (
                <g key={neuron} className="cursor-pointer" onClick={() => pick(neuron)}>
                  <circle cx={point.x} cy={point.y} r={12 / Math.sqrt(view.k)} fill="transparent" />
                  {isSync ? (
                    <rect x={point.x - size / 2} y={point.y - size / 2} width={size} height={size} fill={fill} stroke="#0a0a0a" strokeWidth={1 / view.k} />
                  ) : (
                    <circle cx={point.x} cy={point.y} r={size / 2} fill={fill} stroke={selected === neuron ? "#0a0a0a" : "rgb(10 10 10 / 0.35)"} strokeWidth={(selected === neuron ? 2 : 0.6) / view.k} />
                  )}
                  {selected === neuron && <circle cx={point.x} cy={point.y} r={(size / 2 + 6) / Math.sqrt(view.k)} fill="none" stroke="#ff4500" strokeWidth={2 / view.k} />}
                </g>
              )
            })}
          </g>
        </svg>
      </div>
      <div className="grid gap-x-6 gap-y-2 border-t px-4 py-3 md:grid-cols-[auto_minmax(0,1fr)]">
        <ul className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <li className="label-mono flex items-center gap-1.5 text-muted-foreground"><span aria-hidden className="size-2.5 bg-signal" />Positive output</li>
          <li className="label-mono flex items-center gap-1.5 text-muted-foreground"><span aria-hidden className="size-2.5 bg-foreground" />Negative output</li>
          <li className="label-mono flex items-center gap-1.5 text-muted-foreground"><span aria-hidden className="size-2.5 border border-foreground bg-[#d6d6d6]" />Readout neuron 224–255</li>
        </ul>
        <p className="text-[13px] leading-5 text-muted-foreground md:text-right">
          Positions are a layout from the real links; the links and the activity are real. Each line is one of a neuron's 6 strongest incoming influences (orange excites, black inhibits). Every neuron is in fact fed by all 256.
        </p>
      </div>
    </section>
  )
}

/* ------------------------------------------------------------------ player */

function StepPlayer({ step, certainty, playing, onStep, onPlay }: { step: number; certainty: number[]; playing: boolean; onStep: (step: number) => void; onPlay: () => void }) {
  return (
    <div className="border bg-background">
      <div className="flex flex-wrap items-center gap-1 border-b px-3 py-2">
        <Button size="icon-sm" variant="ghost" aria-label="First step" onClick={() => onStep(1)} disabled={step === 1}><ChevronFirst /></Button>
        <Button size="icon-sm" variant="ghost" aria-label="Previous step" onClick={() => onStep(Math.max(1, step - 1))} disabled={step === 1}><ChevronLeft /></Button>
        <Button size="sm" onClick={onPlay} className="mx-1 min-w-[104px]">{playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}{playing ? "Pause" : step >= STEPS ? "Replay" : "Play"}</Button>
        <Button size="icon-sm" variant="ghost" aria-label="Next step" onClick={() => onStep(Math.min(STEPS, step + 1))} disabled={step === STEPS}><ChevronRight /></Button>
        <Button size="icon-sm" variant="ghost" aria-label="Last step" onClick={() => onStep(STEPS)} disabled={step === STEPS}><ChevronLast /></Button>
        <span className="label-mono ml-auto text-muted-foreground">Same 256 neurons, same weights, 16 times</span>
      </div>
      <div role="radiogroup" aria-label="Internal step" className="grid grid-cols-8 sm:grid-cols-16">
        {certainty.map((value, index) => (
          <button
            key={index}
            type="button"
            role="radio"
            aria-checked={step === index + 1}
            aria-label={`Step ${index + 1}, certainty ${pct(value)}`}
            onClick={() => onStep(index + 1)}
            className={cn("relative flex h-16 flex-col justify-between border-r border-b p-1.5 text-left last:border-r-0 sm:border-b-0", step === index + 1 ? "bg-foreground text-background" : index + 1 < step ? "bg-secondary" : "hover:bg-secondary")}
          >
            <span className="label-mono">{String(index + 1).padStart(2, "0")}</span>
            <span aria-hidden className="flex h-6 w-full items-end">
              <span className={cn("block w-full", step === index + 1 ? "bg-signal" : "bg-foreground/70")} style={{ height: `${Math.max(4, value * 100)}%` }} />
            </span>
          </button>
        ))}
      </div>
      <p className="label-mono border-t px-3 py-2 text-muted-foreground">Bar height // certainty after that step</p>
    </div>
  )
}

/* ------------------------------------------------------------------ inspector */

function NeuronInspector({ thought, step, neuron, edges, syncNeurons, onSelect }: { thought: Thought; step: number; neuron: number | null; edges: [number, number][]; syncNeurons: Set<number>; onSelect: (neuron: number) => void }) {
  if (neuron === null) {
    return (
      <aside className="border bg-background p-5">
        <h3 className="display-sm text-[13px]">Neuron inspector</h3>
        <p className="mt-3 text-[14px] leading-6 text-muted-foreground">
          Pick any neuron in the map, or a row in the 32-step memory below. You will see its real output at this step, what drives it, and the 32 values it remembers.
        </p>
        <dl className="mt-5 border-t">
          <InspectorRow label="What a neuron is" value="One of 256 identical units. It has no name: nobody knows what each one means." />
          <InspectorRow label="What a link does" value="Carries how much one neuron's last output changes another's next input." />
          <InspectorRow label="What a step is" value="One pass of the same network. 16 passes make one thought, every farm second." />
        </dl>
      </aside>
    )
  }
  const column = 15 + step
  const incoming = edges.map((edge, index) => ({ edge, index })).filter(({ edge }) => edge[1] === neuron)
  const outgoing = edges.map((edge, index) => ({ edge, index })).filter(({ edge }) => edge[0] === neuron)
  const post = thought.history_post[neuron], pre = thought.history_pre[neuron]
  const maxAbs = Math.max(1e-6, ...post.map(Math.abs))
  return (
    <aside className="border bg-background" aria-live="polite">
      <div className="border-b px-5 py-4">
        <div className="label-mono text-muted-foreground">Neuron inspector // step {step}</div>
        <h3 className="display mt-2 text-[34px]">#{neuron}</h3>
        <p className="label-mono mt-1">{syncNeurons.has(neuron) ? "Readout neuron: its timing with the other 31 forms the output" : "Not one of the 32 readout neurons"}</p>
      </div>
      <div className="grid md:grid-cols-3 2xl:grid-cols-1">
        <dl className="px-5 md:border-r 2xl:border-r-0">
          <InspectorRow label="Output (post-activation)" value={fmt(post[column])} mono />
          <InspectorRow label="Input (pre-activation)" value={fmt(pre[column])} mono />
          <InspectorRow label="Driven by other neurons" value={thought.per_step.recurrent_drive[step - 1][neuron].toFixed(4)} mono />
          <InspectorRow label="Driven by the readings" value={thought.per_step.sensory_drive[step - 1][neuron].toFixed(4)} mono />
        </dl>
        <div className="border-t px-5 py-4 md:border-r md:border-t-0 2xl:border-r-0 2xl:border-t">
          <div className="label-mono text-muted-foreground">Its 32 remembered outputs</div>
          <svg viewBox="0 0 320 64" className="mt-2 block h-auto w-full" aria-label={`Neuron ${neuron}'s last 32 outputs. Columns 1 to 16 are the previous second, 17 to 32 this thought.`}>
            <rect x="160" y="0" width="160" height="64" fill="#f2f2f2" />
            <line x1="0" y1="32" x2="320" y2="32" stroke="#d1d1d1" />
            {post.map((value, index) => {
              const h = (Math.abs(value) / maxAbs) * 30
              return <rect key={index} x={index * 10 + 1.5} y={value >= 0 ? 32 - h : 32} width="7" height={Math.max(0.8, h)} fill={index === column ? "#ff4500" : index < 16 ? "#8c8c8c" : "#0a0a0a"} />
            })}
            <line x1="160" y1="0" x2="160" y2="64" stroke="#0a0a0a" strokeDasharray="3 2" />
          </svg>
          <div className="mt-1 flex justify-between"><span className="label-mono text-muted-foreground">Previous second</span><span className="label-mono text-muted-foreground">This thought</span></div>
        </div>
        <div className="border-t px-5 py-4 md:border-t-0 2xl:border-t">
          <div className="label-mono text-muted-foreground">Strongest incoming links at this step</div>
          <ul className="mt-2">
            {incoming.map(({ edge, index }) => {
              const weight = thought.per_step.edge_weight[step - 1][index]
              return (
                <li key={index} className="flex items-center justify-between gap-3 border-b py-1.5 last:border-0">
                  {edge[0] === neuron ? (
                    <span className="font-mono text-[13px]">from itself</span>
                  ) : (
                    <button type="button" onClick={() => onSelect(edge[0])} className="font-mono text-[13px] underline decoration-input underline-offset-4 hover:decoration-signal">from #{edge[0]}</button>
                  )}
                  <span className={cn("font-mono text-[13px] tabular-nums", weight >= 0 ? "text-signal-ink" : "text-foreground")}>{fmt(weight, 4)} {weight >= 0 ? "excites" : "inhibits"}</span>
                </li>
              )
            })}
          </ul>
          <p className="mt-3 text-[13px] leading-5 text-muted-foreground">
            {outgoing.length > 0 ? `It is among the 6 strongest influences on ${outgoing.length} ${outgoing.length === 1 ? "neuron" : "neurons"}.` : "It is not among the 6 strongest influences on any other neuron."}
          </p>
        </div>
      </div>
    </aside>
  )
}

function InspectorRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="grid gap-1 border-b py-3 last:border-0">
      <dt className="label-mono text-muted-foreground">{label}</dt>
      <dd className={cn("text-[14px] leading-6", mono && "font-mono tabular-nums")}>{value}</dd>
    </div>
  )
}

/* ------------------------------------------------------------------ effort */

function EffortView({ node, moment, thought, step, brain, latency }: { node: MindNode; moment: MomentKey; thought: Thought; step: number; brain: Brain | null; latency: CtmRecord["latency_ms"] }) {
  const levels = brain?.heads.assessment ?? ["NORMAL", "HANDLED_BY_MACHINE", "WATCHING", "ESCALATE_TO_USER", "DANGER"]
  const probs = thought.per_step.assessment_probabilities[step - 1]
  const final = thought.assessment_probabilities
  const headTop = final.indexOf(Math.max(...final))
  const certainty = thought.per_step.certainty
  const rms = thought.per_step.rms_change
  const rmsMax = Math.max(...rms)
  const flat = Math.max(...certainty) - Math.min(...certainty) < 0.02
  const stated = thought.decided.assessment
  // Axis fitted to this thought's range (to the nearest 10 %), labelled, so a small climb is visible and honest.
  const low = Math.floor(Math.min(...certainty) * 10) / 10, high = Math.min(1, Math.ceil(Math.max(...certainty) * 10) / 10 + (Math.max(...certainty) - Math.min(...certainty) < 0.02 ? 0.1 : 0))
  const yOf = (value: number) => 56 - ((value - low) / Math.max(0.05, high - low)) * 50
  const line = (values: number[]) => values.map((value, index) => `${(index / 15) * 300},${yOf(value)}`).join(" ")
  return (
    <section aria-labelledby="effort-title" className="border bg-background">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b px-5 py-4">
        <h3 id="effort-title" className="display-sm text-[13px]">The effort of one thought</h3>
        <span className="label-mono text-muted-foreground">{NODE_LABEL[node]} // {MOMENT_LABEL[moment]} // step {step}</span>
      </div>
      <div className="grid lg:grid-cols-3">
        <div className="border-b p-5 lg:border-r">
          <div className="label-mono text-muted-foreground">How sure, after each step</div>
          <div className="mt-2 text-[34px] font-semibold leading-none tabular-nums [font-stretch:112%]">{pct(certainty[step - 1])}</div>
          <svg viewBox="0 0 300 64" className="mt-3 block h-auto w-full" aria-label={`Certainty from ${pct(certainty[0])} at step 1 to ${pct(certainty[15])} at step 16`}>
            <line x1="0" y1="6" x2="300" y2="6" stroke="#e6e6e6" />
            <line x1="0" y1="56" x2="300" y2="56" stroke="#d1d1d1" />
            <polyline points={line(certainty)} fill="none" stroke="#0a0a0a" strokeWidth="1.5" />
            <circle cx={((step - 1) / 15) * 300} cy={yOf(certainty[step - 1])} r="4" fill="#ff4500" />
          </svg>
          <div className="mt-1 flex justify-between"><span className="label-mono text-muted-foreground">Axis {pct(low)}–{pct(high)}</span><span className="label-mono text-muted-foreground">Step 1 → 16</span></div>
          <p className="mt-2 text-[13px] leading-5 text-muted-foreground">1 − normalised entropy of the assessment head. {flat ? "This thought hardly moves: the brain is sure from step 1 and nothing changes." : "It climbs as the 16 steps settle on a level."}</p>
        </div>
        <div className="border-b p-5 lg:border-r">
          <div className="label-mono text-muted-foreground">How much it still moves</div>
          <div className="mt-2 text-[34px] font-semibold leading-none tabular-nums [font-stretch:112%]">{rms[step - 1].toFixed(4)}</div>
          <svg viewBox="0 0 300 64" className="mt-3 block h-auto w-full" aria-label="Change of the 256 outputs at each step">
            {rms.map((value, index) => <rect key={index} x={index * 18.75 + 2} y={60 - (value / rmsMax) * 56} width="14" height={(value / rmsMax) * 56} fill={index === step - 1 ? "#ff4500" : "#0a0a0a"} />)}
          </svg>
          <p className="mt-2 text-[13px] leading-5 text-muted-foreground">RMS change of the 256 outputs at each step: how far the thought is still travelling.</p>
        </div>
        <div className="border-b p-5">
          <div className="label-mono text-muted-foreground">Time for one whole thought</div>
          <div className="mt-2 text-[34px] font-semibold leading-none tabular-nums [font-stretch:112%]">{latency.p50.toFixed(1)} ms</div>
          <dl className="mt-3 border-t">
            <div className="flex justify-between border-b py-1.5"><dt className="label-mono text-muted-foreground">Median (p50)</dt><dd className="font-mono text-[13px]">{latency.p50.toFixed(2)} ms</dd></div>
            <div className="flex justify-between border-b py-1.5"><dt className="label-mono text-muted-foreground">p95</dt><dd className="font-mono text-[13px]">{latency.p95.toFixed(2)} ms</dd></div>
            <div className="flex justify-between py-1.5"><dt className="label-mono text-muted-foreground">Thoughts measured</dt><dd className="font-mono text-[13px]">{latency.n}</dd></div>
          </dl>
          <p className="mt-2 text-[13px] leading-5 text-muted-foreground">Perception, 16 steps, heads and decision, measured during the run on one CPU thread. The step count never varies: adaptive thinking is off.</p>
        </div>
      </div>
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="p-5 lg:border-r">
          <div className="label-mono text-muted-foreground">Assessment head, read after step {step}</div>
          <ul className="mt-3 space-y-2">
            {levels.map((label, index) => (
              <li key={label} className="grid grid-cols-[150px_minmax(0,1fr)_52px] items-center gap-3">
                <span className="label-mono truncate" title={humanize(label)}>{humanize(label)}</span>
                <span className="h-3 bg-secondary"><span className="block h-full" style={{ width: `${probs[index] * 100}%`, background: LEVEL_COLOR[index] }} /></span>
                <span className="text-right font-mono text-[12px] tabular-nums">{probs[index].toFixed(2)}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[13px] leading-5 text-muted-foreground">The shipped brain only reads the head after step 16. Reading it earlier is a real "what if it had stopped here" view.</p>
        </div>
        <div className="border-t p-5 lg:border-t-0">
          <div className="label-mono text-muted-foreground">What the node did</div>
          <dl className="mt-2 border-t">
            <div className="flex justify-between gap-3 border-b py-2"><dt className="label-mono text-muted-foreground">Head after 16 steps</dt><dd className="text-right text-[14px] font-semibold">{humanize(levels[headTop])} · {final[headTop].toFixed(2)}</dd></div>
            <div className="flex justify-between gap-3 border-b py-2"><dt className="label-mono text-muted-foreground">Level it stated</dt><dd className="text-right text-[14px] font-semibold">{humanize(stated)}</dd></div>
            <div className="flex justify-between gap-3 border-b py-2"><dt className="label-mono text-muted-foreground">Diagnosis</dt><dd className="text-right text-[14px]">{humanize(thought.decided.diagnosis)}</dd></div>
            <div className="flex justify-between gap-3 border-b py-2"><dt className="label-mono text-muted-foreground">Message</dt><dd className="text-right text-[14px]">{humanize(thought.decided.communication)}</dd></div>
            <div className="flex justify-between gap-3 py-2"><dt className="label-mono text-muted-foreground">Protective action</dt><dd className="text-right text-[14px]">{humanize(thought.decided.action)}</dd></div>
          </dl>
          {levels[headTop] !== stated && (
            <p className="mt-3 border-l-[3px] border-l-signal bg-panel px-3 py-2 text-[13px] leading-5">
              The head already pointed to {humanize(levels[headTop])}, but the node still said {humanize(stated)}. The decision layer waits {brain ? `${brain.decision_layer.k_escalate} s in a row` : "a few seconds"} before escalating, so a flicker stays one event.
            </p>
          )}
          {isKnownFalseAlert(node, moment) && (
            <p className="mt-3 border-l-[3px] border-l-danger bg-panel px-3 py-2 text-[13px] leading-5">
              A false alert. It came before any fault, on a calm farm, and held until tick 224. It is the release's known open issue, shown as it happened.
            </p>
          )}
        </div>
      </div>
    </section>
  )
}

function SeenAndConcluded({ thought }: { thought: Thought }) {
  const zMax = Math.max(1, ...thought.unusual_inputs.map(([, z]) => Math.abs(z)))
  return (
    <section className="grid border-l border-t lg:grid-cols-2">
      <div className="border-b border-r p-5">
        <h3 className="display-sm text-[13px]">What it saw</h3>
        <p className="mt-2 text-[13px] leading-5 text-muted-foreground">The readings furthest from normal, as z-scores against the learned normal (clipped at ±8).</p>
        <ul className="mt-4">
          {thought.unusual_inputs.map(([name, z]) => (
            <li key={name} className="grid grid-cols-[minmax(0,1fr)_120px_48px] items-center gap-3 border-b py-1.5 last:border-0">
              <span className="truncate font-mono text-[12px]" title={name}>{name}</span>
              <span className="relative h-2.5 bg-secondary">
                <span aria-hidden className="absolute inset-y-0 left-1/2 w-px bg-foreground/40" />
                <span className={cn("absolute inset-y-0", z >= 0 ? "left-1/2 bg-signal" : "right-1/2 bg-foreground")} style={{ width: `${(Math.abs(z) / zMax) * 50}%` }} />
              </span>
              <span className="text-right font-mono text-[12px] tabular-nums">{z > 0 ? "+" : ""}{z.toFixed(2)}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="border-b border-r p-5">
        <h3 className="display-sm text-[13px]">What it concluded</h3>
        <div className="mt-4 grid gap-6 sm:grid-cols-2">
          {[["Most likely causes", thought.top_diagnoses], ["Where the cause is", thought.top_sources]].map(([title, rows]) => (
            <div key={title as string}>
              <div className="label-mono text-muted-foreground">{title as string}</div>
              <ul className="mt-2">
                {(rows as [string, number][]).map(([label, p]) => (
                  <li key={label} className="border-b py-2 last:border-0">
                    <div className="flex justify-between gap-3 text-[13px]"><span>{humanize(label)}</span><span className="font-mono tabular-nums">{p.toFixed(2)}</span></div>
                    <span className="mt-1 block h-1.5 bg-secondary"><span className="block h-full bg-foreground" style={{ width: `${p * 100}%` }} /></span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <p className="mt-4 text-[13px] leading-5 text-muted-foreground">Read from the five heads, which are linear readouts of the 528 synchronisation pairs of neurons 224–255, not of single neurons.</p>
      </div>
    </section>
  )
}

/* ------------------------------------------------------------------ history */

function HistoryExplorer({ thought, step, selected, onSelect, syncNeurons }: { thought: Thought; step: number; selected: number | null; onSelect: (neuron: number) => void; syncNeurons: Set<number> }) {
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<"post" | "pre">("post")
  const canvas = useRef<HTMLCanvasElement>(null)
  const matrix = kind === "post" ? thought.history_post : thought.history_pre
  useEffect(() => {
    const element = canvas.current
    if (!open || !element) return
    element.width = 32
    element.height = 256
    const context = element.getContext("2d")
    if (!context) return
    const maxAbs = percentile(matrix.flatMap((row) => row.map(Math.abs)), 0.95)
    matrix.forEach((row, neuron) => row.forEach((value, column) => { context.fillStyle = activityColor(value / maxAbs); context.fillRect(column, neuron, 1, 1) }))
  }, [open, matrix])
  const pickRow = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    onSelect(Math.min(255, Math.max(0, Math.floor(((event.clientY - box.top) / box.height) * 256))))
  }
  return (
    <section className="border bg-background">
      <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)} className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left hover:bg-panel">
        <span>
          <span className="display-sm block text-[13px]">The 32-step memory</span>
          <span className="mt-1 block text-[13px] leading-5 text-muted-foreground">Each neuron privately remembers its last 32 inputs: the previous second's 16 steps and this thought's 16.</span>
        </span>
        <span className="label-mono shrink-0">{open ? "Close −" : "Open +"}</span>
      </button>
      {open && (
        <div className="grid gap-6 border-t p-5 lg:grid-cols-[minmax(0,1fr)_280px]">
          <div className="min-w-0">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              {(["post", "pre"] as const).map((value) => (
                <button key={value} type="button" aria-pressed={kind === value} onClick={() => setKind(value)} className={cn("label-mono h-8 border px-3", kind === value ? "border-foreground bg-foreground text-background" : "border-input hover:border-foreground")}>
                  {value === "post" ? "Outputs (post)" : "Inputs (pre)"}
                </button>
              ))}
            </div>
            <div className="relative">
              <canvas ref={canvas} onPointerDown={pickRow} aria-label="256 neurons by 32 remembered moments. Click a row to inspect that neuron." className="block h-[512px] w-full cursor-crosshair border [image-rendering:pixelated]" />
              <div aria-hidden className="pointer-events-none absolute inset-y-0 left-1/2 border-l-2 border-dashed border-foreground" />
              <div aria-hidden className="pointer-events-none absolute inset-y-0 border-x-2 border-signal" style={{ left: `${((15 + step) / 32) * 100}%`, width: `${100 / 32}%` }} />
              <div aria-hidden className="pointer-events-none absolute inset-x-0 border-y border-foreground/60" style={{ top: `${(224 / 256) * 100}%`, height: `${(32 / 256) * 100}%` }} />
              {selected !== null && <div aria-hidden className="pointer-events-none absolute inset-x-0 h-[3px] bg-signal" style={{ top: `${(selected / 256) * 100}%` }} />}
            </div>
            <div className="mt-2 grid grid-cols-2"><span className="label-mono text-muted-foreground">← Previous second, steps 1–16</span><span className="label-mono text-right text-muted-foreground">This thought, steps 1–16 →</span></div>
          </div>
          <div className="text-[13px] leading-6 text-muted-foreground">
            <p>Rows are neurons 0 to 255, top to bottom. The framed band at the bottom is the 32 readout neurons ({Math.min(...syncNeurons)}–{Math.max(...syncNeurons)}).</p>
            <p className="mt-3">The orange column is the step you are on. The dashed line splits the window: with 16 steps a second, it slides by exactly 16 every thought.</p>
            <p className="mt-3">These numbers are not a picture of layers. They are one neuron's own short-term memory, which its private model reads to make its next output.</p>
          </div>
        </div>
      )}
    </section>
  )
}
