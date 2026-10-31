"""The voice box (P4 section 2, decision 0022): push-to-talk in, the agent's verified reply spoken sentence by sentence.

In: the owner's push-to-talk (the website's button, `jarvis.incubator-1.ptt`), audio from the microphone or a file
source at 16 kHz, `VoiceSettings` (request-reply), and `OwnerAlert`s (`announce`). Out: `VoiceStatus` on
`jarvis.incubator-1.voice` on every change and every STATUS_EVERY_S, audio out, and measured timings (E6, voice part).
- `answer` has the signature of `Agent.reply`; this module never imports the agent. A reply's `ChatChunk`s are spoken
  as they arrive, sentence by sentence, each with the voice and speed in force when it starts.
- `announce(alert)`: an alert is spoken at once: before anything still waiting; a DANGER alert also stops what is
  being said.
- Pressing the button stops what is being said and starts listening; releasing it ends the utterance.
- A missing microphone is `mic = "missing"`, never a crash. Everything is bounded: the utterance length, the queue of
  sentences, each stage's time.
The models are injected (`vad.Vad`, `stt.SpeechToText`, `tts.TextToSpeech` or test fakes), so are the audio devices
(`SoundDeviceAudio` or `FileAudio`). Tests: tests/voice/test_pipeline.py.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np

from comms.jsonlog import log
from contracts import (ChatChunk, ChatDone, OwnerAlert, PushToTalk, VoiceSettings, VoiceSettingsResult, VoiceStatus,
                       jarvis_subject, utc_now)

SAMPLE_RATE = 16_000
MAX_UTTERANCE_S = 30.0                      # the owner's one question; longer audio is cut here
MAX_QUEUE = 32                              # sentences waiting to be spoken
STT_S = 15.0                                # transcription deadline for one utterance
TTS_S = 15.0                                # synthesis deadline for one sentence
STATUS_EVERY_S = 5.0
DEFAULT_VOICE = "bm_george"
DEFAULT_SPEED = 1.0
SETTINGS_KEY = "voice"
MIN_SPEECH_S = 0.2
ABBREVIATIONS = {"approx", "e.g", "i.e", "etc", "vs", "mr", "mrs", "dr", "no", "fig"}

State = Literal["off", "idle", "wake", "listening", "thinking", "speaking", "error"]
AnswerFn = Callable[..., AsyncIterator[ChatChunk | ChatDone]]


class Recognizer(Protocol):
    def transcribe(self, samples: np.ndarray) -> Any: ...


class Synthesizer(Protocol):
    def voices(self) -> list[str]: ...
    def speak(self, text: str, *, voice: str, speed: float) -> Any: ...


class Audio(Protocol):
    """Audio in and out. `mic` is "ok", "missing", "busy" or "off"."""

    mic: str

    def start_capture(self) -> None: ...
    def stop_capture(self) -> np.ndarray: ...
    async def play(self, samples: np.ndarray, rate: int) -> bool: ...
    def stop_playing(self) -> None: ...


def sentences(text: str) -> list[str]:
    """Sentence ends, never inside a number ("37.5") or a short abbreviation ("approx.")."""
    found: list[str] = []
    start = 0
    for index, char in enumerate(text):
        if char in ".!?" and (index + 1 == len(text) or text[index + 1].isspace()):
            word = text[start:index].split()[-1:] or [""]
            if char == "." and word[0].lower().rstrip(".") in ABBREVIATIONS:
                continue
            piece = text[start:index + 1].strip()
            if piece:
                found.append(piece)
            start = index + 1
    rest = text[start:].strip()
    return found + ([rest] if rest else [])


@dataclass
class Spoken:
    """One thing to say: a reply's sentence or an alert."""

    text: str
    kind: Literal["reply", "alert"]
    request_id: str | None = None


