# voice/: the voice box (built in step 6E)

Installed in all four nodes; it runs only where `node.toml` says `voice = true` (incubator-1, the node closest to the
owner). A switched-off voice box loads nothing, so it costs no RAM or CPU.

Microphone (16 kHz) → wake word or push-to-talk → voice detection → speech-to-text + tone hint → agent → sentences →
text-to-speech → speaker.

| File | Model (in `models/voice/`) | What it does |
|---|---|---|
| `pipeline.py` | — | turn manager: listen, understand, answer, speak; interruptions come last |
| `wake_word.py` | openWakeWord "hey jarvis" (ONNX) | wake word; push-to-talk fallback |
| `vad.py` | Silero VAD (sherpa-onnx) | knows when the user stops talking |
| `stt.py` | Parakeet TDT 0.6B v2 int8 (sherpa-onnx) | speech to text, English only |
| `emotion.py` | emotion2vec+ base (FunASR), waiting for the owner's decision | a tone hint next to the words, never a decision on its own |
| `tts.py` | Kokoro-82M v1.0 (sherpa-onnx) | voice picker (British male default) and speed |
| `speaker_arbiter.py` | — | later (A7): which node speaks if incubator-1 can't |

- **In:** microphone audio, or push-to-talk from the website. **Out:** transcript and tone hint to the agent; audio.
- **Tests:** `tests/voice/` (the model-load test is deferred, D1); latency measured as E6.
