import { useCallback, useEffect, useLayoutEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

/** The first-visit tour: the page blurs, one part at a time is lifted out of the blur, and a card with an arrow says
 * what it is for. Seen once per browser (a skip counts); it never blocks anything. */

const SEEN_KEY = "jarvis.welcome-tour.v1"
const GAP = 8          // space between the lifted part and its ring
const CARD_W = 340

type Step = { target?: string; kicker: string; title: string; body: string }

const STEPS: Step[] = [
  {
    kicker: "Welcome",
    title: "This is JARVIS",
    body: "One AI that looks after every machine on a farm: two incubators, the barn's climate control and the animals' water and feed. The farm here is a simulation, so you can break things safely and watch JARVIS react.",
  },
  {
    target: "status",
    kicker: "01 // The live line",
    title: "The farm, right now",
    body: "Farm time, the speed of the simulation and whether every machine is talking. At 1× the farm runs in real time; faster speeds are for watching faults grow, and they pause the chat.",
  },
  {
    target: "nav-simulator",
    kicker: "02 // Simulator",
    title: "Break something",
    body: "The farm stays calm until you step in. Pick a fault, from a stuck heater to a barn fire, or silence a machine. Nobody tells JARVIS: it has to notice on its own.",
  },
  {
    target: "nav-chat",
    kicker: "03 // Chat",
    title: "Talk to it",
    body: "Ask by text or hold to talk. Chat about anything; ask what's wrong and it answers from live readings and the machine's manual, with numbers it checked.",
  },
  {
    target: "nav-jarvis",
    kicker: "04 // JARVIS",
    title: "Look inside its head",
    body: "Each machine has its own trained brain and memory. This page shows them thinking, and which language models do the talking.",
  },
  {
    target: "nav-events",
    kicker: "05 // Events & alarms",
    title: "What happened, and when",
    body: "Every alert and assessment, in order, so you can follow a fault from the first odd reading to JARVIS speaking up.",
  },
  {
    target: "voice",
    kicker: "06 // Voice",
    title: "Hands busy? Just talk",
    body: "This shows when JARVIS is listening or speaking. That's the tour. You can always find these pages in the top bar.",
  },
]

function seen(): boolean {
  try {
    return window.localStorage.getItem(SEEN_KEY) === "1"
  } catch {
    return false
  }
}

function markSeen() {
  try {
    window.localStorage.setItem(SEEN_KEY, "1")
  } catch {
    // private window: the tour may show again next time, which is harmless
  }
}

type Box = { top: number; left: number; width: number; height: number }

/** The visible box of a step's target, or null when it is hidden (the mobile menu, for example). */
function targetBox(target: string | undefined): Box | null {
  if (!target) return null
  const element = document.querySelector<HTMLElement>(`[data-tour="${target}"]`)
  if (!element) return null
  const rect = element.getBoundingClientRect()
  if (rect.width === 0 || rect.height === 0) return null
  return { top: rect.top - GAP, left: rect.left - GAP, width: rect.width + GAP * 2, height: rect.height + GAP * 2 }
}

export function WelcomeTour() {
  const [index, setIndex] = useState<number | null>(() => (seen() ? null : 0))
  const [box, setBox] = useState<Box | null>(null)
  const step = index === null ? null : STEPS[index]

  const close = useCallback(() => {
    markSeen()
    setIndex(null)
  }, [])

  useLayoutEffect(() => {
    if (!step) return
    const measure = () => setBox(targetBox(step.target))
    measure()
    window.addEventListener("resize", measure)
    window.addEventListener("scroll", measure, true)
    return () => {
      window.removeEventListener("resize", measure)
      window.removeEventListener("scroll", measure, true)
    }
  }, [step])

  useEffect(() => {
    if (index === null) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close()
      if (event.key === "ArrowRight") setIndex((i) => (i === null ? i : Math.min(i + 1, STEPS.length - 1)))
      if (event.key === "ArrowLeft") setIndex((i) => (i === null ? i : Math.max(i - 1, 0)))
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [index, close])

  if (index === null || !step) return null
  const last = index === STEPS.length - 1
  const vw = window.innerWidth
  const vh = window.innerHeight
  const cardW = Math.min(CARD_W, vw - 32)
  // the card sits under its target (or above it near the bottom), its arrow pointing at the target's middle
  const below = box ? box.top + box.height + 220 < vh : true
  const cardLeft = box ? Math.min(Math.max(box.left + box.width / 2 - cardW / 2, 16), vw - cardW - 16) : (vw - cardW) / 2
  const arrowLeft = box ? Math.min(Math.max(box.left + box.width / 2 - cardLeft, 20), cardW - 20) : 0
  const cardStyle = box
    ? below
      ? { left: cardLeft, top: box.top + box.height + 14, width: cardW }
      : { left: cardLeft, bottom: vh - box.top + 14, width: cardW }
    : { left: cardLeft, top: "50%", transform: "translateY(-50%)", width: cardW }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="tour-title" className="fixed inset-0 z-[100]">
      {/* four blurred panels around the target leave it sharp; with no target the whole page blurs */}
      {box ? (
        <>
          <div className="tour-veil" style={{ top: 0, left: 0, right: 0, height: Math.max(box.top, 0) }} />
          <div className="tour-veil" style={{ top: box.top + box.height, left: 0, right: 0, bottom: 0 }} />
          <div className="tour-veil" style={{ top: box.top, left: 0, width: Math.max(box.left, 0), height: box.height }} />
          <div className="tour-veil" style={{ top: box.top, left: box.left + box.width, right: 0, height: box.height }} />
          <div
            aria-hidden
            className="tour-ring pointer-events-none absolute border-2 border-signal"
            style={{ top: box.top, left: box.left, width: box.width, height: box.height }}
          />
        </>
      ) : (
        <div className="tour-veil inset-0" />
      )}
      <div key={index} className="tour-card absolute border bg-background p-5 shadow-lg" style={cardStyle}>
        {box && (
          <span
            aria-hidden
            className={cn("absolute size-3 rotate-45 border bg-background", below ? "-top-[7px] border-b-0 border-r-0" : "-bottom-[7px] border-l-0 border-t-0")}
            style={{ left: arrowLeft - 6 }}
          />
        )}
        <p className="font-mono text-[11px] uppercase tracking-[0.08em] text-signal-ink">{step.kicker}</p>
        <h2 id="tour-title" className="mt-2 text-lg font-semibold leading-tight">{step.title}</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{step.body}</p>
        <div className="mt-5 flex items-center gap-2">
          <span className="font-mono text-[11px] text-muted-foreground">{index + 1} / {STEPS.length}</span>
          <button type="button" onClick={close} className="ml-auto px-2 text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
            Skip tour
          </button>
          {index > 0 && (
            <Button variant="outline" size="sm" onClick={() => setIndex(index - 1)}>Back</Button>
          )}
          <Button size="sm" autoFocus onClick={() => (last ? close() : setIndex(index + 1))}>
            {last ? "Start exploring" : "Next"}
          </Button>
        </div>
      </div>
    </div>
  )
}
