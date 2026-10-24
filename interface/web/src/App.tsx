import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import {
  AlertTriangle,
  ArrowLeft,
  Bell,
  CheckCircle2,
  ChevronDown,
  CircleDot,
  CircleHelp,
  Clock3,
  Filter,
  Gauge,
  Info,
  LayoutDashboard,
  Menu,
  Network,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Trash2,
  TriangleAlert,
  X,
  Zap,
} from "lucide-react"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Toaster } from "@/components/ui/sonner"
import { ASSESSMENT_TONES, worstAssessment } from "@/lib/assessment"
import { cn } from "@/lib/utils"
import { useDemoFarm } from "@/hooks/use-demo-farm"
import { AssessmentBadge, EmptyState, Notice, PageHeader } from "@/components/farm-ui"
import { JarvisPage } from "@/components/jarvis/jarvis-page"
import { JarvisVoice } from "@/components/jarvis-voice"
import {
  ASSESSMENT_LABELS,
  DEVICE_IDS,
  SCENARIOS,
  SIMULATION_SPEEDS,
  activeIncidentCount,
  deviceById,
  deviceName,
  diagnosisLabel,
  findScenario,
  formatSimTime,
  simulationLabel,
  type Device,
  type DeviceId,
  type EventRecord,
  type EventSeverity,
  type FarmState,
  type JarvisNode,
  type ScenarioId,
  type SimulationSpeed,
} from "@/services/matrix"

// three.js is large; load the 3D farm only when the Simulator page opens.
const FarmScene3D = lazy(() => import("@/components/farm/FarmScene3D").then((module) => ({ default: module.FarmScene3D })))

const navItems = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "simulator", label: "Simulator", icon: Play },
  { id: "devices", label: "Devices", icon: SlidersHorizontal },
  { id: "jarvis", label: "JARVIS", icon: Network },
  { id: "events", label: "Events & Alarms", icon: Bell },
] as const

type PageId = (typeof navItems)[number]["id"]

function App() {
  const farm = useDemoFarm()
  const [page, setPage] = useState<PageId>("overview")
  const [search, setSearch] = useState("")
  const [selectedDeviceId, setSelectedDeviceId] = useState<DeviceId | null>(null)
  const [mobileOpen, setMobileOpen] = useState(false)

  const state = farm.state
  const selectedDevice = selectedDeviceId ? deviceById(state, selectedDeviceId) ?? null : null

  const [eventQuery, setEventQuery] = useState("")

  const navigate = (next: PageId) => {
    setPage(next)
    setMobileOpen(false)
    setSelectedDeviceId(null)
  }

  const openDevice = (device: Device) => {
    setSelectedDeviceId(device.id)
    setPage("devices")
    setMobileOpen(false)
  }

  const handleSearch = (value: string) => {
    setSearch(value)
    if (!value.trim()) setEventQuery("")
  }

  const submitSearch = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalized = search.toLowerCase().trim()
    if (!normalized) return
    const device = state.devices.find((item) => `${item.id} ${item.name} ${item.category}`.toLowerCase().includes(normalized))
    if (device) {
      openDevice(device)
      return
    }
    const matchingEvent = state.events.find((item) => `${item.device} ${item.type} ${item.description}`.toLowerCase().includes(normalized))
    if (matchingEvent || normalized.length > 0) {
      setEventQuery(search)
      setPage("events")
      setMobileOpen(false)
      if (!matchingEvent) toast.info(`No devices or events match ${search}`)
    }
  }

  const pageTitle = navItems.find((item) => item.id === page)?.label ?? "Overview"

  const activateVoice = () => {
    if (!farm.voiceDemo()) {
      toast.info("Voice isn't connected yet", { description: "Use demo mode to preview how JARVIS listens and speaks." })
    }
  }

  return (
    <div className="min-h-svh bg-background text-foreground">
      <Toaster position="bottom-right" richColors />
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[274px] border-r bg-sidebar md:flex md:flex-col">
        <SidebarBrand />
        <Navigation page={page} onNavigate={navigate} />
        <SidebarFooter source={state.source} onClearPersisted={farm.clearPersisted} />
      </aside>
      <div className="md:pl-[274px]">
        <header className="sticky top-0 z-20 border-b bg-white/95 backdrop-blur">
          <div className="flex h-[60px] items-center justify-between gap-3 px-4 md:gap-4 md:px-6">
            <div className="flex min-w-0 items-center gap-3">
              <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
                <SheetTrigger asChild>
                  <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open navigation">
                    <Menu />
                  </Button>
                </SheetTrigger>
                <SheetContent side="left" className="w-[274px] p-0">
                  <SheetHeader className="sr-only">
                    <SheetTitle>JARVIS navigation</SheetTitle>
                  </SheetHeader>
                  <SidebarBrand />
                  <Navigation page={page} onNavigate={navigate} />
                </SheetContent>
              </Sheet>
              <h1 className="truncate text-lg font-semibold tracking-tight">{pageTitle}</h1>
            </div>
            <div className="flex min-w-0 flex-1 items-center justify-end gap-2 md:gap-3">
              <form role="search" onSubmit={submitSearch} className="relative w-full min-w-0 max-w-[304px]">
                <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  aria-label="Search devices and events"
                  value={search}
                  onChange={(event) => handleSearch(event.target.value)}
                  placeholder="Search devices and events"
                  className="h-8 bg-secondary/70 pl-9 pr-8 text-xs"
                />
                {search && <button type="button" aria-label="Clear search" onClick={() => handleSearch("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground"><X className="size-3.5" /></button>}
              </form>
              <JarvisVoice state={farm.voice} available={state.source === "demo"} onActivate={activateVoice} />
            </div>
          </div>
          <StatusStrip state={state} isConnecting={farm.isConnecting} onEvents={() => navigate("events")} />
        </header>
        <main className="min-h-[calc(100vh-101px)] px-5 py-7 md:px-8 md:py-8">
          {page === "overview" && (
            <Overview
              state={state}
              farm={farm}
              onDevice={openDevice}
              onEvents={() => navigate("events")}
              onJarvis={() => navigate("jarvis")}
            />
          )}
          {page === "simulator" && (
            <Simulator
              state={state}
              farm={farm}
              onDevice={openDevice}
            />
          )}
          {page === "devices" && (
            <Devices
              state={state}
              selectedDevice={selectedDevice}
              onSelect={(device) => setSelectedDeviceId(device.id)}
              onBack={() => setSelectedDeviceId(null)}
            />
          )}
          {page === "jarvis" && <JarvisPage state={state} farm={farm} />}
          {page === "events" && (
            <EventsPage state={state} farm={farm} initialQuery={eventQuery} />
          )}
        </main>
      </div>
    </div>
  )
}

export default App

// ---------------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------------

function SidebarBrand() {
  return (
    <div className="flex items-start gap-3 border-b px-5 py-3.5">
      <div className="grid size-9 shrink-0 place-items-center rounded-[3px] bg-[#102238] shadow-sm">
        <div className="relative size-5 text-cyan-300">
          <span className="absolute left-0 top-1 h-3 w-5 -skew-y-[27deg] rounded-sm border-2 border-cyan-300" />
          <span className="absolute left-0 top-2.5 h-3 w-5 -skew-y-[27deg] rounded-sm border-2 border-cyan-300" />
          <span className="absolute left-0 top-0 h-3 w-5 -skew-y-[27deg] rounded-sm border-2 border-cyan-300" />
        </div>
      </div>
      <div className="leading-none">
        <div className="text-sm font-bold text-[#142238]">JARVIS</div>
        <div className="mt-1 text-xs leading-[14px] text-muted-foreground">
          Smart
          <br />
          farm
          <br />
          simulation
        </div>
      </div>
    </div>
  )
}

function Navigation({ page, onNavigate }: { page: PageId; onNavigate: (page: PageId) => void }) {
  return (
    <nav className="space-y-1 px-3 py-4">
      {navItems.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          onClick={() => onNavigate(id)}
          className={cn(
            "flex w-full items-center gap-3 rounded-md px-3 py-3 text-sm font-medium transition-colors",
            page === id
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-muted-foreground hover:bg-secondary hover:text-foreground",
          )}
        >
          <Icon className="size-[17px]" />
          {label}
        </button>
      ))}
    </nav>
  )
}

