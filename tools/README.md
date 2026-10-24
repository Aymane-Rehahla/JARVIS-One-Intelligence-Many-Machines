# tools/: project tools

| Tool | What it does | Run |
|---|---|---|
| `hash_models.py` | writes and checks `models/MANIFEST.json` (SHA-256, size, source of every model file) | `.venv\Scripts\python tools\hash_models.py check` (the start check); `write` after adding a model |
| `resource_monitor.py` | logs CPU, RAM, commit, VRAM and C:/F: free space every 10 s to `logs/resource_monitor.jsonl`; the **RAM guard** stops only programs registered in `logs/guarded.json` when free RAM stays below 2 GiB or commit headroom falls below 1 GiB | start: `.venv\Scripts\pythonw tools\resource_monitor.py`; stop: `--stop`; one sample: `--once` |
| `knowledge.py` | the nodes' knowledge: header checks, owner approval with recorded hashes, what may be indexed | `status`; `check [--fix]`; the owner runs `approve <file> --by owner` |
| `persona_search/` | the persona search for JARVIS's talking model (system prompts × examples × sampling), automatic scoring and a blind sheet for the owner | see `persona_search/README.md`; the full run is deferred (D2) |
| `sync_nodes.py` | 6A: copies shared code from incubator-1 to the other three, never `node.toml`, the node `README.md`, `memory/rom/` or `data/` | — |

Tests: `tests/tools/`, `tests/test_knowledge.py`.