class FileAudio:
    """A file as the microphone (tests and measurements): capture returns the file's samples; playback is recorded
    and takes real time only when `realtime` is set."""

    mic = "ok"

    def __init__(self, samples: np.ndarray | None = None, *, realtime: bool = False) -> None:
        self.samples = np.zeros(0, dtype=np.float32) if samples is None else samples.astype(np.float32)
        self.realtime = realtime
        self.played: list[tuple[float, int, int]] = []           # (monotonic start, samples, rate)
        self._stop = asyncio.Event()

    @classmethod
    def from_wav(cls, path: Path, **options: Any) -> FileAudio:
        import soundfile
        data, rate = soundfile.read(str(path), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if rate != SAMPLE_RATE:
            positions = np.linspace(0, len(data) - 1, int(len(data) * SAMPLE_RATE / rate))
            data = np.interp(positions, np.arange(len(data)), data).astype(np.float32)
        return cls(data, **options)

    def start_capture(self) -> None:
        pass

    def stop_capture(self) -> np.ndarray:
        return self.samples

    async def play(self, samples: np.ndarray, rate: int) -> bool:
        self._stop.clear()
        self.played.append((time.monotonic(), len(samples), rate))
        if self.realtime:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(len(samples) / rate):
                    await self._stop.wait()
                    return False
        return True

    def stop_playing(self) -> None:
        self._stop.set()


class SoundDeviceAudio:
    """The PC's microphone and speakers through sounddevice; a missing device is reported, never raised."""

    def __init__(self) -> None:
        self._chunks: deque[np.ndarray] = deque(maxlen=int(MAX_UTTERANCE_S * SAMPLE_RATE / 1600) + 1)
        self._stream: Any = None
        self.mic = "ok"
        try:
            import sounddevice
            sounddevice.check_input_settings(samplerate=SAMPLE_RATE, channels=1, dtype="float32")
        except Exception:                                   # no device, no driver, or no PortAudio at all
            self.mic = "missing"

    def start_capture(self) -> None:
        if self.mic != "ok":
            return
        import sounddevice
        self._chunks.clear()

        def callback(data: np.ndarray, frames: int, timing: Any, status: Any) -> None:
            self._chunks.append(data[:, 0].copy())
        try:
            self._stream = sounddevice.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                                   blocksize=1600, callback=callback)
            self._stream.start()
        except Exception:
            self.mic, self._stream = "busy", None

    def stop_capture(self) -> np.ndarray:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None
        return np.concatenate(list(self._chunks)) if self._chunks else np.zeros(0, dtype=np.float32)

    async def play(self, samples: np.ndarray, rate: int) -> bool:
        import sounddevice
        try:
            sounddevice.play(samples, rate)
        except Exception:
            return False
        deadline = time.monotonic() + len(samples) / rate + 1.0
        while time.monotonic() < deadline:
            stream = sounddevice.get_stream()
            if not stream.active:
                return True
            await asyncio.sleep(0.05)
        return True

    def stop_playing(self) -> None:
        with contextlib.suppress(Exception):
            import sounddevice
            sounddevice.stop()


