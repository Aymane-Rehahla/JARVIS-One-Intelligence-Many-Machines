"""Voice activity detection (step 6E): Silero VAD through sherpa-onnx finds where the owner starts and stops talking.

An utterance ends after `silence_s` seconds of silence, bounded to 0.5-0.8 s (plan section 5). The default,
SILENCE_S = 0.6 s, was measured with `tools/vad_silence_probe.py` (table in `voice/README.md`, 31 Oct): on the main
clips (the Parakeet test recording, 2 sentences, and 8 Kokoro clips of 3 sentences with 0.3 s pauses inside a
sentence and 1.2 s between sentences) 0.5 and 0.6 s found exactly the 26 sentences, while 0.7 and 0.8 s merged the
recording's two sentences into one. The tie was broken by longer pauses inside a sentence (0.45-0.65 s): 0.5 s cut
81 sentences, 0.6 s 33 (none with pauses up to 0.5 s). A longer value would cut less on hesitations but merge real
sentences and answer later.

- **In:** 16 kHz mono float32 samples in [-1, 1], in pieces of any length (`accept`).
- **Out:** each finished utterance as a float32 array (`accept`, `flush`); `speaking` says whether speech is going on.
- **Tests:** `tests/voice/test_voice_units.py` (bounds and `frames` without a model; the real model is deferred).
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16000                    # Silero VAD in sherpa-onnx runs at 16 kHz
SILENCE_S = 0.6                        # measured by tools/vad_silence_probe.py (module docstring, README.md)
SILENCE_MIN_S = 0.5
SILENCE_MAX_S = 0.8
THRESHOLD = 0.5                        # Silero's speech probability threshold (sherpa-onnx default)
MIN_SPEECH_S = 0.25                    # shorter blips are not utterances (sherpa-onnx default)
MAX_SPEECH_S = 20.0                    # a longer utterance is cut here, so one utterance is bounded
BUFFER_S = 30.0                        # sherpa-onnx's internal ring buffer; longer than MAX_SPEECH_S
WINDOW = 512                           # Silero's window at 16 kHz (32 ms)
MAX_ACCEPT_S = 60.0                    # the most audio one accept() call takes

Samples = npt.NDArray[np.float32]


def frames(samples: npt.ArrayLike, window: int) -> tuple[list[Samples], Samples]:
    """Split `samples` into whole windows of `window` samples and the remainder (shorter than one window)."""
    if window <= 0:
        raise ValueError(f"window must be positive, not {window}")
    array = as_samples(samples)
    whole = len(array) // window * window
    return [array[start:start + window] for start in range(0, whole, window)], array[whole:].copy()


def as_samples(samples: npt.ArrayLike) -> Samples:
    """`samples` as 1-D float32 (no copy when it already is; ValueError for anything that is not one channel)."""
    array = np.asarray(samples, dtype=np.float32)
    if array.ndim != 1:
        raise ValueError(f"expected 1-D mono samples, got shape {array.shape}")
    return array


class Vad:
    """Silero VAD with an end-of-speech silence of `silence_s` (0.5-0.8 s). Feed audio with `accept`; each finished
    utterance comes back once. Not for two streams at a time: `reset()` between streams. Thread-safe."""

    def __init__(self, model_path: Path, *, silence_s: float = SILENCE_S, threshold: float = THRESHOLD,
                 sample_rate: int = SAMPLE_RATE) -> None:
        if not SILENCE_MIN_S <= silence_s <= SILENCE_MAX_S:
            raise ValueError(f"silence_s must be {SILENCE_MIN_S}-{SILENCE_MAX_S} s, not {silence_s}")
        if not 0.0 < threshold < 1.0:
            raise ValueError(f"threshold must be between 0 and 1, not {threshold}")
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"Silero VAD runs at {SAMPLE_RATE} Hz, not {sample_rate}")
        if not Path(model_path).is_file():
            raise FileNotFoundError(f"no VAD model at {model_path}")
        import sherpa_onnx

        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(model_path)
        config.silero_vad.threshold = threshold
        config.silero_vad.min_silence_duration = silence_s
        config.silero_vad.min_speech_duration = MIN_SPEECH_S
        config.silero_vad.max_speech_duration = MAX_SPEECH_S
        config.silero_vad.window_size = WINDOW
        config.sample_rate = sample_rate
        config.num_threads = 1
        if not config.validate():
            raise ValueError(f"sherpa-onnx refused the VAD config for {model_path}")
        self.silence_s = silence_s
        self.threshold = threshold
        self.sample_rate = sample_rate
        self._vad: Any = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=BUFFER_S)
        self._pending: Samples = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()

    @property
    def speaking(self) -> bool:
        """True while the detector hears speech that has not ended yet."""
        with self._lock:
            return bool(self._vad.is_speech_detected())

    def accept(self, samples: npt.ArrayLike) -> list[Samples]:
        """Feed audio (any length up to MAX_ACCEPT_S); returns the utterances that ended in it, oldest first."""
        array = as_samples(samples)
        if len(array) > MAX_ACCEPT_S * self.sample_rate:
            raise ValueError(f"accept() takes at most {MAX_ACCEPT_S} s of audio at a time")
        with self._lock:
            windows, self._pending = frames(np.concatenate([self._pending, array]), WINDOW)
            done: list[Samples] = []
            for window in windows:
                self._vad.accept_waveform(window)
                done.extend(self._pop())
            return done

    def flush(self) -> list[Samples]:
        """End the stream: the utterance still going on (if any) is finished and returned. Call `reset()` before
        feeding a new stream."""
        with self._lock:
            if len(self._pending):
                padded = np.zeros(WINDOW, dtype=np.float32)
                padded[:len(self._pending)] = self._pending
                self._vad.accept_waveform(padded)
                self._pending = np.zeros(0, dtype=np.float32)
            self._vad.flush()
            return self._pop()

    def reset(self) -> None:
        """Forget everything heard so far (a new stream starts)."""
        with self._lock:
            self._vad.reset()
            self._pending = np.zeros(0, dtype=np.float32)

    def _pop(self) -> list[Samples]:
        done: list[Samples] = []
        while not self._vad.empty():
            done.append(np.array(self._vad.front.samples, dtype=np.float32))
            self._vad.pop()
        return done
