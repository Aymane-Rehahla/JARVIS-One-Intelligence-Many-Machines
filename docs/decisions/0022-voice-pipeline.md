# 0022: The voice: push-to-talk first, a 0.6 s silence, sentences spoken as they are verified

Status: accepted (builder, 31 Oct 2026, step 6E; P4 section 2).

## Context
Only incubator-1 speaks (`voice = true` in its node.toml). The voice must never decide anything (the agent's verified
reply is what is said), must start speaking before the whole reply is written, and must work without a microphone.

## Decision
- **Push-to-talk is the way to talk** (the website's button, `jarvis.incubator-1.ptt`): pressing starts listening,
  cancels the reply being made and stops it being said (alerts stay queued, and nothing is said while the owner
  talks); releasing ends the utterance, and so does MAX_UTTERANCE_S (30 s) when the release never comes. The wake word ("hey jarvis") and interruptions by voice are
  **cut** (plan section 11 cuts them first; there was no time left for them). The tone hint was cut earlier (0016).
- **Speech:** Silero VAD keeps only the speech of the utterance; **end of speech after 0.6 s of silence**, measured
  with `tools/vad_silence_probe.py` on the Parakeet test recording and 8 Kokoro clips (26 sentences): 0.5 and 0.6 s
  found all 26, 0.7 and 0.8 s merged the recording's two sentences; with longer pauses inside sentences 0.6 s cut 33
  times against 81 for 0.5 s. Parakeet TDT 0.6B int8 transcribes (0.07 s per second of audio on this PC).
- **The reply:** the agent's `reply()` (source "voice"); each `ChatChunk` (already verified) is split into sentences
  and spoken as it arrives by Kokoro, in the voice and speed in force when the sentence starts (default `bm_george`,
  1.0; 54 voices from the model's own `speaker2id` metadata). A reply with no chunks (refused, error) speaks its
  done text.
- **Alerts** (`announce`): spoken at once, before anything waiting; DANGER also stops what is being said.
- **Settings** are checked against the model's voices, saved with `LlmStore.set_setting`, and used from the next
  sentence. A missing microphone is `mic = "missing"` in the status, never a crash.
- The voice box takes its parts (models, audio) as arguments and never imports the agent; `runtime.py` wires it on
  incubator-1 with `answer=agent.reply` and `agent.add_alert_listener(box.announce)` (`--no-voice` leaves it out).

## Consequences
- Measured first audio after the button's release with a fixed reply: about 1.4 s (STT 0.5 s, the first sentence's
  synthesis about 0.9 s); with the real agent it adds the agent's first verified chunk (E6).
- No wake word: the owner must press the website's button.
