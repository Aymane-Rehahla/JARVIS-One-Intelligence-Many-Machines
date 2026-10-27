"""This node's own program: one of the four that together are JARVIS.

Usage (from the project folder; the launcher's supervisor normally starts it):
    python nodes/<node>/runtime.py [--nats URL] [--heartbeat-s S] [--latency-ticks N] [--loss P] [--probe]
                                   [--connect-attempts N] [--logs DIR | --no-logs] [--echo]

The node reads its own `node.toml` (next to this file), connects to NATS (the URL from launcher/ports.toml unless
--nats is given) and runs until it is stopped (Ctrl+C, Ctrl+Break, or the process is ended). Logs go to
logs/<node>.jsonl. --probe lets tests and diagnostics make this node ask a peer (`jarvis.<node>.probe`).
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

NODE_DIR = Path(__file__).resolve().parent
ROOT = NODE_DIR.parents[1]
if str(NODE_DIR) not in sys.path:
    sys.path.insert(0, str(NODE_DIR))

from comms.jsonlog import log, program_logger  # noqa: E402 - the node folder is on the path first
from comms.nats_link import CONNECT_ATTEMPTS, NatsLink  # noqa: E402
from comms.peer_network import RadioRules  # noqa: E402
from contracts import ContractError, load_node_config  # noqa: E402
from node import HEARTBEAT_S, Node  # noqa: E402
from nats.errors import NoServersError  # noqa: E402


def nats_url(ports_file: Path = ROOT / "launcher" / "ports.toml") -> str:
    """The NATS URL from launcher/ports.toml (the single source of truth for ports)."""
    ports = tomllib.loads(ports_file.read_text(encoding="utf-8"))
    return f"nats://{ports['host']}:{ports['nats']}"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nats", help="NATS URL (default: from launcher/ports.toml)")
    parser.add_argument("--heartbeat-s", type=float, default=HEARTBEAT_S, help="heartbeat period (default 10 s)")
    parser.add_argument("--latency-ticks", type=int, default=1, help="ZigBee latency in farm ticks (default 1)")
    parser.add_argument("--loss", type=float, default=0.0, help="ZigBee loss probability (default 0)")
    parser.add_argument("--probe", action="store_true", help="answer jarvis.<node>.probe (tests, diagnostics)")
    parser.add_argument("--connect-attempts", type=int, default=CONNECT_ATTEMPTS,
                        help=f"tries to reach NATS before giving up (default {CONNECT_ATTEMPTS})")
    data_root = os.environ.get("JARVIS_DATA_ROOT")
    default_data_dir = Path(data_root) / NODE_DIR.name if data_root else NODE_DIR / "data"
    parser.add_argument("--data-dir", type=Path, default=default_data_dir, help="node data folder (default: data/)")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--logs", type=Path, default=ROOT / "logs", help="log folder (default: logs/)")
    where.add_argument("--no-logs", action="store_true", help="no log file")
    parser.add_argument("--echo", action="store_true", help="also print the log to stderr")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    config = load_node_config(NODE_DIR / "node.toml")
    logger = program_logger(config.id, None if args.no_logs else args.logs, echo=args.echo)
    link = NatsLink(config.id, args.nats or nats_url(), logger=logger)
    try:
        await link.connect(attempts=max(1, args.connect_attempts))
    except NoServersError:
        return 2
    node = Node(config, link, logger=logger, heartbeat_s=args.heartbeat_s, probe=args.probe,
                rules=RadioRules(latency_ticks=args.latency_ticks, loss=args.loss), data_dir=args.data_dir)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            with contextlib.suppress(NotImplementedError, ValueError):
                signal.signal(getattr(signal, name), lambda *_: loop.call_soon_threadsafe(stop.set))
    await node.start()
    try:
        await stop.wait()
    finally:
        log(logger, logging.INFO, "stopping", node=config.id)
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
