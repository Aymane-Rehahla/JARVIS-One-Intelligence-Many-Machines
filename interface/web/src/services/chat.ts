// The chat with JARVIS (P4 section 3, items 1-3): one WebSocket to the gateway's /ws/chat, every message checked with
// zod, and the conversation as a pure reducer (tested by scripts/check-chat.ts). One question at a time; the reply
// grows chunk by chunk; its ChatDone carries the farm time, the label, the numbers verified, the citations, the tools
// and the outcome; a ChatError is shown, never swallowed; an OwnerAlert shows as a banner at once and joins the history.
// Acknowledging an alert is only ever the owner's click on a ChatDone's confirm offer (ground rule 3).

import { AckResponse, ChatServerMessage, LlmFailureSwitches } from "./contracts.ts"
import type { AckOffer, ChatDone, OwnerAlert } from "./contracts.ts"
import { backoffMs, type ActionResult, type LiveConnection } from "./live.ts"

/** Entries kept in the history (the oldest go first). */
export const MAX_ENTRIES = 200
/** Alerts kept for the banner (newest first). */
export const MAX_BANNER = 5
// longer than the gateway's whole acknowledgement (incubator-1 5 s, then the Matrix 5 s): a click that succeeded late
// is never shown as failed (final review, 31 Oct)
const ACTION_TIMEOUT_MS = 12_000

/** One reply as it arrives: its pieces by seq, then its ChatDone. */
export type Reply = {
  requestId: string
  pieces: { seq: number; text: string }[]
  done: ChatDone | null
}

export type ChatEntry =
  | { kind: "owner"; key: string; clientMsgId: string; text: string; requestId: string | null }
  | { kind: "reply"; key: string; reply: Reply }
  | { kind: "alert"; key: string; alert: OwnerAlert }
  | { kind: "error"; key: string; clientMsgId: string | null; requestId: string | null; reason: string }

/** An acknowledgement the owner clicked: in flight, or its answer (the stage reached) or why it failed. */
export type AckView = { status: "sending" } | { status: "answered"; response: AckResponse } | { status: "failed"; reason: string }

export type ChatState = {
  entries: ChatEntry[]
  /** The one question in flight (sending is disabled while it is set). */
  inFlight: { clientMsgId: string; requestId: string | null } | null
  conversationId: string | null
  /** Alerts for the banner, newest first. */
  banner: OwnerAlert[]
  /** Acknowledgements by confirm token. */
  acks: Record<string, AckView>
  /** Messages that failed the zod check (counted, never shown as data). */
  invalid: number
  /** Pieces of a request that is not in flight any more (late, after an error), dropped. */
  late: number
  nextKey: number
}

export type ChatEvent =
  | { type: "send"; clientMsgId: string; text: string }
  | { type: "message"; message: ChatServerMessage }
  | { type: "invalid" }
  | { type: "disconnected" }
  | { type: "dismiss_alert"; alertId: string }
  | { type: "ack_sending"; token: string }
  | { type: "ack_answered"; token: string; response: AckResponse }
  | { type: "ack_failed"; token: string; reason: string }

export function initialChat(): ChatState {
  return { entries: [], inFlight: null, conversationId: null, banner: [], acks: {}, invalid: 0, late: 0, nextKey: 1 }
}

type WithoutKey<T> = T extends unknown ? Omit<T, "key"> : never

function add(state: ChatState, entry: WithoutKey<ChatEntry>): ChatState {
  const entries = [...state.entries, { ...entry, key: `e${state.nextKey}` } as ChatEntry]
  return { ...state, entries: entries.slice(-MAX_ENTRIES), nextKey: state.nextKey + 1 }
}

function withReply(state: ChatState, requestId: string, change: (reply: Reply) => Reply): ChatState {
  const index = state.entries.findIndex((entry) => entry.kind === "reply" && entry.reply.requestId === requestId)
  if (index < 0) {
    const created = add(state, { kind: "reply", reply: { requestId, pieces: [], done: null } })
    return withReply(created, requestId, change)
  }
  const entries = state.entries.slice()
  const entry = entries[index] as Extract<ChatEntry, { kind: "reply" }>
  entries[index] = { ...entry, reply: change(entry.reply) }
  return { ...state, entries }
}

function current(state: ChatState, requestId: string): boolean {
  return state.inFlight !== null && state.inFlight.requestId === requestId
}

