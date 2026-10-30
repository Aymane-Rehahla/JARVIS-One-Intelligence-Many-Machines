# PROGRESS: the one builder's running log (Phase 6, 30 Oct - 1 Nov 2026)

Re-read this file and `docs/handoff/README.md` section 4 after any context summary, before continuing.
Never write the three outside paths (the release folder, the training's Python, the upstream CTM folder) here.

## Done

| Step | Commit / tag | Evidence |
|---|---|---|
| Step 0: redesigned website swapped in | `7de674e` | WORKLOG.md, "Step 0" |
| P1 (6W part 1): demo faults, live website, review fixed, decision 0020 | `aab8b04`, `35e6297`, `9430629`, tag `step-6W` | WORKLOG.md, "Step 6W, part 1" |
| 6C engines: streaming, tool calls, KV cache decision 0017 (keep f16; flash attention works) | `b4c25b3` | decision 0017 |
| 6C agent, peer minds, alerts, failure switches, node wiring; decisions 0018, 0019 | `77c45b5` | tests/llm/test_agent.py, test_minds_alerts_failures.py, tests/test_node.py |
| 6C gateway routes (/ws/chat, /ws/audit, mentor switches, ack) + Matrix ack route + tools/chat_cli.py | `f7e0fcd` | tests/test_gateway_chat.py, tests/test_farm_runtime.py |

Measured on 31 Oct: the snapshot's owner notifications name their sender `jarvis-<node>`, and their incident id is
the one the runtime publishes (README section 6 unknown, answered for the test feed; P3 must check the trained feed).

## Next (in order)

1. 6C still open: install the provisional persona from the D2 persona search results (top eligible automatic config;
   never rate, never open sealed/); the real-model integration test ("What's wrong with the HVAC?" through /ws/chat,
   real 2B + 0.6B servers, fixture-approved manuals, a synthetic hvac fault); WORKLOG 6C section.
2. P4 chat, ack, alerts on the website (chat page, confirm button with countdown, alert banner, mentor LLM switches),
   the identity fix (JARVIS, not "JARVIS Farm"), bundle size report.
3. P3 (6D): torch into requirements, install_release, equivalence proof, trained feed, ack route on both feeds, E9/E10,
   decision 0021.
4. Rest of P2: E7 (real llama-server kill), E8 (draft of 18 attacks in the scratchpad: re-create if lost), E1/E2
   golden, E4, E5 (>= 40 questions), E6 text; review; tag `step-6C`.
5. P4 voice; tag `step-6E`. 6. run_all -> docs/results-phase6.md. 7. 6F (bugs, review, 3 rehearsals, docs, tag).

## Open bugs found (to log in WORKLOG and fix)

- `documents.py` (from the E3 helper): search cuts each passage to 1200 characters (4 passages are longer, may hurt E2);
  keyword query ORs every word including stopwords; H1 titles become tiny distractor passages; `_TokenCounter`
  catches every exception silently.
- The logo/titles still say "JARVIS Farm" (owner's identity fix, MIND.md section 0): not done.
- 6W L8 (known limit): an old open alarm can fall out of the website's 50-event window.
- P1 real run: the humidity-drift demo fault got no reaction from the rule brain in 127 farm minutes (reported, not
  tuned).

## Open questions / owner actions

- The owner alone: approve the 20 ROM documents; rate the persona (blind sheet from the D2 search in
  `logs/persona_search/`); record the backup video.
- The hand-in must carry `interface/web/public/assets/farm/` (3D models, about 277 MB, out of git; see
  `interface/web/assets-manifest.json`).
- Port 5173 may be held by another session's vite dev server (seen 30 Oct, not started by this builder); it must be
  stopped before the cold-start rehearsals.
