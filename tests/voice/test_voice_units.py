"""voice/vad.py, voice/stt.py and voice/tts.py (step 6E).

The fast tests load no model (argument checks, the sentence splitter, the metadata parser, empty audio). The tests
marked `deferred` load the real models from models/voice/, one at a time (each frees its model before the next test):
run them with JARVIS_RUN_DEFERRED=1 and -s to see the timings they print.
"""
from __future__ import annotations

import gc
import re
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
import soundfile

from voice.stt import MIN_AUDIO_S, SpeechToText, Transcript
from voice.tts import (DEFAULT_VOICE, MAX_TEXT_CHARS, Speech, TextToSpeech, parse_speakers,
                       split_for_speech)
from voice.vad import SILENCE_MAX_S, SILENCE_MIN_S, SILENCE_S, WINDOW, Vad, frames

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "models" / "voice"
SILERO = VOICE / "silero-vad" / "silero_vad.onnx"
PARAKEET = VOICE / "parakeet-tdt-0.6b-v2-int8"
KOKORO = VOICE / "kokoro-multi-lang-v1_0"
SAMPLE_WAV = PARAKEET / "test_wavs" / "0.wav"
VOICE_ID = re.compile(r"^[a-z]{2}_[a-z]+$")                      # contracts.VOICE_ID


# --- vad.py, no model ---

def test_the_vad_silence_is_bounded_to_half_a_second_to_0_8_s(tmp_path: Path) -> None:
    assert SILENCE_MIN_S <= SILENCE_S <= SILENCE_MAX_S
    assert (SILENCE_MIN_S, SILENCE_MAX_S) == (0.5, 0.8)
    for silence_s in (0.0, 0.49, 0.81, 2.0):
        with pytest.raises(ValueError, match="silence_s"):
            Vad(SILERO, silence_s=silence_s)
    with pytest.raises(ValueError, match="threshold"):
        Vad(SILERO, silence_s=0.6, threshold=1.0)
    with pytest.raises(ValueError, match="16000"):
        Vad(SILERO, silence_s=0.6, sample_rate=8000)
    with pytest.raises(FileNotFoundError):
        Vad(tmp_path / "missing.onnx", silence_s=SILENCE_MAX_S)


def test_frames_cuts_whole_windows_and_keeps_the_rest() -> None:
    windows, rest = frames(np.arange(1100, dtype=np.float32), 512)
    assert [len(window) for window in windows] == [512, 512] and len(rest) == 76
    assert windows[1][0] == 512 and rest[0] == 1024 and rest.dtype == np.float32
    windows, rest = frames(np.zeros(0, dtype=np.float32), WINDOW)
    assert windows == [] and len(rest) == 0
    with pytest.raises(ValueError, match="window"):
        frames(np.zeros(10), 0)
    with pytest.raises(ValueError, match="mono"):
        frames(np.zeros((2, 10)), 4)


# --- stt.py, no model ---

def test_empty_or_too_short_audio_gives_an_empty_transcript_without_the_model(tmp_path: Path) -> None:
    stt = SpeechToText(tmp_path / "no-model-here")
    assert stt.transcribe(np.zeros(0, dtype=np.float32)) == Transcript(text="")
    short = stt.transcribe(np.zeros(int(16000 * (MIN_AUDIO_S - 0.01)), dtype=np.float32))
    assert short.text == "" and short.decode_s == 0.0 and 0.18 < short.seconds < MIN_AUDIO_S
    assert not stt.loaded                                         # the model was never asked for
    with pytest.raises(FileNotFoundError, match="encoder"):
        stt.transcribe(np.zeros(16000, dtype=np.float32))         # long enough: it would need the model


def test_speech_to_text_checks_its_arguments(tmp_path: Path) -> None:
    for threads in (0, 9):
        with pytest.raises(ValueError, match="threads"):
            SpeechToText(tmp_path, threads=threads)
    stt = SpeechToText(tmp_path)
    with pytest.raises(ValueError, match="mono"):
        stt.transcribe(np.zeros((2, 16000), dtype=np.float32))
    with pytest.raises(ValueError, match="sample_rate"):
        stt.transcribe(np.zeros(16000, dtype=np.float32), sample_rate=0)
    with pytest.raises(ValueError, match="at most"):
        stt.transcribe(np.zeros(16000 * 61, dtype=np.float32))
    with pytest.raises(FileNotFoundError):
        stt.load()


# --- tts.py, no model ---

def test_split_for_speech_keeps_numbers_and_abbreviations_whole() -> None:
    text = ("The supply air is 37.5 degrees. Fan two runs at 1,200 rpm, e.g. at night! Dr. Lee checked it, "
            "approx. an hour ago. Is it safe? Yes.")
    assert split_for_speech(text) == [
        "The supply air is 37.5 degrees.", "Fan two runs at 1,200 rpm, e.g. at night!",
        "Dr. Lee checked it, approx. an hour ago.", "Is it safe?", "Yes."]


