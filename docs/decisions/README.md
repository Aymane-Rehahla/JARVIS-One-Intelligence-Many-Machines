# docs/decisions/: decision records

One short record per big decision (Q9): context, decision, alternatives, consequences.

| # | Decision |
|---|---|
| 0001 | NATS carries messages between the programs |
| 0002 | Two llama.cpp servers: superseded by 0013 |
| 0003 | The LLM talks; it never acts |
| 0004 | The website reaches the system only through the gateway |
| 0005 | Step 6.0 storage: hard links for pre-downloaded models, caches on F: |
| 0006 | One JARVIS, many nodes |
| 0007 | The folder `nodes/` |
| 0008 | Knowledge is approved by the owner before it is indexed |
| 0009 | The persona search for the talking model |
| 0010 | The old project name is removed everywhere, copied code included |
| 0011 | The RAM guard while the training runs |
| 0012 | The 6A test feed stubs the snapshot's CTM module and runs rule brains |
| 0013 | One independent llama.cpp server per node |
| 0014 | MCP exposes only node-local read tools |
| 0015 | The trained brains are installed inside JARVIS, re-saved as plain weights |
| 0016 | The voice's tone hint (emotion2vec+) is cut |
| 0017 | The KV cache stays f16 (measured: q8_0 freed no VRAM and was slower) |
| 0018 | The agent: refusals and routing in code, sentence-gated replies, fallbacks from data |
| 0019 | Proactive alerts go through the radio and are spoken from a template |
| 0020 | The four demo faults are ordinary library variants, injected through the Matrix API |
| 0021 | The trained feed: an ordinary library day, one torch thread, a fresh CMM per run (one owner question) |
| 0022 | The voice: push-to-talk first, a 0.6 s silence, sentences spoken as they are verified |

0023 was reserved for voice and not needed.
