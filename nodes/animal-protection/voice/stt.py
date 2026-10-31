"""Speech to text (step 6E): Parakeet TDT 0.6B v2 int8 through sherpa-onnx, English only.

The model (about 650 MB in RAM) loads on first use, or on `load()`; `close()` frees it. Audio shorter than
MIN_AUDIO_S is not worth decoding and gives an empty transcript without touching the model.

- **In:** one utterance, mono float32 samples in [-1, 1] at 16 kHz (another rate is resampled by sherpa-onnx).
- **Out:** `Transcript(text, seconds, decode_s)`: the words, the audio's length and the time decoding took.
- **Tests:** `tests/voice/test_voice_units.py` (empty audio and argument checks without a model; the real model and the
  round trip with Kokoro are deferred).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy.typing as npt

from voice.vad import SAMPLE_RATE, as_samples

MODEL_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
MIN_AUDIO_S = 0.2                      # shorter audio holds no word: empty transcript, no model call
MAX_AUDIO_S = 60.0                     # one utterance is at most this long (the VAD cuts at 20 s)
MAX_THREADS = 8
MODEL_TYPE = "nemo_transducer"


@dataclass(frozen=True)
class Transcript:
    """What was said in one utterance: `text` ("" for silence or too-short audio), the audio's length in seconds,
    and the seconds decoding took (0 when the model was not called)."""

    text: str
    seconds: float = 0.0
    decode_s: float = 0.0


class SpeechToText:
    """Parakeet TDT through sherpa-onnx's offline transducer recognizer. Thread-safe (one decode at a time)."""

    def __init__(self, folder: Path, *, threads: int = 2) -> None:
        if not 1 <= threads <= MAX_THREADS:
            raise ValueError(f"threads must be 1-{MAX_THREADS}, not {threads}")
        self.folder = Path(folder)
        self.threads = threads
        self._recognizer: Any = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._recognizer is not None

    def load(self) -> None:
        """Load the model now (it otherwise loads on the first `transcribe`). Loading twice does nothing."""
        with self._lock:
            self._load()

    def close(self) -> None:
        """Free the model; the next `transcribe` loads it again."""
        with self._lock:
            self._recognizer = None

    def transcribe(self, samples: npt.ArrayLike, *, sample_rate: int = SAMPLE_RATE) -> Transcript:
        """The words in one utterance. Empty or shorter than MIN_AUDIO_S: an empty transcript, no model call."""
        array = as_samples(samples)
        if sample_rate <= 0:
            raise ValueError(f"sample_rate must be positive, not {sample_rate}")
        seconds = len(array) / sample_rate
        if seconds > MAX_AUDIO_S:
            raise ValueError(f"an utterance is at most {MAX_AUDIO_S} s, not {seconds:.1f} s")
        if seconds < MIN_AUDIO_S:
            return Transcript(text="", seconds=seconds)
        with self._lock:
            self._load()
            started = time.monotonic()
            stream = self._recognizer.create_stream()
            stream.accept_waveform(sample_rate, array)
            self._recognizer.decode_stream(stream)
            text = " ".join(str(stream.result.text).split())
            return Transcript(text=text, seconds=seconds, decode_s=time.monotonic() - started)

    def _load(self) -> None:
        if self._recognizer is not None:
            return
        missing = [name for name in MODEL_FILES if not (self.folder / name).is_file()]
        if missing:
            raise FileNotFoundError(f"Parakeet files missing in {self.folder}: {', '.join(missing)}")
        import sherpa_onnx

        encoder, decoder, joiner, tokens = (str(self.folder / name) for name in MODEL_FILES)
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=encoder, decoder=decoder, joiner=joiner, tokens=tokens, num_threads=self.threads,
            sample_rate=SAMPLE_RATE, model_type=MODEL_TYPE)
