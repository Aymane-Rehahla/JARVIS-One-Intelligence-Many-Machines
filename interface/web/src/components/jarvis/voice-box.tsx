import { cn } from "@/lib/utils"

/**
 * The Voice Box as built in step 6E (decision 0022): push-to-talk, speech to text, the agent's verified reply spoken
 * sentence by sentence. The wake word was cut (plan section 11); its stage stays drawn dashed and labelled CUT.
 */
const STAGES = [
  { id: "wake", name: "Wake word", model: "openWakeWord “hey jarvis”", note: "Cut for now: the website's push-to-talk button is the way to talk." },
  { id: "vad", name: "Voice activity", model: "Silero VAD", note: "Ends speech after 0.6 s of silence, measured on recorded and synthesised sentences." },
  { id: "stt", name: "Speech to text", model: "Parakeet TDT 0.6B v2, int8, via sherpa-onnx", note: "Audio comes in at 16 kHz; about 0.07 s per second of speech on this PC." },
  { id: "answer", name: "The answer", model: "The language model", note: "The agent's reply, checked sentence by sentence against this turn's data, then spoken as each sentence arrives." },
  { id: "tts", name: "Text to speech", model: "Kokoro-82M v1.0 via sherpa-onnx", note: "Sentence by sentence as the reply arrives. British male voice bm_george by default, speed 0.5–2.0." },
] as const

const STATES = ["off", "idle", "wake", "listening", "thinking", "speaking", "error"]

const LLM = [
  { part: "Read-only tools", status: "Built" },
  { part: "Document search over each node's manuals", status: "Built" },
  { part: "Number checker: refuses any number not from a tool or a document", status: "Built" },
  { part: "The chat agent that answers the owner", status: "Built" },
  { part: "Response speed and tool-picking quality", status: "Unknown" },
]

export function VoiceBox() {
  return (
    <div className="space-y-8">
      <section aria-labelledby="voice-pipeline" className="border bg-background">
        <div className="flex flex-wrap items-baseline justify-between gap-3 border-b px-5 py-3">
          <h3 id="voice-pipeline" className="display-sm text-[13px]">How JARVIS listens and speaks</h3>
          <span className="label-mono border border-foreground px-2 py-0.5">Built // push-to-talk</span>
        </div>
        <ol className="grid md:grid-cols-5">
          {STAGES.map((stage, index) => (
            <li key={stage.id} className="relative border-b p-5 md:border-r md:last:border-r-0">
              <div className="flex items-center gap-2">
                <span aria-hidden className={cn("size-2.5 border border-foreground", stage.id === "wake" ? "border-dashed" : "bg-foreground")} />
                <span className="label-mono text-muted-foreground">{String(index + 1).padStart(2, "0")} // {stage.id === "wake" ? "cut" : "built"}</span>
              </div>
              <div className="display-sm mt-3 text-[14px]">{stage.name}</div>
              <div className="mt-2 border border-dashed border-input px-2 py-1.5 font-mono text-[12px] leading-5">{stage.model}</div>
              <p className="mt-3 text-[13px] leading-5 text-muted-foreground">{stage.note}</p>
              {index < STAGES.length - 1 && <span aria-hidden className="absolute -right-2 top-6 z-10 hidden bg-background px-0.5 font-mono text-[13px] md:block">→</span>}
            </li>
          ))}
        </ol>
        <div className="grid gap-x-8 gap-y-3 px-5 py-4 md:grid-cols-2">
          <p className="text-[14px] leading-6">Only incubator-1 speaks. The other three nodes have voice installed but switched off. A DANGER alert interrupts anything being said. A missing microphone is shown as missing, never as a crash.</p>
          <p className="text-[14px] leading-6 text-muted-foreground">Tone of voice was cut from the plan, so no emotion stage is drawn. The voice indicator at the top shows the real voice box's state in live mode; the latency is measured (E6).</p>
        </div>
      </section>

      <section aria-labelledby="voice-states" className="border bg-background p-5">
        <h3 id="voice-states" className="display-sm text-[13px]">States</h3>
        <ol className="mt-4 flex flex-wrap items-center gap-2">
          {STATES.map((state, index) => (
            <li key={state} className="flex items-center gap-2">
              <span className={cn("label-mono border border-dashed px-3 py-1.5", state === "error" ? "border-danger text-danger" : "border-foreground")}>{state}</span>
              {index < STATES.length - 2 && <span aria-hidden className="font-mono text-[12px] text-muted-foreground">→</span>}
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="llm-status" className="border bg-background">
        <div className="border-b px-5 py-3">
          <h3 id="llm-status" className="display-sm text-[13px]">The language model behind the answer</h3>
          <p className="mt-1 text-[13px] leading-5 text-muted-foreground">Incubator-1 runs Qwen 3.5 2B roleplay (Q8_0); the other three run Qwen3-0.6B (Q8_0); all four use bge-small-en-v1.5 for document search. One llama.cpp server per node. It talks; it never acts, and it never opens the CMM.</p>
        </div>
        <ul>
          {LLM.map((row) => (
            <li key={row.part} className="flex items-center justify-between gap-4 border-b px-5 py-3 last:border-0">
              <span className="text-[14px]">{row.part}</span>
              <span className={cn("label-mono shrink-0 border px-2 py-0.5", row.status === "Built" ? "border-ok/50 text-ok-ink" : row.status === "Not built" ? "border-dashed border-foreground" : "border-input text-muted-foreground")}>{row.status}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