function SidebarFooter({
  source,
  onClearPersisted,
}: {
  source: FarmState["source"]
  onClearPersisted: () => void
}) {
  return (
    <div className="mt-auto border-t px-6 py-5 text-xs text-muted-foreground">
      <div className="mb-2 flex items-center gap-2">
        <CircleDot className="size-3 text-slate-400" />
        {source === "demo"
          ? "Demo mode active"
          : source === "live"
            ? "Live Matrix"
            : "Not connected"}
      </div>
      {source === "demo" && (
        <Button
          variant="link"
          size="sm"
          onClick={() => {
            onClearPersisted()
            toast.success("Demo state reset", { description: "Persisted demo data cleared from this browser." })
          }}
          className="h-auto -ml-1 p-0 text-xs text-muted-foreground underline-offset-4 hover:text-foreground"
        >
          Reset persisted demo
        </Button>
      )}
    </div>
  )
}

function StatusStrip({ state, isConnecting, onEvents }: { state: FarmState; isConnecting: boolean; onEvents: () => void }) {
  const matrixLabel =
    state.source === "demo"
      ? "Demo mode"
      : state.source === "live"
        ? "Connected"
        : isConnecting
          ? "Connecting..."
          : "Not connected"
  const worst = worstAssessment(state)
  return (
    <div className="flex min-h-[41px] items-center gap-0 overflow-x-auto border-t bg-white px-4 text-[11px] text-muted-foreground md:px-6">
      <StatusItem
        label="Matrix"
        value={matrixLabel}
        tone={state.source === "demo" ? "info" : state.source === "live" ? "good" : "neutral"}
      />
      <StatusItem label="Simulation" value={state.source === "disconnected" ? "Stopped" : simulationLabel(state)} />
      <StatusItem label="Simulated time" value={formatSimTime(state.simulation.timeS)} mono />
      <StatusItem
        label="JARVIS"
        value={state.source === "demo" ? `Demo · ${ASSESSMENT_LABELS[worst].toLowerCase()}` : "Not connected"}
        tone={state.source !== "demo" ? "neutral" : ASSESSMENT_TONES[worst].status}
      />
      <button type="button" onClick={onEvents} className="shrink-0"><StatusItem label="Incidents" value={`${activeIncidentCount(state)} active`} tone={state.events.some((event) => !event.acknowledged && event.severity === "Critical") ? "critical" : state.events.some((event) => !event.acknowledged && event.severity === "Warning") ? "warning" : "neutral"} last /></button>
    </div>
  )
}

