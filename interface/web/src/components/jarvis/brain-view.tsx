import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { Brain, Info, LocateFixed, Pause, Play, Turtle } from "lucide-react"

import { cn } from "@/lib/utils"
import {
  ASSESSMENTS,
  ASSESSMENT_LABELS,
  DEVICE_IDS,
  deviceName,
  diagnosisLabel,
  formatSimTime,
  type DeviceId,
  type FarmState,
} from "@/services/matrix"

import { BrainEngine, ROLE_ASSOCIATION, ROLE_INPUT, TICKS_PER_THOUGHT, hashSeed } from "./brain-engine"
import { brainProfile, readingChannels } from "./brain-profile"
import {
  BrainRenderer,
  CONCLUSION_RGB,
  HOT_READING_RGB,
  READING_RGB,
  drawCertainty,
  drawTraces,
  rgba,
  type Point,
} from "./brain-renderer"

const ASSOCIATION_RGB = [125, 211, 252] as const

function usePrefersReducedMotion() {
  const query = "(prefers-reduced-motion: reduce)"
  const [reduced, setReduced] = useState(() => typeof window !== "undefined" && Boolean(window.matchMedia?.(query).matches))
  useEffect(() => {
    const media = window.matchMedia?.(query)
    if (!media) return
    const update = () => setReduced(media.matches)
    media.addEventListener("change", update)
    return () => media.removeEventListener("change", update)
  }, [])
  return reduced
}

type Lane = { label: string; detail: string; color: string }

/**
 * The brain view: one node's readings on the left, its neurons in the middle and the five
 * conclusions on the right, with neuron traces and certainty underneath. The animation is an
 * illustration driven by the demo state (see brain-engine.ts).
 */
