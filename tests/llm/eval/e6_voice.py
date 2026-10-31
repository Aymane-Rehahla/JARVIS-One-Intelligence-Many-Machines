"""E6, latency, voice part (P4 section 2): from the end of speech to the first audio out, and from an alert to its
first audio.

In: the real voice models (Silero VAD, Parakeet, Kokoro; models/voice), the voice box (voice/pipeline.py) with a file
source as the microphone. Questions are synthesised once with Kokoro (bm_george), resampled to 16 kHz and fed as the
recorded utterance; "end of speech" is the button's release. Three measurements:
- **fixed reply** (RUNS runs, the five questions in turn): the voice box with tools/voice_check.py's fixed, verified
  reply in place of the agent: STT + the first sentence's synthesis;
- **alert** (ALERTS runs): `announce()` of an ESCALATE alert to its first audio;
- **real agent** (`--real N`, the live rig: the real 2B and 0.6B models and the four nodes; N runs, half simple
  questions and half needing a peer): the full spoken path.
Targets (plan section 8): voice <= 2.5 s p50 simple, <= 4 s with peer questions; alert <= 3 s. The text numbers in
E6.json are kept; these go under keys starting with "voice".
Out: `logs/eval/E6.json`.

Run: `.venv\\Scripts\\python tests\\llm\\eval\\e6_voice.py [--real 10]`
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
for _extra in (ROOT / "tools", ROOT / "nodes" / "incubator-1", Path(__file__).resolve().parent):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from contracts import OwnerAlert, PushToTalk, VoiceStatus, utc_now  # noqa: E402
from e6_text import stats  # noqa: E402
from live_rig import rig_models  # noqa: E402
from eval_results import OUT, EvalResult, git_commit, write_result  # noqa: E402
from voice.pipeline import SAMPLE_RATE, FileAudio, VoiceBox  # noqa: E402
from voice_check import fixed_answer, models  # noqa: E402

RUNS = 50
ALERTS = 10
SIMPLE = ("How is incubator one?", "What is the temperature in incubator one?", "Is incubator one all right?")
PEERS = ("What's wrong with the HVAC?", "Is the animal protection unit fine?")
QUESTIONS = SIMPLE + PEERS


def to_16k(samples: np.ndarray, rate: int) -> np.ndarray:
    positions = np.linspace(0, len(samples) - 1, int(len(samples) * SAMPLE_RATE / rate))
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


async def one(box: VoiceBox, audio: FileAudio, samples: np.ndarray) -> dict[str, Any]:
    audio.samples = samples
    before = len(box.timings)
    await box.handle_ptt(PushToTalk(pressed=True, sent_utc=utc_now()))
    await box.handle_ptt(PushToTalk(pressed=False, sent_utc=utc_now()))
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        found = list(box.timings)[before:]
        if found and found[-1].get("first_audio_ms") is not None and box.state == "idle":
            return dict(found[-1]) | {"transcript": box.transcript}
        await asyncio.sleep(0.02)
    return {"first_audio_ms": None, "transcript": box.transcript}


async def run(real: int) -> dict[str, Any]:
    stt, tts, vad = models()
    spoken = {q: to_16k(np.asarray(s.samples, dtype=np.float32), int(s.sample_rate))
              for q in QUESTIONS for s in [tts.speak(q, voice="bm_george", speed=1.0)]}
    statuses: list[VoiceStatus] = []

    async def publish(status: VoiceStatus) -> None:
        statuses.append(status)
    audio = FileAudio()
    box = VoiceBox(answer=fixed_answer, publish=publish, stt=stt, tts=tts, audio=audio, vad=vad)
    await box.start()
    fixed = []
    for index in range(RUNS):
        question = QUESTIONS[index % len(QUESTIONS)]
        fixed.append(await one(box, audio, spoken[question]) | {"question": question})
    alerts = []
    for index in range(ALERTS):
        alert = OwnerAlert(alert_id=f"a-e6-{index}", node="hvac", level="ESCALATE_TO_USER", incident_id="hvac-1",
                           text="Sir, the HVAC needs your attention: supply fan fault. Farm time 06:08.",
                           farm_time="06:08", feed_id="0000000000a1", label="live: simulated brains", reworded=False,
                           sent_utc=utc_now())
        await box.announce(alert)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not any(t.get("alert_id") == alert.alert_id for t in box.timings):
            await asyncio.sleep(0.02)
        alerts += [t for t in box.timings if t.get("alert_id") == alert.alert_id]
        while box.state != "idle" and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
    await box.stop()
    result: dict[str, Any] = {"voice_fixed_reply": fixed, "voice_alerts": alerts}
    if real:
        from live_rig import LiveRig
        rows = []
        async with LiveRig() as rig:
            agent = rig.nodes["incubator-1"].agent
            assert agent is not None
            audio_real = FileAudio()
            live = VoiceBox(answer=rig.nodes["incubator-1"].owner_turn, publish=publish, stt=stt, tts=tts, audio=audio_real, vad=vad)
            await live.start()
            for index in range(real):
                question = (SIMPLE if index % 2 == 0 else PEERS)[(index // 2) % (len(SIMPLE) if index % 2 == 0 else len(PEERS))]
                rows.append(await one(live, audio_real, spoken[question]) | {
                    "question": question, "kind": "simple" if index % 2 == 0 else "peers"})
                print(f"real {index}: {rows[-1].get('first_audio_ms')} ms {rows[-1].get('outcome')}", flush=True)
            await live.stop()
        result["voice_real_agent"] = rows
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--real", type=int, default=0, help="runs with the real agent on the live rig")
    args = parser.parse_args(argv)
    started_utc, started = utc_now(), time.monotonic()
    found = asyncio.run(run(args.real))
    previous = OUT / "E6.json"
    old = json.loads(previous.read_text(encoding="utf-8")) if previous.is_file() else None
    measured: dict[str, Any] = {k: v for k, v in (old or {}).get("measured", {}).items() if not k.startswith("voice")}

    def ms(rows: list[dict[str, Any]], key: str) -> list[float]:
        return [float(r[key]) for r in rows if r.get(key) is not None]
    voice: dict[str, Any] = {
        "fixed_reply_first_audio_ms": stats(ms(found["voice_fixed_reply"], "first_audio_ms")),
        "fixed_reply_stt_ms": stats(ms(found["voice_fixed_reply"], "stt_ms")),
        "alert_to_audio_ms": stats(ms(found["voice_alerts"], "alert_to_audio_ms"))}
    if "voice_real_agent" in found:
        rows = found["voice_real_agent"]
        voice["real_agent_first_audio_ms"] = {
            kind: stats(ms([r for r in rows if r["kind"] == kind], "first_audio_ms")) for kind in ("simple", "peers")}
    measured["voice"] = voice
    measured["voice_rows"] = json.loads(json.dumps({k: v for k, v in found.items()}, default=str))
    measured["voice_commit"] = git_commit()
    measured.setdefault("text_commit", (old or {}).get("commit"))
    # every target judged (review, 31 Oct): the text part's verdict, and the voice targets on the real agent
    text_pass = (old or {}).get("measured", {}).get("text_passed", (old or {}).get("passed"))
    measured["text_passed"] = text_pass
    real = voice.get("real_agent_first_audio_ms") or {}
    simple = (real.get("simple") or {}).get("p50")
    peers = (real.get("peers") or {}).get("p50")
    alert_p95 = (voice["alert_to_audio_ms"] or {}).get("p95")
    voice_pass = None if simple is None or peers is None or alert_p95 is None else (
        simple <= 2500.0 and peers <= 4000.0 and alert_p95 <= 3000.0)
    measured["voice_passed"] = voice_pass
    passed = None if text_pass is None or voice_pass is None else bool(text_pass and voice_pass)
    write_result(EvalResult(
        id="E6", title="Latency", target="first chunk <= 1.5 s p50 (text); voice <= 2.5 s p50 simple, <= 4 s with "
        "peers; alert <= 3 s (p95)", passed=passed, measured=measured,
        command=(old or {}).get("command", "") + " ; .venv\\Scripts\\python tests\\llm\\eval\\e6_voice.py"
        + (f" --real {args.real}" if args.real else ""), commit=git_commit(), seeds=[7],
        models=rig_models("voice/kokoro-multi-lang-v1_0/model.onnx", "voice/parakeet-tdt-0.6b-v2-int8/encoder.int8.onnx",
                          "voice/silero-vad/silero_vad.onnx"),
        started_utc=(old or {}).get("started_utc", started_utc),
        duration_s=round((old or {}).get("duration_s", 0.0) + time.monotonic() - started, 1),
        notes=((old or {}).get("notes", "") + " Voice: Kokoro-synthesised questions fed as files; end of speech = "
               "the button's release; first audio = the first sentence ready to play. The alert figure is from the "
               "voice box's announce() to audio (not farm -> watcher -> radio -> desk). The live rig runs every node, "
               "the farm and the gateway in one process, unlike the launcher.").strip()))
    print(json.dumps(measured["voice"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
