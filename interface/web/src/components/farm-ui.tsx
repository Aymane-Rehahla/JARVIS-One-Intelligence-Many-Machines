import type { ReactNode } from "react"
import { AlertTriangle, CloudOff, Info } from "lucide-react"

import { Button } from "@/components/ui/button"
import { ASSESSMENT_TONES } from "@/lib/assessment"
import { cn } from "@/lib/utils"
import { ASSESSMENT_LABELS, type Assessment } from "@/services/matrix"

// Small building blocks shared by every page.

export function PageHeader({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b pb-4">
      <h2 className="text-base font-semibold">{title}</h2>
      {action}
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
    <div
      className={cn(
        "flex items-center justify-between gap-3 rounded-md p-3 text-xs text-muted-foreground",
        tone === "warning" ? "border border-amber-100 bg-amber-50/70" : "border border-blue-100 bg-blue-50/70",
      )}
    >
      <span className="flex items-center gap-2">
        {tone === "warning" ? (
          <AlertTriangle className="size-4 text-amber-600" />
        ) : (
          <Info className="size-4 text-slate-500" />
        )}
        {text}
      </span>
      {action && onAction && (
        <Button variant="link" size="sm" onClick={onAction} className="h-auto shrink-0 p-0 text-xs">
          {action}
        </Button>
      )}
    </div>
  )
}

export function EmptyState({ title, text, icon }: { title: string; text: string; icon?: ReactNode }) {
  return (
    <div className="grid place-items-center py-10 text-center">
      {icon ?? <CloudOff className="size-5 text-muted-foreground" />}
      <div className="mt-3 text-sm font-medium">{title}</div>
      <p className="mt-1 max-w-xs text-xs text-muted-foreground">{text}</p>
    </div>
  )
}

export function AssessmentBadge({ assessment }: { assessment: Assessment }) {
  const tone = ASSESSMENT_TONES[assessment]
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium", tone.badge)}>
      <span className={cn("size-1.5 rounded-full", tone.dot, assessment === "DANGER" && "animate-pulse")} />
      {ASSESSMENT_LABELS[assessment]}
    </span>
  )
}
