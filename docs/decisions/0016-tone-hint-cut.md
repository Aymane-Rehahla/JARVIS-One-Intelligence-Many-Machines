# 0016: The voice's tone hint (emotion2vec+) is cut

Status: accepted (owner decision, 27 Oct 2026).

## Context
Plan v2 added a tone hint (V3): emotion2vec+ base reads the speaker's tone from the same audio clip as the
speech-to-text, and the agent receives it as a hint (*angry, 0.8*). The model turned out to be 1.12 GB, not about
0.35 GB, and it needs FunASR and its dependencies. Plan section 11 lists the tone hint third among the things to cut
when time runs short; the hand-in is on 1 Nov.

## Decision
The tone hint is cut. Voice ships with the wake word, voice activity detection, speech-to-text and text-to-speech;
the words alone decide. Nothing is downloaded, `voice/emotion.py` is not written, and E11 is reported as cut.

## Alternatives
Download 1.12 GB, install FunASR and torch-audio, and measure the extra RAM and latency: about half a day of work with
dependency risk on Windows, for a hint the plan already says must never decide anything.

## Consequences
- The voice's latency and RAM budget lose one model.
- `models/voice/emotion2vec-plus-base/` stays empty; D1 loads four voice models, not five.
- The tone hint can come back later as its own step, with its own decision record.
