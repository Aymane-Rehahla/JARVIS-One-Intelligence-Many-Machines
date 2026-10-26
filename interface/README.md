# interface/: the website boundary

The website reaches JARVIS only through `gateway/`; it does not connect to NATS or the Matrix API directly.
`contracts/` holds generated JSON Schemas for the Python messages that cross this boundary.

## In and out

The gateway takes public farm telemetry, node heartbeats and capabilities from NATS, plus permitted farm-clock
requests from the website. It returns `GET /health`, accepts `POST /sim/start`, `/sim/pause`, `/sim/reset` and
`/sim/speed`, and streams `FarmSnapshot` messages on `WS /ws/farm`. The gateway only accepts the configured website
origin. Chat, audit, voice and mentor routes are not part of 6A yet.

A `FarmSnapshot` has a schema version, send time and sequence number; simulation and link state; an entry for each
node with liveness, known peers, capabilities, status and assessment; and the recent public events. The web zod
schemas validate those snapshots before the website uses them.

## Tests

`tests/test_gateway.py` checks gateway routes and snapshot shaping. `tests/test_contracts.py` checks generated schemas;
`tests/test_contracts_zod.py` sends shared valid and invalid samples through both pydantic and the website's zod
schemas. `tests/test_system.py` connects to `/ws/farm` against the supervised no-LLM system.
