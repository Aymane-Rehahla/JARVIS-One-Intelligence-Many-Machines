import { Component, type ErrorInfo, type ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { clearPersistedDemoState } from "@/services/matrix"

type ErrorBoundaryProps = {
  children: ReactNode
  fallback?: ReactNode
  resetLabel?: string
}

type ErrorBoundaryState = {
  hasError: boolean
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // Keep the failure contained so unrelated supervision panels stay usable.
  }

  resetDemoAndReload = (): void => {
    clearPersistedDemoState()
    window.location.reload()
  }

  render() {
    if (!this.state.hasError) return this.props.children
    if (this.props.fallback) return this.props.fallback
    return (
      <main className="grid min-h-svh place-items-center bg-background p-6">
        <Card className="w-full max-w-md p-6 shadow-none">
          <h1 className="text-lg font-semibold">JARVIS needs a fresh start</h1>
          <p className="mt-2 text-sm text-muted-foreground">The saved demo state could not be displayed safely.</p>
          <Button className="mt-5" variant="destructive" onClick={this.resetDemoAndReload}>
            Reset demo data and reload
          </Button>
        </Card>
      </main>
    )
  }
}
