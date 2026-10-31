"""D5 (6A): the no-LLM multi-process farm, three-node start, hot-join, radio question and a cut radio link.

This starts the project's NATS, rule-brain farm, three node programs and gateway through the supervisor on temporary
localhost ports. The fourth node runs in this test process so it can ask a live peer directly. It is skipped before
starting anything when the Phase 5 training leaves less than 3 GiB free RAM.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import socket
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from ctypes import wintypes
from pathlib import Path

import httpx
import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.typing import Origin

import gateway as gw
from comms.nats_link import NatsLink
from conftest import write_test_ports
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


def listens(port: int) -> bool:
    """True when something accepts connections on 127.0.0.1:port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def program_status(status: dict[str, object], name: str) -> dict[str, object] | None:
    """One validated program record from the supervisor's untyped JSON status document."""
    programs = status.get("programs")
    if not isinstance(programs, dict):
        return None
    record = programs.get(name)
    return record if isinstance(record, dict) else None


class ProcessEntry(ctypes.Structure):
    """The Windows process-snapshot record used to prove a supervised launcher takes its interpreter child with it."""

    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def child_pids(parent_pid: int) -> list[int]:
    """The direct child PIDs of this test's owned process, read from one Windows process snapshot."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "could not snapshot processes")
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        found: list[int] = []
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32ParentProcessID == parent_pid:
                found.append(int(entry.th32ProcessID))
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return found
    finally:
        kernel32.CloseHandle(snapshot)


def terminate_owned_process(pid: int) -> None:
    """End exactly this test's supervised launcher process; its Windows job must end the interpreter child too."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)  # PROCESS_TERMINATE | PROCESS_SYNCHRONIZE
    if not handle:
        raise OSError(ctypes.get_last_error(), f"could not open test-owned PID {pid}")
    try:
        if not kernel32.TerminateProcess(handle, 1):
            raise OSError(ctypes.get_last_error(), f"could not end test-owned PID {pid}")
    finally:
        kernel32.CloseHandle(handle)


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
    ports = write_test_ports(tmp_path / "ports.toml", new_port)
    logs, status_path = tmp_path / "logs", tmp_path / "logs" / "supervisor.status.json"
    nats_url = f"nats://127.0.0.1:{ports['nats']}"
    child_environment = os.environ.copy()
    child_environment["JARVIS_DATA_ROOT"] = str(tmp_path / "node-data")
    supervisor = subprocess.Popen([str(PYTHON), str(SUPERVISOR), "--no-llm", "--feed", "simulated",
                                   "--only", ",".join(SYSTEM_PROGRAMS),
                                    "--ports", str(tmp_path / "ports.toml"), "--logs", str(logs)], cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=child_environment)
    animal: Node | None = None
    stopped_status: dict[str, object] | None = None
    try:
        await wait_ready(status_path, supervisor)
        website_origin = f"http://127.0.0.1:{ports['website']}"
        async with connect(f"ws://127.0.0.1:{ports['gateway']}/ws/farm", origin=Origin(website_origin), open_timeout=30) as socket:
            three = await next_snapshot(
                socket,
                lambda snapshot: all(snapshot.nodes[node].alive and snapshot.nodes[node].known == list(THREE_NODES)
                                         for node in THREE_NODES) and snapshot.sim is not None,
                "the gateway never showed all three supervised nodes alive and mutually known")
            assert three.sim is not None and three.sim.label == "live: simulated brains"
            # review H6: each supervised node serves MCP on THIS run's ports file, not launcher/ports.toml's 8101-8104
            for node in THREE_NODES:
                port = int(ports["mcp"][node])
                await until(lambda port=port: listens(port), f"{node} does not serve MCP on its temp port {port}")

            animal_link = NatsLink("system-animal-protection", nats_url, logger=LOG)
            await animal_link.connect(attempts=20)
            animal = Node(load_node_config(ROOT / "nodes" / "animal-protection" / "node.toml"), animal_link, logger=LOG,
                          data_dir=tmp_path / "animal-protection-data")
            hot_join_started = time.monotonic()
            await animal.start()
            four = await next_snapshot(
                socket,
                lambda snapshot: all(snapshot.nodes[node].alive and snapshot.nodes[node].known == list(NODE_IDS)
                                     for node in NODE_IDS),
                "the gateway never showed the hot-joined node known by all four")
            hot_join_s = time.monotonic() - hot_join_started
            print(f"hot join: {hot_join_s:.3f} s")
            assert hot_join_s < 10.0
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

            status = read_status(status_path)
            assert status is not None
            old_hvac = program_status(status, "hvac")
            assert old_hvac is not None and isinstance(old_hvac["pid"], int)
            old_hvac_pid = old_hvac["pid"]
            old_caps = animal.peers["hvac"].capabilities
            assert old_caps is not None
            old_started = old_caps.started_utc                  # a restarted node announces a new start time
            await until(lambda: bool(child_pids(old_hvac_pid)), "the test-owned HVAC launcher had no interpreter child")
            old_hvac_children = child_pids(old_hvac_pid)
            terminate_owned_process(old_hvac_pid)

            def hvac_is_restarting() -> bool:
                status = read_status(status_path)
                hvac = program_status(status, "hvac") if status is not None else None
                return hvac is not None and hvac["state"] == "restarting"

            await until(hvac_is_restarting, "the supervisor did not mark the killed HVAC node restarting")

            def hvac_is_ready_again() -> bool:
                status = read_status(status_path)
                hvac = program_status(status, "hvac") if status is not None else None
                return hvac is not None and hvac["state"] == "ready" and hvac["restarts"] == 1 and \
                    isinstance(hvac["pid"], int) and hvac["pid"] != old_hvac_pid

            await until(hvac_is_ready_again, "the supervisor did not restart HVAC once")
            await until(lambda: not pid_alive(old_hvac_pid) and not any(pid_alive(pid) for pid in old_hvac_children),
                        "the old HVAC launcher or interpreter child remained alive")
            # "alive" and "known" alone would pass on the old process's state (30 s silence limit, peers never forget),
            # so require the NEW process's announcement: a different start time, seen by the gateway and by a peer.
            def new_hvac(snapshot: gw.FarmSnapshot) -> bool:
                caps = snapshot.nodes["hvac"].capabilities
                peer = animal.peers["hvac"].capabilities if animal is not None else None
                return (snapshot.nodes["hvac"].alive and caps is not None and caps.started_utc != old_started
                        and peer is not None and peer.started_utc == caps.started_utc)

            restarted = await next_snapshot(socket, new_hvac,
                                            "the restarted HVAC node never announced itself to the gateway and its peer")
            assert restarted.nodes["hvac"].alive
            after = await animal.radio.ask("hvac", "readings", deadline_s=3.0)
            assert after.ok and after.farm_time                  # the new process answers questions
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
