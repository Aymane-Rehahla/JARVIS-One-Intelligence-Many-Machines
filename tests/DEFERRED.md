# Tests deferred until the Phase 5 training ends

Plan ground rule 1: a test that might disturb the training is not skipped. It is written now, marked `deferred`, listed
here with its exact command, and run once the training has ended (step 6D, or 6E for voice). Plain `pytest` skips these
tests; setting `JARVIS_RUN_DEFERRED=1` runs them.

| # | Test | Why it is deferred | Run in | Exact command (PowerShell, in the project folder) |
|---|---|---|---|---|
| D1 | `tests/voice/test_voice_models_load.py`: Silero VAD finds speech, Parakeet transcribes its sample, Kokoro speaks, openWakeWord loads "hey jarvis" | ground rule 1: no voice model is loaded before training ends (Parakeet alone is about 650 MB) | 6E | `$env:JARVIS_RUN_DEFERRED = "1"; .venv\Scripts\python -m pytest tests\voice -v` |
| D2 | The full persona search (`tools/persona_search/`): 216 configurations × 45 prompts against Qwen 3.5 2B, then the owner's blind rating of the top 3 | the 2B server needs RAM the training is using (owner's point 6: the 2B model only when free RAM is above 3 GB) | after training ends, with 6C | start llama-server 1, then `.venv\Scripts\python tools\persona_search\run.py --server http://127.0.0.1:8081 --seed 7` (about 11 h; add `--limit 36` for about 1 h 50 min) |

When the owner approves emotion2vec+ (1.12 GB), its load test joins D1.
