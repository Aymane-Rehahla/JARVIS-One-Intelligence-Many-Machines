import { useEffect, useState } from "react"

const QUERY = "(prefers-reduced-motion: reduce)"

/** True when the visitor asked the system for less motion. Follows changes live. */
export function useReducedMotion() {
  const [reduced, setReduced] = useState(() => typeof window !== "undefined" && Boolean(window.matchMedia?.(QUERY).matches))
  useEffect(() => {
    const media = window.matchMedia?.(QUERY)
    if (!media) return
    const update = () => setReduced(media.matches)
    media.addEventListener("change", update)
    return () => media.removeEventListener("change", update)
  }, [])
  return reduced
}
