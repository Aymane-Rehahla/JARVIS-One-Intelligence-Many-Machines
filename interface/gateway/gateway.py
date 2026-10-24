"""The gateway: the website's only door into JARVIS (plan section 4.4, W1), on 127.0.0.1 (`gateway` in ports.toml).

It follows NATS and streams the website one farm snapshot at a time, at most MAX_SNAPSHOTS_PER_S per second (the
latest state wins, nothing queues up), and forwards only the farm-clock actions to the Matrix API. Step 6A builds this
skeleton; chat, audit, voice, demo faults and the mentor switches come in 6W, 6C and 6E.

    GET  /health          the gateway, its NATS link, the Matrix API and the nodes it hears
    WS   /ws/farm         FarmSnapshot messages (contract below; the website checks each one with zod)
    POST /sim/{action}    start | pause | reset | speed {"speed": S} → the Matrix API (S one of ALLOWED_SPEEDS)

Mentor fault clicks never go on NATS (ground rule 4). Only the website's own origins may connect (Q7).

Usage: python interface/gateway/gateway.py [--nats URL] [--matrix URL] [--port P] [--logs DIR | --no-logs]
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
import time
import tomllib
from collections import deque
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_NODE = ROOT / "nodes" / "incubator-1"            # the four copies are identical (tests/test_nodes_in_sync.py)
if str(REFERENCE_NODE) not in sys.path:
    sys.path.insert(0, str(REFERENCE_NODE))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError  # noqa: E402

from comms.jsonlog import log, program_logger  # noqa: E402
from comms.nats_link import NatsLink  # noqa: E402
from contracts import (FARM_LINKS, FARM_SIM, MACHINES, NODE_IDS, Capabilities, Contract, FarmAssessment,  # noqa: E402
                       FarmEvent, FarmStatus, Heartbeat, LinkStatus, Machine, NodeId, SimState, farm_subject,
                       jarvis_subject, utc_now)

MAX_SNAPSHOTS_PER_S = 5.0
EVENTS_SHOWN = 50
SILENT_AFTER_S = 30.0
MATRIX_TIMEOUT_S = 5.0
ALLOWED_SPEEDS = (1.0, 10.0, 30.0, 60.0, 120.0)            # the test feed's 1x and the website's 10x-120x
WEBSITE_ORIGINS = ("http://127.0.0.1:{port}", "http://localhost:{port}")


class NodeView(BaseModel):
    """One node as the website sees it: its machine's readings, its reasoning's output and its liveness."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    id: NodeId
    machine: Machine
    alive: bool
    heartbeat_age_s: float | None = Field(default=None, ge=0.0)
    known: list[NodeId]
    capabilities: Capabilities | None = None
    status: FarmStatus | None = None
    assessment: FarmAssessment | None = None


class FarmSnapshot(Contract):
    """`/ws/farm`: the whole farm as one message. The website maps it onto its FarmState (6W)."""

    type: Literal["farm_snapshot"] = "farm_snapshot"
    sent_utc: str
    sequence: int = Field(ge=0)
    sim: SimState | None
    links: LinkStatus | None
    nodes: dict[NodeId, NodeView]
    events: list[FarmEvent]


class SpeedBody(BaseModel):
    """POST /sim/speed."""

    model_config = ConfigDict(extra="forbid", strict=True)
    speed: float


class FarmView:
    """The gateway's picture of the farm, updated from NATS."""

    def __init__(self) -> None:
        self.sim: SimState | None = None
        self.links: LinkStatus | None = None
        self.status: dict[NodeId, FarmStatus] = {}
        self.assessment: dict[NodeId, FarmAssessment] = {}
        self.heartbeats: dict[NodeId, tuple[Heartbeat, float]] = {}
        self.capabilities: dict[NodeId, Capabilities] = {}
        self.events: deque[FarmEvent] = deque(maxlen=EVENTS_SHOWN)
        self.version = 0                                     # bumps on every change
        self.sequence = 0

    def changed(self) -> None:
        self.version += 1

    def alive_nodes(self) -> list[NodeId]:
        """Nodes whose heartbeat arrived within the silence limit."""
        now = time.monotonic()
        return [node for node in NODE_IDS
                if (beat := self.heartbeats.get(node)) is not None and now - beat[1] <= SILENT_AFTER_S]

    def snapshot(self) -> FarmSnapshot:
        now = time.monotonic()
        nodes: dict[NodeId, NodeView] = {}
        for node in NODE_IDS:
            beat = self.heartbeats.get(node)
            age = now - beat[1] if beat else None
            nodes[node] = NodeView(id=node, machine=MACHINES[node], alive=age is not None and age <= SILENT_AFTER_S,
                                   heartbeat_age_s=round(age, 3) if age is not None else None,
                                   known=list(beat[0].known) if beat else [],
                                   capabilities=self.capabilities.get(node), status=self.status.get(node),
                                   assessment=self.assessment.get(node))
        self.sequence += 1
        return FarmSnapshot(sent_utc=utc_now(), sequence=self.sequence, sim=self.sim, links=self.links, nodes=nodes,
                            events=list(self.events))


