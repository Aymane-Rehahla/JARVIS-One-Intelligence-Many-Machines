import { Sun } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useTheme } from "@/components/theme-provider"

export function ModeToggle() {
  const { setTheme } = useTheme()
  return (
    <Button variant="outline" size="icon" aria-label="Light theme" onClick={() => setTheme("light")}>
      <Sun className="size-[1.2rem]" />
      <span className="sr-only">Light theme</span>
    </Button>
  )
}
