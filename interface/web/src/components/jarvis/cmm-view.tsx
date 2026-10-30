import { cn } from "@/lib/utils"
import { NODE_LABEL, farmClock, humanize, useCmm, useTimeline, type CmmRecord, type MindNode } from "./mind-data"

const LEVELS = ["NORMAL", "HANDLED_BY_MACHINE", "WATCHING", "ESCALATE_TO_USER", "DANGER"]
const LEVEL_COLOR: Record<string, string> = { NORMAL: "#1f8a4c", HANDLED_BY_MACHINE: "#2f5d8a", WATCHING: "#e09500", ESCALATE_TO_USER: "#ff4500", DANGER: "#c8102e" }

function parse(text: string): unknown {
  try { return JSON.parse(text) } catch { return null }
}

/** The memory module, from its own export: what was stored after the recorded run, table by table. */
export function CmmView({ node }: { node: MindNode }) {
  const { data, error } = useCmm(node)
  const { data: timeline } = useTimeline()
  if (error) return <p className="text-[14px] text-danger">{NODE_LABEL[node]}'s memory could not be loaded ({error}).</p>
  if (!data) return <div className="hatch grid h-[320px] place-items-center border"><span role="status" className="label-mono bg-background px-3 py-2">Loading {NODE_LABEL[node]}'s memory…</span></div>
  const duration = timeline?.episode.duration_ticks ?? 1800
  const startHour = timeline?.episode.start_hour ?? 0
  return (
    <div className="space-y-8">
      <FlowDiagram />
      <Stores record={data} />
      <Episodes record={data} node={node} duration={duration} startHour={startHour} firstFault={timeline?.episode.faults[0]?.inject_tick ?? null} />
      <div className="grid border-l border-t lg:grid-cols-2">
        <Procedures record={data} />
        <Profile record={data} />
      </div>
      <Rom record={data} />
    </div>
  )
}

function FlowDiagram() {
  return (
    <section aria-labelledby="cmm-flow" className="border bg-background p-5">
      <h3 id="cmm-flow" className="display-sm text-[13px]">Where memory sits</h3>
      <p className="mt-2 max-w-3xl text-[14px] leading-6 text-muted-foreground">
        The CMM records what the brain concluded and what the machine reported, every second. In the shipped recipe the brain does not read it back: its memory-note inputs are blanked. The brain's own short-term memory is its 32-step trace.
      </p>
      <svg viewBox="0 0 900 200" className="mt-5 block h-auto w-full" role="img" aria-label="Brain decisions and machine reports flow into the CMM. The CMM is not read by the brain or by the language model.">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#0a0a0a" /></marker>
        </defs>
        {[
          { x: 20, y: 20, label: "CTM brain", sub: "concludes every second", fill: "#0a0a0a", ink: "#ffffff" },
          { x: 20, y: 120, label: "The machine", sub: "readings and device codes", fill: "#ffffff", ink: "#0a0a0a" },
          { x: 360, y: 70, label: "CMM", sub: "one SQLite file per node", fill: "#ff4500", ink: "#0a0a0a" },
          { x: 690, y: 20, label: "Language model", sub: "never opens the CMM", fill: "#f2f2f2", ink: "#0a0a0a" },
        ].map((box) => (
          <g key={box.label}>
            <rect x={box.x} y={box.y} width="190" height="60" fill={box.fill} stroke="#0a0a0a" />
            <text x={box.x + 14} y={box.y + 26} fill={box.ink} style={{ font: "700 14px Archivo", fontStretch: "118%", textTransform: "uppercase" }}>{box.label}</text>
            <text x={box.x + 14} y={box.y + 46} fill={box.ink} style={{ font: "11px 'JetBrains Mono'", letterSpacing: "0.04em" }}>{box.sub}</text>
          </g>
        ))}
        <line x1="210" y1="50" x2="356" y2="90" stroke="#0a0a0a" strokeWidth="1.5" markerEnd="url(#arrow)" />
        <line x1="210" y1="150" x2="356" y2="110" stroke="#0a0a0a" strokeWidth="1.5" markerEnd="url(#arrow)" />
        <path d="M 360 84 C 300 30 260 30 214 40" fill="none" stroke="#0a0a0a" strokeDasharray="4 4" />
        <text x="232" y="22" style={{ font: "10px 'JetBrains Mono'" }} fill="#5a5a5a">NOT READ BACK // NOTES HIDDEN</text>
        <line x1="550" y1="100" x2="686" y2="58" stroke="#0a0a0a" strokeDasharray="4 4" />
        <g transform="translate(618 79)"><line x1="-7" y1="-7" x2="7" y2="7" stroke="#c8102e" strokeWidth="2" /><line x1="-7" y1="7" x2="7" y2="-7" stroke="#c8102e" strokeWidth="2" /></g>
        <text x="690" y="112" style={{ font: "10px 'JetBrains Mono'" }} fill="#5a5a5a">IT ONLY SEES WHAT</text>
        <text x="690" y="126" style={{ font: "10px 'JetBrains Mono'" }} fill="#5a5a5a">THE REASONING PUBLISHES</text>
      </svg>
    </section>
  )
}

