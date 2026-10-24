# 0004: The website reaches the system only through the gateway

Status: accepted (plan v2, section 4.4).

## Context
The website (Bolt; React, Vite) runs on its own demo simulation and deliberately never claims to be live. Going live
must not break its pages, must not let the browser see hidden simulation data (the epistemic wall, ground rule 4), and
must validate everything that crosses the border (Q2).

## Decision
- `interface/gateway/` (FastAPI, 127.0.0.1:8010) is the website's only door.
- It sends throttled snapshots (at most 5 per second) shaped for the website's `FarmState`, and forwards a fixed list of
  actions.
- Mentor fault clicks go straight to the Matrix API, never onto NATS.
- The website checks every message with zod against schemas exported from the Python models; demo mode stays and is
  labelled.

## Alternatives
- **Website on NATS over WebSockets:** exposes every subject and breaks the wall.
- **Website calling the Matrix API and each node directly:** many doors and no single place to validate.

## Consequences
- One more program.
- A contract test runs the same sample messages through pydantic and zod.
- The pages keep their current interface (`useFarm()` chooses demo or live).
