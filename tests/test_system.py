"""D5 (6A): the no-LLM multi-process farm, three-node start, hot-join, radio question and a cut radio link.

This starts the project's NATS, rule-brain farm, three node programs and gateway through the supervisor on temporary
localhost ports. The fourth node runs in this test process so it can ask a live peer directly. It is skipped before
starting anything when the Phase 5 training leaves less than 3 GiB free RAM.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.typing import Origin

import gateway as gw
from comms.nats_link import NatsLink
from contracts import NODE_IDS, load_node_config
from node import Node
from resource_monitor import GIB, Probe, pid_alive

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
SUPERVISOR = ROOT / "launcher" / "supervisor.py"
THREE_NODES = ("incubator-1", "incubator-2", "hvac")
SYSTEM_PROGRAMS = ("nats", "farm", *THREE_NODES, "gateway")
LOG = logging.getLogger("test-system")


def require_free_ram() -> None:
    """Skip D5 before starting processes when the training needs too much RAM."""
    probe = Probe()
    try:
        free_bytes = probe.sample().ram_free_bytes
    finally:
        probe.close()
    if free_bytes < 3 * GIB:
        pytest.skip(f"D5 needs at least 3 GiB free RAM; only {free_bytes / GIB:.2f} GiB is free")


def write_ports(path: Path, new_port: Callable[[], int]) -> dict[str, int]:
    """A complete temporary ports file; the gateway's existing website-origin contract keeps its configured port."""
    configured = tomllib.loads((ROOT / "launcher" / "ports.toml").read_text(encoding="utf-8"))
    ports = {name: new_port() for name in ("nats", "llama_server_1", "llama_server_2", "matrix_api", "gateway",
                                            "incubator_1", "incubator_2", "hvac", "animal_protection")}
    ports["website"] = int(configured["website"])
    path.write_text("\n".join(("schema_version = 1", 'host = "127.0.0.1"', *(f"{name} = {port}" for name, port in ports.items()),
                                "", "[mcp]", f"incubator-1 = {ports['incubator_1']}",
                                f"incubator-2 = {ports['incubator_2']}", f"hvac = {ports['hvac']}",
                                f"animal-protection = {ports['animal_protection']}", "")), encoding="utf-8")
    return ports


def read_status(path: Path) -> dict[str, object] | None:
    """The supervisor's latest status, or None until it writes one."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


async def until(check: Callable[[], bool], description: str, timeout_s: float = 40.0) -> None:
    """Wait generously for a real multi-process transition on a loaded machine."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if check():
            return
        await asyncio.sleep(0.2)
    assert check(), description


async def wait_ready(status_path: Path, supervisor: subprocess.Popen[bytes]) -> dict[str, object]:
    """All supervised programs reach ready state, or the supervisor's log explains why it ended."""
    status: dict[str, object] | None = None

    def ready() -> bool:
        nonlocal status
        status = read_status(status_path)
        if status is None:
            return False
        programs = status.get("programs", {})
        return isinstance(programs, dict) and all(programs.get(name, {}).get("state") == "ready" for name in SYSTEM_PROGRAMS)

    await until(ready, "supervisor did not make every D5 program ready", timeout_s=50.0)
    assert supervisor.poll() is None
    assert status is not None
    return status


