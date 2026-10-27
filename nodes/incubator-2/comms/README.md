# comms/: a node's bounded peer connection

`nats_link.py` is the node's connection to NATS. It takes typed messages on the farm and JARVIS subjects, validates
them before handlers see them, limits queued work, retries an initial connection, and carries request IDs on asks.
It publishes typed telemetry, heartbeats, capabilities and replies; invalid or unknown-version messages are logged
and dropped.

`peer_network.py` applies the simulated ZigBee rules before a node-to-node question crosses NATS: one-hop delay,
configured loss and Matrix-published link cuts. A cut target consumes the caller's remaining deadline and returns a
clear timeout rather than an invented answer. Farm telemetry and website observability are not radio traffic.

## Hot-join and repair

On start, a node publishes its capabilities and then heartbeats every 10 seconds. Existing nodes reply with their own
capabilities, so all peers learn the new node without restarting. If an announcement is missed, a heartbeat from an
unknown capability causes a bounded re-announcement request; a heartbeat that says a peer does not know this node
causes that node to re-announce. Each repair is limited to once per heartbeat period.

## MCP tools

`mcp.py` serves only `get_status`, `get_assessment`, `get_trend`, `list_events`, `get_incident` and
`search_documents` through Streamable HTTP at `127.0.0.1` on this node's `[mcp]` port from
`launcher/ports.toml`. Every call is validated and audited by the node's `ToolRegistry`; peer questions, conversation
recall and alert acknowledgement are not exposed. Runtime enables the listener by default; `--no-mcp` disables it. A
port conflict is logged and leaves the node running.

## In, out and tests

**In:** NATS farm subjects, peer heartbeats/capabilities/questions, and `farm.links`/`farm.sim`. **Out:** validated NATS
messages, bounded request replies, heartbeats and capability announcements. `tests/test_comms.py` covers the NATS
boundary and radio rules; `tests/test_node.py` covers node lifecycle and repair; `tests/test_mcp.py` checks the six
MCP tools, audit rows, loopback binding and occupied-port fallback; `tests/test_system.py` checks a live hot-join, a
cut link and restoration through the gateway.
