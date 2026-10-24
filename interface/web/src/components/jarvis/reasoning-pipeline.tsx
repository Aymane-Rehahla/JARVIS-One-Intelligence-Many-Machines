import type { ComponentType } from "react"
import { Activity, Binary, Brain, Database, MessageSquareText, Radio, ShieldCheck } from "lucide-react"

import { Card } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { AssessmentBadge } from "@/components/farm-ui"
import { cn } from "@/lib/utils"
import { ASSESSMENTS } from "@/services/matrix"

type Stage = {
  label: string
  /** What the stage runs on in demo mode; null = not connected. */
  demoStatus: "Demo data" | "Demo script" | null
  detail: string
  icon: ComponentType<{ className?: string }>
}

/** What every JARVIS node does with each reading, in order (farm_poc/nodes). */
const STAGES: Stage[] = [
  { label: "Telemetry & device codes", demoStatus: "Demo data", detail: "Readings and device codes from its own machine, every second.", icon: Activity },
  { label: "Peer link", demoStatus: "Demo data", detail: "Broadcasts from the other three nodes over a ZigBee-like link.", icon: Radio },
  { label: "Encoder", demoStatus: null, detail: "Turns readings, codes, memory and peer reports into the brain's input.", icon: Binary },
  {
    label: "Brain (CTM)",
    demoStatus: "Demo script",
    detail: "A small Continuous Thought Machine picks the assessment, diagnosis, action and message. A demo script stands in for it here.",
    icon: Brain,
  },
  { label: "Safety gate", demoStatus: null, detail: "Lets a protective action run only if it's on the approved list and the evidence backs it.", icon: ShieldCheck },
  { label: "Memory (CMM)", demoStatus: null, detail: "Remembers what this machine has been through.", icon: Database },
  { label: "LLM explanation", demoStatus: null, detail: "Will explain decisions to the owner in plain words (later phase).", icon: MessageSquareText },
]

/** The pipeline inside each node, drawn as a connected flow. */
export function ReasoningPipeline({ demo }: { demo: boolean }) {
  const active = (stage: Stage | undefined) => Boolean(demo && stage?.demoStatus)
  return (
    <Card className="gap-0 p-5 shadow-none">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Inside each node</h3>
        <span className="text-[11px] text-muted-foreground">The same pipeline runs on all four machines, left to right</span>
      </div>
      <Separator className="my-4" />
      <ol className="grid grid-cols-1 gap-y-1 xl:grid-cols-7 xl:gap-y-0">
        {STAGES.map((stage, index) => {
          const on = active(stage)
          const brain = stage.label === "Brain (CTM)"
          const next = STAGES[index + 1]
          const linked = on && active(next)
          const Icon = stage.icon
          return (
            <li key={stage.label} className="relative flex gap-3 pb-3 xl:flex-col xl:items-center xl:gap-0 xl:px-2 xl:pb-0 xl:text-center">
              {next && (
                <>
                  <span
                    aria-hidden
                    className={cn(
                      "absolute left-[18px] top-10 bottom-0 w-0 border-l xl:hidden",
                      linked ? "border-emerald-300" : "border-dashed border-slate-300",
                    )}
                  />
                  <span
                    aria-hidden
                    className={cn(
                      "absolute left-[calc(50%+24px)] right-[calc(-50%+24px)] top-[18px] hidden h-0 border-t xl:block",
                      linked ? "border-emerald-300" : "border-dashed border-slate-300",
                    )}
                  />
                </>
              )}
              <span
                className={cn(
                  "relative grid size-9 shrink-0 place-items-center rounded-full border",
                  brain && on
                    ? "border-scope-line bg-scope text-cyan-300"
                    : on
                      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                      : "border-dashed border-slate-300 bg-white text-slate-400",
                )}
              >
                <Icon className="size-4" />
              </span>
              <div className="min-w-0 xl:mt-2.5">
                <div className="text-xs font-medium">{stage.label}</div>
                <div
                  className={cn(
                    "mt-0.5 font-mono text-[10px]",
                    on ? (brain ? "text-sky-700" : "text-emerald-700") : "text-muted-foreground",
                  )}
                >
                  {on ? stage.demoStatus : "Not connected"}
                </div>
                <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{stage.detail}</p>
              </div>
            </li>
          )
        })}
      </ol>
      <Separator className="my-4" />
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
        <span>Assessment levels:</span>
        {ASSESSMENTS.map((assessment) => (
          <AssessmentBadge key={assessment} assessment={assessment} />
        ))}
        <span>Only Danger can carry a protective action, and only after the safety gate approves it.</span>
      </div>
    </Card>
  )
}
