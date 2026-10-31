import { Mic } from "lucide-react"

import { Kicker } from "@/components/farm-ui"
import { Slider } from "@/components/ui/slider"
import { cn } from "@/lib/utils"
import type { Voice } from "@/hooks/use-voice"

const STATE_TEXT: Record<string, string> = {
  off: "Voice off", idle: "Ready", wake: "Heard its name", listening: "Listening", thinking: "Thinking",
  speaking: "Speaking", error: "Voice error",
}

/** The voice menu (W6): push-to-talk, the microphone, the voice and its speed, from incubator-1's real voice box. */
export function VoicePanel({ voice }: { voice: Voice }) {
  const status = voice.status
  const ready = status !== null && status.state !== "off"
  return (
    <section aria-labelledby="voice-title" className="grid gap-3 border p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Kicker>Voice // incubator-1</Kicker>
        <span role="status" className="label-mono text-muted-foreground">
          {status ? `${STATE_TEXT[status.state] ?? status.state} · microphone ${status.mic}` : voice.connection === "connected"
            ? "Waiting for the voice box" : "Voice not connected"}
        </span>
      </div>
      <h2 id="voice-title" className="sr-only">Voice</h2>
      <button
        type="button"
        disabled={!ready}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId)
          voice.press()
        }}
        onPointerUp={() => voice.release()}
        onPointerCancel={() => voice.release()}
        onKeyDown={(event) => {
          if ((event.key === " " || event.key === "Enter") && !event.repeat) {
            event.preventDefault()
            voice.press()
          }
        }}
        onKeyUp={(event) => {
          if (event.key === " " || event.key === "Enter") voice.release()
        }}
        className={cn("flex h-12 items-center justify-center gap-2 border font-mono text-[12px] uppercase tracking-[0.08em]",
          status?.push_to_talk ? "border-foreground bg-foreground text-background" : "hover:border-foreground",
          !ready && "cursor-not-allowed opacity-50")}
      >
        <Mic aria-hidden className="size-4" />
        {status?.push_to_talk ? "Listening: release to ask" : "Hold to talk"}
      </button>
      {status?.transcript && <p className="text-[13px] leading-5"><span className="label-mono mr-2 text-muted-foreground">Heard //</span>{status.transcript}</p>}
      {status?.detail && <p className="label-mono text-signal-ink">{status.detail}</p>}
      {status && (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1">
            <span className="label-mono text-muted-foreground">Voice</span>
            <select
              className="h-9 border bg-background px-2 text-[13px]"
              value={status.voice}
              onChange={(event) => voice.choose(event.target.value, status.speed)}
            >
              {(voice.voices.length ? voice.voices : [status.voice]).map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          <label className="grid gap-1">
            <span className="label-mono text-muted-foreground">Speed {status.speed.toFixed(1)}×</span>
            <Slider min={0.5} max={2} step={0.1} value={[status.speed]} aria-label="Voice speed"
              onValueCommit={(value) => voice.choose(status.voice, value[0])} />
          </label>
        </div>
      )}
      {voice.problem && <p role="alert" className="label-mono text-signal-ink">{voice.problem}</p>}
    </section>
  )
}
