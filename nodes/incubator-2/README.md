# nodes/incubator-2

One node of JARVIS: the local AI that lives with the incubator and joins the others to form one JARVIS.

| | |
|---|---|
| Machine | incubator |
| Talks to the owner | no (answers through incubator-1) |
| Voice | installed, off |
| Language model | Qwen3-0.6B (Q8_0), its own llama-server, slot 0 |
| Document search | bge-small-en-v1.5 + keyword search, its own index of approved documents |
| Reasoning brain | CTM incubator brain (same weights as incubator-1, its own instance and state); rule brain as fallback |
| Knowledge (`memory/rom/`) | `incubator_manual.md`, incubation and hatching guide (after source approval), and the shared owner rules, safety list, farm layout and contacts |
| MCP port | 8102 |

**A silent node.** The owner talks to JARVIS through incubator-1; this node answers through it. Its voice box is installed but off; flipping `speaks` and `voice` in `node.toml` would make it the speaker (failover, later).

Four things are specific to this node: `node.toml` (its identity), this `README.md`, `memory/rom/` (its knowledge) and
`data/` (its databases, not in git). Everything else is identical in all four nodes and kept in sync from incubator-1
(`tools/sync_nodes.py`, from step 6A, which skips those four). The map of the parts is in `nodes/README.md`.

- **In:** its machine's public data from the farm runtime (NATS), questions from the owner or other nodes.
- **Out:** its reasoning output (published by the farm runtime), answers, audit records.
- **Tests:** `tests/test_tree.py` (identity), `tests/test_knowledge.py` (knowledge); per-part tests arrive with each step.
