# models/: model weights, stored once

Each node loads its own instance from here. These files are not in git (too big); only this README and
`MANIFEST.json` are. `setup.ps1` (through `tools/download.py`) downloads every one and checks its SHA-256.

| Folder | Files | Size | Used by | Source |
|---|---|---|---|---|
| `llm/` | `Qwen-3.5-2b-roleplay-tuned.Q8_0.gguf` | 2.08 GB | incubator-1 (its llama server) | mradermacher/Qwen-3.5-2b-roleplay-tuned-GGUF |
| `llm/` | `Qwen3-0.6B-Q8_0.gguf` | 0.64 GB | incubator-2, hvac and animal-protection (one server each; shared read-only file) | Qwen/Qwen3-0.6B-GGUF |
| `embeddings/bge-small-en-v1.5/` | `model.onnx` + 5 tokenizer and config files | 133 MB | all four (document search) | BAAI/bge-small-en-v1.5 |
| `voice/openwakeword/` | `hey_jarvis_v0.1.onnx`, `melspectrogram.onnx`, `embedding_model.onnx` | 3.5 MB | incubator-1 | openWakeWord release v0.5.1 |
| `voice/silero-vad/` | `silero_vad.onnx` | 0.6 MB | incubator-1 | sherpa-onnx release asr-models |
| `voice/parakeet-tdt-0.6b-v2-int8/` | int8 encoder, decoder and joiner, `tokens.txt`, `test_wavs/0.wav` | 631 MB | incubator-1 | sherpa-onnx release asr-models |
| `voice/kokoro-multi-lang-v1_0/` | `model.onnx`, `voices.bin` (53 voices), `espeak-ng-data/`, lexicons, `dict/` | 383 MB | incubator-1 | sherpa-onnx release tts-models |
| `voice/emotion2vec-plus-base/` | empty | 1.12 GB when downloaded | incubator-1 | waiting for the owner's decision (it is over the 1 GB rule) |

The voice models are downloaded but not loaded until step 6E (ground rule 1). Their load test is deferred (`tests/DEFERRED.md`, D1).

## Checking the files

`MANIFEST.json` records the SHA-256, size and source of every file (Q8). Run these from the project folder:

| Check | Command |
|---|---|
| Full check (every hash) | `.venv\Scripts\python tools\hash_models.py check` |
| Quick check (sizes only) | `.venv\Scripts\python tools\hash_models.py check --quick` |
| After adding a model | `.venv\Scripts\python tools\hash_models.py write` |

## Hard links

The two GGUF files and the bge-small files are hard links to `Desktop\jarvis 0.3.4\predownloaded assets`. That is the
same data under two names, so they take no extra disk space (decision 0005). Never edit them in place.
