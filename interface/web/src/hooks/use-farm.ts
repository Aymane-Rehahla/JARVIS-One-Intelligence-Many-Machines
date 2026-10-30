import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { useDemoFarm, type DemoFarm } from "@/hooks/use-demo-farm"
import type { FarmSnapshot } from "@/services/contracts"
import { LiveFarmClient, gatewayUrl, injectFault, simAction, type LiveConnection } from "@/services/live"
import { mapSnapshot } from "@/services/live-map"
import type { FarmState, ScenarioId, SimulationSpeed } from "@/services/matrix"

/** The pages' farm: the same interface as the demo, plus where it comes from. */
export type Farm = DemoFarm & {
  /** Whether the gateway is answering right now (live data can be shown). */
  liveAvailable: boolean
  /** Switch from the demo to the live farm. */
  goLive: () => void
}

const MODE_KEY = "jarvis.mode"
/** The live chart history and events are bounded in the mapping; at most this many snapshots a second are rendered. */
const RENDER_EVERY_MS = 200

function storedMode(): "live" | "demo" | null {
  try {
    const value = window.localStorage.getItem(MODE_KEY)
    return value === "live" || value === "demo" ? value : null
  } catch {
    return null
  }
}

function storeMode(mode: "live" | "demo"): void {
  try {
    window.localStorage.setItem(MODE_KEY, mode)
  } catch {
    // the choice is a convenience; without storage it lasts for this page only
  }
}

type Injected = { scenarioId: ScenarioId; feedId: string; startS: number }

/**
 * Chooses the live farm (through the gateway) or the clearly labelled demo. Live is used whenever the gateway answers,
 * unless the owner chose the demo; the demo stays available. In live mode the actions go to the gateway; chat, voice
 * and acknowledging events are not wired yet (the pages say "available after 6C").
 */
export function useFarm(): Farm {
  const demo = useDemoFarm()
  const [mode, setMode] = useState<"live" | "demo">(() => storedMode() ?? "live")
  const [connection, setConnection] = useState<LiveConnection>("connecting")
  const [liveState, setLiveState] = useState<FarmState | null>(null)
  const latest = useRef<FarmSnapshot | null>(null)
  const invalid = useRef(0)
  const lastError = useRef<string | null>(null)
  const injected = useRef<Injected | null>(null)
  const client = useRef<LiveFarmClient | null>(null)
  const base = useMemo(gatewayUrl, [])

  const render = useCallback(() => {
    const snapshot = latest.current
    if (!snapshot) return
    setLiveState((previous) =>
      mapSnapshot(snapshot, previous, { invalidMessages: invalid.current, lastError: lastError.current, injected: injected.current }),
    )
  }, [])

  useEffect(() => {
    let pending: number | null = null
    const live = new LiveFarmClient(base, {
      onSnapshot: (snapshot) => {
        latest.current = snapshot
        if (pending === null) {
          pending = window.setTimeout(() => {
            pending = null
            render()
          }, RENDER_EVERY_MS)
        }
      },
      onConnection: setConnection,
      onInvalid: (count) => {
        invalid.current = count
      },
    })
    client.current = live
    live.connect()
    return () => {
      if (pending !== null) window.clearTimeout(pending)
      live.close()
      client.current = null
    }
  }, [base, render])

  const fail = useCallback(
    (reason: string | null) => {
      lastError.current = reason
      render()
    },
    [render],
  )

  const act = useCallback(
    async (action: "start" | "pause" | "reset" | "speed", speed?: number) => {
      const result = await simAction(base, action, speed)
      fail(result.ok ? null : `${action}: ${result.reason}`)
    },
    [base, fail],
  )

  const inject = useCallback(
    async (scenarioId: ScenarioId) => {
      const result = await injectFault(base, scenarioId)
      if (result.ok) injected.current = { scenarioId, feedId: result.value.feed_id, startS: result.value.time_s }
      fail(result.ok ? null : `inject: ${result.reason}`)
    },
    [base, fail],
  )

  const liveAvailable = connection === "connected" && liveState !== null
  const useLive = mode === "live" && liveAvailable

  const activateDemo = useCallback(() => {
    setMode("demo")
    storeMode("demo")
    demo.activateDemo()
  }, [demo])

  const goLive = useCallback(() => {
    setMode("live")
    storeMode("live")
    client.current?.connect()
  }, [])

  const retryConnection = useCallback(async () => {
    goLive()
    await demo.retryConnection()
  }, [demo, goLive])

  if (useLive && liveState) {
    return {
      ...demo,
      state: liveState,
      isConnecting: false,
      voice: "idle",
      activateDemo,
      retryConnection,
      start: () => void act("start"),
      pause: () => void act("pause"),
      reset: () => {
        injected.current = null
        void act("reset")
      },
      setSpeed: (speed: SimulationSpeed) => void act("speed", speed),
      inject: (scenarioId: ScenarioId) => void inject(scenarioId),
      // Acknowledging needs the owner's words and a confirm click (ground rule 3): available after 6C, never here.
      acknowledge: () => undefined,
      acknowledgeAllEvents: () => undefined,
      clearResolved: () => undefined,
      sendMessage: () => undefined,
      voiceDemo: () => false,
      liveAvailable,
      goLive,
    }
  }
  return {
    ...demo,
    // Still trying the gateway: say so, unless the owner is in the demo already.
    isConnecting: demo.state.source === "demo" ? demo.isConnecting : connection === "connecting" || demo.isConnecting,
    activateDemo,
    retryConnection,
    liveAvailable,
    goLive,
  }
}
