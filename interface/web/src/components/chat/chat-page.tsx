import { useEffect, useRef, useState, type FormEvent } from "react"
import { OctagonAlert, Send, TriangleAlert, X } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { EmptyState, Kicker, Notice, PageHeader } from "@/components/farm-ui"
import { cn } from "@/lib/utils"
import type { Chat } from "@/hooks/use-chat"
import type { Voice } from "@/hooks/use-voice"
import { VoicePanel } from "@/components/chat/voice-panel"
import type { Farm } from "@/hooks/use-farm"
import type { AckOffer, ChatDone, OwnerAlert } from "@/services/contracts"
import { confirmSecondsLeft, replyView, type AckView, type ChatEntry, type Reply } from "@/services/chat"
import { deviceName, type DeviceId, type FarmState } from "@/services/matrix"

const OUTCOME_TEXT: Record<ChatDone["outcome"], string> = {
  answered: "Answered",
  fallback: "Fallback from the data",
  refused: "Refused",
  error: "No answer",
}

function nodeName(node: string): string {
  return deviceName(node as DeviceId)
}

/** The chat with JARVIS: live through the gateway, or the demo's labelled preview replies. */
export function ChatPage({ state, farm, chat, voice }: { state: FarmState; farm: Farm; chat: Chat; voice?: Voice }) {
  const live = state.source === "live"
  // Talking needs real time: sped up, the farm runs on and its brains keep detecting, but JARVIS does not chat.
  const fast = live && (state.live?.targetSpeed ?? 1) > 1
  return (
    <div className="grid gap-8">
      <PageHeader index="02" kicker={live ? "Live // through incubator-1" : "Demo"} title="Ask JARVIS" />
      {live && voice && !fast && <VoicePanel voice={voice} />}
      {live ? <LiveChat chat={chat} fast={fast} /> : <DemoChat state={state} farm={farm} />}
    </div>
  )
}

function DemoChat({ state, farm }: { state: FarmState; farm: Farm }) {
  const [text, setText] = useState("")
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!text.trim()) return
    farm.sendMessage(text)
    setText("")
  }
  return (
    <section className="grid gap-4">
      <Notice tone="warning" text="Demo: these replies are canned previews from the demo farm, not JARVIS. Switch to live mode to talk to the real agent." />
      <ol className="grid gap-3" aria-label="Demo conversation">
        {state.chat.map((message) => (
          <li key={message.id} className={cn("border px-4 py-3 text-[14px] leading-6", message.role === "operator" ? "bg-panel" : "")}>
            <span className="label-mono mr-2 text-muted-foreground">{message.role === "operator" ? "You //" : "Preview //"}</span>
            {message.text}
          </li>
        ))}
      </ol>
      <QuestionBox text={text} onText={setText} onSubmit={submit} disabled={false} hint="Demo preview" />
    </section>
  )
}

function LiveChat({ chat, fast }: { chat: Chat; fast: boolean }) {
  const [text, setText] = useState("")
  const [refused, setRefused] = useState<string | null>(null)
  const end = useRef<HTMLDivElement | null>(null)
  const { state, connection } = chat
  const busy = state.inFlight !== null
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!text.trim()) return
    if (chat.send(text)) {
      setText("")
      setRefused(null)
    } else {
      setRefused(busy ? "One question at a time: wait for the reply." : "Not sent: the chat is not connected to the gateway.")
    }
  }
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" })
  }, [state.entries])
  return (
    <section className="grid gap-4" aria-label="Conversation with JARVIS">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
        <Kicker>{connection === "connected" ? "Chat connected" : connection === "connecting" ? "Connecting to the gateway" : "Chat not connected"}</Kicker>
        {state.invalid > 0 && <span className="label-mono text-signal-ink">{state.invalid} invalid message(s) dropped</span>}
      </div>
      {state.entries.length === 0 ? (
        <EmptyState title="No questions yet" text="Ask about any machine. JARVIS answers from this turn's data, checks every number, and says the farm time it comes from." />
      ) : (
        <ol className="grid gap-3">
          {state.entries.map((entry) => (
            <li key={entry.key}><Entry entry={entry} chat={chat} /></li>
          ))}
        </ol>
      )}
      <div ref={end} />
      {refused && <p role="status" className="label-mono text-signal-ink">{refused}</p>}
      <QuestionBox text={text} onText={setText} onSubmit={submit} disabled={fast || busy || connection !== "connected"}
        hint={fast ? "Set the speed to 1× (real time) to talk to JARVIS" : busy ? "JARVIS is answering…" : connection === "connected" ? "One question at a time" : "Waiting for the gateway"} />
    </section>
  )
}

