# comms/: how this node talks to the others

| File | Step | What it does |
|---|---|---|
| `peer_network.py` | 6A | the simulated ZigBee rules (delay, loss, cut-offs), applied to every node-to-node message before NATS |
| `nats_link.py` | 6A | the NATS connection: subjects, request-reply, reconnect |
| `events.py` | 6A | the async event bus inside this node |
| `mcp.py` | 6B | serves `llm/tools.py` over MCP (port in `launcher/ports.toml`) |

- **Hot-join:** a node started while JARVIS runs announces its capabilities on NATS; incubator-1 can answer about that
  machine within seconds, with no restart (integration test in 6A/6B, demo step in 6F).
- The reasoning's own peer messages stay on the reasoning's bus: the brains were trained with its timing.
- Observability and website messages skip the ZigBee rules; they aren't farm radio.
- Subjects and their payloads: `docs/phase6-llm-plan.md`, section 4.3.
- **Tests:** unit tests per module; the 6A "done when" checks (heartbeats, ZigBee delay, a cut link times out, hot-join).
