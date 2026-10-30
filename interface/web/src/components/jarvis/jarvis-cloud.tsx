import { useEffect, useMemo, useRef, useState } from "react"

import { useReducedMotion } from "@/hooks/use-reduced-motion"

/**
 * JARVIS's face: a soft cloud with two eyes, drawn like a technical illustration so it sits on the grid.
 * The eyes look forward, glance around now and then, blink, and follow the pointer while it moves.
 * Under reduced motion nothing moves on its own: no float, no glances, no blinks.
 */

type Circle = { x: number; y: number; r: number }
const BASE_Y = 238
/** The silhouette, left to right. The outline is the outer arcs of these circles, closed by a flat base. */
const BUMPS: Circle[] = [
  { x: 128, y: 186, r: 58 },
  { x: 196, y: 128, r: 76 },
  { x: 300, y: 104, r: 92 },
  { x: 394, y: 150, r: 74 },
  { x: 446, y: 200, r: 46 },
]

function upperIntersection(a: Circle, b: Circle) {
  const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy)
  const along = (a.r * a.r - b.r * b.r + d * d) / (2 * d), h = Math.sqrt(Math.max(0, a.r * a.r - along * along))
  const mx = a.x + (dx * along) / d, my = a.y + (dy * along) / d
  const p1 = { x: mx + (dy * h) / d, y: my - (dx * h) / d }, p2 = { x: mx - (dy * h) / d, y: my + (dx * h) / d }
  return p1.y < p2.y ? p1 : p2
}
function arc(circle: Circle, from: { x: number; y: number }, to: { x: number; y: number }) {
  const start = Math.atan2(from.y - circle.y, from.x - circle.x), end = Math.atan2(to.y - circle.y, to.x - circle.x)
  const span = (end - start + Math.PI * 4) % (Math.PI * 2)
  return `A ${circle.r} ${circle.r} 0 ${span > Math.PI ? 1 : 0} 1 ${to.x.toFixed(2)} ${to.y.toFixed(2)}`
}
function cloudPath() {
  const first = BUMPS[0], last = BUMPS[BUMPS.length - 1]
  const startX = first.x - Math.sqrt(first.r ** 2 - (BASE_Y - first.y) ** 2), endX = last.x + Math.sqrt(last.r ** 2 - (BASE_Y - last.y) ** 2)
  let point = { x: startX, y: BASE_Y }
  let d = `M ${point.x.toFixed(2)} ${point.y.toFixed(2)}`
  BUMPS.forEach((circle, index) => {
    const next = index < BUMPS.length - 1 ? upperIntersection(circle, BUMPS[index + 1]) : { x: endX, y: BASE_Y }
    d += ` ${arc(circle, point, next)}`
    point = next
  })
  return `${d} Z`
}

const EYE = { y: 168, left: 258, right: 322, w: 24, h: 42 }
/** The drawing area, with room around the cloud for the callouts. */
const VIEW = { x: -40, y: -50, w: 640, h: 350 }

export type CloudTarget = "ctm" | "cmm" | "voice"

