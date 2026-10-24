import { useEffect, useRef, useState, type ReactNode } from "react"
import { MessageSquare, Network, Radio, Send, Sparkles } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { EmptyState, Notice, PageHeader } from "@/components/farm-ui"
import type { DemoFarm } from "@/hooks/use-demo-farm"
import { ASSESSMENT_TONES } from "@/lib/assessment"
import { cn } from "@/lib/utils"
import {
  ASSESSMENT_LABELS,
  deviceName,
  diagnosisLabel,
  formatSimTime,
  type DeviceId,
  type FarmState,
  type JarvisActivityEntry,
  type PeerMessage,
} from "@/services/matrix"

import { mostSeriousNode } from "./brain-profile"
import { BrainView } from "./brain-view"
import { ReasoningPipeline } from "./reasoning-pipeline"

export function JarvisPage({ state, farm }: { state: FarmState; farm: DemoFarm }) {
  const demo = state.source === "demo"
  // The brain view follows whichever node is most worried, until someone picks a node.
  const [pinned, setPinned] = useState<DeviceId | null>(null)
  const nodeId = pinned ?? mostSeriousNode(state)

  return (
    <div className="mx-auto max-w-[1200px] space-y-6">
      <PageHeader
        title="JARVIS nodes"
        action={
          !demo ? (
            <Button variant="outline" size="sm" onClick={farm.activateDemo}>
              Explore demo mode
            </Button>
          ) : (
            <Badge variant="outline" className="gap-1.5 font-normal">
              <Sparkles className="size-3.5 text-blue-600" />
              Demo script only
            </Badge>
          )
        }
      />
      <Notice
        tone="warning"
        text="The real JARVIS nodes aren't connected to this website yet. A demo script plays their part using the real rules: the five assessment levels, the diagnoses and the device codes. The brain view illustrates how their CTM reasons; no CTM, memory or LLM produces this output."
        action={!demo ? "Use demo mode" : undefined}
        onAction={!demo ? farm.activateDemo : undefined}
      />

      <BrainView
        state={state}
        nodeId={nodeId}
        following={pinned === null}
        onSelectNode={setPinned}
        onFollow={() => setPinned(null)}
        onDemo={farm.activateDemo}
      />

      <ReasoningPipeline demo={demo} />

      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-[1fr_1fr_0.9fr]">
        <PeerLinkLog state={state} />
        <DecisionLog state={state} />
        <div className="md:col-span-2 xl:col-span-1">
          <ChatPanel state={state} onSend={farm.sendMessage} />
        </div>
      </div>
    </div>
  )
}

function PanelHeader({ icon, title, meta }: { icon: ReactNode; title: string; meta: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        {icon}
        {title}
      </h3>
      <span className="font-mono text-[11px] text-muted-foreground">{meta}</span>
    </div>
  )
}

/** A vertical timeline: time, coloured dot on a rail, then the entry. */
function Timeline({ children }: { children: ReactNode }) {
  return <ol className="relative max-h-[360px] space-y-3.5 overflow-y-auto pr-1">{children}</ol>
}

function TimelineEntry({ time, dot, children }: { time: number; dot: string; children: ReactNode }) {
  return (
    <li className="relative grid grid-cols-[52px_12px_minmax(0,1fr)] gap-x-2 text-xs">
      <span className="mt-px font-mono text-[11px] text-muted-foreground">{formatSimTime(time)}</span>
      <span className="relative flex justify-center">
        <span aria-hidden className="absolute -bottom-4 top-3 w-px bg-border" />
        <span className={cn("relative mt-1 size-2 rounded-full ring-2 ring-card", dot)} />
      </span>
      <div className="min-w-0">{children}</div>
    </li>
  )
}

