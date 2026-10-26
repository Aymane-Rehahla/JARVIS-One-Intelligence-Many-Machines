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

## In, out and tests

**In:** NATS farm subjects, peer heartbeats/capabilities/questions, and `farm.links`/`farm.sim`. **Out:** validated NATS
messages, bounded request replies, heartbeats and capability announcements. `tests/test_comms.py` covers the NATS
boundary and radio rules; `tests/test_node.py` covers node lifecycle and repair; `tests/test_system.py` checks a live
hot-join, a cut link and restoration through the gateway.
