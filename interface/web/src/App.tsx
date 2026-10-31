import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import {
  ArrowLeft,
  ArrowRight,
  Bell,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  Filter,
  Info,
  LayoutDashboard,
  Menu,
  MessageSquare,
  Network,
  OctagonAlert,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Trash2,
  TriangleAlert,
  X,
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
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Toaster } from "@/components/ui/sonner"
import { ASSESSMENT_TONES, worstAssessment } from "@/lib/assessment"
import { cn } from "@/lib/utils"
import { useFarm, type Farm } from "@/hooks/use-farm"
import { useChat } from "@/hooks/use-chat"
import { useVoice } from "@/hooks/use-voice"
import { AlertBanner, ChatPage } from "@/components/chat/chat-page"
import type { ChatState } from "@/services/chat"
import { LlmSwitchesPanel } from "@/components/chat/llm-switches"
import { AssessmentBadge, EmptyState, Kicker, Notice, PageHeader } from "@/components/farm-ui"
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
  { id: "jarvis", label: "JARVIS", icon: Network },
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "simulator", label: "Simulator", icon: Play },
  { id: "devices", label: "Devices", icon: SlidersHorizontal },
  { id: "events", label: "Events & alarms", icon: Bell },
] as const

type PageId = (typeof navItems)[number]["id"]

