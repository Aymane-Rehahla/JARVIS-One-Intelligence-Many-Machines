import { ASSESSMENTS, DEVICE_IDS, type Assessment, type FarmState } from "@/services/matrix"

/** Light-theme classes for each of the five conclusions a JARVIS node can reach. */
export const ASSESSMENT_TONES: Record<Assessment, { status: "good" | "info" | "warning" | "critical"; badge: string; dot: string; card: string }> = {
  NORMAL: { status: "good", badge: "border-emerald-200 bg-emerald-50 text-emerald-700", dot: "bg-emerald-500", card: "border-slate-200" },
  HANDLED_BY_MACHINE: { status: "info", badge: "border-teal-200 bg-teal-50 text-teal-700", dot: "bg-teal-500", card: "border-teal-200" },
  WATCHING: { status: "warning", badge: "border-amber-200 bg-amber-50 text-amber-700", dot: "bg-amber-500", card: "border-amber-300" },
  ESCALATE_TO_USER: { status: "warning", badge: "border-orange-200 bg-orange-50 text-orange-700", dot: "bg-orange-500", card: "border-orange-300" },
  DANGER: { status: "critical", badge: "border-red-200 bg-red-50 text-red-700", dot: "bg-red-500", card: "border-red-300" },
}

/** The most serious conclusion across the four nodes (ASSESSMENTS is ordered from calm to danger). */
export function worstAssessment(state: FarmState): Assessment {
  return DEVICE_IDS.map((id) => state.jarvis.nodes[id].assessment).reduce<Assessment>(
    (worst, assessment) => (ASSESSMENTS.indexOf(assessment) > ASSESSMENTS.indexOf(worst) ? assessment : worst),
    "NORMAL",
  )
}
