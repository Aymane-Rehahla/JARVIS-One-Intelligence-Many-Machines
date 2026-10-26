# nodes/: the nodes that form JARVIS

**JARVIS** (Just A Rather Very Intelligent System) is one intelligence made of many machine nodes. Each machine carries
its own local AI node; together the nodes form one JARVIS, which grows with every machine added. The owner always talks
to one JARVIS, through incubator-1 (the node closest to the user); the other nodes answer through it. The farm here is
the test bench that proves the idea.

| | incubator-1 | incubator-2 | hvac | animal-protection |
|---|---|---|---|---|
| Talks to the owner | **yes, for all of JARVIS** | no | no | no |
| Voice | **on** | installed, off | installed, off | installed, off |
| Language model | **Qwen 3.5 2B roleplay**, own server | Qwen3-0.6B, own server | Qwen3-0.6B, own server | Qwen3-0.6B, own server |
| Reasoning brain | CTM incubator brain | same weights, own state | CTM HVAC brain | CTM animal-protection brain |
| Machine manual | incubator manual | same | HVAC manual | animal-protection manual |

All four also hold the shared owner rules, safety list, farm layout and contacts.

## Anatomy of one node

```
                 machine readings, events (farm runtime, NATS)
                                │
   reasoning/  ── notices, diagnoses, names the cause (CTM; rule brain as fallback)
   memory/     ── the CMM: what the reasoning remembers. The LLM never opens it.
      rom/     ── the node's knowledge: machine manual, domain guide, shared documents (owner-approved)
   safety/     ── the gate: the only way an action reaches a machine
                                │ publishes: assessment, evidence, incidents
                                ▼
   llm/        ── the words: answers, asks other nodes, cites documents, verifies numbers
   voice/      ── the voice box (on in incubator-1 only)
   comms/      ── ZigBee rules, NATS, event bus, MCP, hot-join
```

## Rules that hold in all four
- **Identical code.** Only `node.toml`, the node's own `README.md`, `memory/rom/` and `data/` differ. From step 6A,
  `tools/sync_nodes.py` copies everything else from incubator-1 to the others and `tests/test_nodes_in_sync.py` fails if
  they drift apart.
- **Grows by joining.** A node started while JARVIS runs announces its capabilities; incubator-1 can answer about it
  within seconds, with no restart.
- **The LLM talks; it never acts** (ground rule 3). Machine actions and emergency calls come only from the reasoning,
  through `safety/gate.py`.
- **The safety path never depends on the LLM** (ground rule 5): with every LLM program down, the farm, the reasoning and
  the gate keep working.

## In, out, tests
- **In:** each machine's public data from the farm runtime over NATS; questions from the owner (through the gateway) and
  from each other.
- **Out:** reasoning outputs (published by the farm runtime), answers, heartbeats, capabilities, audit records.
- **Tests:** `tests/test_tree.py` and `tests/test_knowledge.py` now; `tests/test_nodes_in_sync.py` from 6A; `tests/llm/`.
