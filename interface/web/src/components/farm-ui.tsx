import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { ASSESSMENT_TONES } from "@/lib/assessment"
import { cn } from "@/lib/utils"
import { ASSESSMENT_LABELS, type Assessment } from "@/services/matrix"

// Small building blocks shared by every page.

/** Uppercase mono metadata with the orange square marker: "01 // SYSTEMS". */
export function Kicker({ children, className, marker = true }: { children: ReactNode; className?: string; marker?: boolean }) {
  return (
    <div className={cn("flex items-center gap-3", className)}>
      {marker && <span aria-hidden className="marker" />}
      <span className="label-mono text-muted-foreground">{children}</span>
    </div>
  )
}

export function PageHeader({ index, kicker, title, action }: { index?: string; kicker?: string; title: ReactNode; action?: ReactNode }) {
  return (
    <header className="grid gap-6 border-b pb-8 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
      <div className="min-w-0">
        {(index || kicker) && <Kicker>{[index, kicker].filter(Boolean).join(" // ")}</Kicker>}
        <h1 className="display mt-5 text-[clamp(2.1rem,5.2vw,4.5rem)] [overflow-wrap:anywhere]">{title}</h1>
      </div>
      {action && <div className="flex flex-wrap items-center gap-2">{action}</div>}
    </header>
  )
}

/** A panel heading: wide uppercase title, optional mono meta on the trailing edge. */
export function PanelTitle({ title, meta, children }: { title: string; meta?: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h3 className="display-sm text-[13px]">{title}</h3>
      {meta && <span className="label-mono text-muted-foreground">{meta}</span>}
      {children}
    </div>
  )
}

export function Notice({
  text,
  action,
  onAction,
  tone = "info",
}: {
  text: string
  action?: string
  onAction?: () => void
  tone?: "info" | "warning"
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border bg-panel px-4 py-3">
      <p className="flex min-w-0 items-start gap-3 text-[13px] leading-5">
        <span aria-hidden className={cn("mt-[5px] size-2 shrink-0", tone === "warning" ? "bg-signal" : "bg-foreground")} />
        <span>
          <span className="label-mono mr-2 text-muted-foreground">{tone === "warning" ? "Note //" : "Info //"}</span>
          {text}
        </span>
      </p>
      {action && onAction && (
        <Button size="sm" variant="outline" onClick={onAction} className="shrink-0">
          {action}
        </Button>
      )}
    </div>
  )
}

export function EmptyState({ title, text, icon }: { title: string; text: string; icon?: ReactNode }) {
  return (
    <div className="hatch grid place-items-center border border-dashed border-input px-6 py-10 text-center">
      <div className="max-w-sm bg-background px-4 py-3">
        {icon && <div aria-hidden className="mb-3 flex justify-center text-muted-foreground [&>svg]:size-5">{icon}</div>}
        <div className="display-sm text-[13px]">{title}</div>
        <p className="mt-2 text-[13px] leading-5 text-muted-foreground">{text}</p>
      </div>
    </div>
  )
}

export function AssessmentBadge({ assessment }: { assessment: Assessment }) {
  const tone = ASSESSMENT_TONES[assessment]
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1.5 border px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.06em]", tone.badge)}>
      <span aria-hidden className={cn("size-1.5", assessment === "DANGER" ? "bg-white motion-safe:animate-pulse" : tone.dot)} />
      {ASSESSMENT_LABELS[assessment]}
    </span>
  )
}
