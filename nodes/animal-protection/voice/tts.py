"""Text to speech (step 6E): Kokoro-82M v1.0 through sherpa-onnx, a British male voice (`bm_george`) by default.

The voice names and speaker ids come from the model's own custom metadata (`model.onnx`, key `speaker2id`, else
`speaker_names`), read once with onnxruntime; nothing is downloaded and no list is guessed. The installed model has 54
voices (`n_speakers` = 54), `bm_george` among them. The synthesiser (about 330 MB in RAM) loads on first use or on
`load()`; `close()` frees it. A reply is spoken sentence by sentence: `split_for_speech()` cuts a text into sentences
without breaking numbers ("37.5") or abbreviations ("e.g."), each at most MAX_TEXT_CHARS long.

- **In:** text (at most MAX_TEXT_CHARS per call), a voice name and a speed (0.5-2.0, larger is faster).
- **Out:** `Speech(samples, sample_rate, synth_s)`: mono float32 audio at the model's rate (24 kHz) and the time it took.
- **Tests:** `tests/voice/test_voice_units.py` (the splitter and argument checks without a model; the real model is
  deferred).
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

MODEL = "model.onnx"
VOICES = "voices.bin"
TOKENS = "tokens.txt"
ESPEAK_DATA = "espeak-ng-data"
# Both English lexicons; where a word is in both, the first one wins (sherpa-onnx ignores the later duplicate), so the
# British one comes first for the British default voice. sherpa-onnx >= 1.12.15 needs no dict_dir for this model.
LEXICONS = ("lexicon-gb-en.txt", "lexicon-us-en.txt")
DEFAULT_VOICE = "bm_george"            # British male (plan section 5)
SPEED_MIN = 0.5
SPEED_MAX = 2.0
MAX_TEXT_CHARS = 600                   # the most text one speak() call takes; split_for_speech keeps under it
MAX_VOICES = 100                       # VoiceSettingsResult.voices holds at most 100
MAX_THREADS = 8
VOICE_NAME = re.compile(r"^[a-z]{2}_[a-z]+$")    # contracts.VOICE_ID

# Words that end in "." without ending a sentence (lowercase, without the final dot). Words that often do end one
# ("etc", "a.m") are left out: before a lowercase word they are kept in the sentence anyway.
ABBREVIATIONS = frozenset({
    "e.g", "i.e", "vs", "approx", "ca", "cf", "dr", "mr", "mrs", "ms", "prof", "st", "fig", "jan", "feb", "mar",
    "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"})
LOOKBACK = 30                          # characters read back from a "." to find its word (abbreviations are short)
_END = re.compile(r"[.!?]+[\"')\]]*(?=\s|$)|\n\s*\n")


@dataclass(frozen=True, eq=False)
class Speech:
    """Synthesised audio: mono float32 samples at `sample_rate`, and the seconds synthesis took."""

    samples: npt.NDArray[np.float32]
    sample_rate: int
    synth_s: float = 0.0

    @property
    def seconds(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate else 0.0


def read_speakers(model_path: Path) -> tuple[dict[str, int], int]:
    """({voice name: speaker id}, sample rate) from the Kokoro model's custom metadata, read with onnxruntime.

    `speaker2id` ("af_alloy->0,af_aoede->1,...") is used when present, else `speaker_names` (a comma list in id
    order). ValueError if the model names no voices, if a name is malformed, or if the count disagrees with
    `n_speakers`."""
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_DISABLE_ALL
    session = onnxruntime.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
    meta: dict[str, str] = dict(session.get_modelmeta().custom_metadata_map)
    del session                                                   # only the metadata is wanted; free the weights
    return parse_speakers(meta), int(meta.get("sample_rate", "24000"))


def parse_speakers(meta: dict[str, str]) -> dict[str, int]:
    """The voice table from Kokoro metadata (see `read_speakers`)."""
    speakers: dict[str, int] = {}
    if meta.get("speaker2id", "").strip():
        for pair in meta["speaker2id"].split(","):
            name, arrow, sid = pair.strip().partition("->")
            if not arrow or not sid.strip().isdigit():
                raise ValueError(f"malformed speaker2id entry {pair!r}")
            speakers[name.strip()] = int(sid)
    elif meta.get("speaker_names", "").strip():
        speakers = {name.strip(): sid for sid, name in enumerate(meta["speaker_names"].split(","))}
    else:
        raise ValueError("the Kokoro model's metadata names no voices (no speaker2id or speaker_names)")
    bad = [name for name in speakers if not VOICE_NAME.fullmatch(name)]
    if bad:
        raise ValueError(f"malformed voice names in the model metadata: {bad[:5]}")
    if len(set(speakers.values())) != len(speakers):
        raise ValueError("two voices share a speaker id in the model metadata")
    count = meta.get("n_speakers", "").strip()
    if count and int(count) != len(speakers):
        raise ValueError(f"n_speakers is {count} but the metadata names {len(speakers)} voices")
    if len(speakers) > MAX_VOICES:
        raise ValueError(f"{len(speakers)} voices is more than {MAX_VOICES}")
    return speakers


def split_for_speech(text: str, *, limit: int = MAX_TEXT_CHARS) -> list[str]:
    """The sentences of `text`, in order, whitespace collapsed, each at most `limit` characters. A sentence ends at
    . ! ? (with any closing quote or bracket) before white space or the end, or at a blank line; never inside a
    number ("37.5"), after an abbreviation ("e.g.", "Dr."), or before a word that starts in lowercase. A sentence longer than `limit` is cut at its last comma, semicolon or space before it."""
    if limit < 20:
        raise ValueError(f"limit must be at least 20, not {limit}")
    sentences: list[str] = []
    start = 0
    for match in _END.finditer(text):
        if match.group().startswith((".", "!", "?")) and not _ends_sentence(text, match.start(), match.end()):
            continue
        _add(sentences, text[start:match.end()], limit)
        start = match.end()
    _add(sentences, text[start:], limit)
    return sentences


def _ends_sentence(text: str, dot: int, after: int) -> bool:
    if text[dot] == ".":
        words = text[max(0, dot - LOOKBACK):dot].split()
        if words and words[-1].lower().lstrip("(\"'[") in ABBREVIATIONS:
            return False
    rest = text[after:].lstrip()
    return not rest or not rest[0].islower()


def _add(sentences: list[str], piece: str, limit: int) -> None:
    piece = " ".join(piece.split())
    while len(piece) > limit:
        cut = max(piece.rfind(", ", 0, limit), piece.rfind("; ", 0, limit))
        cut = cut + 1 if cut > limit // 3 else piece.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        sentences.append(piece[:cut].strip())
        piece = piece[cut:].strip()
    if piece:
        sentences.append(piece)


class TextToSpeech:
    """Kokoro through sherpa-onnx's OfflineTts. Thread-safe (one synthesis at a time)."""

    def __init__(self, folder: Path, *, threads: int = 2) -> None:
        if not 1 <= threads <= MAX_THREADS:
            raise ValueError(f"threads must be 1-{MAX_THREADS}, not {threads}")
        self.folder = Path(folder)
        self.threads = threads
        self._speakers: dict[str, int] | None = None
        self._rate = 0
        self._tts: Any = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._tts is not None

    @property
    def sample_rate(self) -> int:
        """The model's output rate (read from its metadata on first use)."""
        with self._lock:
            self._read_speakers()
            return self._rate

    def voices(self) -> list[str]:
        """Every voice the installed model has, sorted (from its metadata; the synthesiser is not loaded)."""
        with self._lock:
            return sorted(self._read_speakers())

    def load(self) -> None:
        """Load the synthesiser now (it otherwise loads on the first `speak`). Loading twice does nothing."""
        with self._lock:
            self._load()

    def close(self) -> None:
        """Free the synthesiser; the next `speak` loads it again."""
        with self._lock:
            self._tts = None

    def speak(self, text: str, *, voice: str = DEFAULT_VOICE, speed: float = 1.0) -> Speech:
        """Synthesise `text` (at most MAX_TEXT_CHARS; blank text gives empty audio without a model call) in `voice`
        at `speed`. ValueError for an unknown voice, a speed outside 0.5-2.0 or too long a text."""
        if not SPEED_MIN <= speed <= SPEED_MAX:
            raise ValueError(f"speed must be {SPEED_MIN}-{SPEED_MAX}, not {speed}")
        if not VOICE_NAME.fullmatch(voice):
            raise ValueError(f"unknown voice {voice!r}")
        text = " ".join(text.split())
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"at most {MAX_TEXT_CHARS} characters per call, not {len(text)}; use split_for_speech")
        with self._lock:
            speakers = self._read_speakers()
            if voice not in speakers:
                raise ValueError(f"unknown voice {voice!r}; the model has {len(speakers)} voices")
            if not text:
                return Speech(samples=np.zeros(0, dtype=np.float32), sample_rate=self._rate)
            self._load()
            started = time.monotonic()
            audio = self._tts.generate(text, sid=speakers[voice], speed=speed)
            samples = np.asarray(audio.samples, dtype=np.float32)
            return Speech(samples=samples, sample_rate=int(audio.sample_rate), synth_s=time.monotonic() - started)

    def _read_speakers(self) -> dict[str, int]:
        if self._speakers is None:
            model = self.folder / MODEL
            if not model.is_file():
                raise FileNotFoundError(f"no Kokoro model at {model}")
            self._speakers, self._rate = read_speakers(model)
        return self._speakers

    def _load(self) -> None:
        if self._tts is not None:
            return
        self._read_speakers()                                     # before the synthesiser: one model in RAM at a time
        paths = [self.folder / name for name in (MODEL, VOICES, TOKENS, ESPEAK_DATA, *LEXICONS)]
        missing = [path.name for path in paths if not path.exists()]
        if missing:
            raise FileNotFoundError(f"Kokoro files missing in {self.folder}: {', '.join(missing)}")
        import sherpa_onnx

        kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=str(self.folder / MODEL), voices=str(self.folder / VOICES), tokens=str(self.folder / TOKENS),
            data_dir=str(self.folder / ESPEAK_DATA), lexicon=",".join(str(self.folder / name) for name in LEXICONS))
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(kokoro=kokoro, num_threads=self.threads))
        if not config.validate():
            raise ValueError(f"sherpa-onnx refused the Kokoro config for {self.folder}")
        tts = sherpa_onnx.OfflineTts(config)
        if int(tts.num_speakers) != len(self._speakers or {}):
            raise ValueError(f"the synthesiser has {tts.num_speakers} speakers, the metadata {len(self._speakers or {})}")
        self._tts = tts
