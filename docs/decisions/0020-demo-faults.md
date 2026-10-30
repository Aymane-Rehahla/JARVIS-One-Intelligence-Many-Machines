# 0020: The website's demo faults come from the scenario library, one per farm run

Status: accepted (builder, 30 Oct 2026, step 6W; follows plan section 4.4).

## Context
The mentor injects four demo faults from the website (`ScenarioId`): `heater_relay_stuck`, `supply_fan_failure`,
`humidity_sensor_drift` and `supply_overvoltage`. The brains must meet faults they were trained on, injected through
the farm's own physics, and JARVIS must learn of them only through their effects (ground rule 4). Plan section 12,
open point 4, asked for the exact name of the humidity-drift fault.

## Decision
- **Mapping to the library.** Each demo fault takes the first ordinary (not novel) variant, searched from 0, of the
  scenario library's episode builder (`scenarios/library.py`, `_make(family, variant, False)`) whose fault matches the
  website's description, and injects it with the library's own injector (`scenarios/episode.py`, `_faults`). Only the
  start time moves (to the next tick, the end shifted by the same amount); every other parameter is the library's.

  | Website `ScenarioId` | Library family | Variant | Fault, as the library built it |
  |---|---|---|---|
  | `heater_relay_stuck` | `heater_relay_stuck` (equipment fault) | 9 | incubator-1, no end (until reset), no severity |
  | `supply_fan_failure` | `supply_fan_failure` (equipment fault) | 1 | hvac, no end, no severity |
  | `humidity_sensor_drift` | `sensor_fault` | 228 | channel `incubator-2.humidity_1`, mode `drift`, +0.00349 %RH/s, no end |
  | `supply_overvoltage` | `overvoltage` | 1 | pattern `shared` (both incubators), 267.9 V for 221 ticks |

- **The humidity-drift name** (open point 4): the family is `sensor_fault` with `mode = "drift"`; the engine's own
  label for it is `sensor_drift`. There is no separate humidity-drift family.
- **One demo fault per farm run.** A second one answers 409 with the reason; a reset (a new farm run, a new
  `feed_id`) clears it. Faults in the library are single episodes; two at once would be a combination the brains
  never saw in this form, and the website could no longer say which fault it is showing.
- **Nothing about the injection is published.** `POST /faults/{id}` answers a `FaultResult` (scenario, family,
  variant, machines, start tick, farm time, `feed_id`) to the HTTP caller only, and the farm runtime logs it in its own
  log. Tests prove that injecting while paused publishes nothing, and that 120 ticks of every published message after
  each fault contain none of the fault's words (the one match, the incubators' own `power_status` "overvoltage"
  reading, is a field the machine always publishes: telemetry, not a leak).

## Alternatives
- Hand-picked parameters per fault (for example a faster drift for a quicker demo): rejected, it would test the
  brains on something other than what they learned, and "ordinary library variant" is what the spec asks.
- Recorded episodes replayed into the farm: rejected, the farm must stay live and resettable.

## Consequences
- Measured on the 6A test feed (rule brains), public telemetry changes after 1, 1, 15 and 1 farm ticks.
- The drift is slow (about 2 %RH per 10 farm minutes); how fast each brain notices it is reported as measured, never
  tuned (P1 report, E10).
- In 6D the same table drives the trained feed: the release's library is the code that trained the brains.
