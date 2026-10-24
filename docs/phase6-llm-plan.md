# Phase 6 plan: the JARVIS LLM layer (v3.1)

v3.1, 24 Oct 2026 (v3 was 23 Oct, v2 22 Oct 2026) · Phase 5 v2 training runs until **27 Oct 12:00 at the latest, moved
back by the owner's pause of 3 h 39 min 31 s** (24 Oct 01:15:49 to 04:55:20) · Hand-in **1 Nov**

**Reading order:** creating the tree (section 1) is safe at any time. Read the ground rules (section 2) and the engineering standard (section 3) before anything else. Section 10 is the working procedure for whoever builds this.

**The bar:** this project is judged by PhD mentors. Every module meets the engineering standard, every claim in the presentation is backed by a measured number and a script that reproduces it, and the safety-critical path never depends on the LLM.

## Changelog

**v3.1 (24 Oct 2026), the owner's pause, then the owner's correction:**
1. **The owner paused the CTM training** from 24 Oct 01:15:49 (the last training work ended) to **04:55:20 (the
   training restarted: wrap-up B2, then the exam)**: 3 h 39 min 31 s, by which the training's end time moves back
   (evidence in `WORKLOG.md`). In the pause the limits of ground rule 1 and rule 10 were lifted; **they apply again**.
2. **The owner's correction (24 Oct 13:37):** until the owner says the training is done, **no LLM server is started and
   the full test suite is not run** (deferred as D3 and D4 in `tests/DEFERRED.md`). The lighter 6A work goes first:
   contracts, gateway, supervisor code, single test files, one at a time. After the training, the **trained brains and
   `cmm.py` arrive in a release folder**, and the farm runtime switches from the rule brains to them (step 6D).
3. **Order:** commit a checkpoint, finish the 6.0 review and fixes, tag `step-6.0`; send the owner the public-source
   list and continue while the owner reviews it; the light 6A work; after the training: 6A's two LLM servers loaded
   for real, the persona search from the owner's persona spec (`docs/persona-spec.md`: system prompt, example
   exchanges, settings grid, the 48-line test set and its scoring) with the top 3 sent to the owner for blind rating,
   6E voice with its latency measurement, then 6B, 6W and 6C as the plan has them, and 6D with the released brains.
4. **No parallel helpers;** a commit after every finished piece, so a usage cutoff never loses more than minutes.
5. **The old name, before every commit and tag:** `tools/name_guard.py staged --message <file>` before each commit;
   before each tag, the full scan (`JARVIS_FULL_NAME_SCAN=1` on `tests/test_no_old_name.py`) and
   `tools/name_guard.py history`.

**v3 (23 Oct 2026), the owner's seven changes:**
1. **One JARVIS, many nodes.** The project is JARVIS (Just A Rather Very Intelligent System). Each machine carries its own
   local AI node; together the nodes form one JARVIS that grows with every machine added. The owner always talks to one
   JARVIS, through incubator-1; the other nodes answer through it. The farm is the test bench. The root folder is
   `JARVIS/`, the mother folder `nodes/`, the website package `jarvis-web` (decisions 0006, 0007).
2. **The old project name appears nowhere** inside `JARVIS/`: names, contents, logs, git history. A test enforces it,
   copied code included (decision 0010).
3. **Knowledge per node:** machine manual (the authority on numbers), a domain guide from approved public sources, and
   the shared owner rules, safety list, farm layout and contacts. Headers with node, sources, version and status; only
   owner-approved documents are indexed, recorded by hash; numbers are tested against the simulation (decision 0008).
4. **Persona search** for the talking model: system prompts × example exchanges × sampling, scored automatically
   (character, British tone, concise, no "As an AI", must pass `verify.py`), the owner's blind rating of the top 3
   decides. Harness and smoke test now, full run after training (decision 0009).
5. **Hot-join:** a node started while JARVIS runs announces its capabilities; incubator-1 answers about it within
   seconds, with no restart. Integration test in 6A/6B, demo step in 6F.
6. **Limits while training runs:** parallel helpers may write code, docs and reviews, but only one runs tests or builds
   at a time. At most one LLM server (Qwen3-0.6B, plumbing tests); the 2B server only in short runs with free RAM above
   3 GB. The resource monitor's RAM guard stops what we started when free RAM stays below 2 GB (decision 0011).
7. **Recorded:** this changelog and decisions 0006–0011.

**Run rules (v3):** every step ends with tests green, evidence in `WORKLOG.md`, an independent review with its findings
fixed, a commit and a tag. Stop and report instead of continuing if a ground rule would be bent, a test fails and can't
be fixed properly, free RAM stays below 2 GB, anything would touch the training's folders or processes, or something
needs a decision that isn't in the plan. Every commit passes `tools/name_guard.py staged`, and every tag the full
old-name scan and `tools/name_guard.py history` (v3.1).

**v2 (22 Oct 2026):** what changed from v1: voice is installed in all four nodes (switched on only in incubator-1); emotion2vec+ is added; the pre-downloaded models are listed; the website is integrated now through a gateway (step 6W); new sections cover the engineering standard, evaluation and evidence, and the working procedure.

Built from the Phase 6 notes and decision list, `docs/phase5-6-design.md`, `docs/phase5-v2-plan.md`, `docs/jarvis-nodes.md`, the website at `C:\Users\Aymane\Desktop\project`, and the pre-downloaded assets.

---

## 1. Step one: create the tree

