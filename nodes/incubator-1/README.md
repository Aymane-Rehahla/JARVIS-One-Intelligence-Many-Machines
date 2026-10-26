# nodes/incubator-1

One node of JARVIS: the local AI that lives with the incubator and joins the others to form one JARVIS.

| | |
|---|---|
| Machine | incubator |
| Talks to the owner | yes, for all of JARVIS |
| Voice | on |
| Language model | Qwen 3.5 2B roleplay (Q8_0), its own llama-server, slot 0 |
| Document search | bge-small-en-v1.5 + keyword search, its own index of approved documents |
| Reasoning brain | CTM incubator brain (same weights as incubator-2, its own instance and state); rule brain as fallback |
| Knowledge (`memory/rom/`) | `incubator_manual.md`, incubation and hatching guide (after source approval), and the shared owner rules, safety list, farm layout and contacts |
| MCP port | 8101 |

**The voice of JARVIS.** The owner always talks to one JARVIS, through this node, because it is the one closest to the user; the other nodes answer through it. The others have the same voice box, switched off. In 6A this node runs the
validated NATS peer network and rule-brain feed; its LLM server topology is ready but remains off while training runs.

Four things are specific to this node: `node.toml` (its identity), this `README.md`, `memory/rom/` (its knowledge) and
`data/` (its databases, not in git). Everything else is identical in all four nodes and kept in sync from incubator-1
(`tools/sync_nodes.py`, from step 6A, which skips those four). The map of the parts is in `nodes/README.md`.

- **In:** its public incubator feed, peer heartbeats/capabilities and questions over NATS; no private scenario labels.
- **Out:** its capabilities, heartbeat and bounded peer answers with farm time; the gateway receives the public view.
- **Tests:** `tests/test_node.py` and `tests/test_comms.py` exercise the node and its radio rules;
  `tests/test_system.py` starts the live no-LLM system, hot-joins a fourth node and checks a cut-link timeout.
