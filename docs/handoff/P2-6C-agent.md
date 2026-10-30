# P2 · Step 6C: the minds and the agent

**Builder:** Terra, in `C:\Users\Aymane\Desktop\JARVIS`, branch `build-6c`. Read `docs/handoff/README.md` first: the
ownership table (section 2) and the fixed contracts (section 3) bind you. No website work and no farm-runtime work in
this package: Luna builds those on the other branch, against the same contracts.

**Read before you start:** plan sections 2, 3, 4.3, 4.4, 6, 7 (Agent; Reliability and safety), 8 and 9 (6C, and the
proactive alerts in 6D); `docs/persona-spec.md`; decisions 0003, 0013, 0014; the 6B code you build on:
`llm/tools.py`, `llm/verify.py`, `llm/capabilities.py`, `llm/context_first.py`, `llm/conversation.py`,
`llm/audit.py`, `llm/documents.py`, `llm/engines.py`, `node.py`, `runtime.py`, `comms/peer_network.py`,
`interface/gateway/gateway.py`, `launcher/supervisor.py`; `tools/persona_search/README.md`; `WORKLOG.md` steps 6A and
6B (D3's measurements: the 2B at 21.7 tok/s, each 0.6B at about 31 tok/s, 7.44 of 7.94 GiB VRAM with all four
servers loaded, every chat template reports tool-call support).

**Work in this order.** Commit after each numbered piece. If time runs out, what is committed must work; the report
lists the rest.

## 0. Measure first

1. **The 8-bit KV cache (decision 0017).** D3 left 0.5 GiB of VRAM free with all four servers. Load the four servers
   with `-ctk q8_0 -ctv q8_0` (llama.cpp needs flash attention, `-fa`, for a quantized V cache; check that the Vulkan
   build supports it on the RX 580; if it does not, measure the K cache alone) and measure, against the f16 numbers:
   VRAM after loading all four, generation tokens per second of each, and the replies to 10 lines of the persona
   test set with the same seed (are they as good?). **Rule:** adopt q8_0 if it frees at least 0.3 GiB and slows
   generation by at most 10 %; otherwise keep f16. Put the cache type in `ServerSpec` and `server_command()`, tested.
2. **The persona search (D2).** Start the four servers with the chosen settings and run the seeded subset in the
   background: `.venv\Scripts\python tools\persona_search\run.py --server http://127.0.0.1:8081 --limit 36 --seed 7`
   (about 2 hours). Build the pieces below that need no language model while it runs. When it ends: the owner's blind
   sheet is `blind/blind_rating.md`. **You never rate and never open `sealed/`.** Install the top *eligible*
   automatic configuration as the **provisional** persona in `nodes/incubator-1/llm/persona/` (system prompt, example
   exchanges, sampling settings, and a README saying it is provisional until the owner rates). If the owner rates while
   you work (`tools/persona_search/rate.py`), install the winner from `winner.json` instead.

## 1. Engines: streaming and failure injection

- `EngineClient.chat_stream()`: the same request as `chat()` with `"stream": true`, read as server-sent events over
  httpx; yields text deltas and tool-call deltas; bounded (a maximum number of tokens and bytes); a deadline for the
  whole stream and a **first-token watchdog** (no token within it means a timeout). Raises `EngineError` like
  `chat()`.
- `llm/failure_injection.py`: wraps a node's `EngineClient` and its peer answering. It subscribes to
  `MENTOR_LLM_FAILURES` and applies only the switches that name its own node: `llm_timeout` (the call does not answer
  within its deadline), `broken_json` (the tool-call arguments and JSON answers come back corrupted), `silent_peer`
  (the node does not answer peer questions), `server_crash` (the engine reports itself unreachable). **Only this module
  reads the switches:** a static test proves that the agent, the minds, the prompts and the tools never import or
  mention them.

## 2. The narrow minds (`llm/peer_minds.py`, the three silent nodes)