function Stores({ record }: { record: CmmRecord }) {
  const t = record.tables
  const stores: { name: string; count: string; rule: string; empty?: boolean }[] = [
    { name: "Working memory", count: "In RAM", rule: "Open incidents, timers and call status. Resets on restart; not exported." },
    { name: "Episodes", count: String(t.episodes ?? 0), rule: "Salience-gated. Repeated normal seconds are never stored; a bare WATCHING must last 30 s; an episode closes after 300 s of quiet.", empty: !t.episodes },
    { name: "Kept seconds", count: String(t.episode_ticks ?? 0), rule: "The seconds kept inside those episodes, one row each.", empty: !t.episode_ticks },
    { name: "Facts", count: String(t.facts ?? 0), rule: "Stored only when the machine's own device code verified it.", empty: !t.facts },
    { name: "Hypotheses", count: String(t.hypotheses ?? 0), rule: "Kept apart from facts. Never promoted because the brain believed it.", empty: !t.hypotheses },
    { name: "Procedures", count: String(t.procedures ?? 0), rule: "What was done, in what situation, and how it turned out.", empty: !t.procedures },
    { name: "Normal profile", count: `${record.profile.length} rows`, rule: "What normal looks like per hour. Only quiet seconds update it; read from RAM because the 02:00 sleep was not reached." },
    { name: "ROM", count: String(t.rom ?? record.rom.length), rule: "Owner rules and device documents, read-only." },
  ]
  return (
    <section aria-labelledby="cmm-stores">
      <h3 id="cmm-stores" className="display-sm text-[13px]">What is in it after the run</h3>
      <div className="mt-4 grid border-l border-t sm:grid-cols-2 xl:grid-cols-4">
        {stores.map((store) => (
          <div key={store.name} className={cn("border-b border-r p-4", store.empty && "hatch")}>
            <div className={cn(store.empty && "bg-background")}>
              <div className="label-mono text-muted-foreground">{store.name}</div>
              <div className="mt-2 text-[28px] font-semibold leading-none tabular-nums [font-stretch:112%]">{store.count}</div>
              <p className="mt-2 text-[13px] leading-5 text-muted-foreground">{store.rule}</p>
            </div>
          </div>
        ))}
      </div>
      <p className="label-mono mt-3 text-muted-foreground">Sleep at 02:00 farm time turns closed episodes into recurrence facts. This short run did not reach it.</p>
    </section>
  )
}

function Episodes({ record, node, duration, startHour, firstFault }: { record: CmmRecord; node: MindNode; duration: number; startHour: number; firstFault: number | null }) {
  if (record.episodes.length === 0) {
    return (
      <section className="hatch border p-6">
        <div className="max-w-xl bg-background p-4">
          <h3 className="display-sm text-[13px]">No episodes stored</h3>
          <p className="mt-2 text-[14px] leading-6 text-muted-foreground">{NODE_LABEL[node]} stayed normal the whole run, and repeated normal seconds are never stored. An empty memory is the correct result here.</p>
        </div>
      </section>
    )
  }
  return (
    <section aria-labelledby="cmm-episodes" className="space-y-4">
      <h3 id="cmm-episodes" className="display-sm text-[13px]">Stored episodes</h3>
      {record.episodes.map((episode) => {
        const summary = parse(episode.summary) as { codes?: string[]; peak?: string; duration_s?: number } | null
        const peers = parse(episode.peers) as Record<string, { diagnosis?: string; verified_by?: string[] }> | null
        const closed = episode.closed_s ?? duration
        return (
          <article key={episode.id} className="border bg-background">
            <div className="flex flex-wrap items-baseline justify-between gap-3 border-b px-5 py-3">
              <span className="display-sm text-[13px]">Episode {episode.id} // {humanize(episode.diagnosis)}</span>
              <span className="label-mono text-muted-foreground">
                Opened {farmClock(startHour, episode.opened_s)} // {episode.closed_s === null ? "still open at the end of the run" : `closed ${farmClock(startHour, episode.closed_s)}`}
              </span>
            </div>
            <div className="px-5 py-4">
              <div className="label-mono text-muted-foreground">Every kept second, coloured by the level the node stated</div>
              <svg viewBox={`0 0 ${duration} 30`} preserveAspectRatio="none" className="mt-2 block h-8 w-full border bg-panel" aria-label={`${episode.ticks_kept.length} kept seconds between tick ${episode.opened_s} and tick ${closed}`}>
                <rect x={episode.opened_s} y="0" width={Math.max(1, closed - episode.opened_s)} height="30" fill="#e6e6e6" />
                {episode.ticks_kept.map(([tick, level]) => <rect key={tick} x={tick} y="0" width="1" height="30" fill={LEVEL_COLOR[level] ?? "#8c8c8c"} />)}
              </svg>
              <div className="mt-1 flex justify-between"><span className="label-mono text-muted-foreground">Tick 0</span><span className="label-mono text-muted-foreground">Tick {duration}</span></div>
              <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">{LEVELS.map((level) => <li key={level} className="label-mono flex items-center gap-1.5 text-muted-foreground"><span aria-hidden className="size-2" style={{ background: LEVEL_COLOR[level] }} />{humanize(level)}</li>)}</ul>
            </div>
            <dl className="grid border-t sm:grid-cols-2 lg:grid-cols-4">
              {[
                ["Kind", episode.kind ? humanize(episode.kind) : "Not set"],
                ["Peak level", humanize(episode.peak)],
                ["Cause named", episode.source ? humanize(episode.source) : "Not named"],
                ["Kept seconds", String(episode.ticks_kept.length)],
                ["Trigger", episode.trigger.split(",").map(humanize).join(", ")],
                ["Machine codes", summary?.codes?.length ? summary.codes.join(", ") : "None"],
                ["Peers that reported", peers && Object.keys(peers).length ? Object.entries(peers).map(([peer, info]) => `${peer}: ${humanize(info.diagnosis ?? "unknown")}${info.verified_by?.length ? ` (verified by ${info.verified_by.join(", ")})` : ""}`).join("; ") : "None"],
                ["Duration", summary?.duration_s ? `${Math.round(summary.duration_s)} s` : episode.closed_s === null ? "Still open" : `${closed - episode.opened_s} s`],
              ].map(([label, value]) => (
                <div key={label} className="border-b p-4 sm:border-r">
                  <dt className="label-mono text-muted-foreground">{label}</dt>
                  <dd className="mt-1 text-[14px] leading-6 [overflow-wrap:anywhere]">{value}</dd>
                </div>
              ))}
            </dl>
            {node === "animal-protection" && firstFault !== null && episode.opened_s < firstFault && episode.peak !== "NORMAL" && (
              <p className="border-t px-5 py-3 text-[13px] leading-5"><span className="label-mono mr-2 text-danger">False alert //</span>Stored as it happened: this episode began at tick {episode.opened_s}, before any fault, on a calm farm.</p>
            )}
          </article>
        )
      })}
    </section>
  )
}

