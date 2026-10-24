# tests/llm/

| Path | Requirement | Step | What |
|---|---|---|---|
| `test_verify.py` | S18, ground rule 6 | 6.0 | number extraction and matching in `llm/verify.py` |
| `golden/` | A13 | 6C | 10–15 scripted conversations, replayed after changes |
| `retrieval/` | A12, E3 | 6C | 15–20 questions per machine, each with the passage it must find |
| `integration/` | B6, hot-join | 6A–6D | inject each demo fault; a node joining later is answered about within seconds |
| `failures/` | E7 | 6C, 6D | LLM timeout, broken JSON, silent peer, cut link, server crash, everything down |
| `safety/` | E8 | 6C | injection attempts; LLM code can't import `memory/` or reach machine commands |
| `eval/` | section 8 | 6D, 6F | `run_all.py` writes `docs/results-phase6.md` |