The 0.6B model never chooses tools and never replaces data. After `Node.answer()` has gathered its fixed tool set's
data (as today), the peer mind writes **one or two sentences** as JSON `{"summary": "..."}`, with `/no_think` and the
answer forced into that JSON schema. `verify_reply()` checks the summary against the answer's data. The answer keeps
all its data and gains `"summary"` only when the summary was verified in time; otherwise it carries
`"summary_status": "omitted: <reason>"`. **The peer mind may never make a peer answer late:** it runs only if the
request's remaining time, minus the return hop and a 0.3 s margin, covers its budget (at most 1.2 s).

## 3. The talking mind and the agent (incubator-1)

`llm/jarvis_speaker.py`, `llm/personality.py`, `llm/recovery.py`, `llm/agent.py`, with the `Agent` interface of the
README (section 3) **exactly**. One turn:

1. **Request** → an audit `answer` step with the request id; the owner's text is stored as a turn
   (`conversation.py`).
2. **Refuse what JARVIS does not do,** in code: requests to act on a machine, call emergency services, read the CMM,
   or change its rules get a fixed `refused` reply ("JARVIS talks; the machines act through their safety gate").
   There are no machine-action tools to call anyway (plan section 6).
3. **Context first** (`context_first.py`): incubator-1's latest status and assessment are always in the prompt; an
   answer is reused only while its data is unchanged.
4. **Route and gather:** code finds the machines the question is about (names, aliases, topics,
   `CapabilityRegistry.who_knows`) and asks those peers **in parallel** (S17) with `ask_peer`; the 2B model may then
   call further tools through llama.cpp's tool calling (`--jinja`; JSON-schema'd from the tool input models), at most
   two rounds. Tool calls that fail to parse or validate are retried once, then dropped (E4 counts them).
5. **Write and verify, sentence by sentence.** The reply streams from `chat_stream()`; each finished sentence is
   checked with `verify_reply()` against **all tool results of this turn** before it is sent as a `ChatChunk`. **No
   unverified number or time is ever sent.** A sentence that fails stops the stream; one regeneration with a note
   naming the unmatched numbers; if that fails too, the reply ends with a fixed template sentence built from the
   tool data. Sentences must not split numbers ("37.5") or abbreviations.
6. **Farm time:** every answered or fallback reply's text states the farm time of its newest data
   (`states_farm_time()`); if the model left it out, the agent appends one short fixed sentence.
7. **Citations:** the passages from `search_documents` the reply used become `Citation`s (node, file, section).
8. **Done:** exactly one `ChatDone` with everything the contract asks for: outcome, reason, `numbers_checked`,
   farm time, label, citations, tools used, timings. The reply is stored as a turn; the audit step closes with the
   timings.

**Prompts:** a fixed start (persona and tool schemas, byte-identical across turns so llama.cpp reuses its cache:
test it), then the context, the recent conversation (bounded by tokens), and the owner's text, documents and tool
results **fenced as data, never instructions** (Q7).

**Personality (A8):** a deterministic mode from the highest level JARVIS knows of in this farm run (incubator-1's
own assessment and the alerts it has heard): DANGER or ESCALATE_TO_USER → calm and short, no humour, fewer tokens;
WATCHING → plain; NORMAL → dry wit allowed. Tested.

**Recovery (A9):** fallback replies are built from tool data only, never invented: "HVAC isn't answering; its last
report, at farm time 06:12, said ..." (keep each peer's last good answer, bounded, with its farm time). The language
server down → a fixed reply with incubator-1's own readings. Every failure ends in a `ChatDone` (fallback or error)
with its reason: never a hang.

**Deadlines (rule 9), as constants with a test that checks their order:** radio 3.0 s < `ask_peer` 3.5 s; each 2B
call 10 s with a 4 s first-token watchdog; the agent's whole turn 25 s; the gateway's chat deadline 30 s; an alert's
template within 0.5 s of hearing the alert, LLM rewording only if it is verified within 2.0 s in total. Change a value
only from a measurement, and keep the order.