/** The conversation's next state. Pure: the same events give the same state. */
export function reduceChat(state: ChatState, event: ChatEvent): ChatState {
  switch (event.type) {
    case "send": {
      if (state.inFlight !== null) return state                      // one question at a time
      const text = event.text.trim()
      if (!text) return state
      const next = add(state, { kind: "owner", clientMsgId: event.clientMsgId, text, requestId: null })
      return { ...next, inFlight: { clientMsgId: event.clientMsgId, requestId: null } }
    }
    case "invalid":
      return { ...state, invalid: state.invalid + 1 }
    case "disconnected": {
      if (state.inFlight === null) return state
      const { clientMsgId, requestId } = state.inFlight
      const next = add(state, { kind: "error", clientMsgId, requestId,
        reason: "the connection to the gateway closed before the reply ended; ask again when it is back" })
      return { ...next, inFlight: null }
    }
    case "dismiss_alert":
      return { ...state, banner: state.banner.filter((alert) => alert.alert_id !== event.alertId) }
    case "ack_sending":
      return { ...state, acks: { ...state.acks, [event.token]: { status: "sending" } } }
    case "ack_answered":
      return { ...state, acks: { ...state.acks, [event.token]: { status: "answered", response: event.response } } }
    case "ack_failed":
      return { ...state, acks: { ...state.acks, [event.token]: { status: "failed", reason: event.reason } } }
    case "message":
      return receive(state, event.message)
  }
}

function receive(state: ChatState, message: ChatServerMessage): ChatState {
  switch (message.type) {
    case "chat_accepted": {
      if (state.inFlight === null || state.inFlight.clientMsgId !== message.client_msg_id) return state
      const entries = state.entries.map((entry) =>
        entry.kind === "owner" && entry.clientMsgId === message.client_msg_id ? { ...entry, requestId: message.request_id } : entry)
      return { ...state, entries, inFlight: { ...state.inFlight, requestId: message.request_id } }
    }
    case "chat_chunk": {
      if (!current(state, message.request_id)) return { ...state, late: state.late + 1 }
      return withReply(state, message.request_id, (reply) => reply.pieces.some((piece) => piece.seq === message.seq)
        ? reply
        : { ...reply, pieces: [...reply.pieces, { seq: message.seq, text: message.text }].sort((a, b) => a.seq - b.seq) })
    }
    case "chat_done": {
      if (!current(state, message.request_id)) return { ...state, late: state.late + 1 }
      const next = withReply(state, message.request_id, (reply) => ({ ...reply, done: message }))
      return { ...next, inFlight: null, conversationId: message.conversation_id }
    }
    case "chat_error": {
      const mine = state.inFlight !== null && (state.inFlight.clientMsgId === message.client_msg_id
        || (message.request_id !== null && state.inFlight.requestId === message.request_id))
      const next = add(state, { kind: "error", clientMsgId: message.client_msg_id, requestId: message.request_id,
        reason: message.reason })
      return mine ? { ...next, inFlight: null } : next
    }
    case "owner_alert": {
      if (state.banner.some((alert) => alert.alert_id === message.alert_id)) return state
      const next = add(state, { kind: "alert", alert: message })
      return { ...next, banner: [message, ...next.banner].slice(0, MAX_BANNER) }
    }
  }
}

/** What a reply shows: the text so far (the ChatDone's text once it is there), and what is missing on the way. */
export function replyView(reply: Reply): { text: string; streaming: boolean; missingSeqs: number[]; lostChunks: number; mismatch: boolean } {
  const joined = reply.pieces.map((piece) => piece.text).join("")
  const highest = reply.pieces.length ? reply.pieces[reply.pieces.length - 1].seq : -1
  const expected = reply.done ? Math.max(reply.done.chunks, highest + 1) : highest + 1
  const have = new Set(reply.pieces.map((piece) => piece.seq))
  const missingSeqs: number[] = []
  for (let seq = 0; seq < expected; seq += 1) if (!have.has(seq)) missingSeqs.push(seq)
  if (!reply.done) return { text: joined, streaming: true, missingSeqs, lostChunks: missingSeqs.length, mismatch: false }
  return {
    text: reply.done.text,
    streaming: false,
    missingSeqs,
    lostChunks: missingSeqs.length,
    mismatch: missingSeqs.length === 0 && joined !== reply.done.text,
  }
}

/** Whole seconds left on a confirm offer at `nowMs` (0 when it has expired or its time cannot be read). */
export function confirmSecondsLeft(offer: AckOffer, nowMs: number): number {
  const expires = Date.parse(offer.expires_utc)
  if (Number.isNaN(expires)) return 0
  return Math.max(0, Math.ceil((expires - nowMs) / 1000))
}

