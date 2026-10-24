# 0001: NATS carries messages between the programs

Status: accepted (owner's call, plan v2, 22 Oct 2026).

## Context
The four nodes must run as separate programs, like four real devices, next to a farm runtime that keeps its own
Python environment, a website gateway and observability. They need a carrier with subjects and request-reply. The
simulated ZigBee in the Matrix is a *rule* (delay, loss, cut-offs), not a program that moves bytes.

## Decision
- A NATS server (v2.15.1, `third_party/nats-server/`, 127.0.0.1:4222) carries every program-to-program message.
- Every node-to-node message passes through `comms/peer_network.py` first, which applies the ZigBee rules.
  Observability and website traffic skips them.
- The reasoning's own peer messages stay on today's in-process bus, because the brains were trained with its timing.
- Subjects are listed in the plan, section 4.3.

## Alternatives
- **One program with an in-process bus:** simpler, but the four "devices" become one program, and the farm runtime
  can't keep its own environment.
- **HTTP or ZeroMQ between programs:** no subjects or request-reply built in, so more custom code.
- **MQTT:** also needs a broker, with weaker request-reply.

## Consequences
- One more program to start; the launcher handles it (6A).
- A NATS restart must degrade gracefully. It is part of the failure matrix (E7).
