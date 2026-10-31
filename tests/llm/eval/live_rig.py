"""The live rig shared by the language-model evaluations (E1/E2, E6, E7, E8): the real programs, in one process.

- a private nats-server on a free port;
- the real llama.cpp servers of the asked nodes, on free ports (a temporary ports file), each under the RAM guard;
- the four nodes, each with its real engine behind the failure switches (`FaultyEngine`) when its server runs, and
  every node's ROM documents copied and approved by a fixture in a temporary folder (the real approvals are never
  read or written: e3_retrieval.approved_rom_copy);
- the farm runtime's trained feed at 1x (or the simulated one) with a demo fault, publishing on the same NATS;
- the real gateway on a free port; questions go through `/ws/chat` with tools/chat_cli.py, as the owner's do.
Nothing here is evaluated: each E-script uses the rig and measures. Everything is stopped on exit.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
for _extra in (ROOT / "tools", ROOT / "nodes" / "incubator-1", ROOT / "matrix", ROOT / "interface" / "gateway",
               ROOT / "tests" / "llm", ROOT / "tests" / "llm" / "eval", ROOT / "launcher"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import uvicorn  # noqa: E402
import websockets  # noqa: E402

import chat_cli  # noqa: E402
import farm_runtime as fr  # noqa: E402
import gateway as gw  # noqa: E402
from comms.nats_link import NatsLink  # noqa: E402
from comms.peer_network import RadioRules  # noqa: E402
from contracts import NODE_IDS, load_node_config  # noqa: E402
from e3_retrieval import approved_rom_copy  # noqa: E402
from llm.engines import EngineClient, server_spec  # noqa: E402
from llm.failure_injection import FailureSwitches, FaultyEngine  # noqa: E402
from llm.jarvis_speaker import load_persona  # noqa: E402
from node import Node  # noqa: E402

NATS_SERVER = ROOT / "third_party" / "nats-server" / "bin" / "nats-server.exe"
LOG = logging.getLogger("live-rig")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass
class LiveRig:
    """`async with LiveRig(llm_nodes=...) as rig:` then `await rig.ask(text)`."""

    llm_nodes: tuple[str, ...] = tuple(NODE_IDS)
    feed: str = "trained"
    fault: str | None = "supply_fan_failure"
    warm_ticks: int = 60
    #: E8: (node, file, heading, text) passages planted in a node's approved copy before it starts
    planted: tuple[tuple[str, str, str, str], ...] = ()
    folder: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="jarvis-rig-")))
    nodes: dict[str, Node] = field(default_factory=dict)
    servers: dict[str, Any] = field(default_factory=dict)
    specs: dict[str, Any] = field(default_factory=dict)
    #: request id -> the evidence of that finished turn (every outcome; the evaluations verify every reply against it)
    evidence: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    async def __aenter__(self) -> LiveRig:
        try:
            await self._start()
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def _start(self) -> None:
        from test_engines_live import start_server, wait_for_health  # pyright: ignore[reportMissingImports]
        port = free_port()
        self._nats = subprocess.Popen([str(NATS_SERVER), "-a", "127.0.0.1", "-p", str(port)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.nats_url = f"nats://127.0.0.1:{port}"
        self.nats_port = port
        await self._wait_port(port)
        ports = self.folder / "ports.toml"
        llm = "\n".join(f"{node} = {free_port()}" for node in NODE_IDS)
        ports.write_text(f'schema_version = 1\nhost = "127.0.0.1"\nnats = {port}\nmatrix_api = {free_port()}\n'
                         f'gateway = {free_port()}\nwebsite = 5173\n\n[llm]\n{llm}\n', encoding="utf-8")
        self.ports_file = ports
        for node in self.llm_nodes:
            self.specs[node] = server_spec(node, ports)                   # type: ignore[arg-type]
            self.servers[node] = start_server(self.specs[node])
        self.engines: dict[str, Any] = {}
        self.switches: dict[str, FailureSwitches] = {}
        for node in self.llm_nodes:
            client, load_s = await wait_for_health(self.specs[node], self.servers[node][0])
            await client.close()
            LOG.info("%s ready in %.1f s", node, load_s)
        for node in NODE_IDS:
            rom = approved_rom_copy(node, self.folder / "copy")
            self._plant(node, rom)
            link = NatsLink(node, self.nats_url, logger=LOG)
            await link.connect(attempts=5)
            switches = FailureSwitches(node, logger=LOG)                 # type: ignore[arg-type]
            engine = None
            if node in self.llm_nodes:
                spec = self.specs[node]
                engine = FaultyEngine(EngineClient(spec.url, slot=0, model=spec.alias), switches)
            started = Node(load_node_config(ROOT / "nodes" / node / "node.toml"), link, logger=LOG,
                           rules=RadioRules(latency_ticks=0), data_dir=self.folder / "data" / node, engine=engine,
                           switches=switches, persona=load_persona() if node == "incubator-1" and engine else None,
                           rom_dir=rom)
            await started.start()
            self.nodes[node] = started
            self.switches[node] = switches
        farm_link = NatsLink("farm-runtime", self.nats_url, logger=LOG)
        await farm_link.connect(attempts=5)
        self.farm = fr.FarmRuntime(farm_link, logger=LOG, feed=self.feed, data_dir=self.folder / "matrix")  # type: ignore[arg-type]
        self._farm_stop = asyncio.Event()
        self._farm_task = asyncio.create_task(self.farm.run(self._farm_stop))
        if self.fault:
            await self.farm.inject_fault(self.fault)                     # type: ignore[arg-type]
        await self.farm.control("speed", speed=30.0)
        await self._until(lambda: self.farm.tick >= self.warm_ticks, 120.0)
        await self.farm.control("speed", speed=1.0)
        gateway_link = NatsLink("gateway", self.nats_url, logger=LOG)
        await gateway_link.connect(attempts=5)
        self.gateway = gw.Gateway(gateway_link, "http://127.0.0.1:1", logger=LOG)
        await self.gateway.start()
        self.gateway_port = free_port()
        self._server = uvicorn.Server(uvicorn.Config(gw.create_app(self.gateway), host="127.0.0.1",
                                                     port=self.gateway_port, log_level="warning", lifespan="off"))
        self._serving = asyncio.create_task(self._server.serve())
        await self._until(lambda: self._server.started, 15.0)
        speaker = self.nodes["incubator-1"]
        if speaker.agent is not None:
            await self._until(lambda: speaker.agent is not None and speaker.agent.warm_ms is not None, 90.0)
            finish = speaker.agent._finish  # pyright: ignore[reportPrivateUsage]

            async def keep(turn: Any, sent: list[str]) -> Any:
                done = await finish(turn, sent)
                self.evidence[done.request_id] = list(turn.evidence)
                while len(self.evidence) > 500:
                    self.evidence.pop(next(iter(self.evidence)))
                return done
            speaker.agent._finish = keep  # type: ignore[method-assign]
        await self._until(lambda: all(self.nodes["incubator-1"].peers[n].capabilities is not None
                                      for n in NODE_IDS if n != "incubator-1"), 30.0)

    def _plant(self, node: str, rom: Path) -> None:
        """Append the planted sections to the copy and approve the copy's new bytes (the real ROM is untouched)."""
        import json
        from eval_results import file_sha256
        mine = [p for p in self.planted if p[0] == node]
        if not mine:
            return
        approvals = json.loads((rom / "approvals.json").read_text(encoding="utf-8"))
        for _, name, heading, text in mine:
            target = rom / name
            section = "\n\n## " + heading + "\n\n" + text + "\n"
            target.write_text(target.read_text(encoding="utf-8").rstrip("\n") + section, encoding="utf-8")
            approvals["approved"][name]["sha256"] = file_sha256(target)
        (rom / "approvals.json").write_text(json.dumps(approvals), encoding="utf-8")

    @staticmethod
    async def _wait_port(port: int) -> None:
        deadline = time.monotonic() + 15
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(0.1)

    @staticmethod
    async def _until(check: Any, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while not check():
            if time.monotonic() > deadline:
                raise TimeoutError("the rig did not get ready")
            await asyncio.sleep(0.1)

    async def ask(self, text: str, conversation_id: str | None = None) -> tuple[chat_cli.Answer, float]:
        """One question through /ws/chat: the answer, and the client's own seconds until its ChatDone."""
        started = time.monotonic()
        async with websockets.connect(f"ws://127.0.0.1:{self.gateway_port}/ws/chat") as socket_:
            answer = await chat_cli.ask(socket_, text, conversation_id, answer_s=chat_cli.ANSWER_S)
        return answer, time.monotonic() - started

    async def set_failures(self, **on: list[str]) -> None:
        """The mentor's switches, set through the gateway (it publishes them on mentor.llm_failures)."""
        body = {name: list(on.get(name, [])) for name in ("llm_timeout", "broken_json", "silent_peer", "server_crash")}
        await self.gateway.set_switches(gw.LlmFailureBody(**body))  # type: ignore[arg-type]
        await asyncio.sleep(0.5)

    async def __aexit__(self, *exc: Any) -> None:
        from test_engines_live import stop_server  # pyright: ignore[reportMissingImports]
        server = getattr(self, "_server", None)
        if server is not None:
            server.should_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._serving, timeout=10.0)
        if getattr(self, "gateway", None) is not None:
            with contextlib.suppress(Exception):
                await self.gateway.close()
        if getattr(self, "_farm_task", None) is not None:
            self._farm_stop.set()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._farm_task, timeout=10.0)
            self.farm.close_memories()
            with contextlib.suppress(Exception):
                await self.farm.link.close()
        for node in list(self.nodes.values()):
            with contextlib.suppress(Exception):
                await node.stop()
        for process, job, output in self.servers.values():
            with contextlib.suppress(Exception):
                stop_server(process, job, output)
        nats = getattr(self, "_nats", None)
        if nats is not None:
            nats.terminate()
            nats.wait(timeout=10)