function PeerLinkLog({ state }: { state: FarmState }) {
  const messages = state.jarvis.messages.slice(0, 20)
  return (
    <Card className="gap-0 p-5 shadow-none">
      <PanelHeader icon={<Radio className="size-4 text-blue-600" />} title="Peer link" meta={`${state.jarvis.messages.length} messages`} />
      <Separator className="my-4" />
      {messages.length === 0 ? (
        <EmptyState
          title="No peer messages"
          text="A node broadcasts to the other three whenever its conclusion changes. Inject a scenario to see it."
          icon={<Radio className="size-5 text-muted-foreground" />}
        />
      ) : (
        <Timeline>
          {messages.map((message: PeerMessage) => (
            <TimelineEntry key={message.id} time={message.timeS} dot={ASSESSMENT_TONES[message.assessment].dot}>
              <div>
                <span className="font-medium">{deviceName(message.from)}</span>
                <span className="text-muted-foreground">
                  {" "}→ all · {ASSESSMENT_LABELS[message.assessment]}
                  {message.diagnosis === "NONE" ? "" : ` · ${diagnosisLabel(message.diagnosis)}`}
                </span>
              </div>
              <p className="mt-0.5 leading-5 text-muted-foreground">{message.note}</p>
            </TimelineEntry>
          ))}
        </Timeline>
      )}
    </Card>
  )
}

function DecisionLog({ state }: { state: FarmState }) {
  const entries = state.activity.slice(0, 20)
  return (
    <Card className="gap-0 p-5 shadow-none">
      <PanelHeader icon={<Network className="size-4 text-blue-600" />} title="Decision log" meta={`${state.activity.length} entries`} />
      <Separator className="my-4" />
      {entries.length === 0 ? (
        <EmptyState
          title="No decisions yet"
          text="Inject a demo scenario to see what each node concludes and when it tells the owner."
          icon={<Network className="size-5 text-muted-foreground" />}
        />
      ) : (
        <Timeline>
          {entries.map((entry: JarvisActivityEntry) => (
            <TimelineEntry
              key={entry.id}
              time={entry.timeS}
              dot={entry.tone === "decision" ? "bg-blue-500" : entry.tone === "warning" ? "bg-amber-500" : "bg-slate-400"}
            >
              <div className="font-medium">{entry.node ? deviceName(entry.node) : "Farm"}</div>
              <p className="mt-0.5 leading-5 text-muted-foreground">{entry.message}</p>
            </TimelineEntry>
          ))}
        </Timeline>
      )}
    </Card>
  )
}

function ChatPanel({ state, onSend }: { state: FarmState; onSend: (text: string) => void }) {
  const [message, setMessage] = useState("")
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }, [state.chat.length])

  const submit = () => {
    if (!message.trim()) return
    onSend(message)
    setMessage("")
  }

  return (
    <Card className="flex h-full max-h-[480px] min-h-[340px] flex-col gap-0 p-5 shadow-none">
      <div className="flex items-center gap-2">
        <MessageSquare className="size-4 text-muted-foreground" />
        <h3 className="text-sm font-semibold">Operator conversation</h3>
        <Badge variant="outline" className="ml-auto text-[10px] font-normal">
          Preview
        </Badge>
      </div>
      <Separator className="my-4" />
      <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
        {state.chat.length === 0 ? (
          <div className="grid h-full place-items-center text-center text-xs text-muted-foreground">
            <div className="space-y-2">
              <MessageSquare className="mx-auto size-5 text-muted-foreground" />
              <p>This is a frontend conversation preview.</p>
              <p className="text-muted-foreground/70">Ask about status, scenarios, devices, or JARVIS to see canned demonstration replies.</p>
            </div>
          </div>
        ) : (
          state.chat.map((entry) => (
            <div key={entry.id} className={cn("flex", entry.role === "operator" ? "justify-end" : "justify-start")}>
              <div
                className={cn(
                  "max-w-[85%] rounded-lg px-3 py-2 text-xs leading-5",
                  entry.role === "operator" ? "rounded-br-sm bg-primary text-primary-foreground" : "rounded-bl-sm bg-secondary text-secondary-foreground",
                )}
              >
                <p>{entry.text}</p>
                {entry.preview && <p className="mt-1 text-[10px] opacity-70">Illustrative preview · not real AI</p>}
              </div>
            </div>
          ))
        )}
      </div>
      <div className="mt-3 flex gap-2">
        <Textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault()
              submit()
            }
          }}
          aria-label="Message to JARVIS"
          placeholder="Ask JARVIS about your machines..."
          className="min-h-9 resize-none text-xs"
        />
        <Button size="icon" onClick={submit} disabled={!message.trim()} aria-label="Send message">
          <Send className="size-4" />
        </Button>
      </div>
    </Card>
  )
}
