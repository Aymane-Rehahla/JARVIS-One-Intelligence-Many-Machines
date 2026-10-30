# Hand-off: the rest of Phase 6 (6W, 6C, 6D, 6E), for one builder

Written on 30 Oct 2026 by the Claude session that reviewed 6A and 6B. **Read all of it before touching anything.**
Where something is not known, it says so; do not fill the gap with a guess, measure it or ask the owner.

## 0. The situation

- **You are the only builder,** a Claude session, working in `C:\Users\Aymane\Desktop\JARVIS` on branch `master`.
  You commit, review (with an independent subagent, plan Q10) and tag yourself. There is no other builder and no
  other branch or folder. (An earlier version of this file planned two builders in parallel; that plan was dropped
  on 30 Oct and its branches and second folder were removed.)
- **The owner** is busy with other work and wants this done without supervision. **Deadline: the system ships on
  the night of Saturday 31 Oct; the hand-in is 1 Nov** (plan header). What exactly "ship" means beyond the plan's
  step 6F (rehearsals from a cold start, a backup video, the docs) is **not known**: ask the owner when they are back.
- **The plan** is `docs/phase6-llm-plan.md` (v3.2). Sections 2 (ground rules) and 3 (engineering standard) bind every
  line. Section 11 says what to cut first when time runs short; section 12 lists the open points.
- **Your prompt** gives three paths that must never be written into any file in this repository, a log you commit or a
  commit message (two of them contain the old project name): the **release folder** (release v3.1, the trained
  brains), the **training's Python** (it has torch 2.14.1) and the **upstream CTM folder**.

## 1. What is true now (checked on 30 Oct)

- `master` is at `cf933a5`; tags `step-6.0`, `step-6A` (`9621d3e`), `step-6B` (`e3678df`). The tree is clean.
- **Tests.** The full suite last ran on 27 Oct at `e3678df`: 744 passed, 5 skipped (deferred), 91 % coverage, twice.
  `cf933a5` only added contracts, an eval format and docs; for it, the touched test files ran (110 passed), pyright on
  the whole project gave 0 errors, and the old-name test passed. **The full suite has not been run on `cf933a5`.**
- **Built and tagged (6A, 6B):** contracts (`nodes/incubator-1/contracts.py`, exported to `interface/contracts/`, zod in
  `interface/web/src/services/contracts.ts`), NATS link, simulated ZigBee radio (`comms/peer_network.py`), heartbeats,
  hot-join, the supervisor and launcher (`launcher/start_all.ps1`, `-NoLlm`, `-NoWebsite`), the gateway skeleton
  (`/health`, `/ws/farm`, `/sim/*`), the farm runtime on the **simulated** feed (the read-only snapshot in
  `_dev/farm_poc_snapshot/` with rule brains, "live: simulated brains"), the nine tools with permissions and audit,
  hybrid document search (FTS5 + bge-small), the conversation/event store, MCP (six read tools), `verify.py`, and the
  engine client (`llm/engines.py`, non-streaming chat only). WORKLOG.md has the evidence for each.
- **Not built yet:** the agent and the minds (6C), the website on live data, demo faults and every live page (6W), the
  trained brains in the farm (6D), voice (6E), the evaluation scripts and `docs/results-phase6.md`, and step 6F.
- **Fixed on 30 Oct for the next steps** (`cf933a5`, tested): the chat, alert, acknowledgement, mentor and voice
  messages in `contracts.py`'s last section and next to `FarmSnapshot` in `interface/gateway/gateway.py`
  (`WEBSITE_MODELS` lists every route and stream), their zod mirrors, and `tools/eval_results.py` (one `EvalResult`
  per metric, `logs/eval/<id>.json`). They were designed carefully; you may change them if the work shows a real need,
  but then change every side (pydantic, the exported schemas, zod, the samples in `tests/test_contracts.py` and
  `tests/test_contracts_zod.py`) and say why in the commit.
- **Owner decisions:** one llama.cpp server per node (0013); the trained brains are installed inside JARVIS and
  re-saved as plain weights with a tensor-for-tensor proof (0015); the voice's tone hint (emotion2vec+) is cut (0016).
- **Measured in 6A (D3, 27 Oct):** Qwen 3.5 2B roleplay (incubator-1) loads in 4.88 s and generates 21.7 tok/s; each
  Qwen3-0.6B about 31 tok/s; with all four servers loaded, 7.44 of 7.94 GiB VRAM are used; every chat template reports
  tool-call support (its real tool-calling quality is **not** measured).
