import { useRef, useState, type ReactNode } from "react"
import { ArrowRight, X } from "lucide-react"

import { Button } from "@/components/ui/button"
import { EmptyState, Kicker, Notice } from "@/components/farm-ui"
import type { DemoFarm } from "@/hooks/use-demo-farm"
import { useReducedMotion } from "@/hooks/use-reduced-motion"
import { ASSESSMENT_TONES } from "@/lib/assessment"
import { cn } from "@/lib/utils"
import {
  ASSESSMENT_LABELS,
  deviceName,
  diagnosisLabel,
  formatSimTime,
  type FarmState,
  type JarvisActivityEntry,
  type PeerMessage,
} from "@/services/matrix"

import { CmmView } from "./cmm-view"
import { CtmExplorer } from "./ctm-explorer"
import { JarvisCloud, type CloudTarget } from "./jarvis-cloud"
import { MIND_NODES, NODE_LABEL, RECORDED_LABEL, useArchitecture, useManifest, type MindNode } from "./mind-data"
import { VoiceBox } from "./voice-box"

const COMPONENTS: { id: CloudTarget; index: string; name: string; role: string; title: string; summary: string; recorded: boolean }[] = [
  {
    id: "ctm",
    index: "01",
    name: "CTM",
    role: "Thinking",
    title: "How it thinks",
    summary:
      "A Continuous Thought Machine. Every farm second the same 256 neurons, with the same weights, run 16 internal steps. Each neuron has its own small model that reads its last 32 inputs. The answer is read from how strongly 32 of them fire together.",
    recorded: true,
  },
  {
    id: "cmm",
    index: "02",
    name: "CMM",
    role: "Memory",
    title: "What it remembers",
    summary:
      "The memory module: one database per node. It keeps the episodes worth keeping, facts only once a device code verifies them, the shape of a normal hour, and what was done about each problem.",
    recorded: true,
  },
  {
    id: "voice",
    index: "03",
    name: "Voice box",
    role: "Speaks",
    title: "How it speaks",
    summary:
      "Push-to-talk in, speech recognition, the agent's answer checked sentence by sentence, and speech out as each sentence is ready. Only incubator-1 speaks; the wake word was cut.",
    recorded: false,
  },
]

