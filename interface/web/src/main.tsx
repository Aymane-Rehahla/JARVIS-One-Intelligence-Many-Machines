import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"
import { AuthGate } from "@/components/auth-gate"
import { ErrorBoundary } from "@/components/error-boundary"
import { ThemeProvider } from "@/components/theme-provider.tsx"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider defaultTheme="light">
      <ErrorBoundary>
        <AuthGate>
          <App />
        </AuthGate>
      </ErrorBoundary>
    </ThemeProvider>
  </StrictMode>,
)
