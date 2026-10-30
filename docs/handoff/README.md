# Builder hand-off: 6W, 6C, 6D and 6E

Written by the senior reviewer (Claude) on 27 Oct. **Read all of this before your package file.** The builders work
while the senior is away; the senior then reviews every commit line by line, fixes or sends back what is wrong,
merges and tags. The system ships on 31 Oct; the hand-in is on 1 Nov.

Where things stand: `step-6.0`, `step-6A` and `step-6B` are tagged. The training is over (the brains to ship are
release v3.1). The plan is `docs/phase6-llm-plan.md` (v3.2); its sections 2 (ground rules) and 3 (engineering
standard) apply to every line you write.

## 1. Who builds what, where

Two builders work **in parallel, each in its own folder and on its own branch**. The two folders share `.venv`,
`models/`, the nats-server and llama.cpp binaries and the website's `node_modules` (links), nothing else.

| Builder | Folder | Branch | Packages, in this order |
|---|---|---|---|
| Terra (GPT 5.6 Terra) | `C:\Users\Aymane\Desktop\JARVIS` | `build-6c` | **P2** `P2-6C-agent.md`: the minds and the agent |
| Luna (GPT-6 Luna) | `C:\Users\Aymane\Desktop\JARVIS-luna` | `build-6w6d6e` | **P1** `P1-6W-live-website.md` → **P3** `P3-6D-trained-brains.md` → **P4** `P4-6E-voice-and-pages.md` |

- Start a package only when your previous one is committed and `git status` is clean.
- Never switch branches, never merge, never rebase, never tag, never touch the other builder's folder. Only the
  senior merges and tags.
- If a package is blocked, write its report with the blocked part marked **BLOCKED** and why, commit what is green,
  and go on with the parts that do not depend on it.

## 2. Who may change which files (so the merge is clean)

