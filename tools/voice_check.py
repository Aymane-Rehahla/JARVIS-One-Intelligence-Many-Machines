"""Check incubator-1's voice box alone (P4 section 2): push-to-talk, speech to text, a fixed verified reply spoken
sentence by sentence, with each stage's timing. No NATS, no agent, no language model.

    .venv\\Scripts\\python tools\\voice_check.py --wav FILE      the WAV file stands in for the microphone
    .venv\\Scripts\\python tools\\voice_check.py --seconds 4     the microphone: speak for 4 seconds
Add --play to hear the reply on the speakers (otherwise it is synthesised and timed, not played).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nodes" / "incubator-1"))

from contracts import ChatChunk, ChatDone, PushToTalk, ReplyTimings, VoiceStatus, utc_now  # noqa: E402
from voice.pipeline import FileAudio, SoundDeviceAudio, VoiceBox  # noqa: E402
from voice.stt import SpeechToText  # noqa: E402
from voice.tts import TextToSpeech  # noqa: E402
from voice.vad import Vad  # noqa: E402

MODELS = ROOT / "models" / "voice"
REPLY = ("The supply fan has stopped, sir. ", "Airflow is 0 cubic metres an hour. ", "That is as of farm time 06:08.")


async def fixed_answer(text: str, **options: Any) -> AsyncIterator[ChatChunk | ChatDone]:
    """A fixed, verified reply (the agent's place), in three chunks."""
    for seq, piece in enumerate(REPLY):
        yield ChatChunk(request_id=options["request_id"], seq=seq, text=piece)
    yield ChatDone(request_id=options["request_id"], conversation_id="voice-check", text="".join(REPLY), chunks=3,
                   outcome="answered", numbers_checked=2, farm_time="06:08", label="live: simulated brains",
                   timings=ReplyTimings(total_ms=1))


def models() -> tuple[SpeechToText, TextToSpeech, Vad]:
    started = time.perf_counter()
    stt = SpeechToText(MODELS / "parakeet-tdt-0.6b-v2-int8")
    tts = TextToSpeech(MODELS / "kokoro-multi-lang-v1_0")
    stt.load()
    tts.load()
    print(f"models loaded in {time.perf_counter() - started:.1f} s", flush=True)
    return stt, tts, Vad(MODELS / "silero-vad" / "silero_vad.onnx")


async def run(args: argparse.Namespace) -> int:
    stt, tts, vad = models()
    audio: Any = FileAudio.from_wav(args.wav) if args.wav else SoundDeviceAudio()
    if args.play and args.wav:
        player = SoundDeviceAudio()
        audio.play, audio.stop_playing = player.play, player.stop_playing
    statuses: list[VoiceStatus] = []

    async def publish(status: VoiceStatus) -> None:
        statuses.append(status)
        print(f"  [{status.state}] mic={status.mic} {status.detail}", flush=True)
    box = VoiceBox(answer=fixed_answer, publish=publish, stt=stt, tts=tts, audio=audio, vad=vad)
    await box.start()
    if audio.mic != "ok":
        print(f"no usable microphone ({audio.mic}); use --wav FILE")
        await box.stop()
        return 1
    await box.handle_ptt(PushToTalk(pressed=True, sent_utc=utc_now()))
    if not args.wav:
        print(f"speak now for {args.seconds:g} s ...", flush=True)
        await asyncio.sleep(args.seconds)
    await box.handle_ptt(PushToTalk(pressed=False, sent_utc=utc_now()))
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not (statuses[-1].state == "idle" and box.timings):
        await asyncio.sleep(0.05)
    await box.stop()
    print(f"heard: {box.transcript!r}")
    print("timings:", json.dumps(list(box.timings)))
    return 0 if box.transcript else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wav", type=Path, help="a WAV file instead of the microphone")
    parser.add_argument("--seconds", type=float, default=4.0, help="how long the microphone listens")
    parser.add_argument("--play", action="store_true", help="play the reply on the speakers")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
