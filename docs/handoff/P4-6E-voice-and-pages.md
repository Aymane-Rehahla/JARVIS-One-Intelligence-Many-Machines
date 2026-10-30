# P4 · Step 6E voice, step 6W part 2 (the website's live pages), and the evaluation report

**Builder:** Luna, in `C:\Users\Aymane\Desktop\JARVIS-luna`, branch `build-6w6d6e`, after P3 is committed (if P3 is
blocked, P4 may start: it does not depend on P3). Read `docs/handoff/README.md` first, above all section 3: the
contracts are fixed, and the agent is built on the other branch. Here you build against the contracts and the
`Agent` interface, with fakes in your tests; the senior connects the two branches when merging.

**Read before you start:** plan sections 2, 3, 4.3, 4.4, 5 (voice), 7 (Voice; Website and gateway), 8 (E6) and 9 (6W,
6E) and 11 (what to cut first); decision 0016 (the tone hint is cut); `tests/voice/test_voice_models_load.py` (how
each voice model loads); `interface/gateway/gateway.py` (`WEBSITE_MODELS`); `interface/web/src/services/contracts.ts`;
your own P1 code (`live.ts`, `useFarm()`).

**Work in this order** (it follows plan section 11 backwards: what is cut last is built first). Commit after each piece.

## 1. The evaluation report (`tests/llm/eval/run_all.py`), small, first

Reads every result with `tools/eval_results.py` `read_results()` and writes `docs/results-phase6.md`: one row per
metric E1-E11 with its target, the measured numbers, pass or fail, the command, the commit, the seeds and the model
hashes; a metric with no file is written "not run yet"; E11 is "cut (decision 0016)". It never changes a number. A test
on a temporary folder of results.

## 2. Voice (incubator-1 only; `node.toml` `voice = true`)

In `nodes/incubator-1/voice/` (then `sync_nodes.py`; the other three load nothing, because their `voice = false`):
- `vad.py`: Silero VAD through sherpa-onnx; end of speech after a silence of 0.5-0.8 s: measure which value cuts
  sentences least on your test clips, and record it.
