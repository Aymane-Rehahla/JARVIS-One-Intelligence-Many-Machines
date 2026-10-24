import { useCallback, useEffect, useRef, useState } from "react"

import {
  acknowledgeAll,
  acknowledgeEvent,
  advanceTick,
  clearPersistedDemoState,
  clearResolvedEvents,
  cloneState,
  connectToMatrix,
  createDemoState,
  createInitialState,
  injectScenario,
  loadPersistedDemoState,
  pauseSimulation,
  persistDemoState,
  resetSimulation,
  sendChatMessage,
  setSimulationSpeed,
  startSimulation,
  type FarmState,
  type ScenarioId,
  type SimulationSpeed,
} from "@/services/matrix"

/** One demo step per real second; each step moves the simulated clock by the chosen speed. */
const TICK_INTERVAL_MS = 1000

/** Voice states the indicator shows. In demo mode they're simulated; nothing listens to a microphone. */
export type VoiceState = "idle" | "wake" | "listening" | "speaking"

const WAKE_MS = 900
const LISTEN_MS = 2400
const VOICE_QUESTION = "(voice) Jarvis, what's the status?"

/** Roughly how long Jarvis would take to say `text` aloud. */
function speakingMs(text: string): number {
  return Math.min(9000, Math.max(2000, text.length * 55))
}

export type DemoFarm = {
  state: FarmState
  isConnecting: boolean
  voice: VoiceState
  activateDemo: () => void
  retryConnection: () => Promise<void>
  start: () => void
  pause: () => void
  reset: () => void
  setSpeed: (speed: SimulationSpeed) => void
  inject: (scenarioId: ScenarioId) => void
  acknowledge: (eventId: string) => void
  acknowledgeAllEvents: () => void
  clearResolved: () => void
  sendMessage: (text: string) => void
  clearPersisted: () => void
  /** Simulates saying "Jarvis" and asking for the status. Returns false outside demo mode. */
  voiceDemo: () => boolean
}

export function useDemoFarm(): DemoFarm {
  const [state, setState] = useState<FarmState>(() => {
    const persisted = loadPersistedDemoState()
    return persisted ?? createInitialState()
  })
  const [isConnecting, setIsConnecting] = useState(true)
  const [voice, setVoice] = useState<VoiceState>("idle")
  const tickTimer = useRef<number | null>(null)
  const voiceTimers = useRef<number[]>([])
  const spokenAnnouncements = useRef(state.jarvis.announcements)
  const spokenReply = useRef(lastJarvisReplyId(state))

  // Persist whenever demo state changes.
  useEffect(() => {
    if (state.source === "demo") {
      persistDemoState(state)
    }
  }, [state])

  // Boot connection probe. The Matrix is not integrated in this phase, so
  // connectToMatrix always reports disconnected. We never flip the source
  // to "live" based on a health check alone.
  useEffect(() => {
    let mounted = true
    connectToMatrix().then(() => {
      if (!mounted) return
      setIsConnecting(false)
    })
    return () => {
      mounted = false
    }
  }, [])

  const tick = useCallback(() => {
    setState((current) => advanceTick(current))
  }, [])

  // Run the demo clock only while the demo simulation is running.
  useEffect(() => {
    if (state.source === "demo" && state.simulation.running) {
      tickTimer.current = window.setInterval(tick, TICK_INTERVAL_MS)
      return () => {
        if (tickTimer.current !== null) {
          window.clearInterval(tickTimer.current)
          tickTimer.current = null
        }
      }
    }
    return undefined
  }, [state.source, state.simulation.running, tick])

  const clearVoiceTimers = useCallback(() => {
    voiceTimers.current.forEach((timer) => window.clearTimeout(timer))
    voiceTimers.current = []
  }, [])

  const later = useCallback((run: () => void, ms: number) => {
    voiceTimers.current.push(window.setTimeout(run, ms))
  }, [])

  const speak = useCallback((text: string) => {
    clearVoiceTimers()
    setVoice("speaking")
    later(() => setVoice("idle"), speakingMs(text))
  }, [clearVoiceTimers, later])

  useEffect(() => clearVoiceTimers, [clearVoiceTimers])

  // Jarvis speaks every new message it sends to the owner...
  useEffect(() => {
    const { announcements, lastAnnouncement } = state.jarvis
    if (announcements > spokenAnnouncements.current && lastAnnouncement) speak(lastAnnouncement)
    spokenAnnouncements.current = announcements
  }, [state.jarvis, speak])

  // ...and every new chat reply.
  useEffect(() => {
    const replyId = lastJarvisReplyId(state)
    if (replyId && replyId !== spokenReply.current) {
      const reply = state.chat.find((message) => message.id === replyId)
      if (reply) speak(reply.text)
    }
    spokenReply.current = replyId
  }, [state, speak])

  const voiceDemo = useCallback(() => {
    if (state.source !== "demo") return false
    clearVoiceTimers()
    setVoice("wake")
    later(() => setVoice("listening"), WAKE_MS)
    later(() => setState((current) => sendChatMessage(current, VOICE_QUESTION)), WAKE_MS + LISTEN_MS)
    return true
  }, [state.source, clearVoiceTimers, later])

  const activateDemo = useCallback(() => {
    setState((current) => {
      if (current.source === "demo") return cloneState(current)
      return createDemoState()
    })
  }, [])

  const retryConnection = useCallback(async () => {
    setIsConnecting(true)
    await connectToMatrix()
    setIsConnecting(false)
    // The Matrix is not integrated. Stay disconnected / demo — never flip to live.
  }, [])

  const start = useCallback(() => {
    setState((current) => startSimulation(current))
  }, [])

  const pause = useCallback(() => {
    setState((current) => pauseSimulation(current))
  }, [])

  const reset = useCallback(() => {
    clearVoiceTimers()
    setVoice("idle")
    setState(resetSimulation())
  }, [clearVoiceTimers])

  const setSpeed = useCallback((speed: SimulationSpeed) => {
    setState((current) => setSimulationSpeed(current, speed))
  }, [])

  const inject = useCallback((scenarioId: ScenarioId) => {
    setState((current) => injectScenario(current, scenarioId))
  }, [])

  const acknowledge = useCallback((eventId: string) => {
    setState((current) => acknowledgeEvent(current, eventId))
  }, [])

  const acknowledgeAllEvents = useCallback(() => {
    setState((current) => acknowledgeAll(current))
  }, [])

  const clearResolved = useCallback(() => {
    setState((current) => clearResolvedEvents(current))
  }, [])

  const sendMessage = useCallback((text: string) => {
    setState((current) => sendChatMessage(current, text))
  }, [])

  const clearPersisted = useCallback(() => {
    clearPersistedDemoState()
    clearVoiceTimers()
    setVoice("idle")
    setState(createDemoState())
  }, [clearVoiceTimers])

  return {
    state,
    isConnecting,
    voice,
    activateDemo,
    retryConnection,
    start,
    pause,
    reset,
    setSpeed,
    inject,
    acknowledge,
    acknowledgeAllEvents,
    clearResolved,
    sendMessage,
    clearPersisted,
    voiceDemo,
  }
}

function lastJarvisReplyId(state: FarmState): string | null {
  for (let index = state.chat.length - 1; index >= 0; index -= 1) {
    if (state.chat[index].role === "jarvis") return state.chat[index].id
  }
  return null
}
