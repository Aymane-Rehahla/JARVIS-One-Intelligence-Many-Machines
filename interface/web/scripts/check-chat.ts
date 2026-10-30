// Checks the chat logic (src/services/chat.ts) on recorded message sequences made by the Python contracts (P4
// section 3, tests; run by tests/test_chat_logic.py). Every server message goes through the client's own zod check.
//
// Usage: node scripts/check-chat.ts INPUT.json
// INPUT.json: {"cases": {"<name>": [step, ...]}, "offer": <AckOffer>, "now_ms": N}
// A step is {"send": "<text>", "id": "<client_msg_id>"} or {"raw": "<what the gateway sent>"} or {"disconnect": true}.
// Prints one JSON object: per case, the entries, what the replies show, the banner, the counts.

import { readFileSync } from "node:fs"

import type { AckOffer } from "../src/services/contracts.ts"
import { ChatClient, confirmSecondsLeft, initialChat, reduceChat, replyView, type ChatState } from "../src/services/chat.ts"

type Step = { send?: string; id?: string; raw?: string; disconnect?: boolean }
type Input = { cases: Record<string, Step[]>; offer: AckOffer; now_ms: number }

const path = process.argv[2]
if (!path) {
  console.error("usage: node scripts/check-chat.ts INPUT.json")
  process.exit(2)
}
const input = JSON.parse(readFileSync(path, "utf8")) as Input

function run(steps: Step[]): ChatState {
  let state = initialChat()
  const client = new ChatClient("http://unused", {
    onMessage: (message) => { state = reduceChat(state, { type: "message", message }) },
    onInvalid: () => { state = reduceChat(state, { type: "invalid" }) },
    onConnection: () => undefined,
  })
  for (const step of steps) {
    if (step.send !== undefined) state = reduceChat(state, { type: "send", clientMsgId: step.id ?? "m", text: step.send })
    else if (step.raw !== undefined) client.receive(step.raw)
    else if (step.disconnect) state = reduceChat(state, { type: "disconnected" })
  }
  return state
}

const cases: Record<string, unknown> = {}
for (const [name, steps] of Object.entries(input.cases)) {
  const state = run(steps)
  cases[name] = {
    kinds: state.entries.map((entry) => entry.kind),
    owner: state.entries.flatMap((entry) => (entry.kind === "owner" ? [{ text: entry.text, requestId: entry.requestId }] : [])),
    replies: state.entries.flatMap((entry) => {
      if (entry.kind !== "reply") return []
      const view = replyView(entry.reply)
      return [{ requestId: entry.reply.requestId, ...view, outcome: entry.reply.done?.outcome ?? null,
        joined: entry.reply.pieces.map((piece) => piece.text).join("") }]
    }),
    errors: state.entries.flatMap((entry) => (entry.kind === "error" ? [entry.reason] : [])),
    banner: state.banner.map((alert) => alert.alert_id),
    inFlight: state.inFlight,
    conversationId: state.conversationId,
    invalid: state.invalid,
    late: state.late,
  }
}
const offer = input.offer
console.log(JSON.stringify({
  cases,
  confirm: {
    fresh: confirmSecondsLeft(offer, input.now_ms),
    later: confirmSecondsLeft(offer, input.now_ms + 59_500),
    expired: confirmSecondsLeft(offer, input.now_ms + 61_000),
    unreadable: confirmSecondsLeft({ ...offer, expires_utc: "not a time" }, input.now_ms),
  },
}))