- **Waiting for the owner (only the owner can do these):**
  - **None of the 20 ROM documents is approved** (`nodes/*/memory/rom/`, no `approvals.json`), so in the real system
    document search finds nothing and no reply can cite a manual. Tests use approvals made by a fixture in temporary
    folders. Approval is an interactive command (`tools/knowledge.py approve <file> --by owner`) that refuses scripts:
    **never approve on the owner's behalf.** Tell the owner, early, that the live demo's citations depend on it.
  - The public source list (`docs/knowledge/sources-proposal.md`) is unapproved, so no domain guides exist.
  - The persona search (D2) has never run, so the owner has never rated a persona.
- **Hardware:** Windows 11, 16 GB RAM, an RX 580 with 8 GB (llama.cpp Vulkan build in `third_party/llama.cpp/bin`).
  Whether a microphone and speakers are connected is **not known**.

## 2. The specs

`P1-6W-live-website.md`, `P2-6C-agent.md`, `P3-6D-trained-brains.md` and `P4-6E-voice-and-pages.md` were written on
27-30 Oct for two builders in parallel. **Their content and "Done when" tables stand.** With one builder:
- ignore every mention of Terra, Luna, another builder, branches, the second folder, file ownership and merging;
- where a spec says "the senior wires this when merging" (the voice box to the agent), do it yourself;
- where a spec says "do not edit WORKLOG.md / tests/DEFERRED.md / README.md, put it in your report", edit them
  yourself, as every earlier step did;
- the per-package reports in `docs/handoff/reports/` are optional; WORKLOG.md is the record.

## 3. Suggested order, and what to cut

This is a suggestion from the reviewer, not an owner decision. The time each piece takes is **not known** (no
estimate was ever measured); re-plan as you go and tell the owner what you dropped.

1. **P1 (6W part 1):** the website on live data and the four demo faults. Small, and every later demo needs it.
2. **P2 (6C) core:** engine streaming, the agent with sentence-by-sentence verification and fallbacks, chat through
   the gateway (`/ws/chat`), the peer minds. This is the heart of Phase 6; without it JARVIS cannot talk.
3. **P4 section 3, items 1-3:** the website's chat page, the acknowledgement button and the alert banner.
4. **P3 (6D):** the trained brains. If it fails, the simulated feed still runs, honestly labelled.
5. **P2 the rest:** acknowledgement flow, proactive alerts, failure injection, the suites and the evaluation scripts.
6. **P4 voice** (push-to-talk, speech-to-text, the agent, text-to-speech), then the other pages.
7. `run_all.py` → `docs/results-phase6.md`, 6F (rehearsal from a cold start, docs).

The persona search (D2, about 2 hours of model time) and the KV-cache measurement (P2 section 0) need the GPU: run
them in the background when nothing else uses it, or skip them and say so. Without D2, the speaking model uses the
best system prompt of `docs/persona-spec.md`, marked provisional.

Plan section 11's cut order, first cut first: interruptions; wake word; tone hint (already cut); the latency dashboard;
chat summaries; the search cache; the personality state. **Never cut:** the ground rules, the engineering standard,
verification, recovery, document search, NATS and MCP, the gateway, the live farm, the evaluation report, basic voice.

## 4. Rules

1. **Ground rules (plan section 2):** the LLM talks and never acts; it never opens the CMM; nodes see public telemetry
   only (no scenario label, fault schedule, hidden truth, evaluator score or mentor fault click); the safety path
   never depends on the LLM; every number said matches a tool result of the same turn; every answer states the farm
   time of its data; honest labels ("demo", "live: simulated brains", "live: trained brains").
2. **The training's folders and F:** never edit, move or delete anything in the release folder, anything else on F:,
   or the training's folders outside JARVIS (reading and copying are fine). Never install packages into the training's
   Python. Never create a `STOP` file. Never stop a `python.exe` you did not start.
3. **The old project name** never appears in any file, commit message or tag (decision 0010; `tools/name_guard.py`
   writes it as `"thir" + "duni"`). Before every commit run `.venv\Scripts\python tools\name_guard.py staged --message
   <file with the commit message>`; after it `... history`; check each **exit code** (0 = clean), never pipe them
   through anything that hides it. Before each tag also run the full scan:
   `$env:JARVIS_FULL_NAME_SCAN = "1"; .venv\Scripts\python -m pytest tests\test_no_old_name.py`.
4. **Never weaken an existing test** (an assertion, a tolerance, an expected value) to make it pass. If an old test
   fails, the new code is wrong until proven otherwise.
5. Every bug fix has a test that was seen to fail first.
6. Farm data uses farm time (`time_s`, `farm_time`), never the wall clock; timeouts use `time.monotonic()`.
7. A new `feed_id` is a new farm run; never mix runs.
8. Normal states ("no incident", "no events") are successful answers, not errors.
9. Deadlines nest strictly (outer longer than inner, with a margin); every wait has a timeout; every queue and cache
   is bounded.
