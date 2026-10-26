# Tests deferred until the Phase 5 training ends

Plan ground rule 1: a test that might disturb the training is not skipped. It is written now, marked `deferred`, listed
here with its exact command, and run once the training has ended (step 6D, or 6E for voice). Plain `pytest` skips these
tests; setting `JARVIS_RUN_DEFERRED=1` runs them.

| # | Test | Why it is deferred | Run in | Exact command (PowerShell, in the project folder) |
|---|---|---|---|---|
| D1 | `tests/voice/test_voice_models_load.py`: Silero VAD finds speech, Parakeet transcribes its sample, Kokoro speaks, openWakeWord loads "hey jarvis" | ground rule 1: no voice model is loaded before training ends (Parakeet alone is about 650 MB) | 6E | `$env:JARVIS_RUN_DEFERRED = "1"; .venv\Scripts\python -m pytest tests\voice -v` |
| D2 | The full persona search (`tools/persona_search/`) with the owner's persona spec (`docs/persona-spec.md`: its system prompt, example exchanges, settings grid and 48-line test set), then the owner's blind rating of the top 3 | the 2B server needs RAM the training is using; the owner (24 Oct, 13:37): no LLM server until the training is done | after training ends | start llama-server 1, then `.venv\Scripts\python tools\persona_search\run.py --server http://127.0.0.1:8081 --seed 7` (the spec's data and the staged search come with it) |
| D3 | `tests/llm/test_engines_live.py`: all four node LLM servers loaded one at a time through the supervisor commands; each server's health, `/props` model name, one chat, VRAM, RAM, load time, prompt and generation tokens per second; then every PID stopped | the owner (24 Oct, 13:37): no LLM server until the training is done | after training ends | `$env:JARVIS_RUN_DEFERRED = "1"; .venv\Scripts\python -m pytest -q -p no:cacheprovider -o faulthandler_timeout=60 tests\llm\test_engines_live.py` |
| D4 | The full test suite, with the complete old-name scan, after the step 6A changes | the owner (24 Oct, 13:37): no full suite until the training is done; until then single test files only | after training ends | `$env:JARVIS_FULL_NAME_SCAN = "1"; .venv\Scripts\python -m pytest --cov` |

When the owner approves emotion2vec+ (1.12 GB), its load test joins D1.

The owner paused the training on 24 Oct from 01:15:49 to 04:55:20 (`WORKLOG.md`). In that pause the full suite ran
(495 passed) and the 6A work began; D1-D3 did not run, and they wait again for the end of the training.
