import { cn } from "@/lib/utils"

/**
 * The language side of JARVIS, one design choice per card: what was chosen, why, and what it measured
 * (docs/results.md, docs/decisions.md). The numbers are copied from the evaluation report, misses included.
 */
type Choice = { id: string; title: string; chose: string; why: string; measured?: string; decision?: string; star?: boolean }

const CHOICES: Choice[] = [
  {
    id: "agent",
    title: "An agent, not a chatbot",
    chose: "The model plans with typed tools: read a machine's state, search the manuals, ask another node, offer an acknowledgement. Every call is checked against a schema before it runs.",
    why: "The answer comes from the farm's own data, fetched for each question, not from what the model remembers.",
    measured: "E4: 46 of 46 tool calls valid",
    decision: "0018",
    star: true,
  },
  {
    id: "acts",
    title: "It talks; it never acts",
    chose: "No tool can switch a heater, a fan or a valve. Machines act only through their own reasoning and safety gate; the read-only MCP server exposes node-local reads only.",
    why: "A wrong sentence is a nuisance. A wrong action on a live farm can kill the flock.",
    measured: "E8: 0 unsafe calls, 0 actions, 0 memory access",
    decision: "0003, 0014",
    star: true,
  },
  {
    id: "rag",
    title: "Hybrid retrieval (RAG)",
    chose: "SQLite FTS5 keyword search and bge-small-en-v1.5 vectors (ONNX, on the CPU), fused by reciprocal rank (k = 60). Answers cite the manual passage.",
    why: "Keywords catch exact part names and codes, vectors catch paraphrases; fusing them beats either alone.",
    measured: "E3: Recall@4 0.891 (target 0.9)",
    decision: "0008",
    star: true,
  },
  {
    id: "approval",
    title: "Only approved knowledge",
    chose: "Each manual has a header and a SHA-256 recorded when the owner approves it. A changed file goes back to draft and leaves the index.",
    why: "JARVIS must never cite text the owner has not read, nor contradict the simulation.",
    decision: "0008",
  },
  {
    id: "verify",
    title: "Every sentence verified",
    chose: "Before a sentence is shown or spoken, its numbers, times and claimed actions are checked against the evidence the tools returned. A failed sentence is regenerated or replaced by a reply built from the data.",
    why: "Small models invent numbers. The check makes that visible and stops it.",
    measured: "E1: 44 of 44 numbers grounded after the check (24 of 27 before)",
    decision: "0018",
    star: true,
  },
  {
    id: "models",
    title: "Small local models, one per node",
    chose: "Qwen 3.5 2B speaks for incubator-1; each other node runs Qwen3-0.6B. Four llama.cpp servers share one RX 580; the KV cache stays f16.",
    why: "Nothing leaves the farm, it works offline, and one node's server failing leaves the others answering.",
    measured: "E6: first sentence 3.28 s (target 1.5 s)",
    decision: "0013, 0017",
  },
  {
    id: "peers",
    title: "One JARVIS, many minds",
    chose: "incubator-1 asks the other nodes over NATS when a question is about their machines; what a peer says is passed on, never treated as evidence.",
    why: "Each node knows its own machine best; no node may speak for another's numbers.",
    measured: "E5: routing 88.2 % (target 95 %)",
    decision: "0006, 0001",
  },
  {
    id: "wall",
    title: "The epistemic wall",
    chose: "The model sees the brain's conclusions and the sensor readings, never its memory database or its internals.",
    why: "What JARVIS says stays traceable to data a person can check.",
    measured: "E9: 0 leaks",
  },
  {
    id: "ack",
    title: "Signed acknowledgements",
    chose: "When an alert can be acknowledged, the agent offers a token signed with HMAC, bound to the node, the incident and the farm run. Only the owner's Confirm click spends it.",
    why: "Words alone cannot silence an alarm, and an old token cannot be replayed on a new run.",
  },
  {
    id: "alerts",
    title: "Alerts from a template",
    chose: "A proactive alert is written from the brain's conclusion by code and spoken at once; the model does not compose it.",
    why: "An alarm must be fast and exactly right every time.",
    measured: "E6: alert 1.44 s (target 3 s)",
    decision: "0019",
  },
  {
    id: "voice",
    title: "Voice: hear, check, speak",
    chose: "Push-to-talk, Silero VAD (0.6 s of silence ends the turn), Parakeet TDT 0.6B to transcribe, Kokoro to speak, each sentence spoken as soon as it passes the check.",
    why: "Fully local speech, and nothing is said aloud that was not verified.",
    measured: "E6: about 8.5 s to the first spoken word (target 2.5 s)",
    decision: "0022",
  },
  {
    id: "drills",
    title: "Failure drills",
    chose: "A mentor can make the models time out, return broken JSON, crash or go silent, and cut radio links, while the farm runs.",
    why: "The reasoning and the safety gate must keep catching faults with every language model down.",
    measured: "E7: 12 of 12 graceful answers",
  },
]

export function LlmChoices() {
  return (
    <div className="space-y-6">
      <p className="max-w-3xl text-[14px] leading-6 text-muted-foreground">
        Twelve choices make the language side of JARVIS. The four marked <span className="bg-signal px-1 text-[#0a0a0a]">core</span> make
        it an agent you can trust: it fetches its facts, cites them, checks every sentence and never touches a machine.
        The measured numbers are from the evaluation report, misses included.
      </p>
      <ol className="grid border-l border-t sm:grid-cols-2 xl:grid-cols-3">
        {CHOICES.map((choice, index) => (
          <li key={choice.id} className={cn("flex flex-col gap-3 border-b border-r p-5", choice.star && "bg-secondary/60")}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="label-mono text-muted-foreground">{String(index + 1).padStart(2, "0")}</span>
              {choice.star && <span className="label-mono bg-signal px-1 text-[#0a0a0a]">core</span>}
            </div>
            <h4 className="display-sm text-[17px]">{choice.title}</h4>
            <p className="text-[13px] leading-5">{choice.chose}</p>
            <p className="text-[13px] leading-5 text-muted-foreground"><span className="label-mono">Why // </span>{choice.why}</p>
            <div className="mt-auto flex flex-wrap gap-x-4 gap-y-1 pt-2">
              {choice.measured && <span className="label-mono text-[11px]">{choice.measured}</span>}
              {choice.decision && <span className="label-mono text-[11px] text-muted-foreground">decision {choice.decision}</span>}
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}
