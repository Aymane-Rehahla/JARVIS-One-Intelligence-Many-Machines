"""This node's own program: one of the four that together are JARVIS.

Usage (from the project folder; the launcher's supervisor normally starts it):
    python nodes/<node>/runtime.py [--nats URL] [--heartbeat-s S] [--latency-ticks N] [--loss P] [--probe]
                                   [--connect-attempts N] [--ports FILE] [--data-dir DIR] [--no-mcp] [--no-llm]
                                   [--logs DIR | --no-logs] [--echo]

The node reads its own `node.toml` (next to this file), connects to NATS (the URL from the ports file, by default
launcher/ports.toml, unless --nats is given; its MCP port comes from the same file) and runs until it is stopped (Ctrl+C, Ctrl+Break, or the process is ended). Logs go to
logs/<node>.jsonl. The node serves its six read-only tools over localhost MCP unless --no-mcp is given. --probe lets
tests and diagnostics make this node ask a peer (`jarvis.<node>.probe`).
The node talks to its own llama.cpp server (the port from the ports file) through the mentor's failure switches
(6C): incubator-1 runs the agent, the others their narrow minds. With --no-llm it has no engine at all; without its
server running, every language-model call fails cleanly and the node answers from its data (fallbacks).
Exit codes: 0 stopped, 2 bad configuration or NATS unreachable.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
import tomllib
from pathlib import Path
from collections.abc import AsyncIterator
from typing import Any

NODE_DIR = Path(__file__).resolve().parent
ROOT = NODE_DIR.parents[1]
if str(NODE_DIR) not in sys.path:
    sys.path.insert(0, str(NODE_DIR))

from comms.jsonlog import log, program_logger  # noqa: E402 - the node folder is on the path first
from comms.nats_link import CONNECT_ATTEMPTS, NatsLink  # noqa: E402
from comms.peer_network import RadioRules  # noqa: E402
from contracts import ContractError, load_node_config  # noqa: E402
from llm.engines import EngineClient, server_spec  # noqa: E402
from llm.failure_injection import FailureSwitches, FaultyEngine  # noqa: E402
from llm.jarvis_speaker import load_persona  # noqa: E402
from node import HEARTBEAT_S, Node  # noqa: E402
from nats.errors import NoServersError  # noqa: E402


def nats_url(ports_file: Path = ROOT / "launcher" / "ports.toml") -> str:
    """The NATS URL from launcher/ports.toml (the single source of truth for ports)."""
    ports = tomllib.loads(ports_file.read_text(encoding="utf-8"))
    return f"nats://{ports['host']}:{ports['nats']}"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nats", help="NATS URL (default: from the ports file)")
    parser.add_argument("--ports", type=Path, default=ROOT / "launcher" / "ports.toml",
                        help="ports file for the NATS URL and this node's MCP port (default: launcher/ports.toml; "
                             "the supervisor passes its own)")
    parser.add_argument("--heartbeat-s", type=float, default=HEARTBEAT_S, help="heartbeat period (default 10 s)")
    parser.add_argument("--latency-ticks", type=int, default=1, help="ZigBee latency in farm ticks (default 1)")
    parser.add_argument("--loss", type=float, default=0.0, help="ZigBee loss probability (default 0)")
    parser.add_argument("--probe", action="store_true", help="answer jarvis.<node>.probe (tests, diagnostics)")
    parser.add_argument("--connect-attempts", type=int, default=CONNECT_ATTEMPTS,
                        help=f"tries to reach NATS before giving up (default {CONNECT_ATTEMPTS})")
    parser.add_argument("--no-mcp", action="store_true", help="do not serve the six read-only tools over MCP")
    parser.add_argument("--no-llm", action="store_true", help="no language model at all (no engine, no agent)")
    data_root = os.environ.get("JARVIS_DATA_ROOT")
    default_data_dir = Path(data_root) / NODE_DIR.name if data_root else NODE_DIR / "data"
    parser.add_argument("--data-dir", type=Path, default=default_data_dir, help="node data folder (default: data/)")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--logs", type=Path, default=ROOT / "logs", help="log folder (default: logs/)")
    where.add_argument("--no-logs", action="store_true", help="no log file")
    parser.add_argument("--echo", action="store_true", help="also print the log to stderr")
    parser.add_argument("--no-voice", action="store_true", help="do not start the voice box (voice = true nodes)")
    return parser.parse_args(argv)


async def start_voice(node: Node, link: NatsLink, logger: logging.Logger) -> Any:
    """incubator-1's voice box (P4 section 2): the models from models/voice, the PC's microphone and speakers, the
    agent's reply as its answer, every owner alert spoken. The models load in the background; a missing microphone
    is a status, never a crash."""
    from contracts import ChatDone, ReplyTimings, jarvis_subject
    from voice.pipeline import SoundDeviceAudio, VoiceBox
    from voice.stt import SpeechToText
    from voice.tts import TextToSpeech
    from voice.vad import Vad
    models = ROOT / "models" / "voice"
    stt = SpeechToText(models / "parakeet-tdt-0.6b-v2-int8")
    tts = TextToSpeech(models / "kokoro-multi-lang-v1_0")
    vad = Vad(models / "silero-vad" / "silero_vad.onnx")
    agent = node.agent

    async def no_agent(text: str, **options: Any) -> AsyncIterator[Any]:
        yield ChatDone(request_id=options["request_id"], conversation_id=options.get("conversation_id") or "voice",
                       text="My language model is not running, sir.", chunks=0, outcome="error",
                       reason="the agent is not running", numbers_checked=0, timings=ReplyTimings(total_ms=0))

    async def publish(status: Any) -> None:
        await link.publish(jarvis_subject(node.id, "voice"), status)
    audio: Any = SoundDeviceAudio()
    recorded = os.environ.get("JARVIS_VOICE_WAV")            # rehearsals without a person: a recorded question
    if recorded:
        from voice.pipeline import FileAudio
        speakers = audio
        audio = FileAudio.from_wav(Path(recorded))
        audio.play, audio.stop_playing = speakers.play, speakers.stop_playing
        log(logger, logging.WARNING, "the microphone is replaced by a recorded file", file=Path(recorded).name)
    box = VoiceBox(answer=node.owner_turn if agent is not None else no_agent, publish=publish, stt=stt, tts=tts,
                   audio=audio, vad=vad, store=node.store, logger=logger)
    if agent is not None:
        agent.add_alert_listener(box.announce)
    await box.start()
    if recorded:
        box.label = f"test: the microphone is the recorded file {Path(recorded).name}"
    await box.serve(link)
    loading = asyncio.get_running_loop().run_in_executor(None, lambda: (stt.load(), tts.load()))

    def loaded(future: asyncio.Future[Any]) -> None:              # a failed load is logged, never lost
        if not future.cancelled() and future.exception() is not None:
            log(logger, logging.ERROR, "the voice models did not load", error=repr(future.exception())[:300])
    loading.add_done_callback(loaded)
    box.loading = loading
    log(logger, logging.INFO, "voice box started", mic=box.audio.mic)
    return box


async def run(args: argparse.Namespace) -> int:
    config = load_node_config(NODE_DIR / "node.toml")
    ports = tomllib.loads(args.ports.read_text(encoding="utf-8"))
    mcp_port = int(ports["mcp"][config.id])
    logger = program_logger(config.id, None if args.no_logs else args.logs, echo=args.echo)
    link = NatsLink(config.id, args.nats or nats_url(args.ports), logger=logger)
    try:
        await link.connect(attempts=max(1, args.connect_attempts))
    except NoServersError:
        return 2
    switches = FailureSwitches(config.id, logger=logger)
    engine = None
    if not args.no_llm:
        spec = server_spec(config.id, args.ports)
        engine = FaultyEngine(EngineClient(spec.url, slot=0, model=spec.alias), switches)
    node = Node(config, link, logger=logger, heartbeat_s=args.heartbeat_s, probe=args.probe,
                rules=RadioRules(latency_ticks=args.latency_ticks, loss=args.loss), data_dir=args.data_dir,
                engine=engine, switches=switches, persona=load_persona() if config.speaks and engine else None,
                mcp_enabled=not args.no_mcp, mcp_port=mcp_port)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            with contextlib.suppress(NotImplementedError, ValueError):
                signal.signal(getattr(signal, name), lambda *_: loop.call_soon_threadsafe(stop.set))
    await node.start()
    box = None
    if config.voice and not args.no_voice:
        try:
            box = await start_voice(node, link, logger)
        except Exception as error:                            # the node runs on without a voice
            log(logger, logging.ERROR, "voice box not started", error=repr(error)[:300])
    try:
        await stop.wait()
    finally:
        log(logger, logging.INFO, "stopping", node=config.id)
        if box is not None:
            await box.stop()
        await node.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    args = parse_args(argv)
    try:
        return asyncio.run(run(args))
    except ContractError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
