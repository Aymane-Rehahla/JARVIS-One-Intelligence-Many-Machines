"""Which end-of-speech silence (0.5, 0.6, 0.7 or 0.8 s) cuts sentences least (step 6E, `voice/vad.py`'s SILENCE_S).

Test clips, all at 16 kHz:
- the Parakeet test recording `test_wavs/0.wav` (a real reader, 2 sentences: "Well, I don't wish to see it any more,
  observed Phebe, turning away her eyes. It is certainly very like the old portrait.");
- Kokoro-synthesised sentences, each made of 2-3 phrases synthesised apart, trimmed of their own edge silence and joined
  by a short pause inside the sentence (0.3 s, `--inner`); sentences joined by a long pause (1.2 s, `--between`); two
  voices (bm_george, af_heart), clean and with faint white noise (40 dB below the speech level).
- the same Kokoro clips again with longer pauses inside a sentence (hesitations: 0.45, 0.5, 0.55 and 0.65 s,
  `--hesitations`), only to break a tie.

For each silence value, a fresh Vad hears every clip in 100 ms pieces, as from a microphone, then flushes. A clip that
yields more utterances than it has sentences cut a sentence; fewer means two were merged. The rule: the candidates
are the values whose utterance count is exactly right on the main clips (the 0.3 s run with the recording); among
them, the fewest cuts over the hesitation runs, then the shortest (it answers sooner). Kokoro is freed before the VAD
runs (one model in RAM at a time). Nothing is written; the tables are printed.

Usage: .venv\\Scripts\\python tools\\vad_silence_probe.py [--inner 0.3] [--hesitations 0.45,0.5,0.55,0.65]
       [--between 1.2]
"""
from __future__ import annotations

import argparse
import gc
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import soundfile
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "nodes" / "incubator-1") not in sys.path:
    sys.path.insert(0, str(ROOT / "nodes" / "incubator-1"))

from voice.tts import TextToSpeech  # noqa: E402
from voice.vad import SAMPLE_RATE, Vad  # noqa: E402

VOICE = ROOT / "models" / "voice"
PARAKEET_WAV = VOICE / "parakeet-tdt-0.6b-v2-int8" / "test_wavs" / "0.wav"
PARAKEET_SENTENCES = 2
SILENCES = (0.5, 0.6, 0.7, 0.8)
VOICES = ("bm_george", "af_heart")
CHUNK_S = 0.1                          # the piece of audio the VAD hears at a time, as from a microphone
EDGE_S = 0.5                           # silence before and after each clip
TRIM_LEVEL = 0.01                      # Kokoro's own edge silence is below this amplitude
NOISE_DB = -40.0                       # the noisy copies: white noise this far below the speech RMS
SEED = 7
# Each clip: sentences, each a list of phrases spoken apart and joined by the inner pause.
SCRIPTS: tuple[tuple[tuple[str, ...], ...], ...] = (
    (("The supply air", "is twenty four degrees."),
     ("The incubator humidity", "has been steady", "for the last hour."),
     ("Fan two is running", "at eighty percent.")),
    (("Nothing needs your attention", "right now."),
     ("The heater relay", "looks stuck on,", "so the temperature is rising."),
     ("I checked the trend", "and it is", "still within the safe band.")),
)

Samples = npt.NDArray[np.float32]


@dataclass(frozen=True)
class Clip:
    name: str
    samples: Samples
    sentences: int


def trim(samples: Samples) -> Samples:
    """`samples` without the quiet start and end Kokoro adds around a phrase."""
    loud = np.flatnonzero(np.abs(samples) > TRIM_LEVEL)
    return samples[loud[0]:loud[-1] + 1] if len(loud) else samples[:0]


def silence(seconds: float) -> Samples:
    return np.zeros(int(round(seconds * SAMPLE_RATE)), dtype=np.float32)