export function BrainView({
  state,
  nodeId,
  following,
  onSelectNode,
  onFollow,
  onDemo,
}: {
  state: FarmState
  nodeId: DeviceId
  /** True while the view follows the most serious node by itself. */
  following: boolean
  onSelectNode: (id: DeviceId) => void
  onFollow: () => void
  onDemo: () => void
}) {
  const demo = state.source === "demo"
  const node = state.jarvis.nodes[nodeId]
  const channels = useMemo(() => readingChannels(state, nodeId), [state, nodeId])
  const profile = useMemo(() => brainProfile(state, nodeId, channels), [state, nodeId, channels])
  const salienceKey = profile.salience.map((value) => value.toFixed(2)).join(",")

  const reducedMotion = usePrefersReducedMotion()
  const [playing, setPlaying] = useState(true)
  const [slow, setSlow] = useState(false)
  const [forceMotion, setForceMotion] = useState(false)
  const [lanes, setLanes] = useState<Lane[]>([])
  const animate = playing && (!reducedMotion || forceMotion)

  const stageRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const fieldRef = useRef<HTMLDivElement>(null)
  const tracesRef = useRef<HTMLCanvasElement>(null)
  const certaintyCanvasRef = useRef<HTMLCanvasElement>(null)
  const readoutRef = useRef<HTMLDivElement>(null)
  const certaintyRef = useRef<HTMLSpanElement>(null)
  const decisionRef = useRef<HTMLParagraphElement>(null)
  const inputRows = useRef<(HTMLLIElement | null)[]>([])
  const inputBars = useRef<(HTMLSpanElement | null)[]>([])
  const outputRows = useRef<(HTMLLIElement | null)[]>([])
  const outputBars = useRef<(HTMLSpanElement | null)[]>([])
  const outputValues = useRef<(HTMLSpanElement | null)[]>([])

  const engines = useRef(new Map<DeviceId, BrainEngine>())
  const engineRef = useRef<BrainEngine | null>(null)
  const rendererRef = useRef<BrainRenderer | null>(null)
  const salienceRef = useRef(profile.salience)
  const targetRef = useRef(profile.target)
  const animateRef = useRef(animate)
  const speedRef = useRef(1)
  const visibleRef = useRef(true)
  const offlineRef = useRef(profile.offline)
  const dirtyRef = useRef(true)
  const channelCount = useRef(channels.length)
  channelCount.current = channels.length
  animateRef.current = animate
  speedRef.current = slow ? 0.3 : 1

  // Measure where the rows sit so the wiring meets them, and size the canvases.
  const measureRef = useRef(() => {})
  measureRef.current = () => {
    const stage = stageRef.current
    const field = fieldRef.current
    const renderer = rendererRef.current
    if (!stage || !field || !renderer) return
    const s = stage.getBoundingClientRect()
    const f = field.getBoundingClientRect()
    const box = { x: f.left - s.left + 12, y: f.top - s.top + 30, w: Math.max(60, f.width - 24), h: Math.max(60, f.height - 44) }
    const rows = inputRows.current.slice(0, channelCount.current)
    const outs = outputRows.current.slice(0, ASSESSMENTS.length)
    const firstRow = rows[0]?.getBoundingClientRect()
    const sideBySide = Boolean(firstRow && firstRow.right <= f.left + 2)
    const spread = (count: number, x: number) => Array.from({ length: count }, (_, k): Point => ({ x, y: box.y + ((k + 0.5) / count) * box.h }))
    const inputAnchors = sideBySide
      ? rows.map((row): Point => {
          const r = row!.getBoundingClientRect()
          return { x: r.right - s.left, y: r.top + r.height / 2 - s.top }
        })
      : spread(rows.length, box.x - 6)
    const outputAnchors = sideBySide
      ? outs.map((row): Point => {
          const r = row!.getBoundingClientRect()
          return { x: r.left - s.left, y: r.top + r.height / 2 - s.top }
        })
      : spread(outs.length, box.x + box.w + 6)
    const norm = (y: number) => Math.min(0.97, Math.max(0.03, (y - box.y) / box.h))
    const anchors = { inputs: inputAnchors.map((a) => norm(a.y)), outputs: outputAnchors.map((a) => norm(a.y)) }
    engines.current.forEach((engine) => engine.layout(anchors))
    const dpr = window.devicePixelRatio || 1
    renderer.resize({ width: s.width, height: s.height, field: box, inputAnchors, outputAnchors }, dpr)
    for (const canvas of [tracesRef.current, certaintyCanvasRef.current]) {
      if (!canvas) continue
      canvas.width = Math.max(1, Math.round(canvas.clientWidth * Math.min(2, dpr)))
      canvas.height = Math.max(1, Math.round(canvas.clientHeight * Math.min(2, dpr)))
    }
    dirtyRef.current = true
  }

  const laneInfo = (engine: BrainEngine): Lane[] =>
    engine.lanes.map((lane) => {
      if (lane.role === ROLE_INPUT) {
        const channel = channels[lane.group]
        return { label: channel?.label ?? "Reading", detail: `n${lane.neuron}`, color: rgba(channel?.hot ? HOT_READING_RGB : READING_RGB, 1) }
      }
      if (lane.role === ROLE_ASSOCIATION) return { label: "Association", detail: `n${lane.neuron}`, color: rgba(ASSOCIATION_RGB, 1) }
      const assessment = ASSESSMENTS[lane.group]
      return { label: ASSESSMENT_LABELS[assessment], detail: `n${lane.neuron}`, color: rgba(CONCLUSION_RGB[assessment], 1) }
    })

  // Each node keeps its own brain (seeded by its id), so switching back finds it mid-thought.
  useLayoutEffect(() => {
    let engine = engines.current.get(nodeId)
    if (!engine) {
      // A node's brain has been running all along: open it mid-stream, not from a blank state.
      engine = new BrainEngine(hashSeed(nodeId), channelCount.current)
      engine.reset(profile)
      engines.current.set(nodeId, engine)
    }
    engineRef.current = engine
    rendererRef.current?.invalidate()
    measureRef.current()
    // Only a new node needs a new brain; profile changes are handled below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId])

  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return
    engine.setProfile(profile)
    salienceRef.current = profile.salience
    targetRef.current = profile.target
    offlineRef.current = profile.offline
    if (!animateRef.current) engine.settle(3.2)
    setLanes(laneInfo(engine))
    measureRef.current()
    // profile is rebuilt every second; only these parts change what the brain does.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, profile.revision, profile.offline, profile.target, salienceKey])

  useEffect(() => {
    dirtyRef.current = true
  }, [animate])

  // The animation loop. It only draws while the view is on screen.
  useEffect(() => {
    const canvas = canvasRef.current
    const stage = stageRef.current
    if (!canvas || !stage) return
    rendererRef.current = new BrainRenderer(canvas)
    measureRef.current()
    let frame = 0
    let last = performance.now()
    let lastText = 0
    const update = (engine: BrainEngine, now: number, moving: boolean) => {
      for (let k = 0; k < engine.inputCount; k += 1) {
        const bar = inputBars.current[k]
        if (bar) bar.style.transform = `scaleX(${Math.min(1, engine.inputActivity[k] * 1.7).toFixed(3)})`
      }
      const offline = offlineRef.current
      for (let c = 0; c < ASSESSMENTS.length; c += 1) {
        const bar = outputBars.current[c]
        if (bar) bar.style.transform = `scaleX(${offline ? 0 : engine.probs[c].toFixed(3)})`
      }
      if (now - lastText < 120 && moving) return
      lastText = now
      for (let c = 0; c < ASSESSMENTS.length; c += 1) {
        const value = outputValues.current[c]
        if (value) value.textContent = offline ? "—" : `${Math.round(engine.probs[c] * 100)}%`
      }
      if (certaintyRef.current) certaintyRef.current.textContent = offline ? "—" : `${Math.round(engine.certainty * 100)}%`
      if (decisionRef.current) {
        decisionRef.current.textContent = offline
          ? "Waiting for a node"
          : engine.decidedAt !== null
            ? `Settled at tick ${engine.decidedAt + 1} of ${TICKS_PER_THOUGHT}`
            : `Still weighing it up · tick ${Math.min(TICKS_PER_THOUGHT, Math.floor(engine.tick) + 1)}`
      }
      if (readoutRef.current) {
        readoutRef.current.textContent = offline
          ? `${engine.size} neurons · idle`
          : `thought ${engine.thought} · tick ${String(Math.min(TICKS_PER_THOUGHT, Math.floor(engine.tick) + 1)).padStart(2, "0")}/${TICKS_PER_THOUGHT} · ${engine.size} neurons`
      }
    }
    const loop = (now: number) => {
      frame = requestAnimationFrame(loop)
      const dt = Math.min(0.05, Math.max(0, (now - last) / 1000))
      last = now
      const engine = engineRef.current
      const renderer = rendererRef.current
      if (!engine || !renderer || !visibleRef.current) return
      const moving = animateRef.current
      if (!moving && !dirtyRef.current) return
      dirtyRef.current = false
      const step = moving ? dt * speedRef.current : 0
      if (moving) engine.step(step)
      renderer.draw(engine, step, salienceRef.current, targetRef.current)
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      if (tracesRef.current) {
        const colors = engine.lanes.map((lane) => {
          if (lane.role === ROLE_INPUT) return salienceRef.current[lane.group] > 0.5 ? HOT_READING_RGB : READING_RGB
          if (lane.role === ROLE_ASSOCIATION) return ASSOCIATION_RGB
          return CONCLUSION_RGB[ASSESSMENTS[lane.group]]
        })
        drawTraces(tracesRef.current, engine, colors, dpr)
      }
      if (certaintyCanvasRef.current) drawCertainty(certaintyCanvasRef.current, engine, CONCLUSION_RGB[targetRef.current], dpr)
      update(engine, now, moving)
    }
    frame = requestAnimationFrame(loop)
    const resize = new ResizeObserver(() => measureRef.current())
    resize.observe(stage)
    if (tracesRef.current) resize.observe(tracesRef.current)
    const visibility = new IntersectionObserver(
      ([entry]) => {
        visibleRef.current = entry.isIntersecting
        dirtyRef.current = true
      },
      { rootMargin: "120px" },
    )
    visibility.observe(stage)
    return () => {
      cancelAnimationFrame(frame)
      resize.disconnect()
      visibility.disconnect()
    }
  }, [])

  const targetRgb = CONCLUSION_RGB[node.assessment]
  const tick = (value: number) => formatSimTime(value)

  return (
    <section aria-label="Brain view" className="overflow-hidden rounded-xl border border-scope-line bg-scope text-slate-200 shadow-sm">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-3 border-b border-white/10 px-4 py-3 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-md bg-cyan-300/10 text-cyan-300 ring-1 ring-cyan-300/25">
            <Brain className="size-[18px]" />
          </span>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-white">Brain view</h3>
            <p className="truncate text-[11px] text-slate-400">How a node&rsquo;s CTM reasons · illustration</p>
          </div>
        </div>
        <div className="order-3 flex w-full min-w-0 items-center gap-2 xl:order-none xl:ml-auto xl:w-auto">
          <div
            role="tablist"
            aria-label="JARVIS node"
            onKeyDown={(event) => {
              if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return
              event.preventDefault()
              const step = event.key === "ArrowRight" ? 1 : -1
              const next = DEVICE_IDS[(DEVICE_IDS.indexOf(nodeId) + step + DEVICE_IDS.length) % DEVICE_IDS.length]
              onSelectNode(next)
              event.currentTarget.querySelector<HTMLButtonElement>(`[data-node="${next}"]`)?.focus()
            }}
            className="flex min-w-0 gap-1 overflow-x-auto rounded-lg bg-white/[0.04] p-1 ring-1 ring-white/10"
          >
            {DEVICE_IDS.map((id) => {
              const item = state.jarvis.nodes[id]
              const selected = id === nodeId
              const color = rgba(CONCLUSION_RGB[item.assessment], 1)
              return (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  data-node={id}
                  aria-selected={selected}
                  aria-controls="brain-stage"
                  tabIndex={selected ? 0 : -1}
                  onClick={() => onSelectNode(id)}
                  className={cn(
                    "flex shrink-0 items-center gap-2 rounded-md px-2.5 py-1.5 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-cyan-300",
                    selected ? "bg-white/10 text-white" : "text-slate-400 hover:bg-white/5 hover:text-slate-200",
                  )}
                >
                  <span className="relative flex size-2 shrink-0">
                    {demo && item.assessment !== "NORMAL" && (
                      <span className="absolute inline-flex size-full animate-ping rounded-full opacity-60 motion-reduce:animate-none" style={{ background: color }} />
                    )}
                    <span className="relative inline-flex size-2 rounded-full" style={{ background: demo ? color : "#475569" }} />
                  </span>
                  <span className="leading-tight">
                    <span className="block text-xs font-medium">{deviceName(id)}</span>
                    <span className="block text-[10px] text-slate-400">{demo ? ASSESSMENT_LABELS[item.assessment] : "Offline"}</span>
                  </span>
                </button>
              )
            })}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-1.5 xl:ml-0">
          {following ? (
            <span className="inline-flex items-center gap-1.5 px-1 text-[11px] text-slate-400" title="The view switches to whichever node has the most serious conclusion">
              <LocateFixed className="size-3.5" />
              <span className="sr-only 2xl:not-sr-only">Following alerts</span>
            </span>
          ) : (
            <button
              type="button"
              onClick={onFollow}
              className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs text-slate-300 ring-1 ring-white/10 transition-colors hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300"
            >
              <LocateFixed className="size-3.5" />
              Follow alerts
            </button>
          )}
          <button
            type="button"
            aria-pressed={slow}
            onClick={() => setSlow((value) => !value)}
            title="Slow motion"
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs ring-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300",
              slow ? "bg-cyan-300/15 text-cyan-200 ring-cyan-300/40" : "text-slate-300 ring-white/10 hover:bg-white/5",
            )}
          >
            <Turtle className="size-3.5" />
            Slow
          </button>
          <button
            type="button"
            onClick={() => {
              if (reducedMotion && !forceMotion) {
                setForceMotion(true)
                setPlaying(true)
              } else setPlaying((value) => !value)
            }}
            aria-label={animate ? "Pause the brain view" : "Play the brain view"}
            className="grid size-8 place-items-center rounded-md text-slate-200 ring-1 ring-white/10 transition-colors hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300"
          >
            {animate ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
          </button>
        </div>
      </header>

      <div ref={stageRef} id="brain-stage" role="tabpanel" aria-label={`${deviceName(nodeId)}'s brain`} className="relative">
        <canvas ref={canvasRef} aria-hidden className="pointer-events-none absolute inset-0 size-full" />
        <div className="relative grid grid-cols-1 xl:min-h-[410px] xl:grid-cols-[minmax(0,214px)_minmax(0,1fr)_minmax(0,232px)]">
          <div className="px-3 pb-2 pt-3 xl:py-4 xl:pl-4 xl:pr-0">
            <div className="mb-1.5 px-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">What it reads</div>
            <ul className="grid grid-cols-2 gap-1 sm:grid-cols-4 xl:grid-cols-1">
              {channels.map((channel, k) => (
                <li
                  key={channel.key}
                  ref={(element) => {
                    inputRows.current[k] = element
                  }}
                  className={cn(
                    "relative overflow-hidden rounded-md px-2 py-1.5 ring-1",
                    channel.hot ? "bg-amber-300/[0.08] ring-amber-300/30" : "bg-scope-raised/80 ring-white/[0.07]",
                  )}
                >
                  <div className="truncate text-[11px] leading-4 text-slate-400">{channel.label}</div>
                  <div className={cn("truncate text-xs leading-5", channel.mono && "font-mono", channel.hot ? "text-amber-200" : "text-slate-100")} title={channel.value}>
                    {channel.value}
                  </div>
                  <span
                    ref={(element) => {
                      inputBars.current[k] = element
                    }}
                    aria-hidden
                    className="absolute inset-x-0 bottom-0 h-0.5 origin-left"
                    style={{ background: rgba(channel.hot ? HOT_READING_RGB : READING_RGB, 0.85), transform: "scaleX(0)" }}
                  />
                </li>
              ))}
            </ul>
          </div>

          <div ref={fieldRef} className="relative h-[270px] sm:h-[320px] xl:h-auto">
            <div ref={readoutRef} aria-hidden className="pointer-events-none absolute inset-x-0 top-3 truncate whitespace-nowrap px-3 text-center font-mono text-[10px] uppercase tracking-[0.14em] text-slate-400" />
            {!demo && (
              <div className="absolute inset-0 grid place-items-center p-4">
                <div className="max-w-[260px] rounded-lg bg-scope-raised/95 p-4 text-center ring-1 ring-white/10">
                  <Brain className="mx-auto size-5 text-slate-400" />
                  <p className="mt-2 text-sm font-medium text-white">No node is connected</p>
                  <p className="mt-1 text-xs leading-5 text-slate-400">Use demo mode to watch each node&rsquo;s brain reason through the scripted scenarios.</p>
                  <button
                    type="button"
                    onClick={onDemo}
                    className="mt-3 rounded-md bg-cyan-300 px-3 py-1.5 text-xs font-semibold text-scope transition-colors hover:bg-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
                  >
                    Use demo mode
                  </button>
                </div>
              </div>
            )}
            {demo && reducedMotion && !forceMotion && (
              <button
                type="button"
                onClick={() => {
                  setForceMotion(true)
                  setPlaying(true)
                }}
                className="absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-md bg-scope-raised/95 px-3 py-1.5 text-xs text-slate-200 ring-1 ring-white/15 hover:bg-scope-line"
              >
                <Play className="size-3.5" />
                Motion is reduced · play the animation
              </button>
            )}
          </div>

          <div className="flex flex-col px-3 pb-3 pt-2 xl:py-4 xl:pl-0 xl:pr-4">
            <div className="mb-1.5 px-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">What it concludes</div>
            <div className="grid flex-1 gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,220px)] xl:flex xl:flex-col xl:justify-center xl:gap-0">
            <ul className="space-y-1">
              {ASSESSMENTS.map((assessment, c) => {
                const rgb = CONCLUSION_RGB[assessment]
                const chosen = demo && assessment === node.assessment
                return (
                  <li
                    key={assessment}
                    ref={(element) => {
                      outputRows.current[c] = element
                    }}
                    className={cn("rounded-md px-2 py-1.5", chosen ? "bg-white/[0.07]" : "bg-scope-raised/80")}
                    style={{ boxShadow: `inset 0 0 0 1px ${rgba(rgb, chosen ? 0.45 : 0.08)}` }}
                  >
                    <div className="flex items-center justify-between gap-2 text-xs">
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="size-2 shrink-0 rounded-full" style={{ background: rgba(rgb, 1) }} />
                        <span className={cn("truncate", chosen ? "font-semibold text-white" : "text-slate-300")}>{ASSESSMENT_LABELS[assessment]}</span>
                      </span>
                      <span
                        ref={(element) => {
                          outputValues.current[c] = element
                        }}
                        className={cn("font-mono text-[11px] tabular-nums", chosen ? "text-white" : "text-slate-400")}
                      >
                        —
                      </span>
                    </div>
                    <div className="mt-1 h-1 overflow-hidden rounded-full bg-white/[0.06]">
                      <span
                        ref={(element) => {
                          outputBars.current[c] = element
                        }}
                        className="block h-full origin-left rounded-full"
                        style={{ background: rgba(rgb, 0.95), transform: "scaleX(0)" }}
                      />
                    </div>
                  </li>
                )
              })}
            </ul>
            <div className="rounded-md bg-scope-raised/80 p-2.5 ring-1 ring-white/[0.07] sm:self-start xl:mt-3 xl:self-auto">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[11px] text-slate-400">Certainty</span>
                <span ref={certaintyRef} className="font-mono text-sm font-semibold tabular-nums text-white">
                  —
                </span>
              </div>
              <canvas ref={certaintyCanvasRef} aria-hidden className="mt-1.5 block h-10 w-full" />
              <p ref={decisionRef} className="mt-1 text-[11px] text-slate-400">
                —
              </p>
            </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid border-t border-white/10 lg:grid-cols-[minmax(0,1fr)_minmax(0,330px)]">
        <div className="flex flex-col px-4 py-3 sm:px-5">
          <div className="mb-2 flex items-center justify-between gap-3 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">
            <span>Neuron traces</span>
            <span className="truncate font-normal normal-case tracking-normal">activation over the last 4 seconds</span>
          </div>
          <div className="grid min-h-[150px] flex-1 grid-cols-[minmax(0,140px)_minmax(0,1fr)] gap-3">
            <ul className="grid grid-rows-6 text-[11px]">
              {lanes.map((lane, index) => (
                <li key={index} className="flex min-w-0 items-center gap-1.5">
                  <span className="size-1.5 shrink-0 rounded-full" style={{ background: lane.color }} />
                  <span className="truncate text-slate-300">{lane.label}</span>
                  <span className="ml-auto shrink-0 font-mono text-[10px] text-slate-400">{lane.detail}</span>
                </li>
              ))}
            </ul>
            <div className="relative min-w-0">
              <canvas ref={tracesRef} aria-hidden className="absolute inset-0 block size-full" />
            </div>
          </div>
        </div>
        <div className="border-t border-white/10 px-4 py-3 sm:px-5 lg:border-l lg:border-t-0">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">What {deviceName(nodeId)} concluded</div>
          {demo ? (
            <>
              <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="size-2 rounded-full" style={{ background: rgba(targetRgb, 1) }} />
                <span className="text-sm font-semibold text-white">{ASSESSMENT_LABELS[node.assessment]}</span>
                {node.diagnosis !== "NONE" && <span className="text-xs text-slate-400">· {diagnosisLabel(node.diagnosis)}</span>}
                <span className="ml-auto font-mono text-[11px] text-slate-400">since {tick(node.sinceS)}</span>
              </div>
              <p className="mt-1.5 text-xs leading-5 text-slate-300">{node.reason}</p>
              <p className="mt-1.5 text-[11px] text-slate-400">
                Protective action: <span className="text-slate-200">{node.action ? `${node.action} · gate ${node.gate ?? "pending"}` : "none"}</span>
              </p>
              {node.ownerMessage && (
                <div className="mt-2 rounded-md bg-orange-400/10 p-2 ring-1 ring-orange-300/25">
                  <div className="text-[11px] font-medium text-orange-300">Told the owner</div>
                  <p className="mt-0.5 line-clamp-3 text-[11px] leading-4 text-slate-200">{node.ownerMessage}</p>
                </div>
              )}
            </>
          ) : (
            <p className="mt-2 text-xs leading-5 text-slate-400">No node output while disconnected.</p>
          )}
        </div>
      </div>

      <p className="flex gap-2 border-t border-white/10 px-4 py-2.5 text-[11px] leading-4 text-slate-400 sm:px-5">
        <Info className="mt-px size-3.5 shrink-0" />
        <span>
          Illustration. The demo script picks the conclusion; this view animates how the node&rsquo;s CTM would get there: neurons fire over
          internal ticks, and the answer is read from which ones fire in sync. It will draw the real neuron traces once the nodes are
          connected.
        </span>
      </p>
      <p className="sr-only" aria-live="polite">
        {demo ? `${deviceName(nodeId)}: ${ASSESSMENT_LABELS[node.assessment]}. ${node.reason}` : "No JARVIS node is connected."}
      </p>
    </section>
  )
}
