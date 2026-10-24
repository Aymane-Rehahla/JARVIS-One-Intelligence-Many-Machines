import { cn } from "@/lib/utils"
import type { VoiceState } from "@/hooks/use-demo-farm"

const LABELS: Record<VoiceState, string> = {
  idle: "JARVIS",
  wake: "Heard “JARVIS”",
  listening: "Listening…",
  speaking: "Speaking…",
}

const SPOKEN: Record<VoiceState, string> = {
  idle: "JARVIS voice is idle",
  wake: "JARVIS heard the wake word",
  listening: "JARVIS is listening",
  speaking: "JARVIS is speaking",
}

/**
 * Top-right voice indicator: a green burst when Jarvis hears its wake word,
 * breathing ripples while it listens and an equalizer while it speaks.
 */
export function JarvisVoice({
  state,
  available,
  onActivate,
}: {
  state: VoiceState
  /** False when no voice source is connected; the indicator then stays grey. */
  available: boolean
  onActivate: () => void
}) {
  return (
    <button
      type="button"
      onClick={onActivate}
      title={available ? "Voice demo: simulate saying “Hey JARVIS, what's the status?”" : "Voice isn't connected"}
      aria-label={available ? `${SPOKEN[state]}. Simulate saying hey JARVIS.` : "JARVIS voice is not connected"}
      data-state={available ? state : "off"}
      className={cn(
        "jarvis-voice flex h-8 shrink-0 items-center gap-2 rounded-full border px-1.5 text-xs font-medium transition-colors sm:pr-3",
        !available
          ? "border-slate-200 bg-secondary/50 text-muted-foreground"
          : state === "idle"
            ? "border-emerald-200 bg-emerald-50/60 text-emerald-800 hover:bg-emerald-50"
            : "border-emerald-300 bg-emerald-50 text-emerald-800",
      )}
    >
      <span className="jarvis-voice-orb relative grid size-5 place-items-center" aria-hidden>
        <span className="jarvis-voice-ring" />
        <span className="jarvis-voice-ring jarvis-voice-ring-late" />
        <span className="jarvis-voice-core" />
        <span className="jarvis-voice-bars">
          {[0, 1, 2, 3].map((bar) => (
            <span key={bar} className="jarvis-voice-bar" />
          ))}
        </span>
      </span>
      <span className="hidden sm:inline">{available ? LABELS[state] : "Voice off"}</span>
      <span role="status" aria-live="polite" className="sr-only">
        {available && state !== "idle" ? SPOKEN[state] : ""}
      </span>
    </button>
  )
}
