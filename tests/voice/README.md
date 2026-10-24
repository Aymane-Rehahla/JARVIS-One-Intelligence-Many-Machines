# tests/voice/

Voice tests (step 6E). `test_voice_models_load.py` checks that every downloaded voice model loads and works (Silero VAD
finds speech, Parakeet transcribes, Kokoro speaks, openWakeWord loads "hey jarvis"). It is **deferred**: no voice model
may be loaded while the Phase 5 training runs (ground rule 1). See `tests/DEFERRED.md`, entry D1.