class VoiceBox:
    """incubator-1's voice: see the module docstring."""

    def __init__(self, *, answer: AnswerFn, publish: Callable[[VoiceStatus], Awaitable[None]], stt: Recognizer,
                 tts: Synthesizer, audio: Audio, vad: Any | None = None, store: Any | None = None,
                 logger: logging.Logger | None = None) -> None:
        self.answer = answer
        self.publish = publish
        self.stt, self.tts, self.audio, self.vad, self.store = stt, tts, audio, vad, store
        self.logger = logger or logging.getLogger("jarvis.voice")
        self.state: State = "idle"
        self.voice, self.speed = DEFAULT_VOICE, DEFAULT_SPEED
        self.push_to_talk = False
        self.transcript: str | None = None
        self.request_id: str | None = None
        self.detail = ""
        self.conversation_id: str | None = None
        self._queue: deque[Spoken] = deque()
        self._wake = asyncio.Event()
        self._speaker: asyncio.Task[None] | None = None
        self._turn: asyncio.Task[None] | None = None
        self._status_loop: asyncio.Task[None] | None = None
        self._released_at: float | None = None
        self.timings: deque[dict[str, Any]] = deque(maxlen=200)      # E6: end of speech -> first audio, and more
        self._alert_at: dict[str, float] = {}

    # --- life ---------------------------------------------------------------------------------------------------

    async def start(self) -> None:
        if self.store is not None:
            with contextlib.suppress(Exception):
                saved = await self.store.get_setting(SETTINGS_KEY)
                if isinstance(saved, dict) and saved.get("voice") in self._voices():
                    self.voice, self.speed = str(saved["voice"]), float(saved.get("speed", DEFAULT_SPEED))
        self._speaker = asyncio.create_task(self._speak_loop())
        await self._set("idle", detail="" if self.audio.mic == "ok" else "no microphone: push-to-talk cannot hear")

    async def stop(self) -> None:
        for task in (self._turn, self._speaker, self._status_loop):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(t for t in (self._turn, self._speaker, self._status_loop) if t is not None),
                             return_exceptions=True)
        self.audio.stop_playing()
        await self._set("off")

    async def serve(self, link: Any) -> None:
        """NATS: settings (request-reply), push-to-talk, and the status every STATUS_EVERY_S."""
        async def on_settings(settings: VoiceSettings, subject: str) -> VoiceSettingsResult:
            return await self.handle_settings(settings)

        async def on_ptt(ptt: PushToTalk, subject: str) -> None:
            await self.handle_ptt(ptt)
        await link.serve(jarvis_subject("incubator-1", "voice_settings"), VoiceSettings, on_settings)
        await link.subscribe(jarvis_subject("incubator-1", "ptt"), PushToTalk, on_ptt)

        async def every() -> None:
            while True:
                await asyncio.sleep(STATUS_EVERY_S)
                await self._publish()
        self._status_loop = asyncio.create_task(every())

    # --- status -------------------------------------------------------------------------------------------------

    def status(self) -> VoiceStatus:
        return VoiceStatus(state=self.state, mic=self.audio.mic if self.audio.mic in ("ok", "missing", "busy", "off")  # type: ignore[arg-type]
                           else "missing", voice=self.voice, speed=self.speed, push_to_talk=self.push_to_talk,
                           transcript=(self.transcript or None) and self.transcript[:500], request_id=self.request_id,
                           detail=self.detail[:200], sent_utc=utc_now())

    async def _publish(self) -> None:
        try:
            await self.publish(self.status())
        except Exception as error:                              # the status never breaks the voice
            log(self.logger, logging.WARNING, "voice status not sent", error=str(error)[:200])

    async def _set(self, state: State, *, detail: str | None = None) -> None:
        self.state = state
        if detail is not None:
            self.detail = detail
        await self._publish()

    # --- settings -----------------------------------------------------------------------------------------------

    def _voices(self) -> list[str]:
        try:
            return sorted(self.tts.voices())
        except Exception:
            return [self.voice]

    async def handle_settings(self, settings: VoiceSettings) -> VoiceSettingsResult:
        voices = self._voices()
        if settings.voice not in voices:
            return VoiceSettingsResult(ok=False, reason=f"unknown voice {settings.voice}", voice=self.voice,
                                       speed=self.speed, voices=voices[:100])
        self.voice, self.speed = settings.voice, settings.speed
        if self.store is not None:
            with contextlib.suppress(Exception):
                await self.store.set_setting(SETTINGS_KEY, {"voice": self.voice, "speed": self.speed})
        await self._publish()
        return VoiceSettingsResult(ok=True, voice=self.voice, speed=self.speed, voices=voices[:100])

    # --- listening ----------------------------------------------------------------------------------------------

    async def handle_ptt(self, ptt: PushToTalk) -> None:
        if ptt.pressed:
            if self.push_to_talk:
                return
            self._interrupt()
            self.push_to_talk = True
            self.audio.start_capture()
            await self._set("listening", detail="" if self.audio.mic == "ok" else f"microphone {self.audio.mic}")
            return
        if not self.push_to_talk:
            return
        self.push_to_talk = False
        self._released_at = time.monotonic()
        samples = self.audio.stop_capture()[: int(MAX_UTTERANCE_S * SAMPLE_RATE)]
        if self._turn is not None and not self._turn.done():
            self._turn.cancel()
        self._turn = asyncio.create_task(self._understand(samples, self._released_at))

    def _speech(self, samples: np.ndarray) -> np.ndarray:
        """The VAD's speech in the utterance (all of it without a VAD)."""
        if self.vad is None or len(samples) == 0:
            return samples
        with contextlib.suppress(Exception):
            self.vad.reset()
            pieces = list(self.vad.accept(samples)) + list(self.vad.flush())
            if pieces:
                return np.concatenate(pieces).astype(np.float32)
            return np.zeros(0, dtype=np.float32)
        return samples

    async def _understand(self, samples: np.ndarray, released: float) -> None:
        await self._set("thinking", detail="")
        speech = self._speech(samples)
        if len(speech) < MIN_SPEECH_S * SAMPLE_RATE:
            await self._set("idle", detail="heard nothing")
            return
        try:
            async with asyncio.timeout(STT_S):
                result = await asyncio.to_thread(self.stt.transcribe, speech)
        except Exception as error:
            await self._set("error", detail=f"speech to text failed: {type(error).__name__}")
            return
        text = str(getattr(result, "text", result) or "").strip()
        self.transcript = text
        stt_done = time.monotonic()
        if not text:
            await self._set("idle", detail="heard nothing")
            return
        self.request_id = f"v-{uuid.uuid4().hex[:12]}"
        timing: dict[str, Any] = {"request_id": self.request_id, "audio_s": round(len(speech) / SAMPLE_RATE, 2),
                                  "stt_ms": round((stt_done - released) * 1000), "first_audio_ms": None}
        self.timings.append(timing)
        await self._publish()
        said = False
        async for item in self.answer(text, request_id=self.request_id, conversation_id=self.conversation_id,
                                      source="voice"):
            if isinstance(item, ChatChunk):
                said = True
                for sentence in sentences(item.text):
                    self._enqueue(Spoken(sentence, "reply", self.request_id))
            else:
                self.conversation_id = item.conversation_id
                timing["outcome"] = item.outcome
                if not said and item.text:
                    for sentence in sentences(item.text):
                        self._enqueue(Spoken(sentence, "reply", self.request_id))
        timing["answer_ms"] = round((time.monotonic() - released) * 1000)
        if not self._queue and self.state == "thinking":
            await self._set("idle")

    # --- speaking -----------------------------------------------------------------------------------------------

    def _enqueue(self, item: Spoken, *, first: bool = False) -> None:
        if len(self._queue) >= MAX_QUEUE:
            self._queue.pop() if first else None
            if not first:
                log(self.logger, logging.WARNING, "voice queue full: a sentence was dropped", text=item.text[:80])
                return
        (self._queue.appendleft if first else self._queue.append)(item)
        self._wake.set()

    def _interrupt(self) -> None:
        self._queue.clear()
        self.audio.stop_playing()

    async def announce(self, alert: OwnerAlert) -> None:
        """Speak an alert at once; DANGER stops anything being said."""
        self._alert_at[alert.alert_id] = time.monotonic()
        while len(self._alert_at) > 64:
            self._alert_at.pop(next(iter(self._alert_at)))
        if alert.level == "DANGER":
            self._interrupt()
        for sentence in reversed(sentences(alert.text)):
            self._enqueue(Spoken(sentence, "alert", alert.alert_id), first=True)

    async def _speak_loop(self) -> None:
        while True:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            item = self._queue.popleft()
            try:
                async with asyncio.timeout(TTS_S):
                    speech = await asyncio.to_thread(self.tts.speak, item.text, voice=self.voice, speed=self.speed)
            except Exception as error:
                log(self.logger, logging.WARNING, "a sentence could not be spoken", error=repr(error)[:200])
                continue
            if self.state != "speaking":
                await self._set("speaking")
            started = time.monotonic()
            self._first_audio(item, started)
            await self.audio.play(np.asarray(speech.samples, dtype=np.float32), int(speech.sample_rate))
            if not self._queue and not self.push_to_talk and (self._turn is None or self._turn.done()):
                await self._set("idle")

    def _first_audio(self, item: Spoken, started: float) -> None:
        if item.kind == "alert" and item.request_id in self._alert_at:
            self.timings.append({"alert_id": item.request_id,
                                 "alert_to_audio_ms": round((started - self._alert_at.pop(item.request_id)) * 1000)})
            return
        for timing in reversed(self.timings):
            if timing.get("request_id") == item.request_id:
                if timing.get("first_audio_ms") is None and self._released_at is not None:
                    timing["first_audio_ms"] = round((started - self._released_at) * 1000)
                return