def synthesise(inner_s: float, between_s: float) -> list[Clip]:
    """The Kokoro clips (clean and noisy) for one inner pause; Kokoro is freed afterwards."""
    tts = TextToSpeech(VOICE / "kokoro-multi-lang-v1_0")
    started = time.monotonic()
    clips: list[Clip] = []
    rng = np.random.default_rng(SEED)
    try:
        for voice in VOICES:
            for number, script in enumerate(SCRIPTS, start=1):
                parts: list[Samples] = [silence(EDGE_S)]
                for index, sentence in enumerate(script):
                    if index:
                        parts.append(silence(between_s))
                    for piece, phrase in enumerate(sentence):
                        if piece:
                            parts.append(silence(inner_s))
                        speech = tts.speak(phrase, voice=voice)
                        audio = resample_poly(trim(speech.samples), SAMPLE_RATE, speech.sample_rate)
                        parts.append(np.asarray(audio, dtype=np.float32))
                parts.append(silence(EDGE_S))
                clean = np.concatenate(parts)
                rms = float(np.sqrt(np.mean(clean[np.abs(clean) > TRIM_LEVEL] ** 2)))
                noise = rng.normal(0.0, rms * 10 ** (NOISE_DB / 20), len(clean)).astype(np.float32)
                name = f"kokoro {voice} {number}"
                clips.append(Clip(name, clean, len(script)))
                clips.append(Clip(name + " noisy", clean + noise, len(script)))
    finally:
        tts.close()
        del tts
        gc.collect()
    print(f"synthesised {len(clips)} clips (inner pause {inner_s} s) in {time.monotonic() - started:.1f} s", flush=True)
    return clips


def count_utterances(clip: Clip, silence_s: float) -> int:
    vad = Vad(VOICE / "silero-vad" / "silero_vad.onnx", silence_s=silence_s)
    step = int(CHUNK_S * SAMPLE_RATE)
    found = 0
    for start in range(0, len(clip.samples), step):
        found += len(vad.accept(clip.samples[start:start + step]))
    return found + len(vad.flush())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the VAD's end-of-speech silence on test clips.")
    parser.add_argument("--inner", default="0.3", help="pause(s) inside a sentence on the main clips, in seconds")
    parser.add_argument("--hesitations", default="0.45,0.5,0.55,0.65",
                        help="longer pauses inside a sentence for the tie-break runs, comma-separated ('' for none)")
    parser.add_argument("--between", type=float, default=1.2, help="pause between sentences, in seconds")
    args = parser.parse_args(argv)
    real, rate = soundfile.read(str(PARAKEET_WAV), dtype="float32")
    if rate != SAMPLE_RATE:
        raise SystemExit(f"{PARAKEET_WAV} is {rate} Hz, not {SAMPLE_RATE}")
    parakeet = Clip("parakeet 0.wav", np.concatenate([silence(EDGE_S), np.asarray(real, np.float32),
                                                      silence(EDGE_S)]), PARAKEET_SENTENCES)
    main_errors = {silence_s: 0 for silence_s in SILENCES}            # cuts + merges on the main clips
    hesitation_cuts = {silence_s: 0 for silence_s in SILENCES}
    runs = [(float(value), True) for value in args.inner.split(",") if value.strip()]
    runs += [(float(value), False) for value in args.hesitations.split(",") if value.strip()]
    for inner, is_main in runs:
        clips = [parakeet, *synthesise(inner, args.between)] if is_main else synthesise(inner, args.between)
        sentences = sum(clip.sentences for clip in clips)
        print(f"\n{'main' if is_main else 'hesitation'} run: inner pause {inner} s, between sentences "
              f"{args.between} s: {len(clips)} clips, {sentences} sentences")
        print(f"{'silence_s':>9} | {'utterances':>10} | {'cuts':>4} | {'merges':>6} | clips wrong (found/true)")
        for silence_s in SILENCES:
            counts = {clip.name: (count_utterances(clip, silence_s), clip.sentences) for clip in clips}
            cuts = sum(max(0, found - true) for found, true in counts.values())
            merges = sum(max(0, true - found) for found, true in counts.values())
            if is_main:
                main_errors[silence_s] += cuts + merges
            else:
                hesitation_cuts[silence_s] += cuts
            wrong = ", ".join(f"{name} {found}/{true}" for name, (found, true) in counts.items() if found != true)
            print(f"{silence_s:>9} | {sum(found for found, _ in counts.values()):>10} | {cuts:>4} | {merges:>6} | "
                  f"{wrong or '-'}", flush=True)
    fewest = min(main_errors.values())
    candidates = [value for value in SILENCES if main_errors[value] == fewest]
    best = min(candidates, key=lambda value: (hesitation_cuts[value], value))
    print(f"\nmain clips, cuts + merges: {main_errors}; hesitation runs, cuts: {hesitation_cuts}")
    print(f"candidates (fewest errors on the main clips): {candidates}; best: {best} s "
          "(then the fewest hesitation cuts, then the shortest)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
