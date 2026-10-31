import { useEffect, useMemo, useRef } from "react"

import { useReducedMotion } from "@/hooks/use-reduced-motion"
import { cn } from "@/lib/utils"

import { cloudPath } from "./jarvis-cloud"

export type Mood = "idle" | "listening" | "thinking" | "speaking"

const CAPTION: Record<Mood, string> = {
  idle: "JARVIS is on",
  listening: "Listening…",
  thinking: "Thinking: reading the farm's data and checking each sentence",
  speaking: "Speaking",
}
const EYE = { y: 168, left: 258, right: 322, w: 24, h: 42 }

/**
 * A small JARVIS cloud that floats in the corner while JARVIS is on. It glances around when idle, perks up and
 * watches the visitor while listening, looks up with a slow ellipsis while thinking, and moves its mouth while
 * speaking, so the wait for a spoken answer (several seconds on this hardware) is visibly JARVIS at work.
 * Under reduced motion it holds still and only the caption changes.
 */
export function FloatingJarvis({ mood, onOpen }: { mood: Mood; onOpen: () => void }) {
  const reduced = useReducedMotion()
  const path = useMemo(cloudPath, [])
  const eyes = useRef<SVGGElement>(null)
  const lids = useRef<(SVGGElement | null)[]>([])
  const mouth = useRef<SVGEllipseElement>(null)
  const moodRef = useRef(mood)
  moodRef.current = mood

  useEffect(() => {
    if (reduced) return
    let frame = 0, last = performance.now()
    const gaze = { x: 0, y: 0 }, target = { x: 0, y: 0 }
    let glanceUntil = 0, nextGlance = last + 1200, blinkStart = -1, nextBlink = last + 1500
    const loop = (now: number) => {
      const delta = Math.min(0.05, (now - last) / 1000)
      last = now
      const current = moodRef.current
      if (current === "thinking") {
        target.x = Math.sin(now / 900) * 0.6                       // reading left and right, eyes up
        target.y = -0.7
      } else if (current === "listening") {
        target.x = Math.sin(now / 1400) * 0.15                     // attentive: looking at the visitor
        target.y = 0.15
      } else if (now > nextGlance && now > glanceUntil) {
        target.x = (Math.random() * 2 - 1) * 0.9                   // looking around
        target.y = (Math.random() * 2 - 1) * 0.5
        glanceUntil = now + 600 + Math.random() * 900
        nextGlance = glanceUntil + 1400 + Math.random() * 2600
      } else if (now > glanceUntil) {
        target.x = 0
        target.y = 0
      }
      const ease = 1 - Math.exp(-delta * 6)
      gaze.x += (target.x - gaze.x) * ease
      gaze.y += (target.y - gaze.y) * ease
      eyes.current?.setAttribute("transform", `translate(${(gaze.x * 13).toFixed(2)} ${(gaze.y * 9).toFixed(2)})`)
      if (blinkStart < 0 && now > nextBlink && current !== "listening") blinkStart = now
      let open = 1
      if (blinkStart >= 0) {
        const t = now - blinkStart
        open = t < 70 ? 1 - t / 70 : t < 180 ? (t - 70) / 110 : 1
        if (t >= 180) { blinkStart = -1; nextBlink = now + 2200 + Math.random() * 3000 }
      }
      const wide = current === "listening" ? 1.12 : 1
      lids.current.forEach((lid, index) => {
        const cx = index === 0 ? EYE.left : EYE.right
        lid?.setAttribute("transform", `translate(${cx} ${EYE.y}) scale(${wide} ${(Math.max(0.08, open) * wide).toFixed(3)}) translate(${-cx} ${-EYE.y})`)
      })
      // Speaking: the mouth opens and closes in an uneven, speech-like rhythm.
      const talk = current === "speaking" ? 0.35 + 0.65 * Math.abs(Math.sin(now / 95) * Math.sin(now / 230)) : 0
      mouth.current?.setAttribute("ry", (2 + talk * 11).toFixed(2))
      mouth.current?.setAttribute("opacity", current === "speaking" ? "1" : "0")
      frame = requestAnimationFrame(loop)
    }
    frame = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(frame)
  }, [reduced])

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`${CAPTION[mood]}. Open the chat.`}
      className="floating-jarvis group fixed bottom-4 left-4 z-40 flex items-end gap-2 text-left outline-none"
    >
      <span className={cn("relative block w-[96px] xl:w-[150px]", !reduced && "cloud-float")} data-mood={mood}>
        <svg viewBox="40 20 460 280" className="block h-auto w-full drop-shadow-[0_6px_10px_rgba(0,0,0,0.12)]" role="presentation">
          {mood === "listening" && !reduced && [0, 1, 2].map((ring) => (
            <circle key={ring} cx="290" cy="160" r="150" fill="none" stroke="var(--signal)" strokeWidth="3"
              className="fj-ring" style={{ animationDelay: `${ring * 0.45}s` }} />
          ))}
          {mood === "speaking" && !reduced && [0, 1].map((ring) => (
            <circle key={ring} cx="290" cy="160" r="150" fill="none" stroke="#0a0a0a" strokeWidth="2"
              className="fj-wave" style={{ animationDelay: `${ring * 0.5}s` }} />
          ))}
          <path d={path} fill="#ffffff" />
          <path d={path} fill="none" stroke={mood === "listening" ? "var(--signal)" : "#0a0a0a"} strokeWidth={mood === "idle" ? 4 : 6}
            className="transition-[stroke] duration-200 group-focus-visible:stroke-[var(--signal)]" />
          <g ref={eyes}>
            {[EYE.left, EYE.right].map((x, index) => (
              <g key={x} ref={(node) => { lids.current[index] = node }}>
                <rect x={x - EYE.w / 2} y={EYE.y - EYE.h / 2} width={EYE.w} height={EYE.h} rx={EYE.w / 2} fill="#0a0a0a" />
                <circle cx={x - 4} cy={EYE.y - 9} r="3.6" fill="#ffffff" />
              </g>
            ))}
          </g>
          <ellipse ref={mouth} cx="290" cy="212" rx="16" ry="2" fill="#0a0a0a" opacity="0" />
          {mood === "thinking" && [0, 1, 2].map((dot) => (
            <circle key={dot} cx={262 + dot * 28} cy="220" r="7" fill="var(--signal)" className={reduced ? undefined : "fj-dot"}
              style={{ animationDelay: `${dot * 0.18}s` }} />
          ))}
        </svg>
      </span>
      <span className="label-mono mb-3 hidden max-w-[220px] border bg-background px-2 py-1 text-[11px] leading-4 shadow-sm xl:block" aria-live="polite">
        {CAPTION[mood]}
      </span>
    </button>
  )
}