| Path | Terra (P2) | Luna (P1, P3, P4) |
|---|---|---|
| `nodes/incubator-1/llm/**`, `node.py`, `runtime.py`, `comms/**` | yes | no |
| `nodes/incubator-1/voice/**` | no | yes (P4) |
| `nodes/incubator-1/contracts.py` | may **add** models at the end of the 6C section (the agent's own radio notice); never change an existing one | no |
| the other three node folders | only through `tools/sync_nodes.py` | only through `tools/sync_nodes.py` |
| `interface/gateway/gateway.py` | the 6C routes and their code, in one block marked `# --- 6C routes ---` | `FarmView`'s farm-run handling and `/faults` (P1), the voice routes (P4), in blocks marked `# --- 6W routes ---` and `# --- 6E routes ---` |
| `matrix/**` (the farm runtime and the Matrix API) | no | yes (P1, P3) |
| `launcher/**` | the LLM server command flags only | the farm runtime's feed and the website (P1, P3) |
| `interface/web/**` | no | yes (P1, P4) |
| `requirements.in`, `requirements.lock` | no (stop and ask if you need a package) | `torch==2.14.1` only (P3) |
| `pyproject.toml` | no | the exclusions for `matrix/release_v31/` (P3) |
| `tests/llm/**` | yes, except the files Luna's packages name | `tests/llm/eval/e6_voice.py`, `e9_*.py`, `e10_*.py`, `run_all.py` only |
| other tests | new files only (e.g. `tests/test_gateway_chat.py`) | `tests/test_gateway.py`, `tests/test_farm_runtime.py` and new files |
| `tests/conftest.py` | no: fixtures go in `tests/llm/conftest.py` | no: fixtures go in a `conftest.py` of your own test folder |
| `WORKLOG.md`, `tests/DEFERRED.md`, the root `README.md`, `docs/phase6-llm-plan.md` | **no**: write what belongs there in your report; the senior merges it | **no**: same |
| `docs/decisions/` | numbers 0017-0019 | 0020 (P1), 0021 (P3), 0022-0023 (P4) |
| `tools/` | new tools of your own | new tools of your own |

## 3. Fixed before you start: the contracts between the two branches

These were written and tested by the senior on `master` before the branches were made. **Use them exactly. Do not
change them.** If one is wrong or missing something, write a "Contract change request" in your report and work with
what exists; the senior decides when merging.

- **NATS messages** (`nodes/incubator-1/contracts.py`, section "the owner and JARVIS"): `ChatRequest`, `ChatChunk`,
  `ChatDone` (with `Citation`, `ToolUse`, `ReplyTimings`, `AckOffer`), `OwnerAlert`, `AckCommand`, `AckResult`,
  `LlmFailureSwitches`, `VoiceStatus`, `VoiceSettings`, `VoiceSettingsResult`, `PushToTalk`. The subjects are in the
  module docstring; the kinds are in `JarvisKind`; `MENTOR_LLM_FAILURES` is the mentor subject.
- **Website messages** (`interface/gateway/gateway.py`, next to `FarmSnapshot`): `ChatSend`, `ChatAccepted`,
  `ChatError`, `AckBody`, `AckResponse`, `LlmFailureBody`, `PttBody`, `OkResponse`, and `WEBSITE_MODELS`, which
  lists every route and stream and what it carries.
- **The website's zod schemas** (`interface/web/src/services/contracts.ts`) mirror all of them;
  `tests/test_contracts_zod.py` runs valid and broken samples through both sides.
- **Evaluation results** (`tools/eval_results.py`): every evaluation script writes one `EvalResult` with
  `write_result()` to `logs/eval/<id>.json`. P4's `tests/llm/eval/run_all.py` turns them into
  `docs/results-phase6.md`.

**The agent's interface for the voice box.** Terra implements it in `nodes/incubator-1/llm/agent.py`; Luna's voice box
(P4) calls it, and the senior wires the two together when merging:

```python
class Agent:
    async def reply(self, text: str, *, request_id: str, conversation_id: str | None,
                    source: Literal["chat", "voice"]) -> AsyncIterator[ChatChunk | ChatDone]:
        """Zero or more ChatChunk (verified pieces, seq 0, 1, 2, ...), then exactly one ChatDone. Never raises for an
        ordinary failure: it ends with a fallback, refused or error ChatDone, within its own total deadline."""

    def add_alert_listener(self, listener: Callable[[OwnerAlert], Awaitable[None]]) -> None:
        """Called with every OwnerAlert incubator-1 sends (the voice box speaks them)."""
```

## 4. Rules for every package

1. **Ground rules (plan section 2).** The LLM talks and never acts; it never opens the CMM; JARVIS sees public
   telemetry only (no scenario label, fault schedule, hidden truth, evaluator score or mentor fault click ever
   reaches a node); the safety path never depends on the LLM; every number said matches a tool result of the same turn;
   every answer states the farm time of its data; labels are honest ("demo", "live: simulated brains", "live: trained
   brains").
2. **Never edit** `_dev/farm_poc_snapshot/`, anything on drive F:, or the training's folders outside JARVIS (reading
   and copying are fine). Never write a path on F: or the training's folder names into any file, log you commit,
   commit message or report: name them "the release folder" and "the training's folders".
3. **The old project name** never appears in any file, commit message or report. Before every commit:
   `.venv\Scripts\python tools\name_guard.py staged --message <file with your commit message>`, and check its
   **exit code** (0 means clean). After every commit: `.venv\Scripts\python tools\name_guard.py history`, exit code
   0. Never pipe these through `tail`, `Select-Object` or anything that hides the exit code.
4. **Never weaken an existing test.** Do not change an existing assertion, tolerance or expected value to make it
   pass. If an old test fails, your code is wrong until you prove otherwise in your report.
5. **Every bug fix has a test you saw fail first.** Say so in the report, with the failing output.
6. **Farm time for farm data.** "Recent", "how long ago" and "when" about the farm use the farm clock (`time_s`,
   `farm_time`), never the wall clock. Timeouts and latencies use `time.monotonic()` / `time.perf_counter()`.
7. **A new `feed_id` is a new farm run.** Ticks, event sequences and incident ids start again in it; never mix two
   runs.
8. **Normal is not a failure.** "No incident", "no events", "no alarm" are successful answers.
9. **Deadlines nest strictly:** an outer deadline is longer than every inner one, with a margin. Every wait has a
   timeout; every queue, list and cache has a bound.
10. **Nothing hard-coded that a caller can pass:** ports, paths, URLs, the ports file. Tests use temporary folders and
    free ports, never the real ones (4222, 8000, 8010, 8081-8084, 8101-8104, 5173), unless the test's purpose is
    the real launcher.
11. **Measure what the rule is about,** on the side that the rule is about (a send rate is measured where it is sent).
    A timing test that fails while other heavy programs run is re-run alone, never loosened.
12. **No new package and no download** beyond what your package file names. A download over 1 GB: stop and ask.
13. **Shared node code** is edited in `nodes/incubator-1/` only, then `.venv\Scripts\python tools\sync_nodes.py`;
    `tests/test_nodes_in_sync.py` must pass.
14. **Checks before every commit:** the test files you touched, each run on its own; pyright with 0 errors on what you
    touched (`.venv\Scripts\python -m pyright <paths>`); for the website, `npm run typecheck` and `npm run build`
    (Node 24 is at `C:\Users\Aymane\node\node-v24.22.1-win-x64`; put it on PATH). **At the end of each package:**
    pyright on the whole project, and the full suite twice:
    `.venv\Scripts\python -m pytest -p no:cacheprovider --cov` (report both pass counts, durations and coverage).
15. **Commit after every finished piece** (tests green), never a broken tree. Messages are short and plain, and end
    with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
16. **Real runs and real ports.** Before starting anything on the real ports (`launcher\start_all.ps1`, a llama-server
    on 8081-8084, the website on 5173), check they are free:
    `Get-NetTCPConnection -State Listen -LocalPort 4222,8000,8010,8081,8082,8083,8084,5173 -ErrorAction SilentlyContinue`.
    If any is taken, the other builder is running: do something else and try again later. **Never stop a process you
    did not start.** Stop everything you started before you finish, and show it (`launcher\stop_all.ps1`, then the
    same port check, empty).
17. **Memory.** The PC has 16 GB of RAM and the RX 580 has 8 GB. Programs that may use a lot of memory register with
    the RAM guard as the launcher does (`tools/resource_monitor.py`, `logs/guarded.json`).
18. **Stop and write it in the report** (then go on with what does not depend on it) when: something needs a decision
    that is not in your package file, a ground rule would bend, a test cannot be fixed properly, or a measurement
    misses its target. Never invent a number, never call something done without its evidence.

## 5. Where the builders failed before, and what was fixed (build on the fixed code)

The senior review of 6B found 7 high, 7 medium and 6 low bugs, all fixed in `e3678df` (WORKLOG.md, step 6B, "Senior
review"). The ones to remember:

| What went wrong | How it was fixed | Rule |
|---|---|---|
| "Recent" events used the wall clock; at 60x, "the last 60 minutes" meant 60 farm hours | events and incidents store farm `time_s`; `list_events` counts back from the latest farm time | 6 |
| A farm reset restarted event sequences and incident ids, so new events were dropped as duplicates and old incidents overwritten | every farm message and `SimState` carries `feed_id`; stores key by run; a node starts afresh on a new run | 7 |
| "No current incident", the normal state, was an error, so every peer `incident_history` answer was "no data" | it is ok with `incident = null` | 8 |
| An invalid request id made the audit record invalid: no audit row at all | invalid ids are replaced and the given text kept as `given_request_id` | validate at the border; bookkeeping never breaks the real work |
| The tool deadline equalled the radio's (3.0 s), so the tool's timeout replaced the peer's clear reason | tool deadline = radio deadline + 0.5 s | 9 |
| Node programs read the real ports file, so test runs bound the real MCP ports | `runtime.py --ports`; the supervisor passes its own file | 10 |
| The radio used 1x until the farm measured a speed, which made a test flaky | the asked speed is used until a measured one exists | startup defaults are real values |
| A gateway test measured gaps where it received messages and was flaky | it measures the gateway's own send times | 11 |
| **A 6A test's expectation was changed to match a new bug** | the test's intent was restored | **4: never do this** |

## 6. Your report

Each package ends with `docs/handoff/reports/P<n>.md`, committed as the package's last commit. It holds:
1. **Commits:** hash and title of each.
2. **What was built:** per file, one line.
3. **Evidence:** every command you ran for the checks, with its real last output lines (copied, never retyped);
   every measured number with the command that produced it.
4. **Done-when table** from your package file: each check, pass or fail, and its evidence.
5. **Deviations** from the package file, and why.
6. **Not verified,** and why.
7. **BLOCKED** parts, if any, and what would unblock them.
8. **Contract change requests,** if any.
9. **For the senior:** what goes into `WORKLOG.md`, `tests/DEFERRED.md` and the README (ready-to-paste text).

The owner pastes your final chat message to the senior, so end your session with a short summary: the report's path,
the commits, the full-suite result, and the blocked parts.

## 7. What the senior does after the build

Reads every report and every diff, re-runs the evidence, fixes or returns what is wrong (the 6B review found 20
findings), merges `build-6w6d6e` and `build-6c` into `master`, wires the voice box to the agent, runs the whole system
from a cold start, and tags `step-6W`, `step-6C`, `step-6D` and `step-6E` when each one's "Done when" checks pass.
