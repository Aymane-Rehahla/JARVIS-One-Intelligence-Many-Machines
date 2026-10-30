import { ASSESSMENTS, DEVICE_IDS, type Assessment, type FarmState } from "../services/matrix.ts"

/** Classes for each of the five conclusions a JARVIS node can reach. Ink colours are text-safe on the sheet;
 *  the dot colours are the signal shades (non-text, 3:1). Danger is the only filled badge. */
export const ASSESSMENT_TONES: Record<Assessment, { status: "good" | "info" | "warning" | "critical"; badge: string; dot: string; card: string }> = {
  NORMAL: { status: "good", badge: "border-ok/50 bg-panel text-ok-ink", dot: "bg-ok", card: "border-border" },
  HANDLED_BY_MACHINE: { status: "info", badge: "border-info/50 bg-panel text-info", dot: "bg-info", card: "border-info/50" },
  WATCHING: { status: "warning", badge: "border-watch bg-panel text-watch-ink", dot: "bg-watch", card: "border-watch" },
  ESCALATE_TO_USER: { status: "warning", badge: "border-signal bg-panel text-signal-ink", dot: "bg-signal", card: "border-signal" },
  DANGER: { status: "critical", badge: "border-danger bg-danger text-white", dot: "bg-danger", card: "border-danger" },
}

/** The most serious conclusion across the four nodes (ASSESSMENTS is ordered from calm to danger). */
export function worstAssessment(state: FarmState): Assessment | null {
  const known = DEVICE_IDS.map((id) => state.jarvis.nodes[id]).filter((node) => !node.noConclusion)
  if (known.length === 0) return null
  return known.map((node) => node.assessment).reduce<Assessment>(
    (worst, assessment) => (ASSESSMENTS.indexOf(assessment) > ASSESSMENTS.indexOf(worst) ? assessment : worst),
    "NORMAL",
  )
}
