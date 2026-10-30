import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { useDemoFarm, type DemoFarm } from "@/hooks/use-demo-farm"
import type { FarmSnapshot } from "@/services/contracts"
import { LiveFarmClient, gatewayUrl, injectFault, simAction, type LiveConnection } from "@/services/live"
import { mapSnapshot } from "@/services/live-map"
import { createInitialState, type FarmState, type ScenarioId, type SimulationSpeed } from "@/services/matrix"

/** The pages' farm: the same interface as the demo, plus where it comes from. */
export type Farm = DemoFarm & {
  /** Whether the gateway is answering with fresh farm data right now (live data can be shown). */
  liveAvailable: boolean
  /** Why live data is not shown while live mode is chosen (null when it is shown). */
  liveProblem: string | null
  /** Switch from the demo to the live farm. */
  goLive: () => void
}

const MODE_KEY = "jarvis.mode"
/** At most this many snapshots a second are rendered (the gateway sends at most 5). */
const RENDER_EVERY_MS = 200
/** The gateway sends a snapshot at least every 5 s; silence for this long means it or its feed is gone. */
const SILENT_AFTER_MS = 12_000
/** A running farm whose clock has not moved for this long is stalled (its runtime stopped publishing). */
const STALLED_AFTER_MS = 10_000
const WATCH_EVERY_MS = 1_000

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

type Injected = { scenarioId: ScenarioId; feedId: string; startS: number; endTick: number | null }

/**
 * Chooses the live farm (through the gateway) or the clearly labelled demo. Live is the default; the owner may choose
 * the demo, which stays available. While live is chosen and the gateway is down, silent or its farm stalled, the pages
 * show "not connected" (never the demo, never old values as if live). In live mode the actions go to the gateway;
 * chat, voice and acknowledging events are not wired yet (the pages say "available after 6C").
 */
export function useFarm(): Farm {
  const demo = useDemoFarm()
  const [mode, setMode] = useState<"live" | "demo">(() => storedMode() ?? "live")
  const [connection, setConnection] = useState<LiveConnection>("connecting")
  const [liveState, setLiveState] = useState<FarmState | null>(null)
  const [stale, setStale] = useState<string | null>(null)
  const latest = useRef<FarmSnapshot | null>(null)
  const lastSnapshotAt = useRef(0)
  const lastTick = useRef<{ tick: number | null; at: number }>({ tick: null, at: 0 })
  const invalid = useRef(0)
  const lastError = useRef<string | null>(null)
  const injected = useRef<Injected | null>(null)
  const client = useRef<LiveFarmClient | null>(null)
  const base = useMemo(gatewayUrl, [])
  const disconnected = useMemo(() => createInitialState(), [])

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
        const now = Date.now()
        latest.current = snapshot
        lastSnapshotAt.current = now
        const tick = snapshot.sim?.tick ?? null
        if (tick !== lastTick.current.tick || !snapshot.sim?.running) lastTick.current = { tick, at: now }
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
    // The watchdog: silence from the gateway, or a running farm clock that stopped moving, is not live data.
    const watch = window.setInterval(() => {
      const now = Date.now()
      if (latest.current === null) return
      if (now - lastSnapshotAt.current > SILENT_AFTER_MS) setStale(`no farm data from the gateway for ${Math.round((now - lastSnapshotAt.current) / 1000)} s`)
      else if (latest.current.sim?.running && now - lastTick.current.at > STALLED_AFTER_MS)
        setStale(`the farm clock has not moved for ${Math.round((now - lastTick.current.at) / 1000)} s`)
      else setStale(null)
    }, WATCH_EVERY_MS)
    return () => {
      if (pending !== null) window.clearTimeout(pending)
      window.clearInterval(watch)
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
      if (result.ok) {
        injected.current = { scenarioId, feedId: result.value.feed_id, startS: result.value.time_s, endTick: result.value.end_tick }
      }
      fail(result.ok ? null : `inject: ${result.reason}`)
    },
    [base, fail],
  )

  const liveAvailable = connection === "connected" && liveState !== null && stale === null
  const liveProblem =
    mode !== "live" || liveAvailable
      ? null
      : stale ?? (connection === "connecting" ? "connecting to the gateway" : "the gateway is not answering; retrying")

  const activateDemo = useCallback(() => {
    setMode("demo")
    storeMode("demo")
    demo.activateDemo()
  }, [demo])

  const goLive = useCallback(() => {
    setMode("live")
    storeMode("live")
    if (demo.state.source === "demo" && demo.state.simulation.running) demo.pause()   // no demo clock behind live
    if (connection !== "connected") client.current?.connect()
  }, [connection, demo])

  const retryConnection = useCallback(async () => {
    goLive()
  }, [goLive])

  if (mode === "live") {
    const state = liveAvailable && liveState ? liveState : disconnected
    return {
      state,
      isConnecting: !liveAvailable && connection === "connecting",
      voice: "idle",
      activateDemo,
      retryConnection,
      start: () => void act("start"),
      pause: () => void act("pause"),
      // a reset starts a new farm run (a new feed_id): the injected fault is dropped by the feed filter, not here
      reset: () => void act("reset"),
      setSpeed: (speed: SimulationSpeed) => void act("speed", speed),
      inject: (scenarioId: ScenarioId) => void inject(scenarioId),
      // Acknowledging needs the owner's words and a confirm click (ground rule 3): available after 6C, never here.
      acknowledge: () => undefined,
      acknowledgeAllEvents: () => undefined,
      clearResolved: () => undefined,
      sendMessage: () => undefined,
      clearPersisted: demo.clearPersisted,
      voiceDemo: () => false,
      liveAvailable,
      liveProblem,
      goLive,
    }
  }
  return { ...demo, activateDemo, retryConnection, liveAvailable, liveProblem: null, goLive }
}
