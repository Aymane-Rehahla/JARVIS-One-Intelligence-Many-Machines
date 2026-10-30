# PROGRESS: the one builder's running log (Phase 6, 30 Oct - 1 Nov 2026)

Re-read this file and `docs/handoff/README.md` section 4 after any context summary, before continuing.
Never write the three outside paths (the release folder, the training's Python, the upstream CTM folder) here.

## Done

| Step | Commit / tag | Evidence |
|---|---|---|
| Step 0: redesigned website swapped in | `7de674e` | WORKLOG.md, "Step 0" |

## In progress: P1 (6W part 1). Written and tested, NOT committed yet (30 Oct, about 16:30)

Uncommitted files, all passing their own tests:
- `nodes/incubator-1/contracts.py` (+ synced copies): `ScenarioId`, `SCENARIO_IDS`, `FaultResult` (never on NATS);
  `interface/contracts/FaultResult.schema.json`; zod `FaultResult` and the `FarmEvent/FarmStatus/FarmAssessment` types
  in `interface/web/src/services/contracts.ts`; a `FaultResult` sample in `tests/test_contracts_zod.py` (41 passed).
- `matrix/farm_runtime.py`: `POST /faults/{id}`, `DEMO_FAULTS` (first ordinary library variant matching the website:
  heater_relay_stuck v9 on incubator-1; supply_fan_failure v1; sensor_fault v228 = incubator-2.humidity_1 drift
  +0.00349 %/s; overvoltage v1 shared 267.9 V for 221 ticks), one fault per run, 409, reset clears.
  Tests appended to `tests/test_farm_runtime.py`: N per fault = 1, 1, 15, 1 farm ticks; epistemic wall holds for all
  four (the only match is the incubators' own `power_status` "overvoltage" reading, judged telemetry); a paused
  injection publishes nothing. Rule brains: heater ESCALATE after 42 ticks, fan ESCALATE after 18, overvoltage
  HANDLED_BY_MACHINE after 1, humidity drift NO reaction within 900 ticks (measure longer; report honestly).
- `interface/gateway/gateway.py`: `/faults/{id}` (422/404/409/502/503), `FarmView.follow()` (new feed clears, late
  messages dropped, 16 retired runs). `tests/test_gateway_faults.py` (9 tests) + `tests/test_gateway.py`: 17 passed.
- Website: `src/services/live-map.ts` (pure mapping), `src/services/live.ts` (WS client, zod, backoff 1..30 s,
  HTTP actions), `src/hooks/use-farm.ts` (live by default when the gateway answers, demo on request), `App.tsx` and
  `jarvis-page.tsx` live labels (feed label, farm time, asked vs reached speed, refusals shown, acknowledging marked
  "available after 6C"), `matrix.ts` (`LiveInfo`, `import.meta.env?.`). `scripts/check-live-map.ts` +
  `tests/test_live_map.py`: 1 passed; mapping mean 0.75 ms, p95 0.80 ms over 1,000 snapshots.
- Also uncommitted, from helpers, to review before committing: `tests/llm/eval/run_all.py` + `test_run_all.py`
  (7 passed per the helper); `tests/llm/retrieval/retrieval_set.json`, `test_retrieval_set.py`,
  `tests/llm/eval/e3_retrieval.py` (E3 ran: hybrid Recall@4 0.909 overall, misses per machine; `logs/eval/E3.json`).

Before committing P1: website typecheck + build (sizes), pyright on touched paths, the touched tests, name guard.
Then P1 section F (the real run with `start_all.ps1 -NoLlm`, the four faults from the website), WORKLOG evidence,
decision 0020, an independent review, the full suite twice, then tag `step-6W`.

## Open bugs found (to log in WORKLOG and fix)

- `documents.py` (from the E3 helper): search cuts each passage to 1200 characters (4 passages are longer, may hurt E2);
  keyword query ORs every word including stopwords; H1 titles become tiny distractor passages; `_TokenCounter`
  catches every exception silently.
- The logo/titles still say "JARVIS Farm" (owner's identity fix, MIND.md section 0): not done.

## Next

P1 F + tag → P2 core → P4 chat/ack/alerts → P3 → rest of P2 → P4 voice → run_all → 6F (README section 3).

## Open questions / owner actions

- The owner alone: approve the 20 ROM documents; rate the persona; record the backup video.
- The hand-in must carry `interface/web/public/assets/farm/` (3D models, about 277 MB, out of git; see
  `interface/web/assets-manifest.json`).
- On 30 Oct 14:57 another session's vite dev server (on the X: copy, PID 11212) held port 5173; it was not started by
  this builder and was left alone. It must be stopped before the cold-start rehearsals (the launcher needs 5173).