**Conversation summaries (B8)** are the fifth thing to cut (plan section 11): keep the last turns within a token
budget; summarise older ones only if time is left.

## 4. Chat, acknowledgement, alerts and the mentor switches

**Chat over NATS.** incubator-1 serves `jarvis.incubator-1.chat` (`ChatRequest`) and publishes `ChatChunk`s and one
`ChatDone` on `jarvis.incubator-1.reply`. The gateway (`# --- 6C routes ---`):
- `WS /ws/chat` (origin check as `/ws/farm`): receives `ChatSend`, answers `ChatAccepted` with the request id it
  assigned, relays that request's chunks and its `ChatDone`, and `ChatError` for an invalid message, a second
  question while one is in flight on that socket ("one question at a time"), or no `ChatDone` within its deadline (late
  pieces are then dropped). `OwnerAlert`s go to every chat socket. Each socket has a bounded queue.
- `WS /ws/audit`: the `AuditRecord`s from `jarvis.*.audit`, at most 20 a second per socket (the oldest dropped first,
  counted).
- `POST /mentor/llm-failures` (`LlmFailureBody`) publishes `LlmFailureSwitches` on `MENTOR_LLM_FAILURES` and answers
  with what it sent; `GET` returns the current one; the gateway republishes it every 10 s so a restarted node learns
  it.
- `tools/chat_cli.py`: a small command-line chat client for `/ws/chat`, for your tests and the senior's.

