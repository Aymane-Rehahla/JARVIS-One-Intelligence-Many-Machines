# interface/gateway/: the website's only system door

The FastAPI gateway listens on `127.0.0.1` at the `gateway` port in `launcher/ports.toml`. It takes validated public
farm messages and node heartbeats from NATS, reads permitted simulation responses from the Matrix API, and writes a
bounded stream of website-shaped snapshots. It never puts mentor fault clicks on NATS.

| Route | Input | Output |
|---|---|---|
| `GET /health` | none | NATS connection, Matrix health, live-node list and dropped-message count |
| `POST /sim/{action}` | `start`, `pause`, `reset`, or `speed` with an allowed speed | validated `SimState` from Matrix |
| `WS /ws/farm` | configured website origin | changed `FarmSnapshot` messages, at most five per second |

`FarmSnapshot` is the public contract: it contains simulation state, link state, recent events and all four nodes'
liveness, known peers, capabilities, status and assessment. The website zod-checks the stream before use.

**Tests:** `tests/test_gateway.py` covers route validation and snapshots; `tests/test_contracts_zod.py` checks its zod
contract; `tests/test_system.py` proves that the live no-LLM gateway reports hot-join state.
