import { useCallback, useEffect, useMemo, useState } from "react"

import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Kicker } from "@/components/farm-ui"
import type { LlmFailureSwitches } from "@/services/contracts"
import { SWITCH_NAMES, getFailureSwitches, setFailureSwitches, type SwitchName } from "@/services/chat"
import { gatewayUrl } from "@/services/live"
import { DEVICE_IDS, deviceName } from "@/services/matrix"

const SWITCH_TEXT: Record<SwitchName, string> = {
  llm_timeout: "Model too slow",
  broken_json: "Broken model output",
  silent_peer: "Node stops answering peers",
  server_crash: "Model server down",
}

/** The mentor's simulated LLM failures per node (test only): GET and POST /mentor/llm-failures. */
export function LlmSwitchesPanel() {
  const base = useMemo(gatewayUrl, [])
  const [current, setCurrent] = useState<LlmFailureSwitches | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    void getFailureSwitches(base).then((result) => {
      if (result.ok) {
        setCurrent(result.value)
        setProblem(null)
      } else setProblem(result.reason)
    })
  }, [base])
  useEffect(load, [load])

  const toggle = (name: SwitchName, node: string, on: boolean) => {
    if (!current) return
    const next = Object.fromEntries(SWITCH_NAMES.map((key) => [key, [...current[key]]])) as Record<SwitchName, string[]>
    next[name] = on ? [...next[name].filter((n) => n !== node), node] : next[name].filter((n) => n !== node)
    setSaving(true)
    void setFailureSwitches(base, next).then((result) => {
      setSaving(false)
      if (result.ok) {
        setCurrent(result.value)
        setProblem(null)
      } else setProblem(result.reason)
    })
  }

  return (
    <section aria-labelledby="llm-switches-title" className="border p-5 md:p-7">
      <Kicker>Mentor tool // test only</Kicker>
      <h2 id="llm-switches-title" className="display-sm mt-4 text-[15px]">Language model failures</h2>
      <p className="mt-3 text-[14px] leading-6 text-muted-foreground">
        Simulated failures of each node's language model, for testing that JARVIS still answers gracefully. They never reach
        the farm or the machines' reasoning, and the agent is never told: only the failure wrappers read them.
      </p>
      {problem && <p role="status" className="label-mono mt-4 text-signal-ink">{problem}</p>}
      {current && (
        <div className="mt-5 overflow-x-auto">
          <table className="w-full min-w-[520px] text-left text-[13px]">
            <thead>
              <tr className="border-b">
                <th className="label-mono py-2 pr-3 font-normal text-muted-foreground">Failure</th>
                {DEVICE_IDS.map((node) => <th key={node} className="label-mono px-2 py-2 font-normal text-muted-foreground">{deviceName(node)}</th>)}
              </tr>
            </thead>
            <tbody>
              {SWITCH_NAMES.map((name) => (
                <tr key={name} className="border-b">
                  <td className="py-2 pr-3">{SWITCH_TEXT[name]}</td>
                  {DEVICE_IDS.map((node) => (
                    <td key={node} className="px-2 py-2">
                      <Switch
                        aria-label={`${SWITCH_TEXT[name]} on ${deviceName(node)}`}
                        checked={current[name].includes(node)}
                        disabled={saving}
                        onCheckedChange={(on) => toggle(name, node, on)}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Button size="sm" variant="outline" className="mt-4" onClick={load}>Refresh</Button>
    </section>
  )
}