def test_split_for_speech_handles_quotes_blank_lines_and_lowercase_continuations() -> None:
    assert split_for_speech('He said "stop." Then it cooled.') == ['He said "stop."', "Then it cooled."]
    assert split_for_speech("Heater, fans, etc. are fine.\n\nNo alarms") == ["Heater, fans, etc. are fine.",
                                                                            "No alarms"]
    assert split_for_speech("It ran at 3 p.m. and stopped. Wait... what? OK.") == [
        "It ran at 3 p.m. and stopped.", "Wait... what?", "OK."]
    assert split_for_speech("") == [] and split_for_speech("   \n ") == []
    assert split_for_speech("No full stop at the end") == ["No full stop at the end"]
    assert split_for_speech("The   spaces\n collapse.  Two.") == ["The spaces collapse.", "Two."]


def test_split_for_speech_bounds_every_piece() -> None:
    long = ", ".join(f"reading {index} is normal" for index in range(60)) + "."
    pieces = split_for_speech(long)
    assert len(pieces) > 1 and all(len(piece) <= MAX_TEXT_CHARS for piece in pieces)
    assert " ".join(pieces) == long                               # nothing lost, nothing added
    words = split_for_speech("x" * 1500, limit=600)
    assert [len(piece) for piece in words] == [600, 600, 300]
    with pytest.raises(ValueError, match="limit"):
        split_for_speech("Hello.", limit=5)


def test_the_voice_table_comes_from_the_metadata() -> None:
    meta = {"speaker2id": "af_alloy->0,bm_george->26,bm_lewis->27", "n_speakers": "3",
            "speaker_names": "ignored,when,speaker2id_exists"}
    assert parse_speakers(meta) == {"af_alloy": 0, "bm_george": 26, "bm_lewis": 27}
    assert parse_speakers({"speaker_names": "af_alloy,bm_george"}) == {"af_alloy": 0, "bm_george": 1}
    with pytest.raises(ValueError, match="names no voices"):
        parse_speakers({"n_speakers": "54"})                      # a count alone is never turned into guessed names
    with pytest.raises(ValueError, match="n_speakers"):
        parse_speakers({"speaker2id": "af_alloy->0", "n_speakers": "54"})
    with pytest.raises(ValueError, match="malformed"):
        parse_speakers({"speaker2id": "af_alloy=0"})
    with pytest.raises(ValueError, match="malformed voice"):
        parse_speakers({"speaker_names": "af_alloy,George"})
    with pytest.raises(ValueError, match="share"):
        parse_speakers({"speaker2id": "af_alloy->0,bm_george->0"})


def test_text_to_speech_checks_its_arguments_before_any_model(tmp_path: Path) -> None:
    for threads in (0, 9):
        with pytest.raises(ValueError, match="threads"):
            TextToSpeech(tmp_path, threads=threads)
    tts = TextToSpeech(tmp_path / "no-model-here")
    for speed in (0.49, 2.01, 0.0):
        with pytest.raises(ValueError, match="speed"):
            tts.speak("Hello.", speed=speed)
    with pytest.raises(ValueError, match="unknown voice"):
        tts.speak("Hello.", voice="George")
    with pytest.raises(ValueError, match="characters"):
        tts.speak("a" * (MAX_TEXT_CHARS + 1))
    with pytest.raises(FileNotFoundError, match="Kokoro"):
        tts.voices()
    assert not tts.loaded and DEFAULT_VOICE == "bm_george"