export function JarvisPage({ state, farm, lastReply }: { state: FarmState; farm: DemoFarm; lastReply?: { text: string; farmTime: string | null; outcome: string } | null }) {
  const [open, setOpen] = useState<CloudTarget | null>(null)
  const [node, setNode] = useState<MindNode>("incubator-1")
  const explorer = useRef<HTMLElement>(null)
  const reduced = useReducedMotion()
  const { data: architecture } = useArchitecture()
  const { data: manifest } = useManifest()
  const brains = architecture ? [architecture.incubator, architecture.hvac, architecture.animal_protection] : []

  const openMind = (target: CloudTarget) => {
    setOpen(target)
    requestAnimationFrame(() => {
      const element = explorer.current
      if (element) window.scrollTo({ top: element.getBoundingClientRect().top + window.scrollY - 112, behavior: reduced ? "auto" : "smooth" })
    })
  }
  const active = COMPONENTS.find((component) => component.id === open) ?? null
  const fault = manifest?.episode.faults[0]

  return (
    <div className="mx-auto max-w-[1400px] space-y-12">
      {/* The entrance: who JARVIS is, and the cloud that opens the mind. */}
      <section aria-labelledby="jarvis-title" className="grid border-l border-t lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="flex flex-col border-b border-r p-6 md:p-10">
          <Kicker>02 // JARVIS</Kicker>
          <h1 id="jarvis-title" className="display mt-6 text-[clamp(2.2rem,3.9vw,3.7rem)]">
            256 neurons.
            <br />
            16 steps.
            <br />
            Every second.
          </h1>
          <p className="mt-6 max-w-md text-[16px] leading-7">
            Every machine on the farm runs its own small brain, a Continuous Thought Machine of about 0.58 million parameters. Touch the cloud to look inside.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Button size="lg" onClick={() => openMind("ctm")}>
              Open the mind
              <ArrowRight className="size-4" />
            </Button>
          </div>
          <p className="label-mono mt-auto pt-10 text-muted-foreground">{RECORDED_LABEL}</p>
        </div>
        <div
          className="relative order-first flex min-h-[300px] items-center border-b border-r px-4 pb-8 pt-16 sm:min-h-[420px] md:px-8 lg:order-none"
          style={{ backgroundImage: "linear-gradient(var(--border) 1px, transparent 1px), linear-gradient(90deg, var(--border) 1px, transparent 1px)", backgroundSize: "64px 64px", backgroundPosition: "-1px -1px" }}
        >
          <span className="label-mono absolute left-4 top-4 bg-background px-1.5 text-muted-foreground md:left-8">Jarvis // one node per machine</span>
          <span className="label-mono absolute right-4 top-4 hidden items-center gap-2 bg-background px-1.5 text-muted-foreground sm:flex md:right-8"><span aria-hidden className="size-2 bg-ok" />4 nodes</span>
          <JarvisCloud onOpen={openMind} attention={open ? "down" : null} />
        </div>
        <dl className="grid grid-cols-2 lg:col-span-2 lg:grid-cols-4">
          <Fact label="Parameters per brain" value="≈0.58 M" detail={brains.length ? brains.map((brain) => brain.parameters.total.toLocaleString("en-US")).join(" · ") : "Loading…"} />
          <Fact label="Neurons" value={architecture ? String(architecture.incubator.neurons) : "…"} detail="All the same kind. No layers, no input or output neurons." />
          <Fact label="Steps per thought" value={architecture ? String(architecture.incubator.internal_steps_per_tick) : "…"} detail="The same neurons, 16 times. Adaptive thinking is off." />
          <Fact label="Memory per neuron" value={architecture ? String(architecture.incubator.memory_length) : "…"} detail="Its last 32 inputs: the previous second and this one." />
        </dl>
      </section>

      {/* The architecture explorer, laid out like the reference's architecture section. */}
      {open && active && (
        <section ref={explorer} aria-labelledby="mind-title" className="mind-enter grid scroll-mt-28 border-l border-t lg:grid-cols-[280px_minmax(0,1fr)]">
          <aside className="flex flex-col border-b border-r">
            <div className="flex items-center justify-between border-b px-5 py-4">
              <h2 id="mind-title" className="display-sm text-[13px]">Inside JARVIS</h2>
              <Button size="icon-sm" variant="ghost" aria-label="Close the mind" onClick={() => setOpen(null)}><X /></Button>
            </div>
            <nav aria-label="Components">
              {COMPONENTS.map((component) => (
                <button
                  key={component.id}
                  type="button"
                  aria-current={open === component.id ? "true" : undefined}
                  onClick={() => setOpen(component.id)}
                  className={cn("flex w-full flex-col gap-1 border-b border-l-[3px] px-5 py-4 text-left transition-colors duration-150", open === component.id ? "border-l-signal bg-foreground text-background" : "border-l-transparent hover:bg-secondary")}
                >
                  <span className="label-mono">[{component.index} // {component.name}]</span>
                  <span className={cn("label-mono opacity-70", !component.recorded && "underline decoration-dashed underline-offset-4")}>{component.role}</span>
                </button>
              ))}
            </nav>
            {open !== "voice" && (
              <div className="border-b px-5 py-4">
                <div className="label-mono text-muted-foreground">Node</div>
                <div role="radiogroup" aria-label="Node" className="mt-2 grid grid-cols-2 border-l border-t lg:grid-cols-1">
                  {MIND_NODES.map((id) => (
                    <button
                      key={id}
                      type="button"
                      role="radio"
                      aria-checked={node === id}
                      onClick={() => setNode(id)}
                      className={cn("flex items-center gap-2 border-b border-r px-3 py-2.5 text-left text-[13px] transition-colors duration-150", node === id ? "bg-foreground text-background" : "hover:bg-secondary")}
                    >
                      <span aria-hidden className={cn("size-2 shrink-0", node === id ? "bg-signal" : "bg-input")} />
                      {NODE_LABEL[id]}
                    </button>
                  ))}
                </div>
                <p className="mt-2 text-[12px] leading-5 text-muted-foreground">Incubators 1 and 2 load the same weights into two independent instances.</p>
              </div>
            )}
            <div className="mt-auto px-5 py-5">
              <div className="label-mono text-muted-foreground">Note to the visitor</div>
              <p className="mt-2 text-[13px] leading-5">
                {active.recorded
                  ? `${RECORDED_LABEL}. Nothing here is live.${fault ? ` In the episode both incubators get ${fault.voltage_v} V from tick ${fault.inject_tick} to tick ${fault.end_tick}.` : ""}`
                  : "Built in step 6E: live in the voice indicator at the top and on the Chat page (push-to-talk)."}
              </p>
            </div>
          </aside>
          <div className="min-w-0 border-b border-r">
            <header className="grid gap-6 border-b p-5 md:grid-cols-[auto_minmax(0,1fr)] md:p-8">
              <div className="display text-[clamp(3rem,6vw,5rem)] leading-none text-foreground">{active.index}</div>
              <div className="min-w-0">
                <Kicker>{active.name} // {active.role}</Kicker>
                <h3 className="display mt-4 text-[clamp(1.6rem,3vw,2.6rem)]">{active.title}</h3>
                <p className="mt-4 max-w-3xl text-[15px] leading-7 text-muted-foreground">{active.summary}</p>
              </div>
            </header>
            <div key={`${open}-${node}`} className="mind-enter p-5 md:p-8">
              {open === "ctm" && <CtmExplorer node={node} />}
              {open === "cmm" && <CmmView node={node} />}
              {open === "voice" && <VoiceBox />}
            </div>
          </div>
        </section>
      )}

      {/* The demo farm's nodes, which the simulator drives. Separate from the recorded brains above. */}
      <section aria-labelledby="demo-title" className="space-y-6">
        <header className="grid gap-3 border-b pb-6 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
          <div>
            <Kicker>{state.source === "live" ? `In the Matrix test bench // ${state.live?.label ?? "live"}` : "In the simulator // demo script"}</Kicker>
            <h2 id="demo-title" className="display mt-4 text-[clamp(1.6rem,3vw,2.4rem)]">What the nodes say now</h2>
          </div>
          {state.source === "disconnected" && <Button variant="outline" size="sm" onClick={farm.activateDemo}>Explore demo mode</Button>}
        </header>
        <Notice
          tone="warning"
          text={
            state.source === "live"
              ? `Live: each node's conclusion is its own reasoning brain's output (${state.live?.label ?? "live"}), shown with its evidence. The language model's words are shown apart, below; they never decide anything.`
              : "The simulator's nodes follow a demo script that uses the real rules: the five levels, the diagnoses and the device codes."
          }
        />
        {state.source === "live" && (
          <div className="border px-4 py-3 text-[14px] leading-6">
            <span className="label-mono mr-2 text-muted-foreground">The language model's words // incubator-1's last reply</span>
            {lastReply ? <>{lastReply.text} <span className="label-mono text-muted-foreground">({lastReply.outcome}{lastReply.farmTime ? `, farm time ${lastReply.farmTime}` : ""})</span></>
              : <span className="text-muted-foreground">No question asked in this session yet (the Chat page).</span>}
          </div>
        )}
        <div className="grid gap-6 lg:grid-cols-2">
          <PeerLinkLog state={state} />
          <DecisionLog state={state} />
        </div>
      </section>
    </div>
  )
}

function Fact({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="border-b border-r p-5">
      <dt className="label-mono text-muted-foreground">{label}</dt>
      <dd>
        <span className="mt-2 block text-[clamp(1.8rem,3vw,2.5rem)] font-semibold leading-none tabular-nums [font-stretch:112%]">{value}</span>
        <span className="mt-2 block text-[13px] leading-5 text-muted-foreground">{detail}</span>
      </dd>
    </div>
  )
}

function LogPanel({ title, meta, children }: { title: string; meta: string; children: ReactNode }) {
  return (
    <section className="border bg-background">
      <div className="flex items-center justify-between gap-3 border-b px-5 py-3">
        <h3 className="display-sm text-[13px]">{title}</h3>
        <span className="label-mono text-muted-foreground">{meta}</span>
      </div>
      <div className="p-5">{children}</div>
    </section>
  )
}

/** A vertical timeline: time, a square on a rail, then the entry. */
function TimelineEntry({ time, dot, children }: { time: number; dot: string; children: ReactNode }) {
  return (
    <li className="relative grid grid-cols-[64px_12px_minmax(0,1fr)] gap-x-3">
      <span className="font-mono text-[12px] tabular-nums text-muted-foreground">{formatSimTime(time)}</span>
      <span className="relative flex justify-center">
        <span aria-hidden className="absolute -bottom-4 top-3 w-px bg-border" />
        <span aria-hidden className={cn("relative mt-1 size-2", dot)} />
      </span>
      <div className="min-w-0">{children}</div>
    </li>
  )
}

function PeerLinkLog({ state }: { state: FarmState }) {
  const messages = state.jarvis.messages.slice(0, 20)
  return (
    <LogPanel title="Peer link" meta={`${state.jarvis.messages.length} messages`}>
      {messages.length === 0 ? (
        <EmptyState title="No peer messages" text="A node broadcasts to the other three whenever its conclusion changes. Inject a scenario in the simulator to see it." />
      ) : (
        <ol className="max-h-[380px] space-y-4 overflow-y-auto overscroll-contain pr-1">
          {messages.map((message: PeerMessage) => (
            <TimelineEntry key={message.id} time={message.timeS} dot={ASSESSMENT_TONES[message.assessment].dot}>
              <div className="text-[14px]">
                <span className="font-semibold">{deviceName(message.from)}</span>
                <span className="text-muted-foreground"> to all · {ASSESSMENT_LABELS[message.assessment]}{message.diagnosis === "NONE" ? "" : ` · ${diagnosisLabel(message.diagnosis)}`}</span>
              </div>
              <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">{message.note}</p>
            </TimelineEntry>
          ))}
        </ol>
      )}
    </LogPanel>
  )
}

function DecisionLog({ state }: { state: FarmState }) {
  const entries = state.activity.slice(0, 20)
  return (
    <LogPanel title="Decision log" meta={`${state.activity.length} entries`}>
      {entries.length === 0 ? (
        <EmptyState title="No decisions yet" text="Inject a demo scenario to see what each node concludes and when it tells the owner." />
      ) : (
        <ol className="max-h-[380px] space-y-4 overflow-y-auto overscroll-contain pr-1">
          {entries.map((entry: JarvisActivityEntry) => (
            <TimelineEntry key={entry.id} time={entry.timeS} dot={entry.tone === "decision" ? "bg-signal" : entry.tone === "warning" ? "bg-watch" : "bg-input"}>
              <div className="text-[14px] font-semibold">{entry.node ? deviceName(entry.node) : "Farm"}</div>
              <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">{entry.message}</p>
            </TimelineEntry>
          ))}
        </ol>
      )}
    </LogPanel>
  )
}