function App() {
  const farm = useFarm()
  const [page, setPage] = useState<PageId>("overview")
  const [search, setSearch] = useState("")
  const [selectedDeviceId, setSelectedDeviceId] = useState<DeviceId | null>(null)
  const [mobileOpen, setMobileOpen] = useState(false)

  const state = farm.state
  const chat = useChat(state.source === "live")
  const voice = useVoice(state.source === "live")
  const liveVoice = state.source === "live" ? voice.status : null
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


  const activateVoice = () => {
    if (!farm.voiceDemo()) {
      toast.info("Voice isn't connected yet", {
        description: state.source === "live" ? "The voice comes in step 6E; ask in writing on the Chat page. The demo mode previews how JARVIS listens and speaks." : "Use demo mode to preview how JARVIS listens and speaks.",
      })
    }
  }

  return (
    <div className="relative min-h-svh bg-frame text-foreground">
      <div aria-hidden className="frame-stripes pointer-events-none absolute inset-x-0 top-0 h-[260px]" />
      <Toaster position="bottom-right" />
      <div className="relative mx-auto max-w-[1680px] md:px-6 md:pt-10 md:pb-6">
        <div className="min-h-svh border-frame-raised bg-background md:min-h-[calc(100svh-4rem)] md:border">
          <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur-sm">
            <div className="flex h-[60px] items-center gap-3 border-b px-4 md:px-6">
              <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
                <SheetTrigger asChild>
                  <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open navigation">
                    <Menu />
                  </Button>
                </SheetTrigger>
                <SheetContent side="left" className="w-[300px] gap-0 border-r-foreground p-0">
                  <SheetHeader className="sr-only">
                    <SheetTitle>JARVIS navigation</SheetTitle>
                  </SheetHeader>
                  <div className="flex h-[60px] items-center border-b px-5"><BrandMark /></div>
                  <form role="search" onSubmit={submitSearch} className="relative border-b p-4 sm:hidden">
                    <Search aria-hidden className="absolute left-7 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      aria-label="Search devices and events"
                      value={search}
                      onChange={(event) => handleSearch(event.target.value)}
                      placeholder="Search devices, events"
                      className="h-10 pl-9 font-mono text-[13px]"
                    />
                  </form>
                  <TabNavigation page={page} onNavigate={navigate} />
                </SheetContent>
              </Sheet>
              <button type="button" onClick={() => navigate("overview")} aria-label="JARVIS, overview" className="shrink-0">
                <BrandMark />
              </button>
              <nav aria-label="Main" className="ml-auto hidden h-full items-stretch lg:flex">
                {navItems.map(({ id, label }) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => navigate(id)}
                    aria-current={page === id ? "page" : undefined}
                    className={cn(
                      "relative flex items-center gap-2 px-4 text-[13px] font-medium uppercase tracking-[0.04em] transition-colors duration-150",
                      page === id ? "text-foreground" : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {page === id && <span aria-hidden className="size-2 bg-signal" />}
                    {label}
                  </button>
                ))}
              </nav>
              <div className="ml-auto flex min-w-0 items-center gap-2 lg:ml-4">
                <form role="search" onSubmit={submitSearch} className="relative hidden w-[220px] min-w-0 sm:block xl:w-[260px]">
                  <Search aria-hidden className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    aria-label="Search devices and events"
                    value={search}
                    onChange={(event) => handleSearch(event.target.value)}
                    placeholder="Search devices, events"
                    className="h-9 pl-9 pr-8 font-mono text-[12px] tracking-[0.02em]"
                  />
                  {search && <button type="button" aria-label="Clear search" onClick={() => handleSearch("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"><X className="size-3.5" /></button>}
                </form>
                <JarvisVoice
                  state={liveVoice ? (liveVoice.state === "listening" || liveVoice.state === "speaking" ? liveVoice.state : "idle") : farm.voice}
                  available={state.source === "demo" || (liveVoice !== null && liveVoice.state !== "off")}
                  label={liveVoice ? liveVoice.state : undefined}
                  onActivate={state.source === "live" ? () => navigate("chat") : activateVoice}
                />
              </div>
            </div>
            <SystemBar state={state} isConnecting={farm.isConnecting} liveAvailable={farm.liveAvailable} onGoLive={farm.goLive} onEvents={() => navigate("events")} />
            <AlertBanner chat={chat} onOpen={() => navigate("chat")} />
          </header>
          <main className="px-4 py-8 sm:px-6 md:px-10 md:py-12">
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
            {page === "jarvis" && <JarvisPage state={state} farm={farm} lastReply={lastReply(chat.state)} />}
            {page === "chat" && <ChatPage state={state} farm={farm} chat={chat} voice={voice} />}
            {page === "events" && (
              <EventsPage state={state} farm={farm} initialQuery={eventQuery} />
            )}
          </main>
          <SiteFooter source={state.source} onNavigate={navigate} onClearPersisted={farm.clearPersisted} />
        </div>
      </div>
    </div>
  )
}

export default App

/** incubator-1's last finished reply, for the JARVIS page (the words, shown apart from the reasoning). */
function lastReply(chat: ChatState): { text: string; farmTime: string | null; outcome: string } | null {
  for (let index = chat.entries.length - 1; index >= 0; index -= 1) {
    const entry = chat.entries[index]
    if (entry.kind === "reply" && entry.reply.done) {
      return { text: entry.reply.done.text, farmTime: entry.reply.done.farm_time, outcome: entry.reply.done.outcome }
    }
  }
  return null
}

// ---------------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------------

/** The wordmark: "jarvis" reversed out of a dark block. The product is JARVIS; the farm is only its test bench. */
function BrandMark({ inverse = false }: { inverse?: boolean }) {
  return (
    <span className="flex items-center gap-1.5 leading-none">
      <span className={cn("px-1.5 pb-[3px] pt-[2px] text-[17px] font-bold lowercase [font-stretch:125%]", inverse ? "bg-background text-foreground" : "bg-foreground text-background")}>jarvis</span>
    </span>
  )
}

/** The vertical tab list from the architecture section: [01 // OVERVIEW], active tab dark with an orange edge. */
function TabNavigation({ page, onNavigate }: { page: PageId; onNavigate: (page: PageId) => void }) {
  return (
    <nav aria-label="Main">
      {navItems.map(({ id, label }, index) => (
        <button
          key={id}
          type="button"
          onClick={() => onNavigate(id)}
          aria-current={page === id ? "page" : undefined}
          className={cn(
            "flex h-14 w-full items-center border-b border-l-[3px] px-5 text-left font-mono text-[12px] uppercase tracking-[0.08em] transition-colors duration-150",
            page === id ? "border-l-signal bg-foreground text-background" : "border-l-transparent text-foreground hover:bg-secondary",
          )}
        >
          [{String(index + 1).padStart(2, "0")} // {label}]
        </button>
      ))}
    </nav>
  )
}

function SiteFooter({
  source,
  onNavigate,
  onClearPersisted,
}: {
  source: FarmState["source"]
  onNavigate: (page: PageId) => void
  onClearPersisted: () => void
}) {
  const [confirmReset, setConfirmReset] = useState(false)
  return (
    <footer className="relative overflow-hidden bg-frame text-background">
      <div className="grid gap-10 px-4 py-12 sm:px-6 md:grid-cols-[minmax(0,1.4fr)_repeat(2,minmax(0,1fr))] md:px-10">
        <div>
          <BrandMark inverse />
          <p className="mt-5 max-w-sm text-[14px] leading-6 text-[#b4b4b4]">
            One node on every machine. Together they watch the farm, decide what matters and tell the owner.
          </p>
        </div>
        <div>
          <div className="label-mono text-[#8f8f8f]">Explore</div>
          <ul className="mt-4 space-y-2.5 text-[14px]">
            {navItems.map(({ id, label }) => (
              <li key={id}><button type="button" onClick={() => onNavigate(id)} className="text-[#dcdcdc] underline-offset-4 hover:text-white hover:underline">{label}</button></li>
            ))}
          </ul>
        </div>
        <div>
          <div className="label-mono text-[#8f8f8f]">This browser</div>
          <p className="mt-4 text-[14px] leading-6 text-[#dcdcdc]">
            {source === "demo" ? "Demo simulation running from data saved in this browser." : source === "live" ? "Connected to the live farm." : "Not connected to a farm."}
          </p>
          {source === "demo" && (
            <button type="button" onClick={() => setConfirmReset(true)} className="label-mono mt-4 text-white underline decoration-signal underline-offset-4">
              Reset saved demo
            </button>
          )}
        </div>
      </div>
      <div aria-hidden className="pointer-events-none select-none px-4 text-[clamp(5rem,19vw,17rem)] font-bold lowercase leading-[0.78] text-[#232323] [font-stretch:125%] sm:px-6 md:px-10">jarvis</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[#2e2e2e] px-4 py-4 sm:px-6 md:px-10">
        <span className="label-mono text-[#8f8f8f]">Simulated farm · not live hardware</span>
        <span className="label-mono flex items-center gap-2 text-[#dcdcdc]">
          <span aria-hidden className={cn("size-2", source === "disconnected" ? "bg-[#8f8f8f]" : "bg-ok")} />
          {source === "demo" ? "Demo systems running" : source === "live" ? "Live farm connected" : "Not connected"}
        </span>
      </div>
      <AlertDialog open={confirmReset} onOpenChange={setConfirmReset}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reset the saved demo?</AlertDialogTitle>
            <AlertDialogDescription>
              The demo devices, events, scenario progress and chat are cleared from this browser and start again from reference values.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                onClearPersisted()
                toast.success("Saved demo reset")
              }}
            >
              Reset demo
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </footer>
  )
}

/** The status line under the nav, written like the reference's metadata: LABEL // VALUE. */
function SystemBar({
  state,
  isConnecting,
  liveAvailable,
  onGoLive,
  onEvents,
}: {
  state: FarmState
  isConnecting: boolean
  liveAvailable: boolean
  onGoLive: () => void
  onEvents: () => void
}) {
  const connection =
    state.source === "demo"
      ? "Demo mode"
      : state.source === "live"
        ? state.live?.label ?? "Live · no farm data yet"
        : isConnecting
          ? "Connecting…"
          : "Not connected"
  const worst = worstAssessment(state)
  const incidents = activeIncidentCount(state)
  const incidentTone = state.events.some((event) => !event.acknowledged && event.severity === "Critical") ? "critical" : state.events.some((event) => !event.acknowledged && event.severity === "Warning") ? "warning" : "neutral"
  return (
    <div className="flex min-h-[40px] items-stretch overflow-x-auto bg-panel">
      <SystemItem label="Connection" value={connection} tone={state.source === "demo" ? "info" : state.source === "live" ? "good" : "neutral"} />
      <SystemItem label="Simulation" value={state.source === "disconnected" ? "Stopped" : simulationLabel(state)} />
      <SystemItem
        label="Farm time"
        value={
          state.source === "live" && state.live
            ? state.live.farmTime === null ? "No data" : `${state.live.farmTime} · day ${state.live.day ?? "?"}`
            : formatSimTime(state.simulation.timeS)
        }
      />
      {state.source === "live" && state.live && (
        <SystemItem
          label="Speed"
          value={state.live.targetSpeed === null || state.live.actualSpeed === null ? "No data" : `asked ${state.live.targetSpeed}× · reached ${state.live.actualSpeed.toFixed(1)}×`}
        />
      )}
      <SystemItem
        label="JARVIS"
        value={state.source === "disconnected" ? "Not connected" : worst === null ? "No conclusion yet" : ASSESSMENT_LABELS[worst]}
        tone={state.source === "disconnected" || worst === null ? "neutral" : ASSESSMENT_TONES[worst].status}
      />
      {state.source === "demo" && liveAvailable && (
        <button type="button" onClick={onGoLive} className="flex shrink-0 items-stretch hover:bg-secondary">
          <SystemItem label="Live farm" value="Switch to live" tone="good" />
        </button>
      )}
      <button type="button" onClick={onEvents} className="flex shrink-0 items-stretch hover:bg-secondary">
        <SystemItem label="Incidents" value={`${incidents} open`} tone={incidentTone} last />
      </button>
    </div>
  )
}

function SystemItem({
  label,
  value,
  tone = "neutral",
  last = false,
}: {
  label: string
  value: string
  tone?: "good" | "info" | "neutral" | "warning" | "critical"
  last?: boolean
}) {
  return (
    <div className={cn("flex shrink-0 items-center gap-2 border-r px-4 md:px-6", last && "border-r-0")}>
      <span
        aria-hidden
        className={cn(
          "size-2",
          tone === "good" ? "bg-ok" : tone === "info" ? "bg-info" : tone === "warning" ? "bg-signal" : tone === "critical" ? "bg-danger" : "bg-input",
        )}
      />
      <span className="label-mono whitespace-nowrap text-muted-foreground">{label} //</span>
      <span className="label-mono whitespace-nowrap font-semibold text-foreground">{value}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Shared UI atoms
// ---------------------------------------------------------------------------

/** Critical, warning and info differ in shape as well as colour. */
function SeverityIcon({ severity }: { severity: EventSeverity }) {
  return severity === "Critical" ? (
    <OctagonAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-danger" />
  ) : severity === "Warning" ? (
    <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-watch-ink" />
  ) : (
    <Info aria-hidden className="mt-0.5 size-4 shrink-0 text-info" />
  )
}

function OperationalBadge({ state }: { state: Device["operational"] }) {
  const tone =
    state === "normal"
      ? "border-ok/50 text-ok-ink"
      : state === "warning"
        ? "border-signal text-signal-ink"
        : state === "critical"
          ? "border-danger bg-danger text-white"
          : "border-input text-muted-foreground"
  const label = state === "normal" ? "Normal" : state === "warning" ? "Warning" : state === "critical" ? "Critical" : "Unknown"
  return (
    <span className={cn("inline-flex items-center gap-1.5 border bg-panel px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.06em]", state === "critical" && "bg-danger", tone)}>
      <span
        aria-hidden
        className={cn(
          "size-1.5",
          state === "normal" ? "bg-ok" : state === "warning" ? "bg-signal" : state === "critical" ? "bg-white" : "bg-input",
        )}
      />
      {label}
    </span>
  )
}

function DetailRow({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4 border-b py-3 last:border-0">
      <span className="label-mono shrink-0 text-muted-foreground">{label}</span>
      <span className={cn("text-right text-[13px]", mono && "font-mono tabular-nums")}>{value}</span>
    </div>
  )
}

/** A bordered section of the sheet with a wide title and optional meta. */
function Panel({ title, meta, action, className, children }: { title: string; meta?: ReactNode; action?: ReactNode; className?: string; children: ReactNode }) {
  return (
    <section className={cn("border bg-background", className)}>
      <div className="flex min-h-12 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b px-5 py-3">
        <h2 className="display-sm text-[13px]">{title}</h2>
        <div className="flex items-center gap-3">
          {meta && <span className="label-mono text-muted-foreground">{meta}</span>}
          {action}
        </div>
      </div>
      <div className="p-5">{children}</div>
    </section>
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
  farm: Farm
  onDevice: (device: Device) => void
  onEvents: () => void
  onJarvis: () => void
}) {
  const offline = state.source === "disconnected"
  const activeIncidents = activeIncidentCount(state)
  const firstAlarm = state.events.find((event) => !event.acknowledged && event.severity !== "Info")
  const retryConnection = async () => {
    await farm.retryConnection()
    toast.info("Trying the gateway again. Live data appears as soon as it answers.")
  }

  return (
    <div className="mx-auto max-w-[1400px] space-y-10">
      <PageHeader
        index="01"
        kicker="JARVIS"
        title={<>Four machines.<br />One JARVIS.</>}
        action={
          offline ? (
            <Button size="sm" onClick={retryConnection}>
              <RefreshCw className="size-3.5" />
              Retry connection
            </Button>
          ) : (
            <Button size="sm" onClick={onJarvis}>
              Meet JARVIS
              <ArrowRight className="size-3.5" />
            </Button>
          )
        }
      />
      <section aria-label="What JARVIS is" className="grid gap-6 border-b pb-8 md:grid-cols-3">
        <p className="text-[16px] leading-7 md:col-span-2">
          JARVIS is one mind that lives in machines. Every machine carries its own node, and together the nodes are one
          JARVIS that keeps thinking: each node's Continuous Thought Machine thinks every second, carries its thought on
          to the next, and notices, weighs and concludes before anyone asks. Its memory records what it concluded; its
          language model and voice talk to you. They talk; they never act. A safety gate outside the brain checks the
          evidence before any action.
        </p>
        <p className="text-[14px] leading-6 text-muted-foreground">
          <span className="label-mono mr-2 text-foreground">The test bench //</span>
          The four machines below run in the Matrix, a physics simulation built to test JARVIS: faults happen there,
          and JARVIS has to catch them.
        </p>
      </section>
      {activeIncidents > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-l-[3px] border-l-signal bg-foreground px-5 py-4 text-background">
          <p className="min-w-0 text-[14px] leading-6">
            <span className="label-mono mr-3 text-[#b4b4b4]">{activeIncidents} open {activeIncidents === 1 ? "alarm" : "alarms"} //</span>
            {firstAlarm?.description}
          </p>
          <Button size="sm" variant="outline" onClick={onEvents} className="border-background text-background hover:bg-background hover:text-foreground">
            Review alarms
          </Button>
        </div>
      )}
      <Notice
        text={
          state.source === "demo"
            ? "Demo mode: values are simulated reference data. No live farm or JARVIS node is connected."
            : state.source === "live"
              ? `${state.live?.label ?? "Live"}: the machines' own readings from the Matrix test bench, farm time ${state.live?.farmTime ?? "unknown"}. Each node's conclusion comes from its reasoning brain, shown apart from any words.`
              : `Not connected to the live farm${farm.liveProblem ? ` (${farm.liveProblem})` : ""}. Nothing shown here is a reading. Activate demo mode to explore the interactive simulation.`
        }
        action={state.source === "live" ? "Use demo mode" : state.source !== "demo" ? "Use demo mode" : undefined}
        onAction={state.source !== "demo" ? farm.activateDemo : undefined}
      />
      <div className="grid border-l border-t sm:grid-cols-2 xl:grid-cols-4">
        {state.devices.map((device, index) => (
          <DeviceCard key={device.id} index={index + 1} device={device} onDetails={() => onDevice(device)} />
        ))}
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <SupervisorPanel state={state} onDemo={farm.activateDemo} onOpen={onJarvis} />
        <EventsPanel events={state.events} onViewAll={onEvents} activeIncidents={activeIncidents} />
      </div>
    </div>
  )
}

function DeviceCard({ index, device, onDetails }: { index: number; device: Device; onDetails: () => void }) {
  const flagged = device.metrics.some((metric) => metric.flagged)
  const primary = device.metrics.filter((metric) => metric.primary)
  return (
    <article className="relative flex flex-col border-b border-r bg-background p-5">
      {(device.operational === "critical" || device.operational === "warning" || flagged) && (
        <span aria-hidden className={cn("absolute inset-x-0 top-0 h-[3px]", device.operational === "critical" ? "bg-danger" : "bg-signal")} />
      )}
      <div className="flex items-center justify-between gap-3">
        <span className="label-mono text-muted-foreground">{String(index).padStart(2, "0")}</span>
        <OperationalBadge state={device.operational} />
      </div>
      <h2 className="display-sm mt-4 text-[15px]">{device.name}</h2>
      <div className="label-mono mt-1 text-muted-foreground">{device.id}</div>
      {primary.map((metric) => (
        <div key={metric.label} className="mt-8">
          <div className="label-mono text-muted-foreground">{metric.label}</div>
          <div className={cn("mt-1 whitespace-nowrap text-[clamp(1.9rem,2.6vw,2.5rem)] font-semibold leading-none tabular-nums [font-stretch:112%]", metric.flagged ? (device.operational === "critical" ? "text-danger" : "text-signal-ink") : "text-foreground")}>
            {metric.value}
          </div>
        </div>
      ))}
      <dl className="mt-6 flex-1 border-t">
        {device.metrics.filter((metric) => !metric.primary).map((metric) => (
          <div key={metric.label} className="flex items-baseline justify-between gap-3 border-b py-2">
            <dt className="label-mono min-w-0 text-muted-foreground">{metric.label}</dt>
            <dd className={cn("text-right text-[13px] tabular-nums", metric.flagged ? "font-semibold text-signal-ink" : "text-foreground")}>{metric.value}</dd>
          </div>
        ))}
      </dl>
      <Button variant="link" onClick={onDetails} className="mt-5 h-auto justify-start p-0" aria-label={`${device.name} details`}>
        Details →
      </Button>
    </article>
  )
}

function SupervisorPanel({ state, onDemo, onOpen }: { state: FarmState; onDemo: () => void; onOpen: () => void }) {
  return (
    <Panel
      title="JARVIS nodes"
      meta={state.source === "demo" ? "Demo script" : state.source === "live" ? state.live?.label ?? "Live" : "Not connected"}
      action={state.source !== "disconnected" ? <Button variant="link" onClick={onOpen} className="h-auto p-0">Open JARVIS →</Button> : undefined}
    >
      {state.source !== "disconnected" ? (
        <div>
          <p className="mb-3 text-[14px] leading-6 text-muted-foreground">
            {state.source === "demo"
              ? "Each machine carries its own node; together they form one JARVIS. Here they follow a demo script."
              : "Each machine carries its own node; together they form one JARVIS. Each conclusion below is its reasoning brain's own output, with its evidence."}
          </p>
          {DEVICE_IDS.map((id) => {
            const node = state.jarvis.nodes[id]
            return (
              <div key={id} className="flex items-start justify-between gap-4 border-t py-3">
                <div className="min-w-0">
                  <div className="text-[14px] font-semibold">{deviceName(id)}</div>
                  <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">{node.reason}</p>
                </div>
                <AssessmentBadge assessment={node.assessment} />
              </div>
            )
          })}
        </div>
      ) : (
        <>
          <p className="text-[14px] leading-6 text-muted-foreground">
            This interface cannot confirm device or protection status while disconnected.
          </p>
          <Button variant="outline" size="sm" onClick={onDemo} className="mt-4">
            Explore demo mode
          </Button>
        </>
      )}
      <div className="mt-2 flex justify-between border-t pt-3">
        <span className="label-mono text-muted-foreground">Messages to the owner</span>
        <span className="font-mono text-[13px] tabular-nums">{state.source === "demo" ? state.jarvis.announcements : state.source === "live" ? "on the Chat page" : "None"}</span>
      </div>
    </Panel>
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
    <Panel
      title="Recent events"
      meta={`${events.length} logged · ${activeIncidents} open`}
      action={<Button variant="link" onClick={onViewAll} className="h-auto p-0">View all →</Button>}
    >
      {events.length === 0 ? (
        <EmptyState
          title="No events yet"
          text="Events appear once the demo simulation runs or a scenario is injected."
        />
      ) : (
        <ol>
          {events.slice(0, 4).map((event) => (
            <li key={event.id} className="flex gap-3 border-t py-3 first:border-t-0 first:pt-0">
              <SeverityIcon severity={event.severity} />
              <div className="min-w-0 flex-1">
                <p className="text-[14px] leading-6">{event.description}</p>
                <p className="label-mono mt-1 text-muted-foreground">
                  {event.severity} // {event.device} // {event.timestamp}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Panel>
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
  farm: Farm
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
  const live = state.source === "live" ? state.live ?? null : null
  const liveSpeed = live?.targetSpeed ?? null
  const speedValue = String(live ? liveSpeed ?? "" : state.simulation.speed)
  const speedOptions: number[] = liveSpeed !== null && !(SIMULATION_SPEEDS as readonly number[]).includes(liveSpeed)
    ? [liveSpeed, ...SIMULATION_SPEEDS]
    : [...SIMULATION_SPEEDS]
  const faultEnded = live !== null && live.faultEndTick !== null && live.tick !== null && live.tick >= live.faultEndTick

  const handleInject = () => {
    if (activeScenario) {
      toast.warning("A scenario is already running", {
        description: `Wait for ${activeScenario.shortLabel} to finish before injecting another.`,
      })
      return
    }
    farm.inject(scenario)
    if (!state.simulation.running && !live) {
      toast.message("Scenario ready", { description: "Press Start to play its timeline." })
    }
  }

  const handleReset = () => {
    farm.reset()
    setConfirmReset(false)
  }

  return (
    <div className="mx-auto max-w-[1400px] space-y-10">
      <PageHeader
        index="03"
        kicker="Simulator"
        title="Control room"
        action={
          <Badge variant="outline">
            {state.source === "demo" ? "Demo data" : state.source === "live" ? "Live state" : "Not connected"}
          </Badge>
        }
      />
      <div className="grid border-l border-t xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <section aria-labelledby="run-title" className="border-b border-r p-5 md:p-7">
          <div className="flex flex-wrap items-end justify-between gap-6">
            <div>
              <Kicker>Run status</Kicker>
              <div id="run-title" className="display mt-4 text-[clamp(2rem,4vw,3.25rem)]">
                {state.source === "disconnected" ? "Stopped" : simulationLabel(state)}
              </div>
            </div>
            <div className="text-right">
              <div className="label-mono text-muted-foreground">Farm time</div>
              <div className="mt-2 text-[clamp(1.8rem,3vw,2.5rem)] font-semibold leading-none tabular-nums [font-stretch:112%]">{live ? live.farmTime ?? "No data" : formatSimTime(state.simulation.timeS)}</div>
              {live && <div className="label-mono mt-2 text-muted-foreground">day {live.day ?? "?"} · tick {live.tick ?? "?"}</div>}
            </div>
          </div>
          {live && (
            <div className="mt-6 grid border-l border-t sm:grid-cols-3">
              <Metric label="Feed" value={live.label ?? "No data"} />
              <Metric label="Speed asked" value={live.targetSpeed === null ? "No data" : `${live.targetSpeed}×`} />
              <Metric label="Speed reached" value={live.actualSpeed === null ? "No data" : `${live.actualSpeed.toFixed(1)}×`} />
            </div>
          )}
          <div className="mt-8 grid border-l border-t sm:grid-cols-3">
            <Metric label="Active scenario" value={activeScenario ? activeScenario.shortLabel : "None"} />
            <Metric
              label="Scenario time"
              value={
                activeScenario
                  ? live
                    ? faultEnded ? "Ended by itself; reset to inject another" : `${formatSimTime(scenarioElapsed)} since injection`
                    : `${formatSimTime(scenarioElapsed)} of ${formatSimTime(activeScenario.durationS)}`
                  : "Not running"
              }
            />
            <Metric label="Recorded events" value={String(state.events.length)} />
          </div>
          <div className="mt-6 flex flex-wrap items-end gap-3">
            <div className="grid gap-1.5">
              <label htmlFor="sim-speed" className="label-mono text-muted-foreground">Speed</label>
              <Select
                value={speedValue}
                onValueChange={(value) => farm.setSpeed(Number(value) as SimulationSpeed)}
              >
                <SelectTrigger id="sim-speed" className="h-9 w-[190px] font-mono text-[12px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {speedOptions.map((speed) => (
                    <SelectItem key={speed} value={String(speed)} disabled={!(SIMULATION_SPEEDS as readonly number[]).includes(speed)}>
                      {(SIMULATION_SPEEDS as readonly number[]).includes(speed) ? speedLabel(speed as SimulationSpeed) : `${speed}× (as started)`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              onClick={() => (state.simulation.running ? farm.pause() : farm.start())}
              disabled={!connected}
            >
              {state.simulation.running ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
              {state.simulation.running ? "Pause" : state.simulation.timeS > 0 ? "Resume" : "Start"}
            </Button>
            <Button variant="outline" onClick={() => setConfirmReset(true)} disabled={!connected}>
              <RotateCcw className="size-3.5" />
              Reset
            </Button>
          </div>
          {!connected && (
            <div className="mt-6">
              <Notice
                text="Simulation controls are unavailable until Matrix connects. Demo mode supplies an interactive reference state."
                action="Use demo mode"
                onAction={farm.activateDemo}
              />
            </div>
          )}
          {live?.lastError && (
            <p role="alert" className="mt-6 border border-l-[3px] border-l-danger bg-panel p-4 text-[14px] leading-6">
              <span className="font-semibold">Refused by the farm // </span>{live.lastError}
            </p>
          )}
          {activeScenario && (
            <div className="mt-6 border border-l-[3px] border-l-signal bg-panel p-4">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="display-sm text-[13px]">{activeScenario.label}</span>
                <span className="label-mono text-muted-foreground">
                  {live
                    ? faultEnded
                      ? `ended by itself at tick ${live.faultEndTick}`
                      : `${formatSimTime(scenarioElapsed)} since injection · ${live.faultEndTick === null ? "until reset" : `ends at tick ${live.faultEndTick}`}`
                    : `${formatSimTime(scenarioElapsed)} of ${formatSimTime(activeScenario.durationS)}`}
                </span>
              </div>
              <p className="mt-2 text-[14px] leading-6 text-muted-foreground">{live ? "The fault runs in the Matrix test bench with the scenario library's own parameters. The nodes learn of it only through its physical effects." : activeScenario.description}</p>
              {!state.simulation.running && <p className="label-mono mt-3 text-signal-ink">Paused // press Start to advance</p>}
            </div>
          )}
        </section>
        <section aria-labelledby="inject-title" className="flex flex-col border-b border-r p-5 md:p-7">
          <Kicker>Scenario injection</Kicker>
          <h2 id="inject-title" className="display-sm mt-4 text-[15px]">Inject a fault</h2>
          <p className="mt-3 text-[14px] leading-6 text-muted-foreground">
            {live
              ? "Mentor tool, test only. Starts one fault of the real scenario library in the live Matrix test bench, from the next farm second. One fault per farm run; Reset clears it. JARVIS is never told: it has to notice."
              : "Scripted faults based on the real scenario library. Each one plays out as the simulated clock runs, updating telemetry, events and the JARVIS nodes together."}
          </p>
          <label htmlFor="scenario" className="label-mono mt-6 text-muted-foreground">Scenario</label>
          <Select value={scenario} onValueChange={(value) => setScenario(value as ScenarioId)}>
            <SelectTrigger id="scenario" className="mt-1.5 w-full">
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
          <p className="mt-4 flex-1 border-t pt-4 text-[14px] leading-6">{selectedScenario?.description}</p>
          <Button onClick={() => setConfirmInject(true)} disabled={!connected || Boolean(activeScenario)} className="mt-5 h-11 w-full">
            {activeScenario ? "Scenario in progress" : "Inject scenario"}
          </Button>
        </section>
      </div>
      <Suspense
        fallback={
          <div className="hatch grid h-[200px] place-items-center border">
            <span className="label-mono bg-background px-3 py-2 text-muted-foreground">Loading the 3D farm…</span>
          </div>
        }
      >
        <FarmScene3D devices={state.devices} onDevice={onDevice} />
      </Suspense>
      {live && <LlmSwitchesPanel />}
      <TimelineCard state={state} />
      <AlertDialog open={confirmInject} onOpenChange={setConfirmInject}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Inject “{selectedScenario?.label}”?</AlertDialogTitle>
            <AlertDialogDescription>
              {live
                ? `It starts this fault on ${selectedScenario?.devices.map(deviceName).join(" and ")} in the live Matrix test bench. The nodes are not told; they only see its effects.`
                : `It plays a scripted incident on ${selectedScenario?.devices.map(deviceName).join(" and ")}. No real backend action is performed.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleInject}>Inject scenario</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog open={confirmReset} onOpenChange={setConfirmReset}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{live ? "Start a new farm run?" : "Reset the demo simulation?"}</AlertDialogTitle>
            <AlertDialogDescription>
              {live
                ? "The Matrix restarts the farm from its first second with fresh brains and memory; the website clears this run's events. Any demo fault ends."
                : "All devices, events, JARVIS activity and chat return to reference values. This cannot be undone."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleReset}>Reset simulation</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 border-b border-r p-4">
      <div className="label-mono text-muted-foreground">{label}</div>
      <div className="mt-2 text-[14px] font-semibold tabular-nums [overflow-wrap:anywhere]">{value}</div>
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
    <Panel title="Simulation timeline" meta={`${state.events.length} recorded events`}>
      {timeline.length === 0 ? (
        <EmptyState
          title="No simulation events yet"
          text="Start the simulation and inject a scenario to see its incident timeline."
        />
      ) : (
        <ol>
          {timeline.map((event) => (
            <li key={event.id} className="grid grid-cols-[72px_20px_minmax(0,1fr)] gap-x-3 border-t py-3 first:border-t-0 first:pt-0">
              <span className="font-mono text-[12px] tabular-nums text-muted-foreground">{event.timestamp}</span>
              <SeverityIcon severity={event.severity} />
              <div className="min-w-0">
                <span className="text-[14px] font-semibold">{event.type}</span>
                <span className="label-mono ml-2 text-muted-foreground">{event.severity} // {event.device}</span>
                <p className="mt-0.5 text-[14px] leading-6 text-muted-foreground">{event.description}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Panel>
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
        node={state.source !== "disconnected" ? state.jarvis.nodes[selectedDevice.id] : null}
        onBack={onBack}
      />
    )
  }

  return (
    <div className="mx-auto max-w-[1400px] space-y-8">
      <PageHeader
        index="04"
        kicker="Device inventory"
        title="Devices"
        action={<Badge variant="outline">{state.devices.length} systems</Badge>}
      />
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_200px_180px]">
        <div className="relative">
          <Search aria-hidden className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="Search devices"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Device ID, name or category"
            className="h-10 pl-9"
          />
        </div>
        <Select value={filter} onValueChange={setFilter}>
          <SelectTrigger aria-label="Category" className="h-10 w-full">
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
          <SelectTrigger aria-label="Sort devices" className="h-10 w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="name">Sort by name</SelectItem>
            <SelectItem value="category">Sort by category</SelectItem>
            <SelectItem value="operational">Sort by status</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="border bg-background">
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-[14px]">
            <caption className="sr-only">Farm devices</caption>
            <thead className="border-b bg-panel">
              <tr>
                {["Device", "Category", "Connection", "Operational", "Key measurement", "Last update"].map((heading) => (
                  <th key={heading} className="label-mono px-4 py-3 font-medium text-muted-foreground">{heading}</th>
                ))}
                <th className="px-4 py-3"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((device) => (
                <tr key={device.id} className="border-b last:border-0 hover:bg-panel">
                  <td className="px-4 py-4">
                    <button type="button" onClick={() => onSelect(device)} className="text-left">
                      <div className="font-semibold underline decoration-input underline-offset-4 hover:decoration-signal">{device.name}</div>
                      <div className="label-mono mt-1 text-muted-foreground">{device.id}</div>
                    </button>
                  </td>
                  <td className="px-4 py-4 text-muted-foreground">{device.category}</td>
                  <td className="px-4 py-4">
                    <StateText state={device.connection} />
                  </td>
                  <td className="px-4 py-4">
                    <OperationalBadge state={device.operational} />
                  </td>
                  <td className="px-4 py-4 tabular-nums">{device.keyMeasurement}</td>
                  <td className="px-4 py-4 text-muted-foreground">{device.lastUpdate}</td>
                  <td className="px-4 py-4 text-right">
                    <Button variant="link" size="sm" onClick={() => onSelect(device)} className="h-auto p-0" aria-label={`Inspect ${device.name}`}>
                      Inspect →
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {filtered.length === 0 && (
          <div className="p-5">
            <EmptyState title="No matching devices" text="Try a different device name or category." />
          </div>
        )}
      </div>
    </div>
  )
}

function StateText({ state }: { state: Device["connection"] }) {
  return (
    <span className="inline-flex items-center gap-2 text-muted-foreground">
      <span
        aria-hidden
        className={cn(
          "size-2",
          state === "connected" || state === "demo"
            ? "bg-ok"
            : "bg-input",
        )}
      />
      {state === "demo" ? "Reference" : state === "disconnected" ? "Offline" : state.charAt(0).toUpperCase() + state.slice(1)}
    </span>
  )
}

function DeviceDetail({ device, node, onBack }: { device: Device; node: JarvisNode | null; onBack: () => void }) {
  return (
    <div className="mx-auto max-w-[1400px] space-y-8">
      <Button variant="ghost" size="sm" onClick={onBack} className="-ml-3">
        <ArrowLeft className="size-4" />
        Back to devices
      </Button>
      <header className="grid gap-6 border-b pb-8 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
        <div className="min-w-0">
          <Kicker>{device.category} // {device.id}</Kicker>
          <h1 className="display mt-5 text-[clamp(2.1rem,5vw,4.25rem)]">{device.name}</h1>
          <p className="mt-4 max-w-2xl text-[15px] leading-7 text-muted-foreground">{device.detail}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <OperationalBadge state={device.operational} />
          <Badge variant="outline">
            <CircleHelp className="size-3" />
            {device.connection === "demo" ? "Demo reference" : "Reference / unavailable"}
          </Badge>
        </div>
      </header>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
        <Panel title="Key telemetry" meta={`${device.metrics.length} readings`}>
          <div className="grid border-l border-t sm:grid-cols-2">
            {device.metrics.map((metric) => (
              <div key={metric.label} className={cn("relative border-b border-r p-4", metric.flagged && "bg-panel")}>
                {metric.flagged && <span aria-hidden className="absolute inset-y-0 left-0 w-[3px] bg-signal" />}
                <div className="label-mono text-muted-foreground">{metric.label}</div>
                <div className={cn("mt-2 text-[18px] font-semibold tabular-nums [font-stretch:110%]", metric.flagged ? "text-signal-ink" : "text-foreground")}>
                  {metric.value}
                </div>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="JARVIS node" action={node ? <AssessmentBadge assessment={node.assessment} /> : undefined}>
          {node ? (
            <>
              <p className="text-[14px] leading-6 text-muted-foreground">{node.reason}</p>
              <div className="mt-3">
                <DetailRow label="Diagnosis" value={diagnosisLabel(node.diagnosis)} mono={false} />
                <DetailRow label="Since" value={formatSimTime(node.sinceS)} />
                <DetailRow label="Protective action" value={node.action ?? "None"} mono={false} />
                <DetailRow label="Last message to the owner" value={node.ownerMessage ?? "None"} mono={false} />
                <DetailRow label="Controller" value="Local controller (runs without JARVIS)" mono={false} />
              </div>
            </>
          ) : (
            <p className="text-[14px] leading-6 text-muted-foreground">
              No JARVIS node is connected. Activate demo mode to see how this machine's node reasons.
            </p>
          )}
        </Panel>
      </div>
      <Panel title="Device notes" meta={device.category}>
        <p className="max-w-3xl text-[14px] leading-6 text-muted-foreground">
          {device.connection === "demo"
            ? "This device shows labeled demonstration telemetry, using the real farm's defaults. Inject a scenario from the Simulator to see its readings, its JARVIS node and the event log change together."
            : "No live telemetry is available. Activate demo mode to explore an interactive reference for this device."}
        </p>
      </Panel>
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
  farm: Farm
  initialQuery?: string
}) {
  const [query, setQuery] = useState(initialQuery)
  const [severity, setSeverity] = useState("all")
  const [device, setDevice] = useState("all")
  const [sort, setSort] = useState<"newest" | "oldest" | "severity">("newest")
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [confirmAcknowledgeAll, setConfirmAcknowledgeAll] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const openerRef = useRef<HTMLButtonElement | null>(null)

  // Sync query when a new global search navigates here.
  useEffect(() => {
    setQuery(initialQuery)
  }, [initialQuery])

  // Derive the selected event from current state so acknowledgment and
  // resolution updates reflect immediately without a stale local copy.
  const selected = selectedId ? state.events.find((event) => event.id === selectedId) ?? null : null

  const select = (event: EventRecord | null) => {
    // Closing the details returns keyboard focus to the button that opened them.
    if (!event) openerRef.current?.focus()
    setSelectedId(event ? event.id : null)
  }

  const acknowledgeEverything = () => {
    farm.acknowledgeAllEvents()
    setConfirmAcknowledgeAll(false)
  }

  const isLive = state.source === "live"
  const doneText = isLive ? "Cleared" : "Acknowledged"
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.events, query, severity, device, sort])

  const criticalCount = state.events.filter((event) => event.severity === "Critical" && !event.acknowledged).length
  const warningCount = state.events.filter((event) => event.severity === "Warning" && !event.acknowledged).length
  const totalCount = state.events.length
  const ackedCount = state.events.filter((event) => event.acknowledged).length

  return (
    <div className="mx-auto max-w-[1400px] space-y-8">
      <PageHeader
        index="05"
        kicker="Event log"
        title="Events & alarms"
        action={
          isLive ? (
            <span className="label-mono text-muted-foreground">To acknowledge an alert, ask JARVIS in your own words, then confirm</span>
          ) : <>
            <Button variant="outline" size="sm" onClick={() => (criticalOpen ? setConfirmAcknowledgeAll(true) : acknowledgeEverything())} disabled={openCount === 0}>
              <CheckCircle2 className="size-3.5" />
              Acknowledge all
            </Button>
            <Button variant="outline" size="sm" onClick={() => setConfirmClear(true)} disabled={acknowledgedInfoCount === 0}>
              <Trash2 className="size-3.5" />
              Clear acknowledged info
            </Button>
          </>
        }
      />
      <Notice
        text={
          state.source === "demo"
            ? "Demo event log. Entries come from the scripted demo scenarios, not a live Matrix feed."
            : isLive
              ? `Live event log (${state.live?.label ?? "live"}): the machines' own alarms and the safety gate's decisions in this farm run, at farm time. An alarm stays open until its machine clears it.`
              : "Matrix is not connected. Activate demo mode and run a scenario to populate the event log."
        }
        action={state.source === "disconnected" ? "Use demo mode" : undefined}
        onAction={state.source === "disconnected" ? farm.activateDemo : undefined}
      />
      <div className="grid grid-cols-2 border-l border-t lg:grid-cols-4">
        <Summary label="Open critical" value={criticalCount} tone={criticalCount > 0 ? "danger" : "plain"} />
        <Summary label="Open warnings" value={warningCount} tone={warningCount > 0 ? "signal" : "plain"} />
        <Summary label="Total entries" value={totalCount} tone="plain" />
        <Summary label={doneText} value={ackedCount} tone="plain" />
      </div>
      <div className="border bg-background">
        <div className="grid gap-3 border-b p-4 lg:grid-cols-[minmax(0,1fr)_170px_190px_170px]">
          <div className="relative">
            <Search aria-hidden className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              aria-label="Search events"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Event type, device or description"
              className="h-10 pl-9"
            />
          </div>
          <Select value={severity} onValueChange={setSeverity}>
            <SelectTrigger aria-label="Severity" className="h-10 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All severities</SelectItem>
              <SelectItem value="Critical">Critical</SelectItem>
              <SelectItem value="Warning">Warning</SelectItem>
              <SelectItem value="Info">Info</SelectItem>
            </SelectContent>
          </Select>
          <Select value={device} onValueChange={setDevice}>
            <SelectTrigger aria-label="Device" className="h-10 w-full">
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
            <SelectTrigger aria-label="Sort events" className="h-10 w-full">
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
        {events.length === 0 ? (
          <div className="p-5">
            <EmptyState
              title="No matching events"
              text={
                state.source === "demo"
                  ? "No events match the current filters, or the demo simulation hasn't produced any yet."
                  : isLive
                    ? "No events match the current filters, or this farm run has none yet."
                    : "Matrix is not connected, so there is no event history to display."
              }
            />
          </div>
        ) : (
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-[14px]">
              <caption className="sr-only">Farm event history</caption>
              <thead className="border-b bg-panel">
                <tr>
                  {["Severity", "Time", "Event", "Device", "Source", "Status"].map((heading) => (
                    <th key={heading} className="label-mono px-4 py-3 font-medium text-muted-foreground">{heading}</th>
                  ))}
                  <th className="px-4 py-3"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.id} className={cn("border-b align-top last:border-0", selected?.id === event.id ? "bg-panel" : "hover:bg-panel")}>
                    <td className="px-4 py-3">
                      <span className="flex items-center gap-2">
                        <SeverityIcon severity={event.severity} />
                        {event.severity}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 font-mono text-[12px] tabular-nums text-muted-foreground">{event.timestamp}</td>
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        ref={selected?.id === event.id ? openerRef : undefined}
                        onClick={(clickEvent) => {
                          openerRef.current = clickEvent.currentTarget
                          select(event)
                        }}
                        aria-expanded={selected?.id === event.id}
                        className="text-left font-semibold underline decoration-input underline-offset-4 hover:decoration-signal"
                      >
                        {event.type}
                      </button>
                      <div className="mt-1 text-muted-foreground">{event.description}</div>
                    </td>
                    <td className="px-4 py-3">{event.device}</td>
                    <td className="px-4 py-3 text-muted-foreground">{event.source}</td>
                    <td className="px-4 py-3">
                      {event.acknowledged ? (
                        <span className="inline-flex items-center gap-1.5 text-ok-ink">
                          <CheckCircle2 aria-hidden className="size-3.5" />
                          {doneText}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">Open</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {!event.acknowledged && !isLive && (
                        <Button variant="link" size="sm" onClick={() => farm.acknowledge(event.id)} className="h-auto p-0" aria-label={`Acknowledge ${event.type}`}>
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
      </div>
      {selected && (
        <section aria-label="Event details" className="border border-l-[3px] border-l-signal bg-background">
          <div className="flex items-start justify-between gap-4 border-b px-5 py-4">
            <div>
              <div className="flex items-center gap-2">
                <SeverityIcon severity={selected.severity} />
                <h2 className="display-sm text-[14px]">{selected.type}</h2>
              </div>
              <p className="mt-2 text-[14px] leading-6 text-muted-foreground">{selected.description}</p>
            </div>
            <Button variant="ghost" size="icon" aria-label="Close event details" onClick={() => select(null)}>
              <X className="size-4" />
            </Button>
          </div>
          <div className="grid gap-x-8 px-5 py-2 sm:grid-cols-3">
            <DetailRow label="Device" value={selected.device} />
            <DetailRow label="Source" value={selected.source} />
            <DetailRow label="Evidence" value={selected.evidence} />
            <DetailRow label={isLive ? "Farm time" : "Simulated time"} value={selected.timestamp} />
            <DetailRow label="Status" value={selected.acknowledged ? doneText : "Open"} />
          </div>
          {!selected.acknowledged && !isLive && (
            <div className="flex justify-end border-t px-5 py-4">
              <Button size="sm" onClick={() => farm.acknowledge(selected.id)}>
                <CheckCircle2 className="size-3.5" />
                Acknowledge event
              </Button>
            </div>
          )}
        </section>
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
      <AlertDialog open={confirmClear} onOpenChange={setConfirmClear}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {acknowledgedInfoCount} acknowledged info {acknowledgedInfoCount === 1 ? "entry" : "entries"}?</AlertDialogTitle>
            <AlertDialogDescription>
              They are deleted from the event log. Warnings, critical events and unacknowledged entries stay.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                farm.clearResolved()
                setConfirmClear(false)
              }}
            >
              Remove entries
            </AlertDialogAction>
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
  value: number
  tone: "danger" | "signal" | "plain"
}) {
  return (
    <div className="border-b border-r bg-background p-5">
      <div className="label-mono text-muted-foreground">{label}</div>
      <div
        className={cn(
          "mt-3 text-[clamp(2rem,3vw,2.75rem)] font-semibold leading-none tabular-nums [font-stretch:112%]",
          tone === "danger" ? "text-danger" : tone === "signal" ? "text-signal-ink" : "text-foreground",
        )}
      >
        {value}
      </div>
    </div>
  )
}
