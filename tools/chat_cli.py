"""A small command-line chat with JARVIS through the gateway's `/ws/chat` (6C, P2 section 4), for tests and the owner.

    .venv\\Scripts\\python tools\\chat_cli.py "What's wrong with the HVAC?"      one question, then exit
    .venv\\Scripts\\python tools\\chat_cli.py                                   ask line after line (empty line ends)

Each reply streams as it arrives; then its outcome, the farm time of its data and its label. An `AckOffer` is shown
but never confirmed here: confirming is the owner's click on the website. Owner alerts are printed as they come.
Exit code 0 when every question got its ChatDone, 1 when one ended in a ChatError, 2 when the gateway is unreachable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import websockets

ROOT = Path(__file__).resolve().parents[1]
ANSWER_S = 35.0                                 # longer than the gateway's own 30 s chat deadline: its ChatError wins


@dataclass
class Answer:
    """What one question got back: the accepted request id, the streamed text, the ChatDone (or the ChatError)."""

    request_id: str | None = None
    chunks: list[str] = field(default_factory=list[str])
    done: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    alerts: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])


def default_url(ports: Path = ROOT / "launcher" / "ports.toml") -> str:
    config = tomllib.loads(ports.read_text(encoding="utf-8"))
    return f"ws://{config['host']}:{config['gateway']}/ws/chat"


async def ask(socket: Any, text: str, conversation_id: str | None = None, *, answer_s: float = ANSWER_S,
              show: Callable[[str], None] | None = None) -> Answer:
    """Send one ChatSend and read until its ChatDone or a ChatError for it (each wait bounded by `answer_s`)."""
    client_msg_id = f"cli-{secrets.token_hex(4)}"
    await socket.send(json.dumps({"type": "chat_send", "client_msg_id": client_msg_id,
                                  "conversation_id": conversation_id, "text": text}))
    answer = Answer()
    while answer.done is None and answer.error is None:
        message = json.loads(await asyncio.wait_for(socket.recv(), timeout=answer_s))
        kind = message.get("type")
        if kind == "chat_accepted" and message.get("client_msg_id") == client_msg_id:
            answer.request_id = message["request_id"]
        elif kind == "chat_chunk" and message.get("request_id") == answer.request_id:
            answer.chunks.append(message["text"])
            if show:
                show(message["text"])
        elif kind == "chat_done" and message.get("request_id") == answer.request_id:
            answer.done = message
        elif kind == "chat_error" and (message.get("client_msg_id") == client_msg_id
                                       or (answer.request_id and message.get("request_id") == answer.request_id)):
            answer.error = message
        elif kind == "owner_alert":
            answer.alerts.append(message)
    return answer


def describe(answer: Answer) -> str:
    if answer.error is not None:
        return f"[error] {answer.error.get('reason')}"
    done = answer.done or {}
    lines = [f"[{done.get('outcome')}] farm time {done.get('farm_time')} ({done.get('label')})"
             + (f", reason: {done['reason']}" if done.get("reason") else "")]
    for citation in done.get("citations") or []:
        lines.append(f"  source: {citation.get('file')} {citation.get('section', '')}".rstrip())
    if done.get("confirm"):
        offer = done["confirm"]
        lines.append(f"  confirm offered (the website's button): {offer.get('summary')}")
    for alert in answer.alerts:
        lines.append(f"  ALERT: {alert.get('text')}")
    return "\n".join(lines)


async def amain(args: argparse.Namespace) -> int:
    try:
        socket = await asyncio.wait_for(websockets.connect(args.url), timeout=5.0)
    except (OSError, asyncio.TimeoutError, websockets.InvalidHandshake) as error:
        print(f"the gateway is unreachable at {args.url}: {error}", file=sys.stderr)
        return 2
    conversation_id = args.conversation
    status = 0
    try:
        questions = [args.question] if args.question else None
        while True:
            if questions is not None:
                if not questions:
                    break
                text = questions.pop(0)
            else:
                text = await asyncio.to_thread(input, "you> ")
                if not text.strip():
                    break
            print("jarvis> ", end="", flush=True)
            answer = await ask(socket, text, conversation_id, show=lambda piece: print(piece, end="", flush=True))
            print()
            print(describe(answer))
            if answer.error is not None:
                status = 1
            elif answer.done is not None:
                conversation_id = answer.done.get("conversation_id") or conversation_id
    except (asyncio.TimeoutError, websockets.ConnectionClosed) as error:
        print(f"the gateway stopped answering: {type(error).__name__}", file=sys.stderr)
        status = 1
    finally:
        await socket.close()
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="?", help="one question (default: ask line after line)")
    parser.add_argument("--url", help="the gateway's /ws/chat (default: from launcher/ports.toml)")
    parser.add_argument("--conversation", help="continue this conversation id")
    args = parser.parse_args(argv)
    args.url = args.url or default_url()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
