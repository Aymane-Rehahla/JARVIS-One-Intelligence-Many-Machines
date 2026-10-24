"""Each downloaded voice model loads and does its job.

Deferred (plan ground rule 1): no voice model may be loaded before the Phase 5 training ends; Parakeet alone takes
about 650 MB. Listed in tests/DEFERRED.md as D1; runs in step 6E with JARVIS_RUN_DEFERRED=1.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

pytestmark = pytest.mark.deferred

VOICE = Path(__file__).resolve().parents[2] / "models" / "voice"
SAMPLE_WAV = VOICE / "parakeet-tdt-0.6b-v2-int8" / "test_wavs" / "0.wav"


def _read_wav(path: Path) -> tuple[int, npt.NDArray[np.float32]]:
    """16-bit mono WAV as float32 samples in [-1, 1]."""
    with wave.open(str(path)) as clip:
        assert clip.getsampwidth() == 2 and clip.getnchannels() == 1
        rate, frames = clip.getframerate(), clip.readframes(clip.getnframes())
    return rate, np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def test_silero_vad_finds_speech_in_a_recording() -> None:
    import sherpa_onnx

    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(VOICE / "silero-vad" / "silero_vad.onnx")
    config.sample_rate = 16000
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
    rate, samples = _read_wav(SAMPLE_WAV)
    assert rate == 16000
    window = config.silero_vad.window_size
    for start in range(0, len(samples) - window, window):
        vad.accept_waveform(samples[start:start + window])
    vad.flush()
    assert not vad.empty(), "no speech segment found"


def test_parakeet_transcribes_its_sample() -> None:
    import sherpa_onnx

    folder = VOICE / "parakeet-tdt-0.6b-v2-int8"
    recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(folder / "encoder.int8.onnx"), decoder=str(folder / "decoder.int8.onnx"),
        joiner=str(folder / "joiner.int8.onnx"), tokens=str(folder / "tokens.txt"), num_threads=2,
        model_type="nemo_transducer")
    rate, samples = _read_wav(SAMPLE_WAV)
    stream = recognizer.create_stream()
    stream.accept_waveform(rate, samples)
    recognizer.decode_stream(stream)
    assert len(stream.result.text.split()) >= 3, stream.result.text


def test_kokoro_speaks_a_sentence() -> None:
    import sherpa_onnx

    folder = VOICE / "kokoro-multi-lang-v1_0"
    kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
        model=str(folder / "model.onnx"), voices=str(folder / "voices.bin"), tokens=str(folder / "tokens.txt"),
        data_dir=str(folder / "espeak-ng-data"), dict_dir=str(folder / "dict"),
        lexicon=f"{folder / 'lexicon-us-en.txt'},{folder / 'lexicon-zh.txt'}")
    config = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(kokoro=kokoro, num_threads=2))
    assert config.validate()
    audio = sherpa_onnx.OfflineTts(config).generate("The HVAC compressor has failed.", sid=0, speed=1.0)
    assert audio.sample_rate > 0 and len(audio.samples) > audio.sample_rate // 2


def test_open_wake_word_loads_hey_jarvis() -> None:
    from openwakeword.model import Model

    folder = VOICE / "openwakeword"
    model = Model(wakeword_models=[str(folder / "hey_jarvis_v0.1.onnx")], inference_framework="onnx",
                  melspec_model_path=str(folder / "melspectrogram.onnx"),
                  embedding_model_path=str(folder / "embedding_model.onnx"))
    scores = model.predict(np.zeros(1280, dtype=np.int16))
    assert isinstance(scores, dict) and scores, scores
    assert all(0.0 <= float(score) <= 1.0 for score in scores.values())
