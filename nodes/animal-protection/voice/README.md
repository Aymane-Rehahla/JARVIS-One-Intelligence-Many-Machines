# voice/: the voice box (step 6E)

Installed in all four nodes; it runs only where `node.toml` says `voice = true` (incubator-1, the node closest to the
owner). A switched-off voice box loads nothing, so it costs no RAM or CPU. Importing the package loads no model; each
class loads its own on first use (or `load()`) and frees it with `close()`.

Microphone (16 kHz) → push-to-talk (or the wake word, later) → voice detection → speech to text → agent → sentences →
text to speech → speaker. The tone hint is cut (decision 0016).

| File | Model (in `models/voice/`) | What it does |
|---|---|---|
| `vad.py` | Silero VAD (sherpa-onnx), ~2 MB | `Vad`: finds each utterance; it ends after `silence_s` of silence (0.5-0.8 s, default 0.6 s, measured below) |
| `stt.py` | Parakeet TDT 0.6B v2 int8 (sherpa-onnx), ~650 MB | `SpeechToText.transcribe()`: one utterance to text, English only |
| `tts.py` | Kokoro-82M v1.0 (sherpa-onnx), ~330 MB | `TextToSpeech.speak()`: text to 24 kHz audio, 54 voices from the model's metadata, `bm_george` default, speed 0.5-2.0; `split_for_speech()` |
| `pipeline.py` | — | the `VoiceBox` turn manager (not written yet) |
| `wake_word.py` | openWakeWord "hey jarvis" (ONNX) | later; push-to-talk is the main way to talk |

- **`vad.py`.** In: 16 kHz mono float32 audio in pieces of any length (at most 60 s per call). Out: each finished
  utterance once, as float32 (`accept`, `flush`), `speaking` while speech goes on; `reset()` between streams. One
  utterance is at most 20 s. `frames()` cuts audio into whole windows.
- **`stt.py`.** In: one utterance, float32 at 16 kHz (another rate is resampled by sherpa-onnx), at most 60 s. Out:
  `Transcript(text, seconds, decode_s)`. Audio under 0.2 s gives `text = ""` without the model.
- **`tts.py`.** In: at most 600 characters per call, a voice the model has, a speed in 0.5-2.0. Out:
  `Speech(samples, sample_rate, synth_s)`; blank text is empty audio. The voice table is read from `model.onnx`'s
  custom metadata (`speaker2id`, else `speaker_names`; checked against `n_speakers`) with onnxruntime, never guessed.
  The British lexicon comes first, so the British default voice says British words. `split_for_speech()` cuts a reply
  into sentences without breaking numbers (`37.5`) or abbreviations (`e.g.`, `Dr.`), each at most 600 characters.
- **Tests:** `tests/voice/test_voice_units.py` (no model; the real models are `deferred`, run with
  `JARVIS_RUN_DEFERRED=1`, one file at a time) and `tests/voice/test_voice_models_load.py` (D1).

## The measured end-of-speech silence (`tools/vad_silence_probe.py`, 31 Oct)

Main clips: the Parakeet test recording (a real reader, 2 sentences) and 8 Kokoro clips (bm_george and af_heart, clean
and with noise 40 dB down), each 3 sentences of 2-3 phrases with 0.3 s pauses inside a sentence and 1.2 s between
sentences; 26 sentences in all.

| silence_s | utterances (26 true) | cuts | merges | wrong |
|---|---|---|---|---|
| 0.5 | 26 | 0 | 0 | - |
| 0.6 | 26 | 0 | 0 | - |
| 0.7 | 25 | 0 | 1 | the recording's 2 sentences heard as 1 |
| 0.8 | 25 | 0 | 1 | the recording's 2 sentences heard as 1 |

Tie-break, the same Kokoro clips with longer pauses inside a sentence (24 sentences per run), sentences cut:

| silence_s | 0.45 s pauses | 0.5 s | 0.55 s | 0.65 s | total |
|---|---|---|---|---|---|
| 0.5 | 0 | 14 | 31 | 36 | 81 |
| 0.6 | 0 | 0 | 1 | 32 | 33 |
| 0.7 | 0 | 0 | 0 | 2 | 2 |
| 0.8 | 0 | 0 | 0 | 0 | 0 |

**Chosen: 0.6 s** (`vad.SILENCE_S`): with 0.5 s, the only values that found every sentence of the main clips; of the
two, it cuts far fewer sentences when the owner hesitates. 0.7 and 0.8 s cut less on long hesitations but merged the
real recording's two sentences and make every answer start later.
