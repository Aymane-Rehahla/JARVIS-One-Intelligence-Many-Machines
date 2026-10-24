# tests/

One command runs everything: `.venv\Scripts\python -m pytest` (Q3).

| Path | What |
|---|---|
| `test_tree.py` | the tree matches the plan: node identities, ports, READMEs, no empty stubs, frozen parts |
| `test_no_old_name.py` | the project's old name appears nowhere: names, contents, logs, git history |
| `test_knowledge.py` | knowledge headers, sources and the owner-approval flow |
| `test_rom_quotes.py` | the quoted owner rules and safety lists still match the reasoning's code word for word |
| `test_rom_numbers.py` | every number in the machine manuals and shared documents matches the simulation |
| `tools/` | unit tests for `tools/` (hash_models, resource_monitor, persona_search) |
| `llm/` | verify, golden, retrieval, integration, failures, safety and eval suites (6.0–6F) |
| `voice/` | voice tests; the model-load test is deferred (D1) |
| `DEFERRED.md` | tests postponed until the training ends, each with its exact command |
| `test_nodes_in_sync.py` | 6A: fails if the four nodes' shared code differs |