function QuestionBox({ text, onText, onSubmit, disabled, hint }: {
  text: string; onText: (text: string) => void; onSubmit: (event: FormEvent) => void; disabled: boolean; hint: string
}) {
  return (
    <form onSubmit={onSubmit} className="grid gap-2 border p-3">
      <label htmlFor="question" className="label-mono text-muted-foreground">Your question</label>
      <Textarea
        id="question"
        value={text}
        maxLength={2000}
        onChange={(event) => onText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault()
            event.currentTarget.form?.requestSubmit()
          }
        }}
        placeholder="What's wrong with the HVAC?"
        className="min-h-[72px] text-[14px]"
      />
      <div className="flex items-center justify-between gap-3">
        <span className="label-mono text-muted-foreground">{hint}</span>
        <Button type="submit" size="sm" disabled={disabled || !text.trim()}>
          <Send aria-hidden className="size-3.5" />
          Send
        </Button>
      </div>
    </form>
  )
}

function Entry({ entry, chat }: { entry: ChatEntry; chat: Chat }) {
  switch (entry.kind) {
    case "owner":
      return (
        <div className="border bg-panel px-4 py-3 text-[14px] leading-6">
          <span className="label-mono mr-2 text-muted-foreground">You //</span>
          {entry.text}
        </div>
      )
    case "reply":
      return <ReplyCard reply={entry.reply} chat={chat} />
    case "alert":
      return <AlertCard alert={entry.alert} />
    case "error":
      return (
        <div role="alert" className="flex items-start gap-3 border border-signal px-4 py-3 text-[14px] leading-6">
          <TriangleAlert aria-hidden className="mt-1 size-4 shrink-0 text-signal-ink" />
          <span><span className="label-mono mr-2 text-signal-ink">Chat error //</span>{entry.reason}</span>
        </div>
      )
  }
}

function ReplyCard({ reply, chat }: { reply: Reply; chat: Chat }) {
  const view = replyView(reply)
  const done = reply.done
  const tone = done === null || done.outcome === "answered" ? "" : done.outcome === "fallback" ? "border-l-[3px] border-l-foreground" : "border-l-[3px] border-l-signal"
  return (
    <article className={cn("border px-4 py-3", tone)} aria-busy={view.streaming}>
      <p className="text-[15px] leading-7">
        <span className="label-mono mr-2 text-muted-foreground">JARVIS //</span>
        {view.text || (view.streaming ? "…" : "")}
        {view.streaming && <span aria-hidden className="ml-1 inline-block size-2 bg-signal motion-safe:animate-pulse" />}
      </p>
      {view.lostChunks > 0 && (
        <p className="label-mono mt-2 text-signal-ink">
          {view.lostChunks} piece(s) of this reply did not arrive (seq {view.missingSeqs.join(", ")}){done ? "; the full text above is the final reply" : ""}
        </p>
      )}
      {view.mismatch && <p className="label-mono mt-2 text-signal-ink">The streamed pieces differ from the final reply; the final reply is shown.</p>}
      {done && <DoneFacts done={done} />}
      {done?.confirm && <ConfirmOffer offer={done.confirm} view={chat.state.acks[done.confirm.token]} onConfirm={chat.confirm} />}
    </article>
  )
}

function DoneFacts({ done }: { done: ChatDone }) {
  return (
    <dl className="mt-3 grid gap-x-6 gap-y-1 border-t pt-3 text-[12px] sm:grid-cols-2">
      <Fact label="Outcome" value={OUTCOME_TEXT[done.outcome] + (done.reason ? `: ${done.reason}` : "")} strong={done.outcome !== "answered"} />
      <Fact label="Farm time" value={done.farm_time ?? "none"} />
      <Fact label="Data" value={done.label ?? "no data"} />
      <Fact label="Verified" value={done.outcome === "fallback"
        ? "written from this turn's data by a fixed template, not by the model"
        : `${done.numbers_checked} number${done.numbers_checked === 1 ? "" : "s"} and times checked against this turn's data`} />
      {done.citations.length > 0 && (
        <Fact label="Sources" value={done.citations.map((c) => `${nodeName(c.node)}: ${c.file}${c.section ? `, ${c.section}` : ""}`).join(" · ")} />
      )}
      {done.tools.length > 0 && (
        <Fact label="Asked" value={done.tools.map((t) => `${nodeName(t.node)} ${t.tool.replace("ask_peer:", "")}${t.ok ? "" : " (failed)"}`).join(", ")} />
      )}
      <Fact label="Time" value={`first piece ${done.timings.first_chunk_ms === null ? "none" : `${(done.timings.first_chunk_ms / 1000).toFixed(1)} s`}, whole ${(done.timings.total_ms / 1000).toFixed(1)} s`} />
    </dl>
  )
}

