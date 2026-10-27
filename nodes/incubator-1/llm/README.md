# llm/: the words

How this node talks: it answers questions about its machine, asks the other nodes, cites its approved documents and,
where `node.toml` says `speaks = true` (incubator-1), talks to the owner for all of JARVIS. **It never acts on a
machine** (ground rule 3).

| File | Step | What it does |
|---|---|---|
| `verify.py` | 6.0 | every number said must match a tool result from the same turn (S18, ground rule 6) |
| `engines.py` | 6A | connects to its llama.cpp server and slot (from `node.toml`); health checks |
| `tools.py` | 6B | the nine tools, typed and permissioned (plan section 6) |
| `capabilities.py` | 6B | announces what this node knows, so questions reach the right node; a node that joins later is known within seconds |
| `context_first.py` | 6B | answers from context first; reuses an answer only while its data is unchanged |
| `documents.py` | 6B | hybrid search over the approved `memory/rom/` documents: keywords (SQLite FTS5) + bge-small vectors |
| `conversation.py` | 6B | conversation state; summaries of long chats |
| `audit.py` | 6B | tools used, evidence and timings, per request id |
| `../comms/mcp.py` | 6B | the six read-only tools over localhost Streamable HTTP; every call uses `ToolRegistry` and the audit |
| `jarvis_speaker.py` | 6C | the talking mind of JARVIS (used where `speaks = true`) |
| `peer_minds.py` | 6C | the narrow mind: `/no_think`, forced JSON (used where `speaks = false`) |
| `agent.py` | 6C | request → context first → tools/peers → verify → reply |
| `personality.py` | 6C | stable character; calm and short in danger |
| `recovery.py` | 6C | retries, deadlines, fallbacks |
| `persona/` | 6C, after the persona search | the winning system prompt and example exchanges |

- **In:** questions (from the owner through the gateway, or from other nodes over NATS) and the published farm data.
- **Out:** streamed answers, JSON answers to peers, audit records.
- **Never:** machine commands, emergency calls, the CMM, hidden simulation data, mentor actions, another node's tools.
- **Tests:** `tests/llm/` (verify, golden, retrieval, integration, failures, safety, eval).

Step 6B inputs are public farm messages, owner-approved unchanged files in `memory/rom/`, and node-local peer
capabilities. Outputs are bounded `llm.sqlite` records, the node's separate `docs_index.sqlite`, audited JSON tool
results, and capability announcements. Tests: `tests/llm/test_conversation.py`, `test_audit.py`, `test_documents.py`,
`test_tools.py`, `test_context_first.py`, `tests/test_mcp.py` and the integration, retrieval and safety folders.