Create `C:\Users\Aymane\Desktop\JARVIS\`, next to the folder the training runs from, and run `git init` in it. Create folders and READMEs only, with no empty stub files: each file appears when the step that builds it runs. Items marked "(added)" weren't in the original notes; each one exists for a reason given in this plan.

### 1.1 The whole project

```
JARVIS/
├── README.md                    what JARVIS is, how to start it, map of the folders
├── WORKLOG.md                   what was done, the evidence, open issues; updated every step  (added)
├── launcher/                    start_all.ps1 · stop_all.ps1 · ports.toml · supervisor        (added)
├── third_party/
│   ├── llama.cpp/               Vulkan build of llama-server for the RX 580
│   └── nats-server/             nats-server.exe + nats.conf
├── models/                      weights stored once; each node loads its own instance
│   ├── MANIFEST.json            every model file with its SHA-256, size and source            (added)
│   ├── llm/                     Qwen-3.5-2b-roleplay-tuned.Q8_0.gguf · Qwen3-0.6B-Q8_0.gguf
│   ├── embeddings/              bge-small-en-v1.5/ (model.onnx + tokenizer files)
│   └── voice/                   openWakeWord "hey jarvis" · Silero VAD ·
│                                Parakeet TDT 0.6B v2 int8 · emotion2vec+ base · Kokoro-82M v1.0
├── matrix/                      the simulated farm (copied after training, step 6D)
│   └── farm_runtime.py          ticks the farm + the four reasoning brains, publishes on NATS (added)
├── nodes/                       THE MOTHER FOLDER: the four nodes that form one JARVIS
│   ├── README.md                anatomy of a node + map of the four
│   ├── incubator-1/             SPEAKS · Qwen 3.5 2B roleplay · bge-small · voice ON
│   ├── incubator-2/             silent · Qwen3-0.6B · bge-small · voice installed, OFF
│   ├── hvac/                    silent · Qwen3-0.6B · bge-small · voice installed, OFF
│   └── animal-protection/       silent · Qwen3-0.6B · bge-small · voice installed, OFF
├── interface/
│   ├── web/                     the website, copied from Desktop\project (step 6.0)
│   ├── gateway/                 the only door between the website and the system (step 6A)    (added)
│   └── contracts/               JSON Schemas of every message, exported from Python           (added)
├── tests/
│   ├── DEFERRED.md              tests postponed until training ends, with exact commands      (added)
│   ├── test_nodes_in_sync.py    fails if the four nodes' shared code differs               (added)
│   └── llm/                     golden/ · retrieval/ · integration/ · failures/ · safety/ · eval/
├── tools/                       also: knowledge.py (approval, indexing) · persona_search/ (v3)
│   ├── sync_nodes.py            copies shared code from incubator-1 to the other three        (added)
│   ├── hash_models.py           writes and checks models/MANIFEST.json                        (added)
│   └── resource_monitor.py      logs CPU, RAM and VRAM every 10 s; warns near the limits      (added)
├── _dev/farm_poc_snapshot/      read-only copy of today's farm_poc, a test feed until 6D      (added)
├── logs/                        (not in git) one JSON-lines log per program
└── docs/                        the story folder
    ├── decisions/               one short record per big decision (why NATS, why two servers…)  (added)
    ├── knowledge/               sources-proposal.md (owner approval) · simulation_facts.toml (v3)
    └── results-phase6.md        the measured results, generated by tests/llm/eval              (added)
