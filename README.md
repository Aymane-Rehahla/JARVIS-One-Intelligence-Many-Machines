# JARVIS

**JARVIS** (Just A Rather Very Intelligent System) is one intelligence made of many machine nodes. Each machine carries
its own local AI node; together the nodes form **one JARVIS**, which grows with every machine added. You always talk to
one JARVIS, through the node closest to you; the other nodes answer through it.

The proof of concept runs on a realistic simulated farm (the **Matrix**), the test bench: two egg incubators, a central
HVAC and an animal-protection unit, each with its own node. **The goal is to catch faults no single machine can see:**
incubator-1 notices its workspace warming while its own heater backs off, learns from the HVAC node that the compressor
failed, and names the HVAC as the cause.

Every node has a divided mind:

| Part | What it does | How |
|---|---|---|
| **Reasoning** | notices, diagnoses, names the cause, every second | a trained Sakana Continuous Thought Machine (CTM); hand-written rules as baseline and fallback |
| **Memory (CMM)** | remembers what matters: episodes, facts, hypotheses, what normal looks like | SQLite per node; nightly consolidation |
| **Knowledge** | the machine manual, a domain guide and the shared rules, all approved by the owner | `memory/rom/`, indexed only once approved |
| **Words** | explains, answers, asks other nodes, cites its documents, verifies every number | Qwen 3.5 2B roleplay (incubator-1, the voice of JARVIS) and Qwen3-0.6B (the other three), on llama.cpp |
| **Voice** | hands-free conversation | "hey jarvis", Silero VAD, Parakeet speech-to-text, Kokoro text-to-speech (incubator-1) |

The language model **talks; it never acts**. Machine actions come only from the reasoning, through a deterministic
safety gate (`docs/decisions/0003-llm-never-acts.md`).

## Status

Phase 6, the language layer. The plan is `docs/phase6-llm-plan.md` (v3); progress and its evidence are in `WORKLOG.md`.

| Step | What | State |
|---|---|---|
| 6.0 | Set up: tree, environment, models, website copy, knowledge tooling, tools; the v3 changes | see `WORKLOG.md` |
| 6A | Engines, messaging, gateway, the launcher; hot-join | next, after the owner approves the knowledge sources |
| 6B, 6W, 6C | Tools and documents; website live; minds and agent | 24–27 Oct |
| 6D | The trained brains go live (after the Phase 5 training ends) | 27–28 Oct |
| 6E, 6F | Voice; optimisation, evaluation, rehearsal with the hot-join demo | 28–31 Oct |

**It can't run end to end yet.** `launcher\start_all.ps1`, which starts everything, arrives in step 6A.

## Setting up and testing (Windows, PowerShell, in this folder)

```powershell
C:\Users\Aymane\AppData\Local\Python\pythoncore-3.12-64\python.exe -m venv .venv   # once
.venv\Scripts\python -m pip install --require-hashes -r requirements.lock           # exact, hashed versions
.venv\Scripts\python -m pytest                                                      # every test
.venv\Scripts\python tools\hash_models.py check                                     # model files intact?
.venv\Scripts\python tools\knowledge.py status                                      # knowledge: draft or approved
```

## Map of the folders

| Folder | What is in it |
|---|---|
| `nodes/` | **the four nodes that form JARVIS**, one complete folder each, with identical code; see `nodes/README.md` |
| `matrix/` | the simulated farm, JARVIS's test bench (copied in 6D from the code the brains were trained with) |
| `interface/` | the website (`jarvis-web`), the gateway (its only door into the system) and the message schemas |
| `models/` | model weights, stored once, with `MANIFEST.json` (SHA-256 of every file) |
| `third_party/` | llama.cpp (Vulkan build for the RX 580) and the NATS server |
| `launcher/` | start and stop everything; `ports.toml` (all on 127.0.0.1) |
| `tools/` | `hash_models.py`, `resource_monitor.py` (with the RAM guard), `knowledge.py`, `persona_search/` |
| `tests/` | one command, `pytest`; `DEFERRED.md` lists tests waiting for the training to end |
| `docs/` | the plan, decision records, the knowledge sources and simulation facts, results |
| `_dev/` | a read-only copy of the farm, used as a test feed until 6D |
| `logs/` | one JSON-lines log per program (not in git) |

## Ground rules (from the plan, section 2)

1. Don't disturb the Phase 5 training, which runs from its own folder until 27 Oct.
2. The CMM belongs to the reasoning; the language model never opens it.
3. The language model talks; it never acts.
4. JARVIS sees public telemetry only: no labels, no fault schedules, no mentor clicks.
5. The safety path never depends on the language model.
6. No invented numbers: every number said must match a tool result.
7. Honest labels: demo, "live: simulated brains", "live: trained brains".
8. Evidence, not claims: a step is done when its checks pass and the evidence is in `WORKLOG.md`.
9. One JARVIS, one name: the project's old name appears nowhere in this folder (tested).
10. While the training runs: one test or build at a time, at most one small LLM server, and the RAM guard watches what we start.

## Hardware

AMD Ryzen 5 5600G (6 cores, 12 threads), 16 GB RAM, AMD Radeon RX 580 2048SP (8 GB, Vulkan). Both language models run on
the RX 580; the CTMs, embeddings and voice run on the CPU.
