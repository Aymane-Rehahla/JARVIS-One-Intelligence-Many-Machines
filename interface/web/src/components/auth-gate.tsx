import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react"
import { LogOut } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { HEARTBEAT_MS, heartbeat, sessionToken, signIn, signOut, signUp, type Person } from "@/services/auth"
import { cn } from "@/lib/utils"

type Account = { person: Person | null; leave: () => void }
const AccountContext = createContext<Account>({ person: null, leave: () => {} })
export const useAccount = () => useContext(AccountContext)

type Mode = "signin" | "signup"

/**
 * The door: nobody uses JARVIS without an account. The seat rules (two at a time, mentors first) are in
 * interface/gateway/accounts.py and WORKLOG, not on the page.
 * While signed in, a heartbeat keeps the seat; when the gateway says the seat is gone, the visitor is told why.
 * When JARVIS is off, the demo farm is still open without an account.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [person, setPerson] = useState<Person | null>(null)
  const [checking, setChecking] = useState(() => sessionToken() !== null)
  const [notice, setNotice] = useState<string | null>(null)
  const [offline, setOffline] = useState(false)
  const [demo, setDemo] = useState(false)

  useEffect(() => {
    if (!checking) return
    void heartbeat().then((result) => {
      if (result.ok) setPerson(result.person)
      else if (result.status === 0) setOffline(true)
      setChecking(false)
    })
  }, [checking])

  useEffect(() => {
    if (!person) return
    const timer = setInterval(() => {
      void heartbeat().then((result) => {
        if (!result.ok && result.status === 401) {
          setPerson(null)
          setNotice(result.reason)
        }
      })
    }, HEARTBEAT_MS)
    return () => clearInterval(timer)
  }, [person])

  const leave = useCallback(() => {
    void signOut().then(() => {
      setPerson(null)
      setNotice("You signed out. See you soon.")
    })
  }, [])

  if (person || demo) return <AccountContext.Provider value={{ person, leave }}>{children}</AccountContext.Provider>
  if (checking) return <div className="grid min-h-svh place-items-center bg-frame label-mono text-muted-foreground">Checking your seat…</div>
  return (
    <SignInScreen
      notice={notice}
      offline={offline}
      onEntered={(next) => { setNotice(null); setOffline(false); setPerson(next) }}
      onOffline={() => setOffline(true)}
      onDemo={() => setDemo(true)}
    />
  )
}

function SignInScreen({ notice, offline, onEntered, onOffline, onDemo }: {
  notice: string | null
  offline: boolean
  onEntered: (person: Person) => void
  onOffline: () => void
  onDemo: () => void
}) {
  const [mode, setMode] = useState<Mode>("signup")
  const [fields, setFields] = useState({ username: "", nickname: "", password: "", confirm: "", mentor_key: "" })
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (name: keyof typeof fields) => (event: React.ChangeEvent<HTMLInputElement>) => setFields({ ...fields, [name]: event.target.value })
  const mismatch = mode === "signup" && fields.confirm !== "" && fields.confirm !== fields.password

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (mismatch) return
    setBusy(true)
    setProblem(null)
    const result = mode === "signup" ? await signUp(fields) : await signIn({ username: fields.username, password: fields.password })
    setBusy(false)
    if (result.ok) onEntered(result.person)
    else {
      setProblem(result.reason)
      if (result.status === 0) onOffline()
    }
  }

  const field = (name: keyof typeof fields, label: string, props: React.InputHTMLAttributes<HTMLInputElement>, hint?: string) => (
    <div className="grid gap-2">
      <Label htmlFor={`auth-${name}`} className="label-mono">{label}</Label>
      <Input id={`auth-${name}`} value={fields[name]} onChange={set(name)} className="h-11 font-mono text-[14px]" {...props} />
      {hint && <p className="text-[12px] leading-5 text-muted-foreground">{hint}</p>}
    </div>
  )

  return (
    <div className="relative grid min-h-svh place-items-center bg-frame px-4 py-10 text-foreground">
      <div aria-hidden className="frame-stripes pointer-events-none absolute inset-x-0 top-0 h-[260px]" />
      <main className="relative w-full max-w-[460px] border bg-background">
        <header className="border-b p-6">
          <div className="label-mono text-muted-foreground">JARVIS // farm supervisor</div>
          <h1 className="display mt-3 text-[clamp(2rem,6vw,2.8rem)]">{mode === "signup" ? "Meet JARVIS" : "Welcome back"}</h1>
          <p className="mt-3 text-[14px] leading-6 text-muted-foreground">
            One intelligence watching every machine on the farm. Ask it anything, by text or by voice, and get answers
            grounded in live data.
          </p>
        </header>
        <div role="tablist" aria-label="Account" className="grid grid-cols-2 border-b">
          {(["signup", "signin"] as const).map((id) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={mode === id}
              onClick={() => { setMode(id); setProblem(null) }}
              className={cn("label-mono h-11 transition-colors duration-150", mode === id ? "bg-foreground text-background" : "hover:bg-secondary")}
            >
              {id === "signup" ? "Create account" : "Sign in"}
            </button>
          ))}
        </div>
        <form onSubmit={submit} className="grid gap-5 p-6" noValidate>
          {(notice || offline) && (
            <p role="status" className="border-l-[3px] border-l-signal bg-secondary px-3 py-2 text-[13px] leading-5">
              {offline ? "JARVIS is off right now: the gateway does not answer. You can still explore the demo farm." : notice}
            </p>
          )}
          {field("username", "Username", { autoComplete: "username", required: true, maxLength: 24, autoFocus: true },
            mode === "signup" ? "3 to 24 letters, digits, dots, dashes or underscores." : undefined)}
          {mode === "signup" && field("nickname", "Name or nickname", { autoComplete: "nickname", required: true, maxLength: 32 }, "What JARVIS calls you on screen.")}
          {field("password", "Password", { type: "password", autoComplete: mode === "signup" ? "new-password" : "current-password", required: true, maxLength: 128 },
            mode === "signup" ? "At least 8 characters." : undefined)}
          {mode === "signup" && field("confirm", "Confirm password", { type: "password", autoComplete: "new-password", required: true, maxLength: 128, "aria-invalid": mismatch || undefined })}
          {mismatch && <p className="-mt-3 text-[12px] text-destructive">The two passwords are not the same.</p>}
          {mode === "signup" && field("mentor_key", "Mentor key (optional)", { type: "password", autoComplete: "off", maxLength: 64 }, "A mentor key grants priority access to JARVIS. Leave it empty to join as a regular user.")}
          {problem && <p role="alert" className="text-[13px] leading-5 text-destructive">{problem}</p>}
          <Button type="submit" size="lg" disabled={busy || mismatch}>
            {busy ? "One moment…" : mode === "signup" ? "Create account and enter" : "Sign in"}
          </Button>
          {offline && <Button type="button" variant="outline" onClick={onDemo}>Explore the demo farm</Button>}
        </form>
      </main>
    </div>
  )
}

/** The signed-in person in the header, with sign-out. */
export function AccountBadge() {
  const { person, leave } = useAccount()
  if (!person) return null
  return (
    <div className="hidden items-center gap-2 md:flex">
      <span className="label-mono hidden max-w-[140px] truncate xl:inline" title={`${person.nickname} (${person.role})`}>
        {person.nickname}{person.role === "mentor" && <span className="ml-1 bg-signal px-1 text-[#0a0a0a]">mentor</span>}
      </span>
      <Button size="icon-sm" variant="ghost" aria-label="Sign out" onClick={leave}><LogOut /></Button>
    </div>
  )
}
