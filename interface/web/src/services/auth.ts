// The visitor's sign-in (the gateway's /auth routes, interface/gateway/accounts.py). The website keeps only the
// session token the gateway gave; it never holds a password after sending it, nor the mentor key.

import { gatewayUrl } from "./live.ts"

export type Person = { user_id: string; username: string; nickname: string; role: "user" | "mentor" }
export type AuthResult = { ok: true; person: Person } | { ok: false; reason: string; status: number }

const TOKEN_KEY = "jarvis.session"
export const HEARTBEAT_MS = 15_000

let token: string | null = null
try {
  token = window.localStorage.getItem(TOKEN_KEY)
} catch {
  token = null
}

export function sessionToken(): string | null {
  return token
}

function keep(next: string | null): void {
  token = next
  try {
    if (next) window.localStorage.setItem(TOKEN_KEY, next)
    else window.localStorage.removeItem(TOKEN_KEY)
  } catch {
    // storage blocked: the session lasts until the page closes
  }
}

/** Headers for a gateway request: the given ones plus the session, when there is one. */
export function withAuth(headers?: HeadersInit): HeadersInit {
  const all = new Headers(headers)
  if (token) all.set("Authorization", `Bearer ${token}`)
  return all
}

/** A socket URL carrying the session (a browser socket cannot send headers). */
export function socketUrl(url: string): string {
  return token ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : url
}

async function call(path: string, body?: unknown): Promise<{ status: number; json: Record<string, unknown> | null }> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 12_000)
  try {
    const response = await fetch(`${gatewayUrl()}${path}`, {
      method: "POST",
      headers: withAuth(body === undefined ? undefined : { "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })
    return { status: response.status, json: (await response.json().catch(() => null)) as Record<string, unknown> | null }
  } catch {
    return { status: 0, json: null }
  } finally {
    clearTimeout(timer)
  }
}

function reasonOf(status: number, json: Record<string, unknown> | null): string {
  if (status === 0) return "JARVIS is off: the gateway does not answer."
  const detail = json?.detail
  if (typeof detail === "string") return detail
  if (Array.isArray(detail)) return "Check the fields: one of them is too long or missing."
  return `The gateway answered HTTP ${status}.`
}

async function enter(path: string, body: unknown): Promise<AuthResult> {
  const { status, json } = await call(path, body)
  if (status !== 200 || typeof json?.token !== "string") return { ok: false, reason: reasonOf(status, json), status }
  keep(json.token)
  return { ok: true, person: json.user as Person }
}

export function signUp(fields: { username: string; nickname: string; password: string; confirm: string; mentor_key: string }) {
  return enter("/auth/signup", fields)
}

export function signIn(fields: { username: string; password: string }) {
  return enter("/auth/signin", fields)
}

/** Keeps the seat; a 401 means the seat is gone (signed out elsewhere, idle, or a mentor took it). */
export async function heartbeat(): Promise<AuthResult> {
  if (!token) return { ok: false, reason: "Sign in first.", status: 401 }
  const { status, json } = await call("/auth/heartbeat")
  if (status === 401) keep(null)
  if (status !== 200) return { ok: false, reason: reasonOf(status, json), status }
  return { ok: true, person: json?.user as Person }
}

export async function signOut(): Promise<void> {
  if (token) await call("/auth/signout")
  keep(null)
}