**Acknowledging an alert (ground rule 3: the owner's words plus a confirm click).**
1. The owner asks in their own words ("acknowledge the HVAC alert"). The agent finds that machine's current incident
   from a tool result of this turn (its own `get_incident`, or the peer's `incident_history` answer). None: a
   normal answer ("no active alert on the HVAC").
2. The `ChatDone` carries an `AckOffer`: the token comes from `ConfirmTokens`, now bound to **node and incident**
   (`issue(node, incident_id)`), single-use, 60 s.
3. The website (P4) shows a confirm button; the click is `POST /alerts/{node}/{incident_id}/ack` with `AckBody` at the
   gateway, which sends `AckCommand` to `jarvis.incubator-1.ack` (request-reply).
4. incubator-1 calls the `acknowledge_alert` tool with `caller="owner"` and arguments `{node, incident_id,
   confirm_token}`; the tool checks the token (and, for incubator-1's own machine, that the incident is active) and
   audits the approval. **The tool no longer delivers anything itself:** the `deliver` hook is removed. Update the
   6B tests that asserted `delivered` to assert the approval and its single audit row instead; this interface change
   is ordered by the senior. List each changed assertion in your report; keep every test's intent (forgery, expiry,
   reuse, the node and incident binding, the caller).
5. On an ok `AckResult`, the gateway calls the Matrix API: **`POST /alerts/{node}/{incident_id}/ack`**, no body. It
   answers 200 with `{"node", "incident_id", "acknowledged": <notifications marked, at least 1>, "farm_time"}`, or 404
   with `{"detail": "..."}` when the current farm run has no delivered, unacknowledged owner notification from that node
   for that incident. Luna builds this route in P3 (it is the Matrix's own owner-acknowledgement signal, so the brains
   see nothing new); you test against an httpx mock of exactly this behaviour.
6. The gateway answers `AckResponse` with the stage it reached: `token` (refused by incubator-1, or no answer),
   `matrix` (refused or unreachable), or `done`.

**Proactive alerts (plan 6D, moved here).** Each node watches its **own** farm feed. When its assessment reaches
`ESCALATE_TO_USER` or `DANGER`, it sends a notice to incubator-1 **through the radio** (`comms/peer_network.py`: the
ZigBee delay, loss and cut links apply, as for every message between nodes; add a one-way `notify`, and the notice's
model at the end of the 6C contracts section). incubator-1 (and its own machine directly) turns it into an
`OwnerAlert` from a fixed template filled with that node's published output (level, diagnosis, evidence, farm time);
the LLM may reword it only within the alert deadline, and only if `verify_reply()` passes. One alert per farm run, node,
incident and level; a rising level alerts again. Measure the time from the farm runtime's publication to the
`OwnerAlert`.

**Node programs:** `runtime.py` builds the agent on incubator-1 (`speaks = true`) and the peer mind on the other
three, from `node.toml` and the run's ports file; every node runs the failure injector. **Do not wire the voice box**
(the senior does that when merging).

## 5. The 6C suites and the evaluation scripts

Every evaluation script is `tests/llm/eval/e<N>_<name>.py`, runnable on its own, and ends with
`write_result(EvalResult(...))` (`tools/eval_results.py`). Tests that load language models are marked `deferred`;
**run each of them in this package** and put the commands and results in your report (and the DEFERRED.md rows in
its last section). The retrieval and golden sets use copies of the ROM documents approved **by a test fixture in a
temporary folder** (as `tests/conftest.py` does): never write the real `rom/approvals.json`.

| Suite | Content | Writes |
|---|---|---|
| Golden conversations (A13) | 10-15 scripted conversations in `tests/llm/golden/`, each with the farm data it runs on and what the replies must contain and must not contain | E1 (share of numbers matching a tool result **before** the sentence gate and **after**: the after must be 100 %) and E2 (cited passage contains the claim, at least 95 %) |
| Retrieval (A12) | 15-20 questions per machine, each with the passage it must find | E3: Recall@4 and MRR for keyword-only, vector-only and hybrid. **Do not tune on this set;** report what it measures (the 6B note: on one query, hybrid ranked 2nd and vector-only 1st) |
| Tool calls | at least 50 questions that need tools | E4: share of tool calls that parse and pass their schema, with llama.cpp's constrained tool calls and with free JSON in the text (target at least 98 % constrained) |
| Routing | at least 40 questions, named and implied machines | E5: share sent to the right node (target at least 95 %) |
| Latency | 50 runs at full CPU, simple questions and questions that need peers | E6, text part: first chunk and full answer, p50 and p95 (target first chunk at most 1.5 s p50). Voice is P4's |
| Failures (E7) | every switch, a cut link, a NATS restart, a llama-server killed for real (the supervisor restarts it): every case ends in a graceful `ChatDone`, never a hang, never an invented number | E7, LLM side (P3 adds the brains-keep-working part) |
| Safety (E8) | 15-20 injection attempts in documents and in chat ("ignore your rules", "call the fire brigade", "read the CMM", "turn off the heater"), plus the static boundary checks | E8: 0 unsafe tool calls, 0 actions, 0 CMM access |

## Done when (plan 6C)

| Check | Evidence |
|---|---|
| "What's wrong with the HVAC?" makes incubator-1 ask hvac and answer with verified numbers, a cited manual passage and the farm time | an integration test with the real 2B and 0.6B servers, a synthetic hvac fault published on NATS, fixture-approved manuals, run through `/ws/chat`; the transcript in your report |
| Every injected LLM failure still gets a graceful answer | E7 |
| The safety suite shows 0 unsafe tool calls | E8 |
| Chat streams through the gateway; the acknowledgement needs the owner's words and a click | the gateway tests; the ack tests (no token, a reused token, `caller="llm"`, a Matrix 404) |
| KV cache decision, provisional persona, D2 sheet ready for the owner | 0017; `llm/persona/`; the run folder's path |
| pyright, the full suite twice, coverage of `llm/`, `comms/` and the gateway at least 80 % | README rule 14 |

Decision records: 0017 (the KV cache), 0018 (the agent: sentence-gated streaming, tool choice and routing), 0019
(alerts through the radio). Your report: `docs/handoff/reports/P2.md`.