function Procedures({ record }: { record: CmmRecord }) {
  return (
    <section className="border-b border-r p-5">
      <h3 className="display-sm text-[13px]">Procedures</h3>
      {record.procedures.length === 0 ? (
        <p className="mt-3 text-[14px] leading-6 text-muted-foreground">None stored in this run.</p>
      ) : (
        <ul className="mt-3">
          {record.procedures.map((procedure) => (
            <li key={procedure.id} className="grid grid-cols-[64px_minmax(0,1fr)_auto] gap-3 border-b py-2 last:border-0">
              <span className="font-mono text-[12px] tabular-nums text-muted-foreground">{procedure.time_s} s</span>
              <span className="text-[14px]">{humanize(procedure.situation)} → {humanize(procedure.action)}</span>
              <span className="label-mono text-ok-ink">{procedure.result}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Profile({ record }: { record: CmmRecord }) {
  return (
    <section className="border-b border-r p-5">
      <h3 className="display-sm text-[13px]">Normal profile</h3>
      <p className="label-mono mt-1 text-muted-foreground">Source // {record.profile_source.split(" (")[0]}</p>
      <table className="mt-3 w-full text-left text-[13px]">
        <thead><tr className="border-b">{["Hour", "Reading", "n", "Mean", "Spread"].map((heading) => <th key={heading} className="label-mono py-2 pr-3 font-medium text-muted-foreground">{heading}</th>)}</tr></thead>
        <tbody>
          {record.profile.map((row) => (
            <tr key={`${row.hour}-${row.key}`} className="border-b last:border-0">
              <td className="py-1.5 pr-3 font-mono tabular-nums">{String(row.hour).padStart(2, "0")}h</td>
              <td className="py-1.5 pr-3 font-mono text-[12px] [overflow-wrap:anywhere]">{row.key}</td>
              <td className="py-1.5 pr-3 font-mono tabular-nums">{row.n}</td>
              <td className="py-1.5 pr-3 font-mono tabular-nums">{row.mean}</td>
              <td className="py-1.5 font-mono tabular-nums">±{row.spread}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function Rom({ record }: { record: CmmRecord }) {
  return (
    <section className="border bg-background">
      <h3 className="display-sm border-b px-5 py-3 text-[13px]">ROM // read-only owner rules</h3>
      {record.rom.map((entry) => (
        <div key={entry.key} className="grid gap-2 border-b px-5 py-4 last:border-0 md:grid-cols-[200px_minmax(0,1fr)]">
          <span className="label-mono text-muted-foreground">{humanize(entry.key)}</span>
          <p className="max-w-3xl text-[14px] leading-6">{entry.text}</p>
        </div>
      ))}
    </section>
  )
}