/** A short client message id (the gateway's CLIENT_MSG_ID pattern: letters, digits, _ and -). */
export function newClientMsgId(): string {
  const random = Math.random().toString(36).slice(2, 10)
  return `w-${Date.now().toString(36)}-${random}`
}

export type ChatHandlers = {
  onMessage: (message: ChatServerMessage) => void
  onInvalid: () => void
  onConnection: (connection: LiveConnection) => void
}

/** One /ws/chat connection that reconnects with backoff until closed. */
export class ChatClient {
  private socket: WebSocket | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private attempt = 0
  private closed = false
  private readonly url: string
  private readonly handlers: ChatHandlers

  constructor(url: string, handlers: ChatHandlers) {
    this.url = url
    this.handlers = handlers
  }

  connect(): void {
    this.closed = false
    this.open()
  }

  close(): void {
    this.closed = true
    if (this.timer !== null) clearTimeout(this.timer)
    this.timer = null
    this.socket?.close()
    this.socket = null
  }

  /** Send one ChatSend; false when the socket is not open (nothing was sent). */
  send(clientMsgId: string, text: string, conversationId: string | null): boolean {
    if (this.socket === null || this.socket.readyState !== WebSocket.OPEN) return false
    this.socket.send(JSON.stringify({ schema_version: 1, type: "chat_send", client_msg_id: clientMsgId,
      conversation_id: conversationId, text }))
    return true
  }

  receive(data: unknown): void {
    let parsed: unknown
    try {
      parsed = JSON.parse(String(data))
    } catch {
      parsed = undefined
    }
    const result = ChatServerMessage.safeParse(parsed)
    if (result.success) this.handlers.onMessage(result.data)
    else this.handlers.onInvalid()
  }

  private open(): void {
    this.socket?.close()
    this.handlers.onConnection("connecting")
    let socket: WebSocket
    try {
      socket = new WebSocket(`${this.url.replace(/^http/, "ws")}/ws/chat`)
    } catch {
      this.retry()
      return
    }
    this.socket = socket
    socket.onopen = () => {
      this.attempt = 0
      this.handlers.onConnection("connected")
    }
    socket.onmessage = (event) => this.receive(event.data)
    socket.onclose = () => {
      if (this.socket === socket) this.retry()
    }
    socket.onerror = () => socket.close()
  }

  private retry(): void {
    this.socket = null
    if (this.closed) return
    this.handlers.onConnection("disconnected")
    const wait = backoffMs(this.attempt)
    this.attempt += 1
    this.timer = setTimeout(() => {
      this.timer = null
      if (!this.closed) this.open()
    }, wait)
  }
}

async function request<T>(url: string, init: RequestInit, check: (json: unknown) => { success: true; data: T } | { success: false }): Promise<ActionResult<T>> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), ACTION_TIMEOUT_MS)
  try {
    const response = await fetch(url, { ...init, signal: controller.signal })
    const json: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = (json as { detail?: unknown } | null)?.detail
      return { ok: false, reason: typeof detail === "string" ? detail : `the gateway answered HTTP ${response.status}` }
    }
    const checked = check(json)
    return checked.success ? { ok: true, value: checked.data } : { ok: false, reason: "the gateway's answer is not valid (zod check failed)" }
  } catch (error) {
    return { ok: false, reason: controller.signal.aborted ? "the gateway did not answer in time" : `the gateway is unreachable (${String(error)})` }
  } finally {
    clearTimeout(timer)
  }
}

/** The owner's confirm click: POST /alerts/{node}/{incident_id}/ack with the offer's token and farm run. */
export function acknowledge(base: string, offer: AckOffer): Promise<ActionResult<AckResponse>> {
  return request(`${base}/alerts/${offer.node}/${encodeURIComponent(offer.incident_id)}/ack`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm_token: offer.token, feed_id: offer.feed_id }),
  }, (json) => AckResponse.safeParse(json))
}

/** The mentor's LLM failure switches (test only). */
export type SwitchName = "llm_timeout" | "broken_json" | "silent_peer" | "server_crash"
export const SWITCH_NAMES: readonly SwitchName[] = ["llm_timeout", "broken_json", "silent_peer", "server_crash"]

export function getFailureSwitches(base: string): Promise<ActionResult<LlmFailureSwitches>> {
  return request(`${base}/mentor/llm-failures`, { method: "GET" }, (json) => LlmFailureSwitches.safeParse(json))
}

export function setFailureSwitches(base: string, switches: Record<SwitchName, string[]>): Promise<ActionResult<LlmFailureSwitches>> {
  return request(`${base}/mentor/llm-failures`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(switches),
  }, (json) => LlmFailureSwitches.safeParse(json))
}
