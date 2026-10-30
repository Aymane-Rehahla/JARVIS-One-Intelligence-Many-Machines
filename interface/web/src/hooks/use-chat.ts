import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react"

import type { AckOffer } from "@/services/contracts"
import { ChatClient, acknowledge, initialChat, newClientMsgId, reduceChat, type ChatState } from "@/services/chat"
import { gatewayUrl, type LiveConnection } from "@/services/live"

export type Chat = {
  state: ChatState
  connection: LiveConnection
  /** Send one question; false when it was not sent (one is in flight, or the gateway is not connected). */
  send: (text: string) => boolean
  /** The owner's confirm click on an offer (the only way the website acknowledges anything). */
  confirm: (offer: AckOffer) => void
  dismissAlert: (alertId: string) => void
}

/**
 * The live chat with JARVIS through the gateway's /ws/chat (P4 section 3). Connected only while `enabled` (live mode);
 * in demo mode the pages use the demo's own labelled preview chat instead.
 */
export function useChat(enabled: boolean): Chat {
  const [state, dispatch] = useReducer(reduceChat, undefined, initialChat)
  const [connection, setConnection] = useState<LiveConnection>("disconnected")
  const client = useRef<ChatClient | null>(null)
  const base = useMemo(gatewayUrl, [])
  const latest = useRef(state)
  latest.current = state

  useEffect(() => {
    if (!enabled) return
    const chat = new ChatClient(base, {
      onMessage: (message) => dispatch({ type: "message", message }),
      onInvalid: () => dispatch({ type: "invalid" }),
      onConnection: (next) => {
        setConnection(next)
        if (next !== "connected") dispatch({ type: "disconnected" })
      },
    })
    client.current = chat
    chat.connect()
    return () => {
      chat.close()
      client.current = null
      setConnection("disconnected")
      dispatch({ type: "disconnected" })
    }
  }, [base, enabled])

  const send = useCallback((text: string) => {
    const chat = client.current
    const now = latest.current
    if (!chat || now.inFlight !== null || !text.trim()) return false
    const clientMsgId = newClientMsgId()
    if (!chat.send(clientMsgId, text.trim(), now.conversationId)) return false
    dispatch({ type: "send", clientMsgId, text })
    return true
  }, [])

  const confirm = useCallback((offer: AckOffer) => {
    const view = latest.current.acks[offer.token]
    if (view && view.status !== "failed") return                      // a token is single-use: one click
    dispatch({ type: "ack_sending", token: offer.token })
    void acknowledge(base, offer).then((result) =>
      dispatch(result.ok
        ? { type: "ack_answered", token: offer.token, response: result.value }
        : { type: "ack_failed", token: offer.token, reason: result.reason }))
  }, [base])

  const dismissAlert = useCallback((alertId: string) => dispatch({ type: "dismiss_alert", alertId }), [])

  return { state, connection, send, confirm, dismissAlert }
}
