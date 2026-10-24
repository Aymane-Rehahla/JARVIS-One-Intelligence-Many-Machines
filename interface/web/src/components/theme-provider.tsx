/* eslint-disable react-refresh/only-export-components */
import * as React from "react"

type Theme = "light"

type ThemeProviderProps = {
  children: React.ReactNode
  defaultTheme?: Theme
  storageKey?: string
  disableTransitionOnChange?: boolean
}

type ThemeProviderState = {
  theme: Theme
  setTheme: (theme: Theme) => void
}

const ThemeProviderContext = React.createContext<ThemeProviderState | undefined>(undefined)

function applyLightTheme(disableTransitionOnChange: boolean) {
  const root = document.documentElement
  let style: HTMLStyleElement | null = null
  if (disableTransitionOnChange) {
    style = document.createElement("style")
    style.appendChild(document.createTextNode("*,*::before,*::after{-webkit-transition:none!important;transition:none!important}"))
    document.head.appendChild(style)
  }
  root.classList.remove("dark")
  root.classList.add("light")
  if (style) {
    window.getComputedStyle(document.body)
    requestAnimationFrame(() => requestAnimationFrame(() => style?.remove()))
  }
}

export function ThemeProvider({ children, storageKey = "theme", disableTransitionOnChange = true, ...props }: ThemeProviderProps) {
  const [theme, setThemeState] = React.useState<Theme>("light")

  const setTheme = React.useCallback((_nextTheme: Theme) => {
    try {
      localStorage.setItem(storageKey, "light")
    } catch {
      // Ignore storage failures.
    }
    setThemeState("light")
  }, [storageKey])

  React.useEffect(() => {
    try {
      const storedTheme = localStorage.getItem(storageKey)
      if (storedTheme !== "light") localStorage.setItem(storageKey, "light")
    } catch {
      // Ignore storage failures.
    }
    applyLightTheme(disableTransitionOnChange)
  }, [disableTransitionOnChange, storageKey])

  React.useEffect(() => {
    const handleStorageChange = (event: StorageEvent) => {
      if (event.key !== storageKey) return
      setThemeState("light")
      applyLightTheme(false)
    }
    window.addEventListener("storage", handleStorageChange)
    return () => window.removeEventListener("storage", handleStorageChange)
  }, [storageKey])

  const value = React.useMemo(() => ({ theme, setTheme }), [theme, setTheme])

  return <ThemeProviderContext.Provider {...props} value={value}>{children}</ThemeProviderContext.Provider>
}

export const useTheme = () => {
  const context = React.useContext(ThemeProviderContext)
  if (context === undefined) throw new Error("useTheme must be used within a ThemeProvider")
  return context
}