function Fact({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="flex min-w-0 gap-2">
      <dt className="label-mono shrink-0 text-muted-foreground">{label}</dt>
      <dd className={cn("min-w-0 [overflow-wrap:anywhere]", strong && "font-medium text-signal-ink")}>{value}</dd>
    </div>
  )
}

/** The owner's confirm button for one AckOffer, with its countdown (ground rule 3: their words, then this click). */
function ConfirmOffer({ offer, view, onConfirm }: { offer: AckOffer; view: AckView | undefined; onConfirm: (offer: AckOffer) => void }) {
  const [now, setNow] = useState(() => Date.now())
  const left = confirmSecondsLeft(offer, now)
  useEffect(() => {
    if (left <= 0 || (view && view.status !== "failed")) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [left, view])
  return (
    <div className="mt-3 grid gap-2 border bg-panel px-3 py-3">
      <p className="text-[13px] leading-5"><span className="label-mono mr-2 text-muted-foreground">Confirm //</span>{offer.summary}</p>
      {view?.status === "answered" ? (
        <p role="status" className={cn("label-mono", view.response.ok ? "text-foreground" : "text-signal-ink")}>
          {view.response.ok ? "Acknowledged: the Matrix recorded it" : `Not acknowledged (stopped at the ${view.response.stage === "token" ? "confirmation check" : "Matrix"}): ${view.response.reason}`}
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" onClick={() => onConfirm(offer)} disabled={left <= 0 || view?.status === "sending"}>
            {view?.status === "sending" ? "Acknowledging…" : "Confirm acknowledgement"}
          </Button>
          <span className="label-mono text-muted-foreground">{left > 0 ? `${left} s left` : "Expired: ask again"}</span>
          {view?.status === "failed" && <span className="label-mono text-signal-ink">{view.reason}</span>}
        </div>
      )}
    </div>
  )
}

function AlertCard({ alert }: { alert: OwnerAlert }) {
  const danger = alert.level === "DANGER"
  return (
    <div role="alert" className={cn("flex items-start gap-3 border px-4 py-3 text-[14px] leading-6", danger ? "border-transparent bg-danger text-white" : "border-signal")}>
      {danger ? <OctagonAlert aria-hidden className="mt-1 size-4 shrink-0" /> : <TriangleAlert aria-hidden className="mt-1 size-4 shrink-0 text-signal-ink" />}
      <span>
        <span className="label-mono mr-2">{danger ? "Danger" : "Alert"} // {nodeName(alert.node)} // {alert.farm_time}</span>
        {alert.text}
      </span>
    </div>
  )
}

/** Owner alerts at the top of every page, at once (DANGER stands out); each also joins the chat history. */
export function AlertBanner({ chat, onOpen }: { chat: Chat; onOpen: () => void }) {
  if (chat.state.banner.length === 0) return null
  const [newest, ...older] = chat.state.banner
  const danger = newest.level === "DANGER"
  return (
    <div role="alert" aria-live="assertive" className={cn("flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-3 sm:px-6 md:px-10", danger ? "bg-danger text-white" : "bg-signal/15")}>
      {danger ? <OctagonAlert aria-hidden className="size-5 shrink-0" /> : <TriangleAlert aria-hidden className="size-5 shrink-0" />}
      <p className="min-w-0 flex-1 text-[14px] leading-6">
        <span className="label-mono mr-2">{danger ? "Danger" : "JARVIS alert"} // {nodeName(newest.node)} // farm time {newest.farm_time}</span>
        {newest.text}
        {older.length > 0 && <span className="label-mono ml-2">(+{older.length} more)</span>}
      </p>
      <Button size="sm" variant={danger ? "secondary" : "outline"} onClick={onOpen}>Open the chat</Button>
      <button type="button" aria-label="Dismiss this alert" onClick={() => chat.dismissAlert(newest.alert_id)} className="shrink-0 p-1">
        <X className="size-4" />
      </button>
    </div>
  )
}
