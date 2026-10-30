# P1 · Step 6W, part 1: the website on live data, and the demo faults

**Builder:** Luna, in `C:\Users\Aymane\Desktop\JARVIS-luna`, branch `build-6w6d6e`. Read `docs/handoff/README.md`
first. No language model is used in this package.

**Read before you start:** plan sections 2, 3, 4.4 (the website, the gateway and the demo-fault table) and 9 (6W);
`interface/gateway/gateway.py`; `matrix/farm_runtime.py`; `interface/web/src/services/matrix.ts` and `contracts.ts`;
`interface/web/src/hooks/use-demo-farm.ts` (the `DemoFarm` interface the pages use); `interface/web/src/App.tsx`;
`WORKLOG.md`, steps 6A and 6B.

## A. Demo faults in the farm (`matrix/farm_runtime.py`, the simulated feed)

1. Add `POST /faults/{scenario_id}` to the Matrix API for the website's four `ScenarioId`s (plan 4.4):
   `heater_relay_stuck`, `supply_fan_failure`, `humidity_sensor_drift`, `supply_overvoltage`.
2. Find out **by reading the snapshot's code** how its engine injects each fault family:
   `_dev/farm_poc_snapshot/scenarios/library.py` (the episode builders, e.g. `_sensor_fault`, `_overvoltage` and the
   equipment faults), `matrix/fault_scheduler.py`, `matrix/equipment_faults.py`, `matrix/scenario.py` and
   `matrix/engine.py` (`start_sensor_fault`). Call that public engine API from the farm runtime. Use the library's
   own parameters for an ordinary (not "novel") variant of each family: the same device, channel, mode and duration
   the brains were trained on. Write down which builder and which parameters you used for each fault.
3. **Confirm the humidity-drift name** (plan section 12, open point 4) and record it in your report.
4. The fault starts at the next tick. **One demo fault at a time per farm run:** a second one answers 409 with the
   reason; a reset clears it. An unknown id answers 404. `reset` and the new `feed_id` work as before.
5. The answer is a small JSON model of your own (`FaultResult`: scenario id, the family, the device, farm time, tick,
   feed_id). It goes **only** to the HTTP caller. It is **never** published on NATS, and never contains the engine's
   hidden truth or any label. Log each injection in the farm runtime's own log.
6. The gateway: `POST /faults/{scenario_id}` (in its `# --- 6W routes ---` block), the `scenario_id` checked against
   the four (422 otherwise), forwarded to the Matrix API with the same error style as `/sim` (503 unreachable, 502 for
   a bad answer, the Matrix's 404 and 409 passed on with their reason).
7. Decision record 0020: how the demo faults map onto the fault families, and why one at a time.

**Tests** (`tests/test_farm_runtime.py` and a new `tests/test_gateway_faults.py`):
- each of the four faults changes the farm's **public** telemetry within N farm ticks; measure N for each fault and
  report it (do not choose N first and then tune);
- 404 for an unknown id at the Matrix API, 422 at the gateway; 409 for a second fault; a reset clears the fault;
- the epistemic wall: run 120 ticks after an injection with a NATS link that records every published message, and
  prove that no message contains the scenario id, the family name, the fault's mode or the words of the engine's
  hidden truth;
- nothing about the injection itself is published: inject while the farm is paused and prove the call publishes
  nothing.

## B. The gateway follows the farm run

`FarmView` keeps the last 50 events of every run it ever saw. Apply rule 7 on the gateway side too: a message (status,
assessment, event or `SimState`) from a **new** `feed_id` clears the old run's status, assessments and events; a late
message from a run that was already replaced is dropped (keep the last 16 replaced runs, as `node.py` does). Test it
with two runs, including a late message from the first one.

## C. `live.ts` and `useFarm()` (the website)

1. `src/services/live.ts`: **one** WebSocket to the gateway's `/ws/farm` (the gateway's URL comes from
   `VITE_GATEWAY_URL`, default `http://127.0.0.1:8010`). Every message is checked with zod (`contracts.ts`); an invalid
   one is counted and logged to the console once per minute at most, never shown, never a crash. Reconnect with
   backoff 1, 2, 4 ... at most 30 s; a clear "disconnected" state while it is down.
2. A **pure, tested** mapping function `FarmSnapshot -> FarmState` (the types in `matrix.ts`), so the pages keep
   working unchanged. Severity `info`/`warning`/`critical` maps to `Info`/`Warning`/`Critical`. Farm time and the
   label are shown exactly as received. A new `feed_id` clears the previous run's events, chart history and
   incidents (rule 7). The asked speed and the reached speed are both shown (plan 4.4: the honest speed control).
3. `useFarm()`: the same interface as `DemoFarm`; it chooses demo or live. Demo stays available and clearly labelled
   "demo". Live data never carries the `preview` flag.
4. Live actions call the gateway: start, pause, reset, speed and inject (the four demo faults).
5. Chat, voice, alert acknowledgement and the mentor's LLM failure switches stay as they are and are marked "available
   after 6C" in live mode. P4 builds them; do not build them now.

## D. Tests for the mapping and the client

A Node 24 script like `scripts/check-contracts.ts`, run by a pytest file like `tests/test_contracts_zod.py`. It feeds
real `FarmSnapshot`s made by the Python gateway (`FarmView.snapshot()`) through the mapping and checks: every device
and metric present; the label; the farm time; a `feed_id` change clearing the old events; invalid messages counted,
never thrown.

## E. Smoothness, measured

The gateway sends at most 5 snapshots a second; the website must stay smooth with 4 nodes and 50 events.
- Memoise derived data, do not re-create large objects per message, keep at most 50 events and a bounded chart
  history.
- A small Node benchmark maps 1,000 recorded snapshots: report the mean and p95 milliseconds per snapshot. Target: p95
  under 2 ms.
- Report the production bundle sizes before and after (`npm run build`); a growth over 10 % needs a reason.

## F. The real thing, once (no language model)

Check the real ports are free (README rule 16). Run `launcher\start_all.ps1 -NoLlm` (the website included). Open the
site, check that live mode shows "live: simulated brains", inject each of the four faults from the website, and report
what changed on screen and at which farm time. Then `launcher\stop_all.ps1`, and show that nothing is left running.

## Done when

| Check | Evidence |
|---|---|
| In live mode the website shows the test-feed farm labelled "live: simulated brains" | F, with what you saw |
| Each of the four demo faults injected from the website changes the farm, and the brains react | F, with farm times; A's tests with N per fault |
| No fault click reaches NATS; no hidden truth leaves the farm | A's epistemic-wall and paused-injection tests |
| A farm reset gives the website and the gateway a clean new run | B's test, D's feed test |
| Mapping p95 under 2 ms; bundle growth reported | E |
| Typecheck, build, pyright, the full suite twice | README rule 14 |

The chat part of 6W's "Done when" belongs to P4. Your report: `docs/handoff/reports/P1.md`. Then start P3.
