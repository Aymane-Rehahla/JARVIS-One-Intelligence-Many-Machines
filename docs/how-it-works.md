# How JARVIS works

JARVIS is one mind made of machine nodes. Every machine carries its own node; together the nodes are one JARVIS
that keeps thinking. You always talk to one JARVIS, through incubator-1; it asks the other nodes for you.

The farm is the test bench, not the product. Two incubators, a barn climate unit (HVAC) and an animal-protection unit
(water and feed) run in the **Matrix**, a physics simulation with noisy sensors, where faults can be injected safely.

## One node, four parts

| Part | What it does | How |
|---|---|---|
| **Reasoning (CTM)** | every second, reads its machine's sensors and concludes: NORMAL, HANDLED_BY_MACHINE, WATCHING, ESCALATE_TO_USER or DANGER, with a diagnosis | a trained Continuous Thought Machine (Sakana AI's design): about 0.58 million parameters, 256 neurons thinking 16 internal steps per second, carrying their state from one second to the next; one brain per machine type |
| **Memory (CMM)** | records what the reasoning concluded: incidents, what normal looks like | per-node store, owned by the reasoning; the language model never opens it |
| **Knowledge** | the machine's manual and the farm's shared rules (20 documents, approved by the owner) | hybrid search: SQLite FTS5 keywords + bge-small-en-v1.5 vectors, fused |
| **Words** | answers questions, asks other nodes, cites its documents | Qwen 3.5 2B (incubator-1, the voice of JARVIS) and Qwen3-0.6B (the other three), each on its own llama.cpp server |

incubator-1 also runs the **voice**: push-to-talk, Silero VAD, Parakeet TDT 0.6B speech-to-text and Kokoro
text-to-speech, all through sherpa-onnx on the CPU.

## The programs (one PC, all on 127.0.0.1)

| Program | Port | What it does |
|---|---|---|
| NATS | 4222 | the message bus between every program |
| farm runtime (`matrix/farm_runtime.py`) | 8000 | ticks the Matrix and the four CTM brains; publishes only public telemetry and the brains' conclusions |
| four nodes (`nodes/<node>/runtime.py`) | 8101-8104 | each follows its own machine and answers the others over a simulated radio; incubator-1 also runs the agent, the alerts and the voice |
| four llama.cpp servers | 8081-8084 | the language models; incubator-1's has two slots, so two people are answered at once |
| gateway (`interface/gateway`) | 8010 | the website's only door: accounts, farm snapshots, chat, voice, faults |
| website (`interface/web`) | 5173 | React, Three.js; every message checked with zod |
| supervisor (`launcher/`) | | starts everything in order, health-checks it and restarts what falls over |

## From a fault to an alert, from a question to an answer

```
 Matrix (physics, machines, faults) --every second--> 4 CTM brains + safety gate
        | public telemetry + each brain's conclusion       | the gate acts on evidence only
        v                                                  v
 NATS: farm.<node>.status / assessment / events / incident     machine commands inside the Matrix
        |
        +--> each node: its own machine --radio--> incubator-1
        |        ESCALATE / DANGER --> alert desk --> template alert --> website banner + spoken alert
        |
 owner --chat or voice--> gateway --> incubator-1's agent
                                       - refusals and routing decided in code
                                       - its own data + the machines asked about, in parallel
                                       - the 2B model writes; every sentence is checked against this turn's data
                                       - streamed back with farm time and citations
```

**The calm farm.** Nothing alerts the owner until someone injects a fault (Simulator page or the API); then only the
machines that fault touches may alert. The brains keep assessing everything, every second.

**How it talks.** Small talk gets a friend's answer: a warm, experienced engineer, relaxed, no farm report. A question
about the farm gets facts from live data with units and farm time. When any machine needs the owner, the tone turns
serious and short. The tone is chosen in code (`llm/personality.py`), not by the model.

**Mentors first.** Two people can use JARVIS at once. When more questions arrive, they wait in line, and a mentor's
question goes to the front.

## The rules, and where the code holds them

| Rule | Where |
|---|---|
| The language model talks; it never acts | its tools are read-only; refusals are in code; a sentence claiming an action is never sent |
| It never opens the CMM | the CMM lives in the farm runtime; a static test checks no node source opens it |
| JARVIS only sees what a real farm would show | the runtime publishes public telemetry only; measured: 0 leaks of fault names or schedules |
| The safety path never depends on the language model | the brains and the gate import nothing from the language layer; with every language model off, they still catch faults |
| No invented numbers | every sentence is checked before it is sent; a failing sentence is regenerated or replaced by a template from the data |
| Honest labels | "demo", "live: simulated brains" or "live: trained brains" on every page and message |

## How it was built

- **Before October 2026:** the Matrix farm simulation, its scenario library and hand-written rule brains, then the
  training of the CTM brains (release v3.1).
- **23-27 Oct:** the runtime on one PC, NATS, the nodes, the gateway, the read-only tools, document search over
  owner-approved manuals, and the number checker.
- **30-31 Oct:** the website on live data, the agent, the trained brains inside JARVIS, the voice, the calm farm
  with its 20-fault menu, parallel answers with mentors first, and the friend persona.