export function JarvisCloud({ onOpen, attention }: { onOpen: (target: CloudTarget) => void; attention?: "down" | null }) {
  const reduced = useReducedMotion()
  const path = useMemo(cloudPath, [])
  const eyes = useRef<SVGGElement>(null)
  const lids = useRef<(SVGGElement | null)[]>([])
  const stage = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState(false)

  useEffect(() => {
    if (reduced) {
      eyes.current?.setAttribute("transform", "translate(0 0)")
      lids.current.forEach((lid) => lid?.setAttribute("transform", ""))
      return
    }
    let frame = 0, last = performance.now()
    const gaze = { x: 0, y: 0 }, target = { x: 0, y: 0 }
    let pointerAt = -Infinity, glanceUntil = 0, nextGlance = last + 2500 + Math.random() * 3000
    let blinkStart = -1, nextBlink = last + 1800 + Math.random() * 2500, doubleBlink = false
    const onPointer = (event: PointerEvent) => {
      const box = stage.current?.getBoundingClientRect()
      if (!box) return
      // Eye centre in page space, then a direction that saturates about 400 px away.
      const cx = box.left + box.width * ((290 - VIEW.x) / VIEW.w), cy = box.top + box.height * ((EYE.y - VIEW.y) / VIEW.h)
      const dx = event.clientX - cx, dy = event.clientY - cy, d = Math.hypot(dx, dy) || 1, reach = Math.min(1, d / 400)
      target.x = (dx / d) * reach
      target.y = (dy / d) * reach * 0.8
      pointerAt = performance.now()
    }
    window.addEventListener("pointermove", onPointer, { passive: true })
    const loop = (now: number) => {
      const delta = Math.min(0.05, (now - last) / 1000)
      last = now
      const pointerActive = now - pointerAt < 2200
      if (!pointerActive) {
        if (attention === "down") { target.x = 0; target.y = 0.85 }
        else if (now > nextGlance && now > glanceUntil) {
          // A short glance somewhere, held for a moment, then back to looking forward.
          target.x = (Math.random() * 2 - 1) * 0.85
          target.y = (Math.random() * 2 - 1) * 0.5
          glanceUntil = now + 500 + Math.random() * 900
          nextGlance = glanceUntil + 2600 + Math.random() * 4200
          if (Math.random() < 0.35) nextBlink = Math.min(nextBlink, now + 80)
        } else if (now > glanceUntil) { target.x = 0; target.y = 0 }
      }
      const ease = 1 - Math.exp(-delta * (pointerActive ? 9 : 6))
      gaze.x += (target.x - gaze.x) * ease
      gaze.y += (target.y - gaze.y) * ease
      eyes.current?.setAttribute("transform", `translate(${(gaze.x * 13).toFixed(2)} ${(gaze.y * 9).toFixed(2)})`)
      // Blink: 70 ms down, 110 ms up; sometimes twice.
      if (blinkStart < 0 && now > nextBlink) { blinkStart = now; doubleBlink = Math.random() < 0.18 }
      let open = 1
      if (blinkStart >= 0) {
        const t = now - blinkStart
        open = t < 70 ? 1 - t / 70 : t < 180 ? (t - 70) / 110 : 1
        if (t >= 180) {
          blinkStart = doubleBlink ? now + 90 : -1
          if (doubleBlink) doubleBlink = false
          else nextBlink = now + 2600 + Math.random() * 3600
        }
      }
      const scaleY = Math.max(0.08, open)
      lids.current.forEach((lid, index) => {
        if (!lid) return
        const cx = index === 0 ? EYE.left : EYE.right
        // The eye on the far side of the gaze narrows a little, which reads as turning.
        const turn = 1 - Math.max(0, (index === 0 ? gaze.x : -gaze.x)) * 0.12
        lid.setAttribute("transform", `translate(${cx} ${EYE.y}) scale(${turn.toFixed(3)} ${scaleY.toFixed(3)}) translate(${-cx} ${-EYE.y})`)
      })
      frame = requestAnimationFrame(loop)
    }
    frame = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener("pointermove", onPointer)
    }
  }, [reduced, attention])

  /** Leader-line callouts in the reference's annotation style; each one opens its component. */
  const callouts: { id: CloudTarget; label: string; meta: string; line: [number, number][]; at: [number, number] }[] = [
    { id: "ctm", label: "CTM", meta: "Thinks", line: [[300, 14], [300, -28], [334, -28]], at: [340, -28] },
    { id: "cmm", label: "CMM", meta: "Remembers", line: [[486, 196], [500, 196], [500, 120], [504, 120]], at: [508, 120] },
    { id: "voice", label: "Voice box", meta: "Planned", line: [[76, 214], [20, 214], [20, 262], [26, 262]], at: [30, 262] },
  ]

  return (
    <div ref={stage} className="relative mx-auto w-full max-w-[720px]">
      <svg viewBox={`${VIEW.x} ${VIEW.y} ${VIEW.w} ${VIEW.h}`} className="block h-auto w-full" role="presentation">
        <defs>
          <linearGradient id="cloud-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#ffffff" />
            <stop offset="0.62" stopColor="#f7f7f7" />
            <stop offset="1" stopColor="#e3e3e3" />
          </linearGradient>
          <radialGradient id="cloud-light" cx="0.42" cy="0.18" r="0.55">
            <stop offset="0" stopColor="#ffffff" stopOpacity="0.95" />
            <stop offset="1" stopColor="#ffffff" stopOpacity="0" />
          </radialGradient>
          <pattern id="cloud-hatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(-40)">
            <line x1="0" y1="0" x2="0" y2="7" stroke="#0a0a0a" strokeWidth="0.7" />
          </pattern>
          <linearGradient id="hatch-fade" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0.55" stopColor="#fff" stopOpacity="0" />
            <stop offset="1" stopColor="#fff" stopOpacity="0.55" />
          </linearGradient>
          <mask id="hatch-mask"><rect x="40" y="0" width="460" height="250" fill="url(#hatch-fade)" /></mask>
          <clipPath id="cloud-clip"><path d={path} /></clipPath>
        </defs>

        {/* Ground shadow, hatched like the reference drawings; it tightens as the cloud rises. */}
        <g className="cloud-shadow">
          <ellipse cx="290" cy="286" rx="168" ry="9" fill="url(#cloud-hatch)" opacity="0.35" />
        </g>

        <g className="cloud-float">
          <g
            role="button"
            tabIndex={0}
            aria-label="Open JARVIS's architecture"
            onClick={() => onOpen("ctm")}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault()
                onOpen("ctm")
              }
            }}
            onPointerEnter={() => setHover(true)}
            onPointerLeave={() => setHover(false)}
            className="cloud-hit cursor-pointer outline-none"
          >
            <path d={path} fill="url(#cloud-fill)" />
            <path d={path} fill="url(#cloud-light)" />
            <g clipPath="url(#cloud-clip)">
              <rect x="40" y="0" width="460" height="250" fill="url(#cloud-hatch)" mask="url(#hatch-mask)" />
            </g>
            <path d={path} fill="none" stroke="#0a0a0a" strokeWidth="1.5" className="cloud-outline" />
            <g ref={eyes}>
              {[EYE.left, EYE.right].map((x, index) => (
                <g key={x} ref={(node) => { lids.current[index] = node }}>
                  <rect x={x - EYE.w / 2} y={EYE.y - EYE.h / 2} width={EYE.w} height={EYE.h} rx={EYE.w / 2} fill="#0a0a0a" style={{ transform: hover ? "scale(1.06)" : "none", transformBox: "fill-box", transformOrigin: "center", transition: "transform 160ms cubic-bezier(0.2,0,0,1)" }} />
                  <circle cx={x - 4} cy={EYE.y - 9} r="3.6" fill="#ffffff" />
                </g>
              ))}
            </g>
          </g>
        </g>

        {callouts.map((callout) => (
          <g key={callout.id} aria-hidden>
            <polyline points={callout.line.map(([x, y]) => `${x},${y}`).join(" ")} fill="none" stroke="#0a0a0a" strokeWidth="0.8" />
            <rect
              x={callout.line[0][0] - 4}
              y={callout.line[0][1] - 4}
              width="8"
              height="8"
              fill={callout.id === "voice" ? "#f2f2f2" : "#ff4500"}
              stroke={callout.id === "voice" ? "#0a0a0a" : "none"}
              strokeDasharray={callout.id === "voice" ? "2 1.5" : undefined}
            />
          </g>
        ))}
      </svg>
      {callouts.map((callout) => (
        <button
          key={callout.id}
          type="button"
          onClick={() => onOpen(callout.id)}
          className="absolute -translate-y-1/2 bg-background px-1.5 py-1 text-left transition-colors duration-150 hover:bg-foreground hover:text-background focus-visible:bg-foreground focus-visible:text-background"
          style={{ left: `${((callout.at[0] - VIEW.x) / VIEW.w) * 100}%`, top: `${((callout.at[1] - VIEW.y) / VIEW.h) * 100}%` }}
        >
          <span className="label-mono block font-semibold max-sm:text-[10px]">{callout.label}</span>
          <span className="label-mono block opacity-70 max-sm:text-[10px]">// {callout.meta}</span>
        </button>
      ))}
    </div>
  )
}