class Gateway:
    """The gateway's state: its NATS link, its farm view, its Matrix API client."""

    def __init__(self, link: NatsLink, matrix_url: str, *, logger: logging.Logger,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.link = link
        self.logger = logger
        self.view = FarmView()
        self.matrix = httpx.AsyncClient(base_url=matrix_url.rstrip("/"), timeout=MATRIX_TIMEOUT_S, transport=transport)

    async def start(self) -> None:
        """Subscribe to everything the website shows."""
        view = self.view

        async def on_status(message: FarmStatus, subject: str) -> None:
            view.status[message.node] = message
            view.changed()

        async def on_assessment(message: FarmAssessment, subject: str) -> None:
            view.assessment[message.node] = message
            view.changed()

        async def on_event(message: FarmEvent, subject: str) -> None:
            view.events.append(message)
            view.changed()

        async def on_sim(message: SimState, subject: str) -> None:
            view.sim = message
            view.changed()

        async def on_links(message: LinkStatus, subject: str) -> None:
            view.links = message
            view.changed()

        async def on_heartbeat(message: Heartbeat, subject: str) -> None:
            view.heartbeats[message.node] = (message, time.monotonic())
            view.changed()

        async def on_capabilities(message: Capabilities, subject: str) -> None:
            view.capabilities[message.node] = message
            view.changed()

        await self.link.subscribe(farm_subject("*", "status"), FarmStatus, on_status)
        await self.link.subscribe(farm_subject("*", "assessment"), FarmAssessment, on_assessment)
        await self.link.subscribe(farm_subject("*", "events"), FarmEvent, on_event)
        await self.link.subscribe(FARM_SIM, SimState, on_sim)
        await self.link.subscribe(FARM_LINKS, LinkStatus, on_links)
        await self.link.subscribe(jarvis_subject("*", "heartbeat"), Heartbeat, on_heartbeat)
        await self.link.subscribe(jarvis_subject("*", "capabilities"), Capabilities, on_capabilities)

    async def close(self) -> None:
        await self.matrix.aclose()
        await self.link.close()

    async def matrix_health(self) -> str:
        try:
            response = await self.matrix.get("/health")
            return "ok" if response.status_code == 200 else f"HTTP {response.status_code}"
        except httpx.HTTPError as error:
            return f"unreachable ({type(error).__name__})"

    async def sim_action(self, action: str, speed: float | None) -> dict[str, Any]:
        """Forward one farm-clock action to the Matrix API; raises HTTPException with the reason."""
        body = {"speed": speed} if action == "speed" else None
        try:
            response = await self.matrix.post(f"/sim/{action}", json=body)
        except httpx.HTTPError as error:
            raise HTTPException(503, f"the Matrix API is unreachable: {type(error).__name__}") from error
        if response.status_code != 200:
            raise HTTPException(502, f"the Matrix API answered HTTP {response.status_code}")
        try:
            state: dict[str, Any] = SimState.model_validate_json(response.content).model_dump(mode="json")
        except ValidationError as error:
            raise HTTPException(502, "the Matrix API's answer is not a valid SimState") from error
        log(self.logger, logging.INFO, "farm clock action", action=action, speed=speed)
        return state


def create_app(gateway: Gateway, *, website_port: int = 5173) -> FastAPI:
    """The gateway's routes (see the module docstring)."""
    origins = [pattern.format(port=website_port) for pattern in WEBSITE_ORIGINS]
    app = FastAPI(title="JARVIS gateway", version="0.6.0", docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])

    @app.get("/health")
    async def health() -> dict[str, JsonValue]:
        alive: list[JsonValue] = list(gateway.view.alive_nodes())
        return {"status": "ok", "nats": gateway.link.connected, "matrix_api": await gateway.matrix_health(),
                "nodes_alive": alive, "dropped_messages": gateway.link.dropped}

    @app.post("/sim/{action}")
    async def sim(action: Literal["start", "pause", "reset", "speed"], body: SpeedBody | None = None) -> dict[str, Any]:
        speed = None
        if action == "speed":
            if body is None or body.speed not in ALLOWED_SPEEDS:
                raise HTTPException(422, f"speed must be one of {', '.join(f'{s:g}' for s in ALLOWED_SPEEDS)}")
            speed = body.speed
        return await gateway.sim_action(action, speed)

    @app.websocket("/ws/farm")
    async def farm(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if origin is not None and origin not in origins:
            await websocket.close(code=1008)                  # policy violation: not the website
            return
        await websocket.accept()
        sent = -1
        try:
            while True:
                if gateway.view.version != sent:
                    sent = gateway.view.version
                    await websocket.send_text(gateway.view.snapshot().model_dump_json())
                await asyncio.sleep(1.0 / MAX_SNAPSHOTS_PER_S)
        except (WebSocketDisconnect, RuntimeError):
            return

    return app


def read_ports(path: Path = ROOT / "launcher" / "ports.toml") -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


async def amain(args: argparse.Namespace) -> int:
    logger = program_logger("gateway", None if args.no_logs else args.logs, echo=args.echo)
    ports = read_ports()
    host = ports["host"]
    link = NatsLink("gateway", args.nats or f"nats://{host}:{ports['nats']}", logger=logger)
    try:
        await link.connect()
    except Exception as error:                                # NoServersError and friends
        log(logger, logging.ERROR, "NATS unreachable", error=str(error))
        return 2
    gateway = Gateway(link, args.matrix or f"http://{host}:{ports['matrix_api']}", logger=logger)
    await gateway.start()
    server = uvicorn.Server(uvicorn.Config(create_app(gateway, website_port=int(ports["website"])), host=host,
                                           port=args.port or int(ports["gateway"]), log_level="warning",
                                           lifespan="off"))
    try:
        await server.serve()
    finally:
        with contextlib.suppress(Exception):
            await gateway.close()
        log(logger, logging.INFO, "gateway stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nats", help="NATS URL (default: from launcher/ports.toml)")
    parser.add_argument("--matrix", help="Matrix API URL (default: from launcher/ports.toml)")
    parser.add_argument("--port", type=int, help="gateway port (default: from launcher/ports.toml)")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--logs", type=Path, default=ROOT / "logs", help="log folder (default: logs/)")
    where.add_argument("--no-logs", action="store_true", help="no log file")
    parser.add_argument("--echo", action="store_true", help="also print the log to stderr")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