async def next_snapshot(socket: ClientConnection, predicate: Callable[[gw.FarmSnapshot], bool], description: str,
                        timeout_s: float = 40.0) -> gw.FarmSnapshot:
    """Read snapshots until one proves the required live-system state."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        snapshot = gw.FarmSnapshot.model_validate_json(await asyncio.wait_for(socket.recv(), timeout=remaining))
        if predicate(snapshot):
            return snapshot
    raise AssertionError(description)


def program_pids(status: dict[str, object]) -> list[int]:
    """Every PID ever recorded for this supervisor run."""
    programs = status["programs"]
    assert isinstance(programs, dict)
    return [int(item["pid"]) for item in programs.values() if isinstance(item, dict) and item.get("pid")]


async def stop_supervisor(logs: Path, supervisor: subprocess.Popen[bytes]) -> None:
    """Use the supervisor's stop path; only terminate this test's own handle as a last-resort cleanup."""
    stopped = await asyncio.to_thread(subprocess.run, [str(PYTHON), str(SUPERVISOR), "--stop", "--logs", str(logs)],
                                      cwd=ROOT, capture_output=True, timeout=60, check=False)
    if supervisor.poll() is None and stopped.returncode != 0:
        supervisor.terminate()
    await asyncio.to_thread(supervisor.wait, 30)
    assert stopped.returncode == 0, stopped.stdout.decode() + stopped.stderr.decode()


@pytest.mark.asyncio
async def test_system_hot_join_questions_and_cut_links(tmp_path: Path, new_port: Callable[[], int]) -> None:
    """D5: three nodes become four live, a question answers with farm time, and an HVAC cut times out then restores."""
    require_free_ram()
    ports = write_ports(tmp_path / "ports.toml", new_port)
    logs, status_path = tmp_path / "logs", tmp_path / "logs" / "supervisor.status.json"
    nats_url = f"nats://127.0.0.1:{ports['nats']}"
    supervisor = subprocess.Popen([str(PYTHON), str(SUPERVISOR), "--no-llm", "--only", ",".join(SYSTEM_PROGRAMS),
                                    "--ports", str(tmp_path / "ports.toml"), "--logs", str(logs)], cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    animal: Node | None = None
    stopped_status: dict[str, object] | None = None
    try:
        await wait_ready(status_path, supervisor)
        website_origin = f"http://127.0.0.1:{ports['website']}"
        async with connect(f"ws://127.0.0.1:{ports['gateway']}/ws/farm", origin=Origin(website_origin), open_timeout=30) as socket:
            three = await next_snapshot(
                socket,
                lambda snapshot: all(snapshot.nodes[node].alive and snapshot.nodes[node].known == list(THREE_NODES)
                                     for node in THREE_NODES),
                "the gateway never showed all three supervised nodes alive and mutually known")
            assert three.sim is not None and three.sim.label == "live: simulated brains"

            animal_link = NatsLink("system-animal-protection", nats_url, logger=LOG)
            await animal_link.connect(attempts=20)
            animal = Node(load_node_config(ROOT / "nodes" / "animal-protection" / "node.toml"), animal_link, logger=LOG)
            await animal.start()
            four = await next_snapshot(
                socket,
                lambda snapshot: all(snapshot.nodes[node].alive and snapshot.nodes[node].known == list(NODE_IDS)
                                     for node in NODE_IDS),
                "the gateway never showed the hot-joined node known by all four")
            status = read_status(status_path)
            assert status is not None
            programs = status["programs"]
            assert isinstance(programs, dict) and all(item["restarts"] == 0 for item in programs.values()
                                                       if isinstance(item, dict))
            assert four.nodes["animal-protection"].capabilities is not None

            await until(lambda: animal is not None and animal.status is not None and animal.links is not None,
                        "the in-process node did not receive the live farm feed")
            answer = await animal.radio.ask("hvac", "readings", deadline_s=3.0)
            assert answer.ok and answer.answer and answer.farm_time and answer.tick is not None

            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{ports['matrix_api']}", timeout=20.0) as matrix:
                cut = await matrix.post("/links/hvac/cut", json={"seconds": 60})
                assert cut.status_code == 200 and cut.json()["cut"] == ["hvac"]
                await until(lambda: animal is not None and animal.links is not None and animal.links.cut == ["hvac"],
                            "the in-process node did not receive the HVAC link cut")
                blocked = await animal.radio.ask("hvac", "readings", deadline_s=3.0)
                assert not blocked.ok and blocked.error == "timeout" and "link is cut" in (blocked.detail or "")
                assert blocked.elapsed_ms is not None
                assert blocked.elapsed_ms >= 2_500

                restored = await matrix.post("/links/hvac/restore")
                assert restored.status_code == 200 and restored.json()["cut"] == []
                await until(lambda: animal is not None and animal.links is not None and animal.links.cut == [],
                            "the in-process node did not receive the restored HVAC link")
            answer_after_restore = await animal.radio.ask("hvac", "readings", deadline_s=3.0)
            assert answer_after_restore.ok and answer_after_restore.farm_time
        stopped_status = read_status(status_path)
        assert stopped_status is not None
        await animal.stop()
        animal = None
        await stop_supervisor(logs, supervisor)
        await until(lambda: not any(pid_alive(pid) for pid in program_pids(stopped_status)),
                    "a supervisor-owned process was still running after --stop", timeout_s=30.0)
    finally:
        if animal is not None:
            await animal.stop()
        if supervisor.poll() is None:
            await stop_supervisor(logs, supervisor)
