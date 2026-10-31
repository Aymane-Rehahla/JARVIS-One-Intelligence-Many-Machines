// The voice box through the gateway (P4 section 3, item 5): /ws/voice for the real VoiceStatus (zod-checked), and the
// push-to-talk and settings calls. The website never guesses the voice's state: it shows the last status it received.

import { OkResponse, VoiceSettingsResult, VoiceStatus } from "./contracts.ts"
import { backoffMs, type ActionResult, type LiveConnection } from "./live.ts"

const ACTION_TIMEOUT_MS = 8_000

export class VoiceClient {
  private socket: WebSocket | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private attempt = 0
  private closed = false
  private readonly url: string
  private readonly onStatus: (status: VoiceStatus) => void
  private readonly onConnection: (connection: LiveConnection) => void

  constructor(url: string, onStatus: (status: VoiceStatus) => void, onConnection: (connection: LiveConnection) => void) {
    this.url = url
    this.onStatus = onStatus
    this.onConnection = onConnection
  }

  connect(): void {
    this.closed = false
    this.open()
  }

  close(): void {
    this.closed = true
    if (this.timer !== null) clearTimeout(this.timer)
    this.socket?.close()
    this.socket = null
  }

  private open(): void {
    this.onConnection("connecting")
    let socket: WebSocket
    try {
      socket = new WebSocket(`${this.url.replace(/^http/, "ws")}/ws/voice`)
    } catch {
      this.retry()
      return
    }
    this.socket = socket
    socket.onopen = () => {
      this.attempt = 0
      this.onConnection("connected")
    }
    socket.onmessage = (event) => {
      let parsed: unknown
      try {
        parsed = JSON.parse(String(event.data))
      } catch {
        return
      }
      const result = VoiceStatus.safeParse(parsed)
      if (result.success) this.onStatus(result.data)
    }
    socket.onclose = () => {
      if (this.socket === socket) this.retry()
    }
    socket.onerror = () => socket.close()
  }

  private retry(): void {
    this.socket = null
    if (this.closed) return
    this.onConnection("disconnected")
    const wait = backoffMs(this.attempt)
    this.attempt += 1
    this.timer = setTimeout(() => {
      this.timer = null
      if (!this.closed) this.open()
    }, wait)
  }
}

async function post<T>(url: string, body: unknown, check: (json: unknown) => { success: true; data: T } | { success: false }): Promise<ActionResult<T>> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), ACTION_TIMEOUT_MS)
  try {
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: controller.signal })
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

export function pushToTalk(base: string, pressed: boolean): Promise<ActionResult<OkResponse>> {
  return post(`${base}/voice/ptt`, { pressed }, (json) => OkResponse.safeParse(json))
}

export function voiceSettings(base: string, voice: string, speed: number): Promise<ActionResult<VoiceSettingsResult>> {
  return post(`${base}/voice/settings`, { schema_version: 1, type: "voice_settings", voice, speed },
    (json) => VoiceSettingsResult.safeParse(json))
}