10. Nothing hard-coded that a caller can pass (ports, paths, URLs, the ports file). Tests use temporary folders and
    free ports.
11. Measure what a rule is about, where it applies; a timing test that fails under load is re-run alone, never
    loosened.
12. **Ask the owner before:** a download over 1 GB, a new program or service not in the plan, any change to the
    Matrix physics, encoders or labels, or anything not decided here or in the plan. Packages: only from
    `requirements.in` + the hashed `requirements.lock` (P3 adds `torch==2.14.1`, the training's version).
13. Shared node code is edited in `nodes/incubator-1/` only, then `.venv\Scripts\python tools\sync_nodes.py`.
14. Before each commit: the touched test files, pyright on the touched paths, and for the website `npm run typecheck`
    and `npm run build`. Before each tag: pyright on the whole project and the full suite
    (`.venv\Scripts\python -m pytest -p no:cacheprovider --cov`), the evidence in WORKLOG.md, and an independent
    review by a subagent whose findings are fixed (plan Q10).
15. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The owner's email is used only
    for git authorship.
16. Stop every program you started before you finish (`launcher\stop_all.ps1`), and check the ports are free:
    `Get-NetTCPConnection -State Listen -LocalPort 4222,8000,8010,8081,8082,8083,8084,5173 -ErrorAction SilentlyContinue`.
17. Programs that may use a lot of memory register with the RAM guard as the launcher does
    (`tools/resource_monitor.py`).

## 5. Bugs found in earlier reviews (do not repeat them)

The 6B review (`e3678df`, WORKLOG.md step 6B) found 7 high, 7 medium and 6 low bugs. The kinds to watch for:

| Bug | Fix | Rule |
|---|---|---|
| "Recent" events counted on the wall clock (at 60x, "the last hour" was 60 farm hours) | farm `time_s` | 6 |
| A farm reset restarted sequences and ids; new events were dropped as duplicates | `feed_id` on every farm message | 7 |
| "No current incident" was an error | ok with `incident = null` | 8 |
| An invalid request id broke the audit row | replaced, the given text kept | validate at the border |
| Tool deadline = radio deadline, so the clear reason was lost | tool = radio + 0.5 s | 9 |
| Node programs read the real ports file in tests | `--ports` | 10 |
| The radio used 1x until the farm measured a speed (flaky test) | the asked speed first | startup defaults are real |
| A test measured send gaps on the receiving side (flaky) | the sender's own times | 11 |
| **A builder changed a 6A test's expectation to match its new bug** | intent restored | **4** |

## 6. What is not known (find out; never assume)

- Whether llama.cpp's Vulkan build supports flash attention on the RX 580 (needed for a quantized V cache, P2 section 0).
- How well the 2B model chooses tools with llama.cpp's tool calling (E4 measures it).
- Whether torch 2.14.1 resolves to a Windows wheel for Python 3.12 from PyPI (the training's copy is a `+cpu` build
  on Python 3.14), and whether the installed brains decide exactly like the release on 3.12 (P3 step 4 proves it).
- How long one exam-style episode takes with the four trained brains, and what speed the farm reaches with them.
- Whether the incident id the farm runtime publishes equals the one in the brain's owner notifications (P3 tests it).
- How a notification's `sender` names a node (`gate.py` passes `caller`; its value is not checked).
- Whether Kokoro's `model.onnx` carries `speaker2id` metadata (its README says the metadata script writes it; not
  checked), and whether a microphone and speakers are present.
- Which engine seeds the training used (the library derives them with `_seed()`; P3's E10 must prove its seeds are new).
- When the owner will approve the ROM documents and rate a persona.

## 7. Practical notes

- Python: `.venv\Scripts\python` (3.12). Node 24: `C:\Users\Aymane\node\node-v24.22.1-win-x64` (not on PATH; run
  `npm.cmd` from there or put it on PATH). Caches and downloads go to `F:\JARVIS-cache` (C: is low on space); that
  folder is ours, unlike the release folder.
- Through the Bash tool, a heredoc collapses `\\` to `\`: on 30 Oct it wrote a raw control character into a test file.
  Write any text with backslashes with the Write or Edit tools.
- A shell `cd` does not persist between commands; use absolute paths.
- The first old-name scan in a fresh folder takes about 6 minutes (it reads every file once, then caches in `.cache/`).
- The launcher: `powershell -ExecutionPolicy Bypass -File launcher\start_all.ps1 [-NoLlm] [-NoWebsite]`, then
  `launcher\stop_all.ps1`. Ports are in `launcher/ports.toml`.
- **When you finish a step** tell the owner in plain words: what works, the evidence, what was cut or is not
  verified, and what the owner must do (approvals, rating, the rehearsal).