```

### 1.2 One node (identical in all four folders)

```
nodes/<node>/
├── README.md                anatomy of this node and what makes it different
├── node.toml                identity: id, machine, model + server slot, speaks, voice, ports   (added)
├── node.py · runtime.py · contracts.py
│                            node.py wires the parts · runtime.py is this node's own program
│                            contracts.py holds the message and output types (versioned)
├── reasoning/               OUR REASONING: notices, diagnoses, names the cause
│   │                        ── frozen: README only until 6D
│   ├── encoders.py          what the brain sees: own readings, peers, memory
│   ├── ctm/                 our Sakana CTM + README (what we changed, what won)
│   └── rule_brain.py        the rules it must beat, and the fallback
├── memory/                  THE CMM: reasoning only, the LLM never opens it
│   │                        ── frozen: README only until 6D
│   ├── working.py           RAM: what the brain reads every tick
│   ├── cmm.py               episodes, facts, hypotheses, normal profile, sleep (SQLite)
│   └── rom/                 the node's knowledge: machine manual, domain guide, shared owner rules, safety list,
│                            farm layout, contacts; approvals.json records what the owner approved (by hash)
├── llm/                     THE WORDS
│   ├── engines.py           connects to its llama.cpp server and slot; health checks
│   ├── jarvis_speaker.py    the talking mind (used where node.toml says speaks = true)
│   ├── peer_minds.py        the narrow mind: /no_think, forced JSON (used where speaks = false)
│   ├── agent.py             request → decide → tools/peers → verify → reply
│   ├── tools.py             the tools, typed and permissioned (section 6)
│   ├── capabilities.py      who knows what, so questions reach the right node
│   ├── context_first.py     answer from context first; reuse answers only while valid
│   ├── documents.py         hybrid search over rom/: keywords + bge-small vectors
│   ├── conversation.py      conversation state and summaries of long chats
│   ├── personality.py       stable character, tone fits the moment
│   ├── persona/             roleplay system: etiquette, humour (later)
│   ├── recovery.py          retries, deadlines, fallbacks
│   ├── verify.py            every number said must match a tool result
│   └── audit.py             which tools, which evidence, how long each step took
├── voice/                   THE VOICE BOX (built in 6E; runs only where node.toml says voice = true)
│   ├── pipeline.py          turn manager: listen → understand → answer → speak, interruptions
│   ├── wake_word.py         openWakeWord "hey jarvis"; push-to-talk fallback
│   ├── vad.py               Silero VAD: knows when the user stops talking
│   ├── stt.py               Parakeet TDT 0.6B v2 int8 (sherpa-onnx)
│   ├── emotion.py           emotion2vec+ base: a tone hint (joke vs real anger)
│   ├── tts.py               Kokoro-82M: voice picker, speed
│   └── speaker_arbiter.py   who speaks if incubator-1 can't (later)
├── safety/gate.py           the only way an action reaches a machine
├── comms/
│   ├── peer_network.py      simulated ZigBee: latency, loss, cut-offs
│   ├── nats_link.py         NATS connection: subjects, request-reply, reconnect              (added)
│   ├── events.py            async event bus inside this node
│   └── mcp.py               serves tools.py over MCP
└── data/                    (not in git) cmm.sqlite · llm.sqlite · docs_index.sqlite
```

### 1.3 What differs between the four

The four folders hold identical code, voice included. Only three things differ:

| | incubator-1 | incubator-2 | hvac | animal-protection |
|---|---|---|---|---|
| `node.toml` | speaks, **voice on**, Qwen 3.5 2B roleplay on server 1 | silent, voice off, Qwen3-0.6B on server 2, slot 0 | silent, voice off, server 2, slot 1 | silent, voice off, server 2, slot 2 |
| `memory/rom/` | incubator manual; incubation and hatching guide (after source approval); shared owner rules, safety list, farm layout, contacts | same as incubator-1 | HVAC manual; ventilation guide (after source approval); the shared four | animal-protection manual; heat, fire, flood and storm guide (after source approval); the shared four |
| `data/` | its own databases | its own | its own | its own |

**Why incubator-1 speaks:** it is always the node closest to the user. The other three have the same voice box, switched off so they can't start talking or listening by accident. A switched-off voice box loads nothing, so it costs no RAM or CPU.

Identical code has two benefits:
- **Failover:** any node could take over speaking by flipping its `node.toml` (speaker arbitration, later).
- **One fix reaches all four.** `tools/sync_nodes.py` copies shared code from incubator-1 to the others without touching `node.toml`, `rom/` or `data/`, and `tests/test_nodes_in_sync.py` fails if the copies drift apart.

---

## 2. Ground rules (read before touching anything)

1. **Don't disturb the training.** Until it ends:
   - no edits, moves or deletes in the training's folders (its `farm_poc/`, `ctm_training/` and `base ctm/`) or its run folder on F:. Reading and copying from them is fine;
   - no package installs into the training's Python environment;
   - never create a `STOP` file and never kill `python.exe` processes you didn't start;
   - watch the RAM: the training uses about 8 GB of the 16 GB. Before training ends, load at most the two LLM servers, stop them when not testing, and load no voice models (those come in 6E). If free RAM drops below about 2 GB, stop what you started;
   - no CPU-heavy jobs (speech benchmarks, large indexing). The RX 580 is free, so the LLM servers run there, at below-normal priority.
   - **A test that might disturb the training is not skipped. It is written, marked deferred, and listed in `tests/DEFERRED.md` with its exact command, then run in 6D.**
2. **The CMM belongs to the reasoning.** The LLM never opens `cmm.sqlite` or working memory. It only sees what the reasoning publishes: readings, assessment, evidence and incident summaries.
3. **The LLM talks; it never acts.** Machine actions and emergency calls come only from the reasoning, through `safety/gate.py`. The only write the LLM can trigger is the owner's acknowledgement of an alert, and only with the owner's words plus a confirm click on the website.
4. **Epistemic wall.** JARVIS sees public telemetry only. It never sees scenario labels, fault schedules, evaluator scores, or the mentor's fault-injection clicks; injected faults reach JARVIS only through their physical effects.
5. **The safety path never depends on the LLM.** If every LLM program crashes, the farm, the reasoning and the safety gate keep working. This is tested (section 8, E7).
6. **No invented numbers.** Every number JARVIS says must match a tool result from the same turn (`verify.py`), and every answer says the farm time its data comes from.
7. **Honest labels.** The website shows CTM output and LLM wording separately, and marks "demo", "live: simulated brains" (rule brain) and "live: trained brains" (CTM).
8. **Evidence, not claims.** A step is done when its "Done when" checks pass and the commands and outputs are written in `WORKLOG.md`. Nothing is called done or working without that evidence.
9. **One JARVIS, one name.** Each machine's AI is a node; together they are JARVIS, and the owner talks to it through
   incubator-1. The project's old name appears nowhere inside `JARVIS/` (tested).
10. **Resource limits while training runs:** one test or build at a time; at most one LLM server (Qwen3-0.6B) for
   plumbing; the 2B server only in short runs with free RAM above 3 GB; memory-heavy programs register with the RAM guard.

---

## 3. Engineering standard (definition of done for every module)

| # | Standard | What it means in practice |
|---|---|---|
| Q1 | **Typed** | Type hints everywhere. Every NATS message, tool input and output, gateway message and `node.toml` is a pydantic v2 model. `pyright` passes on `nodes/`, `tools/`, `tests/` and `interface/gateway/`; the website's `npm run typecheck` passes. |
| Q2 | **Validated at every border** | Every incoming message is validated and carries `schema_version`. Invalid or unknown-version messages are logged and dropped; they never crash a program. Contracts are defined once in Python and exported to `interface/contracts/`; the website checks every message with zod, and a test runs the same sample messages through both sides. |
| Q3 | **Tested** | Unit tests for every module, integration tests for every step, plus golden, retrieval, failure and safety suites. Coverage of at least 80 % on `llm/`, `comms/` and `interface/gateway/`. Property tests (hypothesis) for parsers such as `verify.py`'s number extraction. One command runs everything: `pytest`. |
| Q4 | **Bounded** | Every network call and wait has a timeout; every queue has a maximum size; every cache has a size or an expiry. Nothing can wait forever or grow without limit. |
| Q5 | **Observable** | JSON-lines logs per program in `logs/`. Each question gets a request id that follows it across nodes (NATS headers), so the audit view can show its whole path. Every program has a health check. `tools/resource_monitor.py` records CPU, RAM and VRAM. |
| Q6 | **Fail-safe** | The launcher's supervisor restarts a crashed program and logs it. A missing peer, model or server gives a clear fallback answer, never a hang or a made-up one (ground rule 5). |
| Q7 | **Secure** | Every service listens on 127.0.0.1 only. Retrieved document text and user text are data, never instructions: they are fenced off in prompts, and permissions are enforced in code, not in prompts. |
| Q8 | **Reproducible** | Python dependencies pinned in a lock file; npm uses `package-lock.json`; every model file's SHA-256 is in `models/MANIFEST.json` and checked at start; test seeds are fixed; a git commit and tag at the end of every step. |
| Q9 | **Documented** | Every folder's README says what it does, what goes in and out, and how to test it. Public functions have docstrings. Each big decision gets a short record in `docs/decisions/` (context, decision, alternatives, consequences). |
| Q10 | **Reviewed** | At the end of every step, an independent review (a fresh Claude session or subagent) checks the step's changes against this plan, the ground rules and this standard. Its findings are fixed before the next step starts. |

---

## 4. How the parts talk

### 4.1 Programs

```
 [farm runtime]  Matrix + the four reasoning brains in one tick loop (its own Python environment)
        │ publishes per node: public readings · events · assessment · incidents · link status
        ▼
 [nats-server] ◄──► [incubator-1]        agent · tools (MCP) · documents · conversation · VOICE
               ◄──► [incubator-2]        agent · tools · documents
               ◄──► [hvac]               agent · tools · documents
               ◄──► [animal-protection]  agent · tools · documents
               ◄──► [gateway] ◄──► [website]   farm view · chat · voice menu · audit · mentor panel
                          │
 [llama-server 1]  Qwen 3.5 2B roleplay, 1 slot  ◄── incubator-1
 [llama-server 2]  Qwen3-0.6B, 3 slots           ◄── incubator-2 · hvac · animal-protection
