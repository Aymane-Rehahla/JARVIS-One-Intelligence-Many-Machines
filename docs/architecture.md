# JARVIS: the architecture (Phase 6, 1 Nov 2026)

JARVIS is one mind made of machine nodes. Every machine has its own node with three parts: a **reasoning brain**
(a trained Continuous Thought Machine, CTM) that notices, weighs and concludes every second; a **memory** (the CMM)
that records what it concluded; and **words** (a small language model) that answer questions about what the
reasoning concluded. Only incubator-1 speaks to the owner; it asks the other nodes. The words never act: a safety
gate outside the brain checks the evidence before any action. The farm, simulated in the **Matrix**, is the test
bench where JARVIS proves itself.

## The programs (one PC, all on 127.0.0.1; launcher/ports.toml)

| Program | Folder | Port | What it does |
|---|---|---|---|
| NATS | `third_party/nats-server` | 4222 | the message bus between every program |
| the farm runtime | `matrix/farm_runtime.py` | 8000 (Matrix API) | ticks the Matrix and the four brains; publishes only public telemetry and the brains' outputs |
| four nodes | `nodes/<node>/runtime.py` | MCP 8101-8104 | each follows its own machine, answers peers over the radio, runs its own language model's tools; incubator-1 also runs the agent, the alert desk and the voice |
| four llama.cpp servers | `third_party/llama.cpp` | 8081-8084 | incubator-1: Qwen 3.5 2B roleplay; the others: Qwen3-0.6B (decision 0013) |
| the gateway | `interface/gateway/gateway.py` | 8010 | the website's only door: farm snapshots, chat, audit, voice, the demo faults, the owner's acknowledgement |
| the website | `interface/web` | 5173 | React; every message checked with zod; live mode through the gateway, or a labelled demo |
| the supervisor | `launcher/supervisor.py` | – | starts, health-checks and restarts all of the above; `launcher/start_all.ps1` and `stop_all.ps1` |

## How a fault becomes an alert, and a question becomes an answer

```
 Matrix (physics, devices, faults) ──tick──► 4 trained CTM brains + safety gate (farm runtime)
        │ public telemetry + each brain's conclusion                │ the gate acts on evidence only
        ▼                                                           ▼
 farm.<node>.status / .assessment / .events / .incident  (NATS)   machine commands inside the Matrix
        │
        ├──► each node: its own machine only ──radio (ZigBee rules)──► incubator-1
        │        └─ ESCALATE / DANGER ──► AlertNotice ──► alert desk ──► OwnerAlert (template) ──► website banner, voice
        │
 owner ── website Chat / push-to-talk ──► gateway ──► incubator-1's agent
                                                        ├─ refusals and routing in code
                                                        ├─ its own data + the asked peers (ask_peer, in parallel)
                                                        ├─ the 2B model writes; each sentence verified against this turn's data
                                                        └─ ChatChunks + ChatDone (farm time, label, citations) ──► website / voice
 owner's "acknowledge" + confirm click ──► gateway ──► incubator-1 checks the single-use token ──► Matrix API ack
```

## The ground rules, and where the code holds them

| Rule | Where |
|---|---|
| The language model talks and never acts | the tools are read-only (`llm/tools.py`); refusals in code (`llm/agent.py`); a sentence claiming an action, naming a tool, or stating a machine action not in the data is never sent |
| It never opens the CMM | the CMM runs in the farm runtime (decision 0021); no node source opens it (E8's static check) |
| The epistemic wall | the farm runtime publishes public telemetry and outputs only; E9: 0 leaks on every subject a node hears |
| The safety path never depends on the language model | the brains and the gate run in the farm runtime, which imports nothing from `llm/` (test); P3's real run with no language model at all caught three of the four faults |
| Every number said matches a tool result of the same turn | `verify_reply()` on every sentence before it is sent; E1 |
| Every answer states its farm time | the agent adds it when the model leaves it out |
| Honest labels | "demo", "live: simulated brains", "live: trained brains" on every message and page |

## Where to look

- Decisions: `docs/decisions/` (0001-0022). Results: `docs/results-phase6.md`. The story: `docs/story.md`.
- The day-by-day evidence: `WORKLOG.md`. The builder's running log: `docs/handoff/PROGRESS.md`.
