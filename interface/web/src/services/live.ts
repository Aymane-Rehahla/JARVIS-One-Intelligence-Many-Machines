// The gateway client (P1 section C1): one WebSocket to /ws/farm, every message checked with zod, and the farm actions
// (start, pause, reset, speed, the four demo faults) as HTTP calls. The gateway is the website's only door into
// JARVIS; its URL comes from VITE_GATEWAY_URL (default http://127.0.0.1:8010).

import { FarmSnapshot, FaultResult, SimState } from "./contracts.ts"
import type { ScenarioId } from "./matrix.ts"

export const DEFAULT_GATEWAY_URL = "http://127.0.0.1:8010"
/** Reconnect waits 1, 2, 4 ... seconds, never more than this. */
export const MAX_BACKOFF_MS = 30_000
/** Invalid messages are counted always, logged at most once per this interval, and never shown as data. */
const INVALID_LOG_EVERY_MS = 60_000
const ACTION_TIMEOUT_MS = 8_000

export type LiveConnection = "connecting" | "connected" | "disconnected"

export function gatewayUrl(): string {
  const configured = (import.meta.env?.VITE_GATEWAY_URL as string | undefined)?.trim()
  return (configured || DEFAULT_GATEWAY_URL).replace(/\/+$/, "")
}

/** The next reconnect wait after `attempt` failures in a row: 1 s, 2 s, 4 s ... capped at MAX_BACKOFF_MS. */
export function backoffMs(attempt: number): number {
  return Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.max(0, attempt))
}

export type LiveHandlers = {
  onSnapshot: (snapshot: FarmSnapshot) => void
  onConnection: (connection: LiveConnection) => void
  onInvalid: (count: number) => void
}

/** One /ws/farm connection that reconnects with backoff until closed. */
export class LiveFarmClient {
  private socket: WebSocket | null = null
  private timer: number | null = null
  private attempt = 0
  private closed = false
  private invalid = 0
  private lastInvalidLog = -Infinity
  private readonly url: string
  private readonly handlers: LiveHandlers

  constructor(url: string, handlers: LiveHandlers) {
    this.url = url
    this.handlers = handlers
  }

  connect(): void {
    this.closed = false
    if (this.timer !== null) clearTimeout(this.timer)
    this.timer = null
    this.open()
  }

  close(): void {
    this.closed = true
    if (this.timer !== null) clearTimeout(this.timer)
    this.timer = null
    this.socket?.close()
    this.socket = null
  }

  private open(): void {
    this.socket?.close()
    this.handlers.onConnection("connecting")
    let socket: WebSocket
    try {
      socket = new WebSocket(`${this.url.replace(/^http/, "ws")}/ws/farm`)
    } catch {
      this.retry()
      return
    }
    this.socket = socket
    socket.onmessage = (event) => this.receive(event.data)
    socket.onclose = () => {
      if (this.socket === socket) this.retry()
    }
    socket.onerror = () => socket.close()
  }

  private receive(data: unknown): void {
    let parsed: unknown
    try {
      parsed = JSON.parse(String(data))
    } catch {
      parsed = undefined
    }
    const result = FarmSnapshot.safeParse(parsed)
    if (!result.success) {
      this.invalid += 1
      this.handlers.onInvalid(this.invalid)
      const now = Date.now()
      if (now - this.lastInvalidLog >= INVALID_LOG_EVERY_MS) {
        this.lastInvalidLog = now
        console.warn(`gateway: ${this.invalid} invalid farm message(s) dropped`, result.error.issues.slice(0, 3))
      }
      return
    }
    this.attempt = 0
    this.handlers.onConnection("connected")
    this.handlers.onSnapshot(result.data)
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

/** A farm action's outcome: ok, or refused with the gateway's reason (shown to the owner, never swallowed). */
export type ActionResult<T> = { ok: true; value: T } | { ok: false; reason: string }

type Checked<T> = { success: true; data: T } | { success: false }

async function post<T>(url: string, body: unknown, check: (json: unknown) => Checked<T>): Promise<ActionResult<T>> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), ACTION_TIMEOUT_MS)
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })
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

export function simAction(base: string, action: "start" | "pause" | "reset" | "speed", speed?: number): Promise<ActionResult<SimState>> {
  return post(`${base}/sim/${action}`, action === "speed" ? { speed } : undefined, (json) => SimState.safeParse(json))
}

export function injectFault(base: string, scenarioId: ScenarioId): Promise<ActionResult<FaultResult>> {
  return post(`${base}/faults/${scenarioId}`, undefined, (json) => FaultResult.safeParse(json))
}