def test_an_unknown_voice_is_refused_and_blank_text_is_silence(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    tts = TextToSpeech(tmp_path)
    monkeypatch.setattr(tts, "_speakers", {"bm_george": 26, "af_heart": 3})
    monkeypatch.setattr(tts, "_rate", 24000)
    assert tts.voices() == ["af_heart", "bm_george"]
    with pytest.raises(ValueError, match="unknown voice 'bm_nobody'"):
        tts.speak("Hello.", voice="bm_nobody")
    blank = tts.speak("  \n ")
    assert isinstance(blank, Speech) and len(blank.samples) == 0 and blank.sample_rate == 24000
    assert blank.seconds == 0.0 and not tts.loaded


# --- the real models (deferred: run one file at a time with JARVIS_RUN_DEFERRED=1) ---

def _read(path: Path) -> np.ndarray:
    samples, rate = soundfile.read(str(path), dtype="float32")
    assert rate == 16000
    return np.asarray(samples, dtype=np.float32)


def _words(text: str) -> list[str]:
    text = text.lower().replace("-", " ").replace("24", "twenty four")
    return re.findall(r"[a-z']+", text)


@pytest.fixture
def stt() -> Iterator[SpeechToText]:
    model = SpeechToText(PARAKEET)
    yield model
    model.close()
    gc.collect()


@pytest.fixture
def tts() -> Iterator[TextToSpeech]:
    model = TextToSpeech(KOKORO)
    yield model
    model.close()
    gc.collect()


@pytest.mark.deferred
def test_the_vad_finds_the_sentences_of_a_real_recording() -> None:
    samples = _read(SAMPLE_WAV)                                   # 7.4 s, two sentences with a pause between
    padded = np.concatenate([np.zeros(8000, np.float32), samples, np.zeros(8000, np.float32)])
    found: dict[float, list[np.ndarray]] = {}
    for silence_s in (SILENCE_S, SILENCE_MAX_S):
        vad = Vad(SILERO, silence_s=silence_s)
        started = time.monotonic()
        utterances = [utterance for start in range(0, len(padded), 1600)
                      for utterance in vad.accept(padded[start:start + 1600])]
        utterances += vad.flush()
        print(f"\nVAD silence {silence_s} s: {len(utterances)} utterances "
              f"{[round(len(u) / 16000, 2) for u in utterances]} in {time.monotonic() - started:.3f} s")
        found[silence_s] = utterances
        assert all(u.dtype == np.float32 and u.ndim == 1 for u in utterances)
        assert not vad.speaking
        vad.reset()
        assert vad.flush() == []                                  # nothing left after a reset
    assert len(found[SILENCE_S]) == 2                             # one per sentence at the measured default
    assert len(found[SILENCE_MAX_S]) == 1                         # 0.8 s hears both as one utterance
    assert sum(len(u) for u in found[SILENCE_MAX_S]) > 0.8 * len(samples)


@pytest.mark.deferred
def test_parakeet_transcribes_a_recording(stt: SpeechToText) -> None:
    started = time.monotonic()
    stt.load()
    load_s = time.monotonic() - started
    assert stt.loaded
    samples = _read(SAMPLE_WAV)
    stt.transcribe(samples)                                       # warm-up
    result = stt.transcribe(samples)
    print(f"\nParakeet: load {load_s:.2f} s; {result.seconds:.2f} s of audio decoded in {result.decode_s:.2f} s "
          f"({result.decode_s / result.seconds:.3f} s per second of audio): {result.text!r}")
    assert len(result.text.split()) >= 3
    assert {"portrait", "eyes"} <= set(_words(result.text))
    assert result.seconds == pytest.approx(len(samples) / 16000) and result.decode_s > 0


@pytest.mark.deferred
def test_kokoro_lists_its_voices_and_speaks(tts: TextToSpeech) -> None:
    started = time.monotonic()
    voices = tts.voices()
    meta_s = time.monotonic() - started
    assert DEFAULT_VOICE in voices and len(voices) == 54 and voices == sorted(voices)
    assert all(VOICE_ID.fullmatch(voice) for voice in voices)
    started = time.monotonic()
    tts.load()
    load_s = time.monotonic() - started
    tts.speak("Ready.")                                           # warm-up
    speech = tts.speak("The incubator temperature is steady at thirty seven point five degrees.")
    fast = tts.speak("The incubator temperature is steady at thirty seven point five degrees.", speed=1.5)
    print(f"\nKokoro: metadata {meta_s:.2f} s, load {load_s:.2f} s; {speech.seconds:.2f} s of audio at "
          f"{speech.sample_rate} Hz in {speech.synth_s:.2f} s ({speech.synth_s / speech.seconds:.3f} s per second "
          f"of audio); at speed 1.5: {fast.seconds:.2f} s")
    assert speech.sample_rate == tts.sample_rate == 24000
    assert speech.seconds > 0.5 and speech.samples.dtype == np.float32
    assert float(np.max(np.abs(speech.samples))) > 0.05            # not silence
    assert fast.seconds < speech.seconds
    with pytest.raises(ValueError, match="unknown voice"):
        tts.speak("Hello.", voice="zz_nobody")


@pytest.mark.deferred
def test_round_trip_kokoro_then_parakeet() -> None:
    sentence = "The supply air is twenty four degrees."
    tts = TextToSpeech(KOKORO)
    speech = tts.speak(sentence)
    tts.close()                                                   # one model in RAM at a time
    del tts
    gc.collect()
    stt = SpeechToText(PARAKEET)
    try:
        result = stt.transcribe(speech.samples, sample_rate=speech.sample_rate)
    finally:
        stt.close()
        gc.collect()
    expected, heard = _words(sentence), set(_words(result.text))
    matched = sum(word in heard for word in expected)
    print(f"\nround trip: {sentence!r} -> {result.text!r} ({matched}/{len(expected)} words)")
    assert matched >= 0.8 * len(expected)