- `stt.py`: Parakeet TDT 0.6B v2 int8 through sherpa-onnx (`OfflineRecognizer.from_transducer`, as the D1 test does).
- `tts.py`: Kokoro through sherpa-onnx. The voice names and their speaker ids come from the model's own metadata
  (`model.onnx`'s custom metadata, `speaker2id`, read with onnxruntime; no download). Default voice `bm_george`;
  speed 0.5-2.0. A reply is spoken **sentence by sentence as its `ChatChunk`s arrive**.
- `pipeline.py`: the `VoiceBox`. States `off`, `idle`, `wake`, `listening`, `thinking`, `speaking`, `error`
  (`VoiceStatus`). **Push-to-talk** is the main way to talk (the website's button). Audio in and out through
  sounddevice at 16 kHz in; a missing microphone is `mic = "missing"`, never a crash, and a **file source** replaces
  the microphone for tests and measurements.
  - Its constructor takes `answer`, a callable with the signature of `Agent.reply` (README section 3), and a
    `publish` callable for `VoiceStatus`. It never imports `llm/agent.py`.
  - `announce(alert: OwnerAlert)`: speaks an alert at once; a DANGER alert interrupts anything else being said.
  - `handle_settings(VoiceSettings) -> VoiceSettingsResult`: checks the voice against the model's voices and the
    speed, saves both through `LlmStore.set_setting()` (use it, do not change it), and uses them from the next
    sentence.
  - `handle_ptt(PushToTalk)`: pressed starts listening, released ends the utterance.
  - `serve(link)`: subscribes `jarvis.incubator-1.voice_settings` (request-reply) and `jarvis.incubator-1.ptt` on a
    `NatsLink`, and publishes `VoiceStatus` on `jarvis.incubator-1.voice` on every change and every 5 s. **Do not
    edit `node.py` or `runtime.py`:** the senior wires `VoiceBox` to the agent with one call when merging.
- `tools/voice_check.py`: runs the voice box alone with a fake `answer` (a fixed, verified reply), from the
  microphone or from a WAV file, and prints each stage's timing.
- The wake word (`wake_word.py`, openWakeWord "hey jarvis", ONNX backend, the three model files in
  `models/voice/openwakeword/`) comes after everything else in this package, and **interruptions** (speaking over
  JARVIS stops it) come last of all (plan section 11 cuts them first). The tone hint is cut (0016).
- **D1:** `tests/voice/test_voice_models_load.py`, run with `JARVIS_RUN_DEFERRED=1`; it no longer mentions the tone
  hint's model.
- **E6, voice part** (`tests/llm/eval/e6_voice.py`): with recorded questions (synthesise them with Kokoro, then feed
  them as files) and the fake answer: from the end of speech to the first audio out, p50 and p95 over 50 runs; and
  from an `OwnerAlert` to its first audio. The text part of E6 is Terra's; whichever of you writes `E6.json` second
  must keep the other's numbers (read it first, add yours under their own keys). The full spoken path with the real
  agent is measured after the merge.

**The gateway's voice routes** (`# --- 6E routes ---`): `WS /ws/voice` relays `VoiceStatus`; `POST /voice/settings`
sends `VoiceSettings` to `jarvis.incubator-1.voice_settings` and answers its `VoiceSettingsResult` (503 with a reason
when incubator-1 does not answer in time); `POST /voice/ptt` (`PttBody`) publishes `PushToTalk` and answers
`OkResponse`. Tested with a fake responder on the test NATS server.

## 3. The website's live pages (6W part 2)

Everything below is checked with the fixed zod schemas, works in live mode through the gateway only, and keeps demo
mode clearly labelled "demo". Live replies never carry the `preview` flag.
1. **Chat** (`src/services/chat.ts` and the chat page): one WebSocket to `/ws/chat` with reconnect; sends `ChatSend`;
   one question at a time (sending is disabled while one is in flight); shows the reply growing chunk by chunk; when
   `ChatDone` arrives: the farm time, the label, "n numbers verified" (`numbers_checked`), the citations (file and
   section), the tools used, and the outcome (fallback, refused and error look different, with their reason).
   `ChatError` is shown, never swallowed.
2. **Acknowledging an alert:** a `ChatDone` with `confirm` shows a confirm button with its summary and a countdown to
   `expires_utc`; the click posts `/alerts/{node}/{incident_id}/ack` with `AckBody`; the `AckResponse` is shown with its
   stage. Nothing else on the website can acknowledge anything (ground rule 3).
3. **Alerts:** an `OwnerAlert` shows as a banner at once (DANGER stands out) and joins the chat history.
4. **The mentor panel:** the four demo faults (P1) and the four LLM failure switches per node (`GET` and `POST
   /mentor/llm-failures`), clearly marked "test only".
5. **The voice menu (W6):** the voice picker (the voices from `VoiceSettingsResult.voices`), the speed slider, the
   push-to-talk button (press and release), and the microphone state. The voice indicator shows the real state from
   `/ws/voice`; in demo mode it stays the simulated one, labelled demo.
6. **The JARVIS AI page (W7):** per node, the reasoning's output (assessment, diagnosis, confidence, evidence, `ctm`
   or `rule`, the feed label) and the language model's wording (incubator-1: its last reply; the others: the last
   verified `summary` of their peer answers, from the audit stream) shown **separately** and labelled as such.
7. **The audit and latency view (A10):** from `/ws/audit`, the recent requests with their steps and durations; and the
   p50 and p95 of `first_chunk_ms` and `total_ms` over the last 50 replies, labelled "measured in this session".
   (Plan section 11 cuts this fourth.)

**Tests:** the chat and voice state logic as pure functions, driven by recorded message sequences in a Node 24 script
run by pytest (as in P1): the chunks joined equal the done text; a lost chunk (a gap in `seq`, or `chunks` larger than
what arrived) is shown as such; `ChatError`; an alert in the middle of a reply; a `confirm` that expires. The
smoothness benchmark from P1 still passes; report the bundle size again (growth over 10 % needs a reason).

## 4. The real thing (what this branch can show)

Check the ports (README rule 16). `start_all.ps1 -NoLlm` with the trained feed: the website in live mode, the mentor
panel's faults, the voice menu (incubator-1's voice box is not wired on this branch, so the menu shows "voice
unavailable" honestly), the chat page showing the gateway's honest `ChatError` because the agent is not on this branch.
Then `tools/voice_check.py` with the microphone if there is one (else from files): what it heard, what it said, the
timings. Stop everything and show that nothing is left.

## Done when (the parts on this branch)

| Check | Evidence |
|---|---|
| Voice: push-to-talk → speech-to-text → a reply spoken sentence by sentence; changing the voice or the speed takes effect at the next sentence | `voice_check.py` runs and the voice tests |
| Voice latency measured (E6, voice part) | `logs/eval/E6.json` |
| D1 passes | its command and output |
| The website's chat, acknowledgement, alerts, mentor switches, voice menu, AI page and audit view work against the fixed contracts | the Node tests; the real run |
| `docs/results-phase6.md` is generated from the results that exist | `run_all.py` and its test |
| Typecheck, build, pyright, the full suite twice | README rule 14 |

Decision records 0022 (the voice pipeline: VAD silence, push-to-talk first, sentence streaming) and 0023 if needed.
Your report: `docs/handoff/reports/P4.md`.