function StatusItem({
  label,
  value,
  mono = false,
  tone = "neutral",
  last = false,
}: {
  label: string
  value: string
  mono?: boolean
  tone?: "good" | "info" | "neutral" | "warning" | "critical"
  last?: boolean
}) {
  return (
    <div className={cn("flex shrink-0 items-center gap-2 border-r px-4 first:pl-0", last && "border-0")}>
      <span
        className={cn(
          "size-2 rounded-full",
          tone === "good" ? "bg-emerald-500" : tone === "info" ? "bg-blue-500" : tone === "warning" ? "bg-amber-500" : tone === "critical" ? "bg-red-500" : "bg-slate-400",
        )}
      />
      <span>{label}:</span>
      <span className={cn("font-medium text-foreground", mono && "font-mono")}>{value}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Shared UI atoms
// ---------------------------------------------------------------------------

function SeverityIcon({ severity }: { severity: EventSeverity }) {
  return severity === "Critical" ? (
    <TriangleAlert className="mt-0.5 size-4 shrink-0 text-red-500" />
  ) : severity === "Warning" ? (
    <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-500" />
  ) : (
    <Info className="mt-0.5 size-4 shrink-0 text-blue-500" />
  )
}

function OperationalBadge({ state }: { state: Device["operational"] }) {
  const tone =
    state === "normal"
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : state === "warning"
        ? "bg-amber-50 text-amber-700 border-amber-200"
        : state === "critical"
          ? "bg-red-50 text-red-700 border-red-200"
          : "bg-slate-50 text-slate-600 border-slate-200"
  const label = state === "normal" ? "Normal" : state === "warning" ? "Warning" : state === "critical" ? "Critical" : "Unknown"
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium", tone)}>
      <span
        className={cn(
          "size-1.5 rounded-full",
          state === "normal" ? "bg-emerald-500" : state === "warning" ? "bg-amber-500" : state === "critical" ? "bg-red-500" : "bg-slate-400",
        )}
      />
      {label}
    </span>
  )
}

function DetailRow({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4 border-b py-3 text-xs last:border-0">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className={cn(mono && "font-mono", "text-right")}>{value}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

function Overview({
  state,
  farm,
  onDevice,
  onEvents,
  onJarvis,
}: {
  state: FarmState
  farm: ReturnType<typeof useDemoFarm>
  onDevice: (device: Device) => void
  onEvents: () => void
  onJarvis: () => void
}) {
  const offline = state.source === "disconnected"
  const activeIncidents = activeIncidentCount(state)
  const retryConnection = async () => {
    await farm.retryConnection()
    toast.info("Matrix still unreachable - showing reference data")
  }

  return (
    <div className="mx-auto max-w-[1100px] space-y-6">
      <PageHeader
        title="Systems"
        action={
          offline ? (
            <Button size="sm" onClick={retryConnection} className="gap-2">
              <RefreshCw className="size-3.5" />
              Retry connection
            </Button>
          ) : undefined
        }
      />
      {activeIncidents > 0 && (
        <div className="flex items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-700">
          <span>{activeIncidents} active alarms - {state.events.find((event) => !event.acknowledged && event.severity !== "Info")?.description}</span>
          <Button variant="link" size="sm" onClick={onEvents} className="h-auto p-0 text-xs text-amber-700">Review</Button>
        </div>
      )}
      <Notice
        text={
          state.source === "demo"
            ? "Demo mode is active. Values are simulated reference data — no live farm or JARVIS node is connected."
            : "Matrix isn't connected. Showing reference values. Activate demo mode to explore the interactive simulation."
        }
        action={state.source !== "demo" ? "Use demo mode" : undefined}
        onAction={state.source !== "demo" ? farm.activateDemo : undefined}
      />
      <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
        {state.devices.map((device) => (
          <DeviceCard key={device.id} device={device} onDetails={() => onDevice(device)} />
        ))}
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <SupervisorPanel state={state} onDemo={farm.activateDemo} onOpen={onJarvis} />
        <EventsPanel events={state.events} onViewAll={onEvents} activeIncidents={activeIncidents} />
      </div>
    </div>
  )
}

function DeviceCard({ device, onDetails }: { device: Device; onDetails: () => void }) {
  const flagged = device.metrics.some((metric) => metric.flagged)
  return (
    <Card className={cn("gap-0 border-dashed p-4 shadow-none", device.operational === "critical" ? "border-red-300" : flagged || device.operational === "warning" ? "border-amber-300" : "border-slate-200")}>
      <div className="flex items-center justify-between">
        <div className="font-mono text-[11px] text-muted-foreground">{device.id}</div>
        <OperationalBadge state={device.operational} />
      </div>
      <h3 className="mt-1 text-sm font-semibold">{device.name}</h3>
      <Separator className="my-6" />
      {device.metrics.filter((metric) => metric.primary).map((metric) => <div key={metric.label} className={cn("text-xl font-semibold font-mono", metric.flagged ? device.operational === "critical" ? "text-red-700" : "text-amber-700" : "text-foreground")}>{metric.value}</div>)}
      <div className="mt-4 space-y-2">
        {device.metrics.filter((metric) => !metric.primary).map((metric) => (
          <div key={metric.label} className="flex items-start justify-between gap-3 text-xs">
            <span className="text-muted-foreground">{metric.label}</span>
            <span
              className={cn(
                "text-right font-mono font-medium",
                metric.flagged ? "text-amber-700" : "text-foreground",
              )}
            >
              {metric.value}
            </span>
          </div>
        ))}
      </div>
      <Separator className="my-5" />
      <Button variant="link" onClick={onDetails} className="h-auto justify-start p-0 text-xs">
        Details
      </Button>
    </Card>
  )
}

function SupervisorPanel({ state, onDemo, onOpen }: { state: FarmState; onDemo: () => void; onOpen: () => void }) {
  return (
    <Card className="gap-0 p-5 shadow-none">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">JARVIS nodes</h3>
        <div className="flex items-center gap-3">
          {state.source === "demo" && (
            <Button variant="link" onClick={onOpen} className="h-auto p-0 text-xs">
              Watch them reason
            </Button>
          )}
          <Badge variant="outline" className="text-[11px]">
            {state.source === "demo" ? "Demo script" : "Not connected"}
          </Badge>
        </div>
      </div>
      <Separator className="my-4" />
      {state.source === "demo" ? (
        <div className="space-y-1 text-xs">
          <p className="mb-2 leading-5 text-muted-foreground">
            Each machine carries its own node; together they form one JARVIS. Here they follow a demo script, not the real brains.
          </p>
          {DEVICE_IDS.map((id) => {
            const node = state.jarvis.nodes[id]
            return (
              <div key={id} className="flex items-start justify-between gap-3 border-b py-2.5 last:border-0">
                <div className="min-w-0">
                  <div className="font-medium">{deviceName(id)}</div>
                  <p className="mt-0.5 leading-5 text-muted-foreground">{node.reason}</p>
                </div>
                <AssessmentBadge assessment={node.assessment} />
              </div>
            )
          })}
        </div>
      ) : (
        <>
          <p className="text-xs leading-5 text-muted-foreground">
            This interface cannot confirm device or protection status while disconnected.
          </p>
          <Button variant="link" onClick={onDemo} className="mt-3 h-auto justify-start p-0 text-xs">
            Explore demo mode
          </Button>
        </>
      )}
      <Separator className="my-4" />
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground">Messages to the owner</span>
        <span className="font-mono">{state.source === "demo" ? state.jarvis.announcements : "—"}</span>
      </div>
    </Card>
  )
}

function EventsPanel({
  events,
  onViewAll,
  activeIncidents,
}: {
  events: EventRecord[]
  onViewAll: () => void
  activeIncidents: number
}) {
  return (
    <Card className="gap-0 p-5 shadow-none">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Recent events</h3>
        <Button variant="link" onClick={onViewAll} className="h-auto p-0 text-xs">
          View all
        </Button>
      </div>
      <Separator className="my-4" />
      {events.length === 0 ? (
        <EmptyState
          title="No events yet"
          text="Events appear once the demo simulation runs or a scenario is injected."
          icon={<Bell className="size-5 text-muted-foreground" />}
        />
      ) : (
        <div className="space-y-3">
          {events.slice(0, 4).map((event) => (
            <div key={event.id} className="flex gap-3 text-xs">
              <SeverityIcon severity={event.severity} />
              <div className="min-w-0 flex-1">
                <p className="font-medium">{event.description}</p>
                <p className="mt-1 text-muted-foreground">
                  {event.device} · {event.timestamp}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}
      <Separator className="my-4" />
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground">Event log</span>
        <span className="font-mono text-muted-foreground">
          {events.length} entries · {activeIncidents} active
        </span>
      </div>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Simulator
// ---------------------------------------------------------------------------

function Simulator({
  state,
  farm,
  onDevice,
}: {
  state: FarmState
  farm: ReturnType<typeof useDemoFarm>
  onDevice: (device: Device) => void
}) {
  const [scenario, setScenario] = useState<ScenarioId>("heater_relay_stuck")
  const [confirmInject, setConfirmInject] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)
  const connected = state.source === "live" || state.source === "demo"
  const activeScenario = state.simulation.activeScenario
    ? findScenario(state.simulation.activeScenario)
    : null
  const scenarioElapsed = activeScenario && state.simulation.scenarioStartS !== null
    ? state.simulation.timeS - state.simulation.scenarioStartS
    : 0
  const selectedScenario = findScenario(scenario)

  const handleInject = () => {
    if (activeScenario) {
      toast.warning("A scenario is already running", {
        description: `Wait for ${activeScenario.shortLabel} to finish before injecting another.`,
      })
      return
    }
    farm.inject(scenario)
    toast.success("Demo scenario injected", {
      description: `${selectedScenario?.label}. It plays as the simulated clock runs.`,
    })
    if (!state.simulation.running) {
      toast.message("Simulation is paused", {
        description: "Press Start to advance the scenario timeline.",
      })
    }
  }

  const handleReset = () => {
    farm.reset()
    setConfirmReset(false)
    toast.success("Simulation reset", { description: "All demo state returned to reference values." })
  }

  return (
    <div className="mx-auto max-w-[1100px] space-y-6">
      <PageHeader
        title="Simulation control room"
        action={
          <Badge variant="outline" className="gap-1.5 font-normal">
            <CircleDot className="size-3 text-slate-400" />
            {state.source === "demo" ? "Demo data" : state.source === "live" ? "Live state" : "Not connected"}
          </Badge>
        }
      />
      <div className="grid gap-5 xl:grid-cols-[1.4fr_0.8fr]">
        <Card className="p-5 shadow-none">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="text-xs text-muted-foreground">Simulation run status</div>
              <div className="mt-1 text-xl font-semibold">{state.source === "disconnected" ? "Stopped" : simulationLabel(state)}</div>
            </div>
            <div className="text-right">
              <div className="text-xs text-muted-foreground">Simulated time</div>
              <div className="mt-1 font-mono text-xl font-semibold">{formatSimTime(state.simulation.timeS)}</div>
            </div>
          </div>
          <Separator className="my-5" />
          <div className="grid gap-4 sm:grid-cols-3">
            <Metric label="Active scenario" value={activeScenario ? activeScenario.shortLabel : "None"} icon={<Zap />} />
            <Metric
              label="Scenario time"
              value={activeScenario ? `${formatSimTime(scenarioElapsed)} of ${formatSimTime(activeScenario.durationS)}` : "—"}
              icon={<Gauge />}
            />
            <Metric label="Speed" value={speedLabel(state.simulation.speed)} icon={<Clock3 />} />
          </div>
          <div className="mt-6 flex flex-wrap gap-2">
            <div className="flex items-center gap-2 rounded-md border bg-secondary/30 px-3 py-1.5">
              <span className="text-[11px] text-muted-foreground">Speed</span>
              <Select
                value={String(state.simulation.speed)}
                onValueChange={(value) => farm.setSpeed(Number(value) as SimulationSpeed)}
              >
                <SelectTrigger className="h-7 w-[168px] text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SIMULATION_SPEEDS.map((speed) => (
                    <SelectItem key={speed} value={String(speed)}>{speedLabel(speed)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              size="sm"
              onClick={() => {
                if (state.simulation.running) {
                  farm.pause()
                  toast.message("Simulation paused")
                } else {
                  farm.start()
                  toast.success(state.simulation.timeS > 0 ? "Simulation resumed" : "Simulation started")
                }
              }}
              disabled={!connected}
            >
              {state.simulation.running ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
              {state.simulation.running ? "Pause" : state.simulation.timeS > 0 ? "Resume" : "Start"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setConfirmReset(true)} disabled={!connected}>
              <RotateCcw className="size-3.5" />
              Reset
            </Button>
          </div>
          {!connected && (
            <Notice
              text="Simulation controls are unavailable until Matrix connects. Demo mode supplies an interactive reference state."
              action="Use demo mode"
              onAction={farm.activateDemo}
            />
          )}
          {activeScenario && (
            <div className="mt-5 rounded-md border border-amber-100 bg-amber-50/70 p-3 text-xs leading-5">
              <div className="flex items-center gap-2 font-medium text-amber-800">
                <AlertTriangle className="size-4" />
                {activeScenario.label} · {formatSimTime(scenarioElapsed)} of {formatSimTime(activeScenario.durationS)}
              </div>
              <p className="mt-1 text-muted-foreground">{activeScenario.description}</p>
              {!state.simulation.running && <p className="mt-2 font-medium text-amber-800">Paused - press Start to advance</p>}
            </div>
          )}
        </Card>
        <Card className="p-5 shadow-none">
          <div className="flex items-center gap-2">
            <Zap className="size-4 text-blue-600" />
            <h3 className="text-sm font-semibold">Scenario injection</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">
            Scripted demo faults based on the real scenario library. Each one plays out as the simulated clock runs,
            updating telemetry, events and the JARVIS nodes together.
          </p>
          <Select value={scenario} onValueChange={(value) => setScenario(value as ScenarioId)}>
            <SelectTrigger className="mt-5 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SCENARIOS.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="mt-4 rounded-md bg-secondary/60 p-3 text-xs leading-5 text-muted-foreground">
            <span className="font-medium text-foreground">Selected:</span>{" "}
            {selectedScenario?.description}
          </div>
          <Button onClick={() => setConfirmInject(true)} disabled={!connected || Boolean(activeScenario)} className="mt-4 w-full" size="sm">
            {activeScenario ? "Scenario in progress" : "Inject scenario"}
          </Button>
        </Card>
      </div>
      <Suspense
        fallback={
          <Card className="grid h-[200px] place-items-center p-5 text-xs text-muted-foreground shadow-none">
            Loading the 3D farm…
          </Card>
        }
      >
        <FarmScene3D devices={state.devices} onDevice={onDevice} />
      </Suspense>
      <TimelineCard state={state} />
      <AlertDialog open={confirmInject} onOpenChange={setConfirmInject}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Inject demo scenario?</AlertDialogTitle>
            <AlertDialogDescription>
              This will inject the {selectedScenario?.label} demo scenario into{" "}
              {selectedScenario?.devices.map(deviceName).join(" and ")}. It plays a scripted incident timeline; no real
              backend action is performed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleInject}>Inject</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog open={confirmReset} onOpenChange={setConfirmReset}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reset demo simulation?</AlertDialogTitle>
            <AlertDialogDescription>
              All devices, events, JARVIS activity, and chat will return to reference values. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleReset}>Reset</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function Metric({ label, value, icon }: { label: string; value: string; icon: ReactNode }) {
  return (
    <div className="flex items-center gap-3 rounded-md border bg-secondary/30 p-3">
      <div className="text-muted-foreground [&>svg]:size-4">{icon}</div>
      <div className="min-w-0">
        <div className="text-[11px] text-muted-foreground">{label}</div>
        <div className="mt-1 truncate font-mono text-xs font-medium">{value}</div>
      </div>
    </div>
  )
}

/** Simulated time per real second, for example "60× (1 min per s)". */
function speedLabel(speed: SimulationSpeed): string {
  return `${speed}× (${speed >= 60 ? `${speed / 60} min` : `${speed} s`} per s)`
}

function TimelineCard({ state }: { state: FarmState }) {
  const timeline = useMemo(() => {
    return [...state.events].sort((a, b) => b.timeS - a.timeS).slice(0, 12)
  }, [state.events])
  return (
    <Card className="p-5 shadow-none">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Simulation timeline</h3>
        <span className="font-mono text-[11px] text-muted-foreground">{state.events.length} recorded events</span>
      </div>
      <Separator className="my-4" />
      {timeline.length === 0 ? (
        <EmptyState
          title="No simulation events yet"
          text="Start the simulation and inject a scenario to see its incident timeline."
          icon={<Clock3 className="size-5 text-muted-foreground" />}
        />
      ) : (
        <ol className="space-y-3">
          {timeline.map((event) => (
            <li key={event.id} className="flex gap-3 text-xs">
              <span className="mt-0.5 w-14 shrink-0 font-mono text-muted-foreground">{event.timestamp}</span>
              <SeverityIcon severity={event.severity} />
              <div className="min-w-0 flex-1">
                <span className="font-medium">{event.type}</span>
                <span className="text-muted-foreground"> — {event.device}</span>
                <p className="mt-0.5 text-muted-foreground">{event.description}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}


// ---------------------------------------------------------------------------
// Devices
// ---------------------------------------------------------------------------

function Devices({
  state,
  selectedDevice,
  onSelect,
  onBack,
}: {
  state: FarmState
  selectedDevice: Device | null
  onSelect: (device: Device) => void
  onBack: () => void
}) {
  const [query, setQuery] = useState("")
  const [filter, setFilter] = useState("all")
  const [sort, setSort] = useState<"name" | "category" | "operational">("name")

  const filtered = useMemo(() => {
    const list = state.devices.filter(
      (device) =>
        `${device.id} ${device.name} ${device.category}`.toLowerCase().includes(query.toLowerCase()) &&
        (filter === "all" || device.category === filter),
    )
    list.sort((a, b) => {
      if (sort === "category") return a.category.localeCompare(b.category)
      if (sort === "operational") {
        const order: Record<Device["operational"], number> = { critical: 0, warning: 1, unknown: 2, normal: 3 }
        return order[a.operational] - order[b.operational]
      }
      return a.name.localeCompare(b.name)
    })
    return list
  }, [state.devices, query, filter, sort])

  if (selectedDevice) {
    return (
      <DeviceDetail
        device={selectedDevice}
        node={state.source === "demo" ? state.jarvis.nodes[selectedDevice.id] : null}
        onBack={onBack}
      />
    )
  }

  return (
    <div className="mx-auto max-w-[1100px] space-y-6">
      <PageHeader
        title="Device inventory"
        action={<Badge variant="outline" className="font-normal">{state.devices.length} systems</Badge>}
      />
      <div className="flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search device ID, name, or category"
            className="pl-9"
          />
        </div>
        <Select value={filter} onValueChange={setFilter}>
          <SelectTrigger className="w-full sm:w-[190px]">
            <Filter className="size-3.5 text-muted-foreground" />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All categories</SelectItem>
            <SelectItem value="Incubator">Incubators</SelectItem>
            <SelectItem value="HVAC">HVAC</SelectItem>
            <SelectItem value="Animal protection">Animal protection</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sort} onValueChange={(value) => setSort(value as typeof sort)}>
          <SelectTrigger className="w-full sm:w-[170px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="name">Sort: Name</SelectItem>
            <SelectItem value="category">Sort: Category</SelectItem>
            <SelectItem value="operational">Sort: Status</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <Card className="overflow-hidden shadow-none">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b bg-secondary/40 text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Device</th>
                <th className="px-4 py-3 font-medium">Category</th>
                <th className="px-4 py-3 font-medium">Connection</th>
                <th className="px-4 py-3 font-medium">Operational</th>
                <th className="px-4 py-3 font-medium">Key measurement</th>
                <th className="px-4 py-3 font-medium">Last update</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((device) => (
                <tr key={device.id} className="border-b last:border-0 hover:bg-secondary/30">
                  <td className="px-4 py-4">
                    <button onClick={() => onSelect(device)} className="text-left">
                      <div className="font-medium text-blue-700 hover:underline">{device.name}</div>
                      <div className="mt-1 font-mono text-[10px] text-muted-foreground">{device.id}</div>
                    </button>
                  </td>
                  <td className="px-4 py-4 text-muted-foreground">{device.category}</td>
                  <td className="px-4 py-4">
                    <StateText state={device.connection} />
                  </td>
                  <td className="px-4 py-4">
                    <OperationalBadge state={device.operational} />
                  </td>
                  <td className="px-4 py-4 font-mono">{device.keyMeasurement}</td>
                  <td className="px-4 py-4 text-muted-foreground">{device.lastUpdate}</td>
                  <td className="px-4 py-4 text-right">
                    <Button variant="link" size="sm" onClick={() => onSelect(device)} className="h-auto p-0 text-xs">
                      Inspect
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {filtered.length === 0 && (
          <EmptyState title="No matching devices" text="Try a different device name or category." />
        )}
      </Card>
    </div>
  )
}

function StateText({ state }: { state: Device["connection"] }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-muted-foreground">
      <span
        className={cn(
          "size-1.5 rounded-full",
          state === "connected" || state === "demo"
            ? "bg-emerald-500"
            : "bg-slate-400",
        )}
      />
      {state === "demo" ? "Reference" : state === "disconnected" ? "Offline" : state.charAt(0).toUpperCase() + state.slice(1)}
    </span>
  )
}

function DeviceDetail({ device, node, onBack }: { device: Device; node: JarvisNode | null; onBack: () => void }) {
  return (
    <div className="mx-auto max-w-[1100px] space-y-6">
      <Button variant="ghost" size="sm" onClick={onBack} className="-ml-3 gap-2">
        <ArrowLeft className="size-4" />
        Back to devices
      </Button>
      <div className="flex flex-wrap items-start justify-between gap-4 border-b pb-5">
        <div>
          <div className="font-mono text-xs text-muted-foreground">{device.id}</div>
          <h2 className="mt-1 text-xl font-semibold">{device.name}</h2>
          <p className="mt-2 max-w-xl text-sm text-muted-foreground">{device.detail}</p>
        </div>
        <div className="flex items-center gap-2">
          <OperationalBadge state={device.operational} />
          <Badge variant="outline" className="gap-1.5 font-normal">
            <CircleHelp className="size-3.5" />
            {device.connection === "demo" ? "Demo reference" : "Reference / unavailable"}
          </Badge>
        </div>
      </div>
      <div className="grid gap-5 lg:grid-cols-[1.2fr_0.8fr]">
        <Card className="p-5 shadow-none">
          <h3 className="text-sm font-semibold">Key telemetry</h3>
          <Separator className="my-4" />
          <div className="grid gap-3 sm:grid-cols-2">
            {device.metrics.map((metric) => (
              <div
                key={metric.label}
                className={cn(
                  "rounded-md border bg-secondary/20 p-3",
                  metric.flagged && "border-amber-200 bg-amber-50/50",
                )}
              >
                <div className="text-xs text-muted-foreground">{metric.label}</div>
                <div
                  className={cn(
                    "mt-2 font-mono text-sm font-semibold",
                    metric.flagged ? "text-amber-700" : "text-foreground",
                  )}
                >
                  {metric.value}
                </div>
              </div>
            ))}
          </div>
        </Card>
        <Card className="p-5 shadow-none">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold">JARVIS node</h3>
            {node && <AssessmentBadge assessment={node.assessment} />}
          </div>
          <Separator className="my-4" />
          {node ? (
            <>
              <p className="text-xs leading-5 text-muted-foreground">{node.reason}</p>
              <div className="mt-3">
                <DetailRow label="Diagnosis" value={diagnosisLabel(node.diagnosis)} mono={false} />
                <DetailRow label="Since" value={formatSimTime(node.sinceS)} />
                <DetailRow label="Protective action" value={node.action ?? "None"} mono={false} />
                <DetailRow label="Last message to the owner" value={node.ownerMessage ?? "None"} mono={false} />
                <DetailRow label="Controller" value="Local controller (runs without JARVIS)" mono={false} />
              </div>
            </>
          ) : (
            <p className="text-xs leading-5 text-muted-foreground">
              No JARVIS node is connected. Activate demo mode to see how this machine's node reasons.
            </p>
          )}
        </Card>
      </div>
      <Card className="p-5 shadow-none">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Device notes</h3>
          <Badge variant="outline" className="font-normal">
            {device.category}
          </Badge>
        </div>
        <Separator className="my-4" />
        <p className="text-xs leading-5 text-muted-foreground">
          {device.connection === "demo"
            ? "This device shows labeled demonstration telemetry, using the real farm's defaults. Inject a scenario from the Simulator to see its readings, its JARVIS node and the event log change together."
            : "No live telemetry is available. Activate demo mode to explore an interactive reference for this device."}
        </p>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Events & Alarms
// ---------------------------------------------------------------------------

function EventsPage({
  state,
  farm,
  initialQuery = "",
}: {
  state: FarmState
  farm: ReturnType<typeof useDemoFarm>
  initialQuery?: string
}) {
  const [query, setQuery] = useState(initialQuery)
  const [severity, setSeverity] = useState("all")
  const [device, setDevice] = useState("all")
  const [sort, setSort] = useState<"newest" | "oldest" | "severity">("newest")
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [confirmAcknowledgeAll, setConfirmAcknowledgeAll] = useState(false)
  const selectedRowRef = useRef<HTMLTableRowElement | null>(null)

  // Sync query when a new global search navigates here.
  useEffect(() => {
    setQuery(initialQuery)
  }, [initialQuery])

  // Derive the selected event from current state so acknowledgment and
  // resolution updates reflect immediately without a stale local copy.
  const selected = selectedId ? state.events.find((event) => event.id === selectedId) ?? null : null

  const select = (event: EventRecord | null) => {
    // Closing the details returns keyboard focus to the row that opened them.
    if (!event) selectedRowRef.current?.focus()
    setSelectedId(event ? event.id : null)
  }

  const acknowledgeEverything = () => {
    farm.acknowledgeAllEvents()
    setConfirmAcknowledgeAll(false)
    toast.success("All open events acknowledged")
  }

  const openCount = state.events.filter((event) => !event.acknowledged).length
  const acknowledgedInfoCount = state.events.filter((event) => event.severity === "Info" && event.acknowledged).length
  const criticalOpen = state.events.some((event) => event.severity === "Critical" && !event.acknowledged)

  const severityOrder: Record<EventSeverity, number> = { Critical: 0, Warning: 1, Info: 2 }

  const events = useMemo(() => {
    const list = [...state.events]
      .filter((event) => `${event.type} ${event.device} ${event.description}`.toLowerCase().includes(query.toLowerCase()))
      .filter((event) => severity === "all" || event.severity === severity)
      .filter((event) => device === "all" || event.deviceId === device)
    list.sort((a, b) => {
      if (sort === "oldest") return a.timeS - b.timeS
      if (sort === "severity") return severityOrder[a.severity] - severityOrder[b.severity]
      return b.timeS - a.timeS
    })
    return list
  }, [state.events, query, severity, device, sort])

  const criticalCount = state.events.filter((event) => event.severity === "Critical" && !event.acknowledged).length
  const warningCount = state.events.filter((event) => event.severity === "Warning" && !event.acknowledged).length
  const totalCount = state.events.length
  const ackedCount = state.events.filter((event) => event.acknowledged).length

  return (
    <div className="mx-auto max-w-[1100px] space-y-6">
      <PageHeader
        title="Events & alarms"
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => (criticalOpen ? setConfirmAcknowledgeAll(true) : acknowledgeEverything())} disabled={openCount === 0}>
              <CheckCircle2 className="size-3.5" />
              Acknowledge all
            </Button>
            <Button variant="outline" size="sm" onClick={() => { farm.clearResolved(); toast.success(`Removed ${acknowledgedInfoCount} entries`) }} disabled={acknowledgedInfoCount === 0}>
              <Trash2 className="size-3.5" />
              Clear acknowledged info
            </Button>
          </div>
        }
      />
      <Notice
        text={
          state.source === "demo"
            ? "Demo event log. Entries come from the scripted demo scenarios, not a live Matrix feed."
            : "Matrix is not connected. Activate demo mode and run a scenario to populate the event log."
        }
        action={state.source !== "demo" ? "Use demo mode" : undefined}
        onAction={state.source !== "demo" ? farm.activateDemo : undefined}
      />
      <div className="grid gap-3 sm:grid-cols-4">
        <Summary label="Active critical" value={String(criticalCount)} tone="red" />
        <Summary label="Active warnings" value={String(warningCount)} tone="amber" />
        <Summary label="Total entries" value={String(totalCount)} tone="blue" />
        <Summary label="Acknowledged" value={String(ackedCount)} tone="slate" />
      </div>
      <Card className="p-5 shadow-none">
        <div className="flex flex-col gap-3 lg:flex-row">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search event type, device, or description"
              className="pl-9"
            />
          </div>
          <Select value={severity} onValueChange={setSeverity}>
            <SelectTrigger className="lg:w-[160px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All severity</SelectItem>
              <SelectItem value="Critical">Critical</SelectItem>
              <SelectItem value="Warning">Warning</SelectItem>
              <SelectItem value="Info">Info</SelectItem>
            </SelectContent>
          </Select>
          <Select value={device} onValueChange={setDevice}>
            <SelectTrigger className="lg:w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All devices</SelectItem>
              {state.devices.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={sort} onValueChange={(value) => setSort(value as typeof sort)}>
            <SelectTrigger className="lg:w-[150px]">
              <ChevronDown className="size-3.5 text-muted-foreground" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="newest">Newest first</SelectItem>
              <SelectItem value="oldest">Oldest first</SelectItem>
              <SelectItem value="severity">By severity</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Separator className="my-5" />
        {events.length === 0 ? (
          <EmptyState
            title="No matching events"
            text={
              state.source === "demo"
                ? "No events match the current filters, or the demo simulation hasn't produced any yet."
                : "Matrix is not connected, so there is no event history to display."
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <caption className="sr-only">Farm event history</caption>
              <thead className="border-b text-muted-foreground">
                <tr>
                  <th className="px-2 py-3 font-medium">Severity</th>
                  <th className="px-2 py-3 font-medium">Time</th>
                  <th className="px-2 py-3 font-medium">Event</th>
                  <th className="px-2 py-3 font-medium">Device</th>
                  <th className="px-2 py-3 font-medium">Source</th>
                  <th className="px-2 py-3 font-medium">Status</th>
                  <th className="px-2 py-3"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr
                    key={event.id}
                    ref={selected?.id === event.id ? selectedRowRef : undefined}
                    tabIndex={0}
                    onClick={() => select(event)}
                    onKeyDown={(keyEvent) => {
                      if (keyEvent.target !== keyEvent.currentTarget) return
                      if (keyEvent.key === "Enter" || keyEvent.key === " ") {
                        keyEvent.preventDefault()
                        select(event)
                      }
                    }}
                    className={cn(
                      "cursor-pointer border-b outline-none last:border-0 hover:bg-secondary/30 focus-visible:bg-secondary/40 focus-visible:ring-2 focus-visible:ring-ring/50",
                      selected?.id === event.id && "bg-secondary/40",
                    )}
                  >
                    <td className="px-2 py-3">
                      <span className="flex items-center gap-2">
                        <SeverityIcon severity={event.severity} />
                        {event.severity}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-2 py-3 font-mono text-muted-foreground">{event.timestamp}</td>
                    <td className="px-2 py-3">
                      <div className="font-medium">{event.type}</div>
                      <div className="mt-1 text-muted-foreground">{event.description}</div>
                    </td>
                    <td className="px-2 py-3">{event.device}</td>
                    <td className="px-2 py-3 text-muted-foreground">{event.source}</td>
                    <td className="px-2 py-3">
                      {event.acknowledged ? (
                        <span className="inline-flex items-center gap-1.5 text-emerald-600">
                          <CheckCircle2 className="size-3.5" />
                          Acknowledged
                        </span>
                      ) : (
                        <span className="text-muted-foreground">Open</span>
                      )}
                    </td>
                    <td className="px-2 py-3 text-right">
                      {!event.acknowledged && (
                        <Button
                          variant="link"
                          size="sm"
                          onClick={(event_) => {
                            event_.stopPropagation()
                            farm.acknowledge(event.id)
                          }}
                          className="h-auto p-0 text-xs"
                        >
                          Acknowledge
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {selected && (
        <Card className="border-blue-200 p-5 shadow-none">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold">
                <SeverityIcon severity={selected.severity} />
                {selected.type}
              </div>
              <p className="mt-2 text-xs text-muted-foreground">{selected.description}</p>
            </div>
            <Button variant="ghost" size="icon" aria-label="Close event details" onClick={() => select(null)}>
              <X className="size-4" />
            </Button>
          </div>
          <Separator className="my-4" />
          <div className="grid gap-3 sm:grid-cols-3">
            <DetailRow label="Device" value={selected.device} />
            <DetailRow label="Source" value={selected.source} />
            <DetailRow label="Evidence" value={selected.evidence} />
            <DetailRow label="Simulated time" value={selected.timestamp} />
            <DetailRow label="Status" value={selected.acknowledged ? "Acknowledged" : "Open"} />
          </div>
          {!selected.acknowledged && (
            <div className="mt-4 flex justify-end">
              <Button
                size="sm"
                onClick={() => {
                  farm.acknowledge(selected.id)
                  toast.success("Event acknowledged", { description: selected.type })
                }}
              >
                <CheckCircle2 className="size-3.5" />
                Acknowledge event
              </Button>
            </div>
          )}
        </Card>
      )}
      <AlertDialog open={confirmAcknowledgeAll} onOpenChange={setConfirmAcknowledgeAll}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Acknowledge every open event?</AlertDialogTitle>
            <AlertDialogDescription>
              {criticalCount} critical {criticalCount === 1 ? "event is" : "events are"} still open. Acknowledging marks them
              as seen; it doesn't fix anything on the farm.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={acknowledgeEverything}>Acknowledge all</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function Summary({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone: "red" | "amber" | "blue" | "slate"
}) {
  return (
    <Card className="p-4 shadow-none">
      <div
        className={cn(
          "text-xl font-semibold",
          tone === "red" ? "text-red-600" : tone === "amber" ? "text-amber-600" : tone === "blue" ? "text-blue-600" : "text-slate-600",
        )}
      >
        {value}
      </div>
      <div className="mt-1 text-xs text-muted-foreground">{label}</div>
    </Card>
  )
}