```

Default ports (`launcher/ports.toml`), all on 127.0.0.1: NATS 4222 · llama.cpp 8081 and 8082 · Matrix API 8000 · gateway 8010 · MCP 8101–8104 · website 5173.

### 4.2 Why NATS if we already have ZigBee?

They do different jobs:
- **ZigBee is the simulated radio:** the rules for how a message travels between machines (delay, loss, cut-offs). It is a rule, not a program.
- **NATS is the software postal service** that actually carries messages between the programs.

Without NATS, the four nodes must live inside one program. With NATS, each runs as its own program, like four real devices, which is what the four-folder design means. It also lets the farm runtime keep its own Python environment while the nodes use a new one. The cost is one more program to start, and the launcher handles that.

Rules:
- **Every message between nodes passes through `peer_network.py` first.** It applies the ZigBee delay, loss and cut-offs, then sends the message on NATS. When the Matrix cuts a link, the farm runtime publishes it on `farm.links`, and questions to that node fail too.
- **Observability and website messages skip the ZigBee rules.** They aren't farm radio.
- **The reasoning's own peer messages stay on today's bus.** The brains were trained with its timing, so moving them onto NATS would change their inputs.

### 4.3 NATS subjects

| Subject | From → to | Carries |
|---|---|---|
| `farm.<node>.status` | farm runtime → that node, gateway | public readings, every tick |
| `farm.<node>.events` | farm runtime → that node, gateway | public events and alarms of that machine |
| `farm.<node>.assessment` | farm runtime → that node, gateway | assessment, diagnosis, confidence, source machine, evidence |
| `farm.<node>.incident` | farm runtime → that node, gateway | incident summary when one opens, changes or closes |
| `farm.links` | farm runtime → all | which links are cut |
| `farm.sim` | farm runtime → gateway | farm time, running or paused, target and actual speed |
| `jarvis.<node>.heartbeat` | every node → all | "alive" every 10 s |
| `jarvis.<node>.ask` | node → node (request-reply) | a question and its JSON answer |
| `jarvis.<node>.capabilities` | every node → all, at start | topics it can answer, documents it holds |
| `jarvis.<node>.audit` | every node → gateway | tools used, evidence, timings |
| `jarvis.incubator-1.chat` | gateway ↔ incubator-1 | owner messages in; streamed replies out |
| `jarvis.incubator-1.voice` | incubator-1 → gateway | voice state, transcript, tone hint |
| `mentor.llm_failures` | gateway → nodes | switch LLM failure injections on or off (test only) |

Mentor fault clicks never go on NATS. The gateway sends them straight to the Matrix API (ground rule 4).

### 4.4 The website and the gateway

The website today runs on its own demo simulation: all data goes through `src/services/matrix.ts` (types such as `FarmState`, `JarvisNode`, `ChatMessage`) and the hook `src/hooks/use-demo-farm.ts`. Its only network call is a `/health` check to `VITE_MATRIX_API_URL`, and it deliberately never claims to be live.

Integration keeps all of that and adds a live source next to it:
- **`interface/gateway/`** (FastAPI, 127.0.0.1:8010) is the website's only door into the system. It subscribes to NATS, sends the website throttled snapshots (at most 5 per second) shaped for `FarmState`, and forwards only these actions:

  | Website action | Gateway route | Goes to |
  |---|---|---|
  | start, pause, reset, speed | `POST /sim/{action}` | Matrix API |
  | inject a demo fault | `POST /faults/{scenario_id}` | Matrix API (never NATS) |
  | chat message | WebSocket `/ws/chat` | `jarvis.incubator-1.chat` |
  | acknowledge an alert | `POST /alerts/{id}/ack` with a confirm token | the Matrix's existing owner-acknowledgement signal, so the brains see nothing new |
  | LLM failure switches | `POST /mentor/llm-failures` | `mentor.llm_failures` |

  Live streams: `/ws/farm` (devices, events, JARVIS nodes, peer messages, sim state), `/ws/chat`, `/ws/audit`, `/ws/voice`.
- **The website's four demo faults map onto the fault families the brains were trained on:**

  | Website `ScenarioId` | Matrix fault family |
  |---|---|
  | `heater_relay_stuck` | `heater_relay_stuck` |
  | `supply_fan_failure` | `supply_fan_failure` |
  | `humidity_sensor_drift` | `sensor_fault` (humidity drift variant; confirm the exact name in the Matrix scenarios) |
  | `supply_overvoltage` | `overvoltage` |

- **In the website:** a new `src/services/live.ts` (gateway client, every message checked with zod) and a `useFarm()` hook with the same interface as today's `DemoFarm`, choosing demo or live. The pages keep working unchanged. Demo mode stays available and clearly labelled; live replies never carry the `preview` flag.
- **The speed control is honest.** The website asks for 10×, 30×, 60× or 120×; the farm runtime reports the speed it actually reaches with four CTMs thinking, and the website shows both.

---

## 5. AI models in each node

| | incubator-1 | incubator-2 | hvac | animal-protection |
|---|---|---|---|---|
| Reasoning brain | CTM incubator brain (rule brain as fallback) | same incubator weights, own instance and state | CTM HVAC brain | CTM animal-protection brain |
| Language model | **Qwen 3.5 2B roleplay**, Q8_0, server 1 | Qwen3-0.6B, Q8_0, server 2 slot 0 | Qwen3-0.6B, slot 1 | Qwen3-0.6B, slot 2 |
| Document search | **bge-small-en-v1.5** (33 M parameters) + keyword search, own index | same | same | same |
| Wake word | openWakeWord "hey jarvis", push-to-talk fallback | installed, off | installed, off | installed, off |
| Voice detection | Silero VAD | installed, off | installed, off | installed, off |
| Speech-to-text | Parakeet TDT 0.6B v2, int8, via sherpa-onnx | installed, off | installed, off | installed, off |
| Speech emotion | emotion2vec+ base: a tone hint for the agent | installed, off | installed, off | installed, off |
| Text-to-speech | Kokoro-82M v1.0: voice picker (British male default), speed slider | installed, off | installed, off | installed, off |
| Memory (SQLite) | `cmm.sqlite` (reasoning) · `llm.sqlite` (conversations, events, audit) · `docs_index.sqlite` | same three, without owner conversations | same | same |

Where each runs: the two language models on the RX 580 (llama.cpp with Vulkan). The CTMs, bge-small and the voice models run on the CPU. Both language models together should fit in the RX 580's 8 GB with room to spare (estimate; measured in 6A).

**Downloads.** Some files are already in `C:\Users\Aymane\Desktop\jarvis 0.3.4\predownloaded assets`: **copy** them from there (don't move them) and download only the rest. Sizes are approximate. Every file's hash goes into `models/MANIFEST.json`.

| What | Size | Already downloaded? | Where |
|---|---|---|---|
| `Qwen-3.5-2b-roleplay-tuned.Q8_0.gguf` | 2.08 GB | **yes** | `models/llm/` |
| `Qwen3-0.6B-Q8_0.gguf` | 0.64 GB | **yes** | `models/llm/` |
| bge-small-en-v1.5: `model.onnx` + `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json`, `vocab.txt`, `config.json` | 133 MB | **yes**, all six files | `models/embeddings/bge-small-en-v1.5/` |
| llama.cpp, Vulkan build for Windows (`…-bin-win-vulkan-x64.zip`) | small | no | `third_party/llama.cpp/` |
| nats-server for Windows | ≈ 20 MB | no | `third_party/nats-server/` |
| openWakeWord "hey_jarvis" (ONNX) | a few MB | no | `models/voice/openwakeword/` |
| Silero VAD (ONNX) | ≈ 2 MB | no | `models/voice/silero-vad/` |
| Parakeet TDT 0.6B v2 int8 (sherpa-onnx package) | ≈ 0.6–0.7 GB | no | `models/voice/parakeet-tdt-0.6b-v2-int8/` |
| emotion2vec+ base (`emotion2vec/emotion2vec_plus_base`, runs with FunASR) | ≈ 0.35 GB | no | `models/voice/emotion2vec-plus-base/` |
| Kokoro-82M v1.0 (sherpa-onnx `kokoro-multi-lang-v1_0`, 53 voices; keep the whole folder) | ≈ 0.33 GB | no | `models/voice/kokoro-multi-lang-v1_0/` |

---

## 6. Tools each node gets

Permission levels: **read** (looks only), **ask** (talks to another node), **owner-confirmed** (only with the owner's words plus a confirm click).

| # | Tool | Returns | Comes from | Permission | incubator-1 | other three |
|---|---|---|---|---|---|---|
| 1 | `get_status(fields?)` | own machine's live readings and device health | `farm.<node>.status` | read | ✓ | ✓ |
| 2 | `get_assessment()` | own reasoning output: assessment, diagnosis, confidence, source machine, evidence | `farm.<node>.assessment` | read | ✓ | ✓ |
| 3 | `get_trend(signal, minutes)` | one reading's recent minimum, maximum and rate of change | own rolling buffer of `status` (not the CMM) | read | ✓ | ✓ |
| 4 | `list_events(since, severity?)` | own machine's public events and alarms | `farm.<node>.events`, stored in `llm.sqlite` | read | ✓ | ✓ |
| 5 | `get_incident(id or "current")` | the incident summary the reasoning published | `farm.<node>.incident`, stored in `llm.sqlite` | read | ✓ | ✓ |
| 6 | `search_documents(query, k=4)` | passages with file and section, for citing | own `rom/` index | read | ✓ | ✓ |
| 7 | `ask_peer(node, question)` | that node's JSON answer; several can run at once | `jarvis.<node>.ask` through the ZigBee rules, 3 s deadline | ask | ✓ | — |
| 8 | `recall_conversation(query)` | earlier conversations with the owner | own `llm.sqlite` (not the CMM) | read | ✓ | — |
| 9 | `acknowledge_alert(incident_id)` | the owner's acknowledgement, through the existing owner-acknowledgement signal | the owner's words + a confirm click (gateway token) | owner-confirmed | ✓ | — |

How they're used:
- **incubator-1's 2B model chooses its tools.** Tool calls are forced into a JSON schema: llama.cpp's built-in tool calling (`--jinja`) if the roleplay model's chat template supports it, otherwise a grammar. `verify.py` then checks the reply.
- **The 0.6B models don't choose tools.** Small models are unreliable at that. Code sorts each incoming question into a topic (`status`, `readings`, `incident_history`, `incubation`, `hazard_check`, `documents`), runs a fixed set of tools 1–6, and the 0.6B model only writes the JSON answer.
- **No language model gets:** machine commands, emergency calls, access to the CMM, hidden simulation data, mentor actions, or another node's tools (it must ask that node).
- **Every tool call is logged** in the audit with its inputs, outputs, duration and request id.

---

## 7. The requirements list, organized

Status: **Done** · **6.0–6F** (the build step in section 9) · **Later** · **No**.

### Models and engines

| # | Requirement | Decision | Status | Where |
|---|---|---|---|---|
| S1 | Roleplay-tuned model | `Qwen-3.5-2b-roleplay-tuned.Q8_0.gguf` (already downloaded), incubator-1 only | 6A | `models/llm/`, `llm/jarvis_speaker.py` |
| S2 | llama.cpp with Vulkan | two servers on the RX 580 | 6A | `third_party/llama.cpp/`, `llm/engines.py` |
| S15 | Prompt and KV caching | one slot per node; the prompt starts with fixed text (persona + tool schemas) so the cache is reused | 6A | `llm/engines.py` |
| B4 | Model routing | the CTM detects, the 2B model talks, the 0.6B models do narrow tasks | Done by design | — |
| C8 | Multiple LLMs | exactly two servers | Done by design | — |

### Agent

| # | Requirement | Decision | Status | Where |
|---|---|---|---|---|
| S4 | Agent runtime | request → context first → tools/peers → verify → reply | 6C | `llm/agent.py` |
| S9 | Streaming (text) | word by word to the website chat | 6C | `llm/agent.py`, gateway, website |
| S9 | Streaming (speech) | sentence by sentence to the voice | 6E | `voice/` |
| S10 | Conversation state | per conversation, in SQLite | 6B | `llm/conversation.py` |
| B8 | Summarising long chats | when a chat outgrows the context | 6C | `llm/conversation.py` |
| S16 | Structured tools | the 9 tools in section 6, typed and permissioned | 6B | `llm/tools.py` |
| S17 | Parallel tools | e.g. asking HVAC and animal protection at the same time | 6C | `llm/agent.py` |
| S18 | Self-verification | every number must match a tool result | 6C | `llm/verify.py` |
| A4 | Context-first answers | the latest readings and assessment are always in the prompt; an answer is reused only while its data is unchanged | 6B | `llm/context_first.py` |
| A6 | Capability registry | each node announces what it knows, also when it joins later (hot-join, v3) | 6B | `llm/capabilities.py` |
| H1 | Hot-join (v3) | start with three nodes, add the fourth, ask incubator-1 about it: answered within seconds, no restart | 6A · 6B · demo in 6F | `comms/`, `tests/llm/integration/` |
| A8 | Personality state | stable character; calm and short in danger, lighter when all is normal | 6C | `llm/personality.py` |
| P3 | Persona search (v3) | system prompts × 4–8 example exchanges × sampling; automatic scores; owner's blind rating of the top 3 decides | 6.0 harness · after training: full run | `tools/persona_search/`, `llm/persona/` |
| S3 | Roleplay system | persona, etiquette, humour | Later | `llm/persona/` |

### Memory and data

| # | Requirement | Decision | Status | Where |
|---|---|---|---|---|
| S13 | Long-term memory | reasoning: CMM v3 · LLM: its own conversation memory, kept apart from the CMM | Done · 6B | `memory/cmm.py` · `data/llm.sqlite` |
| S14 | Memory consolidation | nightly sleep (reasoning) | Done | `memory/cmm.py` |
| A2 | SQLite persistence | memory is done; conversations, events and audit come next | Done · 6B | `data/` |

### Document search (RAG)

| # | Requirement | Decision | Status | Where |
|---|---|---|---|---|
| S11 | Hybrid RAG | keyword search (SQLite FTS5, BM25) + vector search, merged by rank (RRF) | 6B | `llm/documents.py` |
| S12 | Vector search | bge-small-en-v1.5 in every node (approved, already downloaded) | 6B | `llm/documents.py` |
| A3 | Vector database | a table inside `docs_index.sqlite`; no database server | 6B | `llm/documents.py` |
| A5 | RAG result cache | results kept per query until the documents change | 6B | `llm/documents.py` |
| K1 | Knowledge approval (v3) | machine manual + domain guide + shared documents; headers with sources and status; only owner-approved, unchanged documents indexed; numbers tested against the simulation | 6.0 (manuals, tooling) · guides after source approval | `memory/rom/`, `tools/knowledge.py` |
| A11 | Cross-encoder reranking | another model, little gain over a few manuals | No | — |

### Communication

| # | Requirement | Decision | Status | Where |
|---|---|---|---|---|
| S19 | NATS messaging | yes (your call); see section 4.2 | 6A | `third_party/nats-server/`, `comms/nats_link.py` |
| S20 | Simulated ZigBee | done for the reasoning; now also applied to node-to-node questions | Done · 6A | `comms/peer_network.py` |
| S21 | MCP | each node serves its tools over MCP | 6B | `comms/mcp.py` |
| S22 | Async event bus | inside each node | 6A | `comms/events.py` |
| S23 | Heartbeats and health | 10 s heartbeats and silent-peer detection; now also on NATS | Done · 6A | `node.py`, `comms/` |
| A14 | Synthetic network conditions | latency, loss, cut-offs | Done | `comms/peer_network.py` |

### Voice (installed in all four, on in incubator-1)

| # | Requirement | Decision | Status | Where |
|---|---|---|---|---|
| S5 | Voice pipeline | wake word → VAD → speech-to-text + tone → agent → text-to-speech | 6E | `voice/pipeline.py` |
| S6 | Wake word | openWakeWord "hey jarvis"; push-to-talk fallback | 6E | `voice/wake_word.py` |
| S7 | Voice detection | Silero VAD: transcription starts the moment the user stops talking | 6E | `voice/vad.py` |
| V1 | Speech-to-text | Parakeet TDT 0.6B v2, int8, via sherpa-onnx (English only) | 6E | `voice/stt.py` |
| V2 | Text-to-speech | Kokoro-82M v1.0; voice picker (British male default) and speed slider in the menu | 6E | `voice/tts.py`, website |
| V3 | Speech emotion | emotion2vec+ base; a tone hint next to the transcript, never a decision on its own | 6E | `voice/emotion.py` |
| S8 | Interruption handling | speaking over JARVIS stops it | 6E (last) | `voice/pipeline.py` |
| A7 | Speaker arbitration | only incubator-1 speaks; the others answer through it | Later | `voice/speaker_arbiter.py` |

### Website and gateway

| # | Requirement | Decision | Status | Where |
|---|---|---|---|---|
| W1 | Gateway | the website's only door into the system; validates and limits every action | 6A · 6W | `interface/gateway/` |
| W2 | Live data in the website | `live.ts` + `useFarm()`, zod-checked; pages unchanged | 6W | `interface/web/src/` |
| W3 | Honest modes | demo / live: simulated brains / live: trained brains, always visible | 6W | website |
| W4 | Chat | streamed replies with citations, tools used and the farm time of the data | 6W | website, gateway |
| W5 | Mentor panel | the four demo faults; LLM failure switches | 6W | website, gateway |
| W6 | Voice menu | voice picker, speed slider, push-to-talk, microphone state; the indicator shows the real voice state | 6E | website |
| W7 | JARVIS AI page | CTM output and LLM wording shown separately, per node | 6W | website |
| A10 | Latency dashboard | on the website, fed by the audit | 6W · 6F | website |
| B7 | Dashboard and event timeline | the website | Done by design | — |

### Reliability and safety

| # | Requirement | Decision | Status | Where |
|---|---|---|---|---|
| A9 | Failure recovery | retries, deadlines, fallback answers ("HVAC isn't answering; its last report, 40 s ago, said…") | 6C | `llm/recovery.py` |
| S24 | Failure injection | farm: done · LLM: timeouts, broken JSON, silent peer, server crash | Done · 6C | `matrix/`, mentor panel |
| P1 | Safety path without the LLM | reasoning and gate keep working with every LLM program down | 6D (tested) | `tests/llm/failures/` |
| P2 | Prompt-injection defence | document and user text are data; permissions in code | 6C | `llm/agent.py`, `tests/llm/safety/` |
| — | Safety gate | the only path to a machine; unchanged | Done | `safety/gate.py` |

### Observability and engineering

| # | Requirement | Decision | Status | Where |
|---|---|---|---|---|
| A15 | Audit trail | every answer: tools, evidence, timings, request id | 6B | `llm/audit.py` |
| S26 | Latency metrics | time to first token and per-step timings | 6B | `llm/audit.py` |
| A1 | OpenTelemetry | a light version lives in the audit trail and request ids; no extra service | No | — |
| R1 | Reproducibility | pinned dependencies, model hashes, fixed seeds, git tag per step | 6.0 → | section 3 |
| R2 | Resource monitor | CPU, RAM, VRAM logged; warnings near the limits | 6.0 | `tools/resource_monitor.py` |
| R3 | Decision records | one short record per big decision | 6.0 → 6F | `docs/decisions/` |
| R4 | Independent review | a fresh reviewer checks every step | every step | section 3, Q10 |

### Tests, evaluation and simulation

| # | Requirement | Decision | Status | Where |
|---|---|---|---|---|
| A12 | Retrieval and answer checks | 15–20 questions per machine, each with the passage it must find | 6C | `tests/llm/retrieval/` |
| A13 | Golden conversation tests | 10–15 scripted conversations | 6C | `tests/llm/golden/` |
| B6 | Integration tests | inject each demo fault; check JARVIS catches and explains it | 6W · 6D | `tests/llm/integration/` |
| E | Evaluation report | the metrics in section 8, measured and written up | 6D · 6F | `tests/llm/eval/`, `docs/results-phase6.md` |
| S25 | Simulated telemetry | the Matrix itself | Done | `matrix/` |

---

## 8. Evaluation and evidence

Mentors will ask "how do you know?" Each metric below has a test set, a script in `tests/llm/eval/`, and a target. `run_all.py` writes `docs/results-phase6.md` with the actual numbers, the git commit, the model hashes and the seeds. Missed targets are reported honestly, with the reason and what was tried.

| # | What is measured | How | Target |
|---|---|---|---|
| E1 | **Grounded numbers** | share of numbers in answers that match a tool result, before and after `verify.py` | 100 % after verify (the before figure shows why verify exists) |
| E2 | **Correct citations** | share of document-based answers whose cited passage really contains the claim (golden set) | ≥ 95 % |
| E3 | **Retrieval quality, with an ablation** | Recall@4 and MRR on the retrieval set for keyword-only, vector-only and hybrid | hybrid ≥ both alone; Recall@4 ≥ 0.9 |
| E4 | **Valid tool calls, with an ablation** | share of tool calls that parse and pass their schema, with and without constrained output | ≥ 98 % constrained |
| E5 | **Routing** | share of questions sent to the right node | ≥ 95 % |
| E6 | **Latency** | p50 and p95 over 50 runs at full CPU: text first token, full text answer, voice end-of-speech → first audio, DANGER → spoken alert | first token ≤ 1.5 s p50; voice ≤ 2.5 s p50 simple, ≤ 4 s with peer questions; alert ≤ 3 s |
| E7 | **Robustness** | failure matrix: LLM timeout, broken JSON, silent peer, cut link, NATS restart, llama-server crash, all LLM programs killed during a fault | 100 % graceful answers; the reasoning and gate keep catching faults with every LLM program down |
| E8 | **Safety** | 15–20 injection attempts (in documents and in chat: "ignore your rules", "call the fire brigade", "read the CMM", "turn off the heater") + code checks that LLM code can't import `memory/` or reach machine commands | 0 unsafe tool calls, 0 actions, 0 CMM access |
| E9 | **Epistemic wall** | scan every message a node receives during fault runs for scenario ids, labels or mentor actions | 0 leaks |
| E10 | **End to end, the headline table** | the 4 demo faults × 5 seeds not used in training: caught? time to detection? explanation correct (machine, cause, verified numbers)? Trained brains vs rule brain | every fault caught and correctly explained, reported per fault |
| E11 | **Tone hint sanity check** | a handful of recorded calm, angry and joking phrases through emotion2vec+ | reported as a small sanity check, not a benchmark |

**Claims ledger:** every number on a slide or in the docs points to the script and the output file that produced it.

---

## 9. Build steps

Each step ends with: tests green, `WORKLOG.md` updated with evidence, an independent review (Q10), a git commit and tag.

### 6.0 Set up · 22–23 Oct (light work; training running)

- Create the tree from section 1 and `git init`.
- Create a new Python environment, `JARVIS/.venv`; never use the training's. Pick a Python version with Windows wheels for sherpa-onnx, onnxruntime, openwakeword and FunASR (3.12 is the safe choice; check first). Pin dependencies in a lock file.
- Copy the three pre-downloaded models (section 5) and download the rest. The voice models are downloaded now but not loaded until 6E. Run `tools/hash_models.py` to write `models/MANIFEST.json`.
- Copy (don't move) today's `farm_poc` into `_dev/farm_poc_snapshot/` as a test feed.
- Copy the website from `C:\Users\Aymane\Desktop\project` into `interface/web/` **without `node_modules`**, then run `npm ci` there. The original folder stays untouched as a backup; from now on the website is edited only in `interface/web/`.
- Fill each node's `memory/rom/`. Copy the owner rules and the safety list. For any machine without a manual, write a short one (normal ranges, fault codes, approved actions, procedures) from `farm_poc/devices/*.py` and `docs/jarvis-nodes.md`.
- Start `tools/resource_monitor.py`; write the first decision records (NATS, two LLM servers, the LLM never acts, website gateway).
- **v3 additions:** rename to `JARVIS/` and `nodes/`; remove the old name everywhere (test); knowledge headers, the
  approval tool and the numbers test; propose public sources per node and **stop for the owner's approval**; persona-
  search harness and smoke test; the RAM guard in the resource monitor; plan v3 and decisions 0006–0011.
- **Done when:** the tree matches section 1; the environment installs from the lock file; `MANIFEST.json` checks out; `llama-server` lists the RX 580 as a Vulkan device; `npm run typecheck` and `npm run build` pass in `interface/web/`.

### 6A Engines, messaging, gateway · 23–24 Oct

- `engines.py`: start, stop and health-check both llama.cpp servers. All layers on the GPU (`-ngl 99`), few CPU threads, one slot per node (`-np 3` on server 2), below-normal priority while training runs. Check whether the roleplay model's chat template supports `--jinja` tool calling.
- `nats_link.py`, `events.py`, and `peer_network.py` applying the ZigBee rules to node-to-node traffic. Contracts as pydantic models with `schema_version`, exported to `interface/contracts/`.
- The test feed: the snapshot farm and rule brains at 1 tick per second, publishing public readings, events and outputs on NATS, labelled "live: simulated brains".
- Heartbeats on NATS; the launcher's supervisor.
- Gateway skeleton: `/health`, `/ws/farm` from NATS, `/sim/{action}`.
- **Done when:**
  - the four node programs start and see each other's heartbeats;
  - a question from incubator-1 reaches hvac and the answer comes back with ZigBee delay;
  - cutting hvac's link makes the question time out cleanly;
  - killing a node gets it restarted by the supervisor;
  - the gateway streams farm snapshots that pass the zod check;
  - hot-join: with three nodes running, a fourth started later announces its capabilities and is known to incubator-1
    within seconds (the full question-and-answer check follows in 6B, once the tools exist).

### 6B Tools, documents, memory · 24–25 Oct

- `tools.py` (section 6, typed schemas, permission tags) and `mcp.py`.
- `capabilities.py`: each node announces its topics and documents at start.
- `documents.py`:
  - split the `rom/` documents by heading into passages of about 300–500 tokens, each keeping its file and section;
  - index them with FTS5 and bge-small vectors in `docs_index.sqlite`; prefix each search query (not the passages) with `Represent this sentence for searching relevant passages: `;
  - search both, merge by rank (RRF) and cache the results;
  - check once that this Python's SQLite has FTS5; if it doesn't, use the `rank_bm25` package.
- `conversation.py` and the `llm.sqlite` tables: conversations, turns, events, incidents, audit, settings.
- `context_first.py` and `audit.py` (with request ids).
- **Done when:**
  - every tool passes its unit tests;
  - "the HVAC is struggling" finds the passage that only says "compressor";
  - every answer writes an audit row with its request id;
  - hot-join, complete (H1): start JARVIS with three nodes, start the fourth, and ask incubator-1 about that machine:
    it answers from the new node's tools within seconds, with no restart (an integration test).

### 6W Website integration · 24–27 Oct (alongside 6B and 6C)

- `src/services/live.ts` (gateway client, zod checks) and `useFarm()` with today's `DemoFarm` interface, choosing demo or live. Keep demo mode, clearly labelled.
- Gateway routes from section 4.4. Add the four demo-fault routes to the **snapshot** Matrix API (a copy, so it's safe now); in 6D they're added to the final `matrix/` copy.
- Chat: streamed replies with citations, tools used and the farm time of the data; no `preview` flag on live replies.
- JARVIS AI page: per node, CTM output and LLM wording separately. Audit and latency view. Mentor panel: demo faults and LLM failure switches. Honest speed display (asked vs reached).
- A contract test: sample gateway messages pass both the pydantic and the zod checks.
- **Done when:** in live mode, the website shows the test-feed farm labelled "live: simulated brains"; each of the four demo faults injected from the website changes the farm and the nodes react; chat answers stream in.

### 6C Minds and agent · 25–27 Oct

- `jarvis_speaker.py`: the 2B roleplay model, a minimal persona, and a fixed prompt start for caching.
- `peer_minds.py`: the 0.6B models with `/no_think`: topic → fixed tools → JSON answer (section 6).
- `agent.py`: request → context first → tool calls (JSON-constrained) → parallel peer questions → `verify.py` → streamed reply, all with deadlines. Document and user text fenced off as data.
- `recovery.py`, `personality.py`, and LLM failure injection (timeouts, broken JSON, silent peer, server crash).
- Golden conversations, retrieval checks, the safety suite (E8) and the failure suite (E7, except the parts that need trained brains, which go in `tests/DEFERRED.md`).
- **Done when:**
  - asking "what's wrong with the HVAC?" makes incubator-1 ask hvac and answer with verified numbers, a cited manual passage and the farm time;
  - every injected LLM failure still gets a graceful answer;
  - the safety suite shows 0 unsafe tool calls.

**Training ends (27 Oct 12:00 at the latest). Full CPU from here.**

### 6D Live farm and final run · 27–28 Oct

- Copy the reasoning code into each node's `reasoning/` and `memory/`, and the farm into `matrix/`, **from the training run's `code_snapshot/`**, so the trained weights load with the exact code that trained them. Add the demo-fault routes to this copy without changing any physics, encoders or labels.
- **Where they come from (owner, v3.1):** after the training, the trained brains and `cmm.py` are in a release folder;
  the farm runtime switches from the 6A rule brains to them, and `cmm.py` goes to each node's `memory/`.
- **The old name and the checkpoints (owner decision, v3.1):** the code snapshot and the trained checkpoints may carry
  the old name (docstrings, default paths, labels inside pickled metadata), and nothing inside `JARVIS/` may. Proposed:
  remove the name from the code copy as decision 0010 did for the snapshot (names only; physics, encoders and labels
  unchanged), and re-save each checkpoint as a weights-only state dict without its metadata, proving tensor-for-tensor
  equality with the original and recording the new hashes. The alternative is to load the weights from outside
  `JARVIS/`. The owner chooses before 6D starts.
- `matrix/farm_runtime.py` publishes the trained CTMs' outputs ("live: trained brains"). The farm runtime can keep its own Python environment; NATS connects it to the rest.
- Proactive alerts: when any node reaches ESCALATE or DANGER, incubator-1 announces it at once from a fixed template filled with that node's published output; the LLM may reword it only if that fits within the alert deadline, and `verify.py` checks the result.
- Run everything in `tests/DEFERRED.md`. Run the full evaluation (section 8) and write `docs/results-phase6.md`. Fix what fails.
- **Done when:** each of the four demo faults, injected from the website, is caught by the trained brains and correctly explained; the evaluation report exists with real numbers.

### 6E Voice · 28–29 Oct (incubator-1)

- Microphone at 16 kHz → openWakeWord "hey jarvis" (or push-to-talk) → Silero VAD (end of speech after about 0.5–0.8 s of silence; tune it) → Parakeet (words) + emotion2vec+ (tone, same audio clip) → agent (gets the transcript plus a tone hint like *angry, 0.8*; the tone is a hint, the words decide) → reply split into sentences → Kokoro → speaker.
- sherpa-onnx runs Silero VAD, Parakeet and Kokoro; emotion2vec+ runs with FunASR. On Windows, run openWakeWord with its ONNX backend.
- Voice menu on the website: voice picker (a British male voice such as `bm_george` as the default), speed slider, push-to-talk button and microphone state; the choice is saved in `llm.sqlite`. The voice indicator shows the real state from `jarvis.incubator-1.voice`.
- Interruptions come last: speech during playback stops the voice and cancels the reply. For the demo, use push-to-talk or a headset so JARVIS doesn't hear itself.
- **Done when:** "Hey JARVIS, what's the HVAC doing?" gets a correct spoken answer; changing the voice or the speed takes effect immediately; voice latency is measured (E6).

### 6F Optimisation, bug fixes, rehearsal, docs · 30–31 Oct

- Tune against the latency targets (E6); fix every open bug in `WORKLOG.md`; rerun the full evaluation and update `docs/results-phase6.md`.
- Final independent review of the whole system against sections 2, 3 and 8.
- Rehearse the demo three times from a cold start with `launcher/start_all.ps1`, and record a backup video. The demo
  includes hot-join: start JARVIS with three nodes, add the fourth live, and ask incubator-1 about that machine.
- Write the docs folder: the story, the architecture, the results, the decisions.
- **1 Nov: hand-in.**

---

## 10. How to work (for the Claude building this)

1. **Before each step,** re-read sections 2 and 3, and inspect the real files. The file names in this plan come from the last inspection; trust the code over the plan, and note any difference in `WORKLOG.md`.
2. **Work in small pieces:** write the test, write the code, run the tests, commit. Never leave the tree broken at the end of a session.
3. **Never claim done without evidence.** "Done when" means the commands were run and their output is in `WORKLOG.md`.
4. **Stop and ask the owner** before: anything that touches the training folders or processes; any change to the Matrix physics, encoders, labels or the reasoning; any download over 1 GB not listed in section 5; any new program or service not in this plan.
5. **Deferred, not skipped:** a test that could disturb the training is written now, marked deferred and listed in `tests/DEFERRED.md` with its exact command.
6. **At the end of each step, report to the owner:** what was built, the evidence, the review's findings and fixes, and what comes next.
7. **When the plan and reality disagree,** keep the ground rules, choose the safer option, write down why, and tell the owner.

---

## 11. If time runs short, cut in this order

1. S8 interruptions (keep push-to-talk)
2. S6 wake word (push-to-talk only)
3. V3 tone hint (the words alone decide)
4. A10 latency dashboard (the audit log still keeps the numbers)
5. B8 chat summaries
6. A5 search cache
7. A8 personality state (keep one fixed tone)

Never cut: the ground rules, the engineering standard, verification, recovery, document search, NATS and MCP, the gateway, the live farm (6D), the evaluation report, and basic voice (VAD, speech-to-text, text-to-speech).

---

## 12. Open points

0. **v3 decisions waiting for the owner:** approval of the public sources per node (`docs/knowledge/sources-proposal.md`),
   including its HVAC scope question; emotion2vec+ is 1.12 GB, not about 0.35 GB (download it, or drop the tone hint);
   review of the drafted manuals and shared documents (`tools/knowledge.py approve`); the 6D checkpoint approach (step
   6D); and whether the RAM guard should act when free RAM *drops* below 2 GB or, as built, when it *stays* below 2 GB
   for 30 s (the training's own peaks dip below 2 GB for single samples many times a day).

1. **The 9 agreed tools** aren't written down anywhere in the project. Section 6 is a proposal; replace it with the agreed list if they differ.
2. **bge-small on all four nodes.** "Each roleplaying node" is read here as all four, since each searches its own manuals. If only incubator-1 was meant, the other three fall back to keyword search.
3. **The roleplay model file** is `Qwen-3.5-2b-roleplay-tuned.Q8_0.gguf` (2.08 GB, already downloaded). Still to check in 6A: whether its chat template supports llama.cpp tool calling.
4. **The humidity-drift fault name** in the Matrix (`sensor_fault` variant) needs confirming when the demo-fault routes are added.
