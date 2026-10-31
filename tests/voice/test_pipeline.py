"""The voice box (voice/pipeline.py) with fake models and a file source: push-to-talk to spoken sentences, settings,
alerts (DANGER interrupts), nothing heard, a missing microphone, and the timings E6 reads. No model is loaded."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from contracts import ChatChunk, ChatDone, OwnerAlert, PushToTalk, ReplyTimings, VoiceSettings, VoiceStatus, utc_now
from voice.pipeline import FileAudio, VoiceBox, sentences


@dataclass
class Said:
    samples: np.ndarray
    sample_rate: int = 24_000


class FakeTts:
    def __init__(self) -> None:
        self.spoken: list[tuple[str, str, float]] = []

    def voices(self) -> list[str]:
        return ["af_heart", "bm_george", "bm_lewis"]

    def speak(self, text: str, *, voice: str, speed: float) -> Said:
        self.spoken.append((text, voice, speed))
        return Said(np.zeros(2400, dtype=np.float32))


class FakeStt:
    def __init__(self, text: str = "what is wrong with the hvac") -> None:
        self.text = text
        self.heard: list[int] = []

    def transcribe(self, samples: np.ndarray) -> Any:
        self.heard.append(len(samples))
        return type("T", (), {"text": self.text})()


class Store:
    def __init__(self) -> None:
        self.saved: dict[str, Any] = {}

    async def get_setting(self, key: str) -> Any:
        return self.saved.get(key)

    async def set_setting(self, key: str, value: Any) -> None:
        self.saved[key] = value


def reply(*pieces: str, outcome: str = "answered", text: str | None = None, delay: float = 0.0) -> Any:
    calls: list[dict[str, Any]] = []

    async def answer(question: str, **options: Any) -> AsyncIterator[ChatChunk | ChatDone]:
        calls.append({"question": question, **options})
        for seq, piece in enumerate(pieces):
            await asyncio.sleep(delay)
            yield ChatChunk(request_id=options["request_id"], seq=seq, text=piece)
        yield ChatDone(request_id=options["request_id"], conversation_id="conv-v", text=text or "".join(pieces),
                       chunks=len(pieces), outcome=outcome, reason="" if outcome == "answered" else "refused here",  # type: ignore[arg-type]
                       numbers_checked=0, farm_time="06:08" if outcome == "answered" else None,
                       label="live: simulated brains" if outcome == "answered" else None,
                       timings=ReplyTimings(total_ms=1))
    answer.calls = calls  # type: ignore[attr-defined]
    return answer


async def box(answer: Any, *, audio: FileAudio | None = None, stt: FakeStt | None = None,
              store: Store | None = None) -> tuple[VoiceBox, list[VoiceStatus], FakeTts]:
    statuses: list[VoiceStatus] = []

    async def publish(status: VoiceStatus) -> None:
        statuses.append(status)
    tts = FakeTts()
    made = VoiceBox(answer=answer, publish=publish, stt=stt or FakeStt(), tts=tts,
                    audio=audio or FileAudio(np.full(16_000, 0.1, dtype=np.float32)), store=store)
    await made.start()
    return made, statuses, tts


async def until(check: Any, timeout_s: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "not in time"
        await asyncio.sleep(0.01)


def ptt(pressed: bool) -> PushToTalk:
    return PushToTalk(pressed=pressed, sent_utc=utc_now())


def test_sentences_keep_numbers_and_abbreviations_whole() -> None:
    assert sentences("The air is 24.1 °C, sir. Airflow is approx. zero! Check it?") == [
        "The air is 24.1 °C, sir.", "Airflow is approx. zero!", "Check it?"]


@pytest.mark.asyncio
async def test_push_to_talk_to_spoken_sentences_in_order_with_timings() -> None:
    answer = reply("The supply fan has failed, sir. ", "Farm time 06:08.")
    made, statuses, tts = await box(answer)
    await made.handle_ptt(ptt(True))
    await made.handle_ptt(ptt(False))
    await until(lambda: len(tts.spoken) == 2 and made.state == "idle")
    assert [s[0] for s in tts.spoken] == ["The supply fan has failed, sir.", "Farm time 06:08."]
    assert answer.calls[0]["question"] == "what is wrong with the hvac" and answer.calls[0]["source"] == "voice"
    states = [s.state for s in statuses]
    assert states.index("listening") < states.index("thinking") < states.index("speaking") < len(states) - 1
    assert statuses[-1].state == "idle" and made.conversation_id == "conv-v"
    timing = made.timings[-1]
    assert timing["first_audio_ms"] is not None and timing["outcome"] == "answered"
    await made.stop()


@pytest.mark.asyncio
async def test_a_reply_without_chunks_speaks_its_done_text() -> None:
    made, _, tts = await box(reply(outcome="refused", text="JARVIS talks; the machines act. Sir."))
    await made.handle_ptt(ptt(True))
    await made.handle_ptt(ptt(False))
    await until(lambda: len(tts.spoken) == 2)
    assert tts.spoken[0][0] == "JARVIS talks; the machines act."
    await made.stop()


@pytest.mark.asyncio
async def test_nothing_heard_never_asks_the_agent() -> None:
    answer = reply("x.")
    made, statuses, _ = await box(answer, audio=FileAudio(np.zeros(800, dtype=np.float32)))
    await made.handle_ptt(ptt(True))
    await made.handle_ptt(ptt(False))
    await until(lambda: made.state == "idle" and made.detail == "heard nothing")
    assert answer.calls == []
    empty = FakeStt("")
    made2, _, _ = await box(answer, stt=empty)
    await made2.handle_ptt(ptt(True))
    await made2.handle_ptt(ptt(False))
    await until(lambda: made2.detail == "heard nothing")
    assert empty.heard and answer.calls == []
    await made.stop()
    await made2.stop()


@pytest.mark.asyncio
async def test_settings_are_checked_saved_and_used_from_the_next_sentence() -> None:
    store = Store()
    made, _, tts = await box(reply("One. ", "Two."), store=store)
    refused = await made.handle_settings(VoiceSettings(voice="xx_nobody", speed=1.0))
    assert not refused.ok and refused.voice == "bm_george" and "bm_lewis" in refused.voices
    done = await made.handle_settings(VoiceSettings(voice="bm_lewis", speed=1.4))
    assert done.ok and store.saved["voice"] == {"voice": "bm_lewis", "speed": 1.4}
    await made.handle_ptt(ptt(True))
    await made.handle_ptt(ptt(False))
    await until(lambda: len(tts.spoken) == 2)
    assert all(s[1:] == ("bm_lewis", 1.4) for s in tts.spoken)
    again, _, _ = await box(reply(), store=store)                   # a restarted box keeps the saved choice
    assert (again.voice, again.speed) == ("bm_lewis", 1.4)
    await made.stop()
    await again.stop()


@pytest.mark.asyncio
async def test_a_danger_alert_interrupts_and_goes_first() -> None:
    audio = FileAudio(np.full(16_000, 0.1, dtype=np.float32), realtime=True)
    made, _, tts = await box(reply("First sentence. ", "Second sentence. ", "Third sentence."), audio=audio)
    await made.handle_ptt(ptt(True))
    await made.handle_ptt(ptt(False))
    await until(lambda: len(tts.spoken) >= 1)
    alert = OwnerAlert(alert_id="a-1", node="hvac", level="DANGER", incident_id="hvac-1",
                       text="Sir, danger at the HVAC. Farm time 06:09.", farm_time="06:09", feed_id="0000000000a1",
                       label="live: simulated brains", reworded=False, sent_utc=utc_now())
    await made.announce(alert)
    await until(lambda: any(s[0] == "Farm time 06:09." for s in tts.spoken))
    said = [s[0] for s in tts.spoken]
    assert said[1:3] == ["Sir, danger at the HVAC.", "Farm time 06:09."]       # right after the interrupted one
    assert "Third sentence." not in said                                         # the rest of the reply was dropped
    assert any("alert_to_audio_ms" in t for t in made.timings)
    await made.stop()


@pytest.mark.asyncio
async def test_a_missing_microphone_is_a_status_not_a_crash() -> None:
    audio = FileAudio()
    audio.mic = "missing"
    made, statuses, _ = await box(reply("x."), audio=audio)
    assert statuses[-1].mic == "missing" and "no microphone" in statuses[-1].detail
    await made.handle_ptt(ptt(True))
    await made.handle_ptt(ptt(False))
    await until(lambda: made.state == "idle")
    await made.stop()


def alert(level: str, alert_id: str = "a-1") -> OwnerAlert:
    return OwnerAlert(alert_id=alert_id, node="hvac", level=level, incident_id="hvac-equipment-0001",  # type: ignore[arg-type]
                      text="Sir, the HVAC needs you. Farm time 06:09.", farm_time="06:09", feed_id="0000000000a1",
                      label="live: simulated brains", reworded=False, sent_utc=utc_now())


@pytest.mark.asyncio
async def test_pressing_again_stops_the_reply_but_keeps_waiting_alerts() -> None:
    """Review (31 Oct): a second press cleared the queue but the old turn went on adding sentences, so JARVIS kept
    talking into the microphone; and the clear dropped an ESCALATE alert waiting behind the reply."""
    answer = reply("One. ", "Two. ", "Three. ", "Four. ", delay=0.2)
    made, _, tts = await box(answer)
    await made.handle_ptt(ptt(True))
    await made.handle_ptt(ptt(False))
    await until(lambda: len(tts.spoken) >= 1)
    await made.announce(alert("ESCALATE_TO_USER"))
    await made.handle_ptt(ptt(True))                                  # the owner asks again
    spoken = len(tts.spoken)
    await asyncio.sleep(0.6)
    assert len(tts.spoken) == spoken, "nothing is said while the owner talks"
    assert made._turn is not None and made._turn.done()
    await made.handle_ptt(ptt(False))
    await until(lambda: any("HVAC needs you" in s[0] for s in tts.spoken))
    await made.stop()


@pytest.mark.asyncio
async def test_a_lost_release_ends_listening_after_the_longest_utterance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review (31 Oct): with the release lost (the tab closed), the box stayed "listening" for ever."""
    import voice.pipeline as pipeline
    monkeypatch.setattr(pipeline, "MAX_UTTERANCE_S", 0.2)
    made, _, _ = await box(reply("Fine, sir. "))
    await made.handle_ptt(ptt(True))
    await until(lambda: not made.push_to_talk and made.state != "listening", timeout_s=3.0)
    await made.stop()


@pytest.mark.asyncio
async def test_a_recorded_microphone_stays_labelled() -> None:
    """Review (31 Oct): the recorded-file label was the status detail, which the first "thinking" erased."""
    made, statuses, _ = await box(reply("Fine, sir. "))
    made.label = "test: the microphone is the recorded file q.wav"
    await made.handle_ptt(ptt(True))
    await made.handle_ptt(ptt(False))
    await until(lambda: made.state == "idle" and len(statuses) > 4)
    assert all("recorded file" in s.detail for s in statuses[-3:])
    await made.stop()
