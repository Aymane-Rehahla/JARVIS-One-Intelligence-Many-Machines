import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import type { VoiceStatus } from "@/services/contracts"
import { gatewayUrl, type LiveConnection } from "@/services/live"
import { VoiceClient, pushToTalk, voiceSettings } from "@/services/voice"

export type Voice = {
  status: VoiceStatus | null
  connection: LiveConnection
  voices: string[]
  problem: string | null
  press: () => void
  release: () => void
  choose: (voice: string, speed: number) => void
}

/** The real voice box through the gateway (live mode only): its last VoiceStatus, push-to-talk and settings. */
export function useVoice(enabled: boolean): Voice {
  const [status, setStatus] = useState<VoiceStatus | null>(null)
  const [connection, setConnection] = useState<LiveConnection>("disconnected")
  const [voices, setVoices] = useState<string[]>([])
  const [problem, setProblem] = useState<string | null>(null)
  const base = useMemo(gatewayUrl, [])
  const asked = useRef(false)

  useEffect(() => {
    if (!enabled) return
    const client = new VoiceClient(base, setStatus, setConnection)
    client.connect()
    return () => {
      client.close()
      setStatus(null)
      setConnection("disconnected")
    }
  }, [base, enabled])

  // The voice list comes with a settings answer: ask once with the settings already in force (nothing changes).
  useEffect(() => {
    if (!status || asked.current) return
    asked.current = true
    void voiceSettings(base, status.voice, status.speed).then((result) => {
      if (result.ok) setVoices(result.value.voices)
      else setProblem(result.reason)
    })
  }, [base, status])

  const press = useCallback(() => {
    void pushToTalk(base, true).then((result) => setProblem(result.ok ? (result.value.ok ? null : result.value.reason) : result.reason))
  }, [base])
  const release = useCallback(() => {
    void pushToTalk(base, false).then((result) => setProblem(result.ok ? (result.value.ok ? null : result.value.reason) : result.reason))
  }, [base])
  const choose = useCallback((voice: string, speed: number) => {
    void voiceSettings(base, voice, speed).then((result) => {
      if (!result.ok) setProblem(result.reason)
      else if (!result.value.ok) setProblem(result.value.reason)
      else {
        setProblem(null)
        setVoices(result.value.voices)
      }
    })
  }, [base])

  return { status, connection, voices, problem, press, release, choose }
}
