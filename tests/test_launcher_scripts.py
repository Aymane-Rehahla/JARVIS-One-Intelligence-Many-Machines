"""The PowerShell launchers (6A): isolated ports, gateway readiness, clean supervisor stop and monitor ownership."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.request import urlopen

import pytest

from resource_monitor import pid_alive, same_process

ROOT = Path(__file__).resolve().parents[1]
START = ROOT / "launcher" / "start_all.ps1"
STOP = ROOT / "launcher" / "stop_all.ps1"
MONITOR_PID = ROOT / "logs" / "resource_monitor.pid"
POWERSHELL = "powershell.exe"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the launchers use Windows PowerShell and WMI")


def free_port() -> int:
    """A currently unused localhost TCP port."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def write_ports(path: Path) -> int:
    """A complete temporary launcher ports file; return its gateway port."""
    ports = {name: free_port() for name in ("nats", "llama_server_1", "llama_server_2", "matrix_api", "gateway",
                                            "website", "incubator_1", "incubator_2", "hvac", "animal_protection")}
    path.write_text("\n".join(("schema_version = 1", 'host = "127.0.0.1"', *(f"{name} = {port}" for name, port in ports.items()),
                                "", "[mcp]", f"incubator-1 = {ports['incubator_1']}",
                                f"incubator-2 = {ports['incubator_2']}", f"hvac = {ports['hvac']}",
                                f"animal-protection = {ports['animal_protection']}", "")), encoding="utf-8")
    return ports["gateway"]


def pid_record(path: Path) -> tuple[int, int]:
    """The monitored process identity from a PID file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return int(data["pid"]), int(data["created"])


def until(check: Callable[[], bool], timeout_s: float = 15.0) -> None:
    """Wait for a short-lived launcher transition."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    assert check(), "condition did not become true"


def powershell(script: Path, *arguments: str, timeout: float = 150.0) -> subprocess.CompletedProcess[str]:
    """Run a launcher with its output retained for an assertion failure."""
    return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *arguments],
                          cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)


def test_start_and_stop_with_temporary_ports_leaves_the_monitor_running(tmp_path: Path) -> None:
    """The launcher reaches the gateway then leaves an independently started monitor alone."""
    before_pid, before_created = pid_record(MONITOR_PID)
    assert same_process(before_pid, before_created), "the real resource monitor must be running before this test"
    ports, logs = tmp_path / "ports.toml", tmp_path / "logs"
    gateway = write_ports(ports)
    start = powershell(START, "-NoLlm", "-NoWebsite", "-NoMonitor", "-Only", "nats,farm,gateway", "-Ports",
                       str(ports), "-Logs", str(logs))
    try:
        assert start.returncode == 0, start.stdout + start.stderr
        with urlopen(f"http://127.0.0.1:{gateway}/health", timeout=10) as response:  # noqa: S310 - local test server
            assert response.status == 200
        duplicate = powershell(START, "-NoLlm", "-NoWebsite", "-NoMonitor", "-Only", "nats,farm,gateway", "-Ports",
                                str(ports), "-Logs", str(logs))
        assert duplicate.returncode == 2, duplicate.stdout + duplicate.stderr
        stop = powershell(STOP, "-Logs", str(logs), timeout=60)
        assert stop.returncode == 0, stop.stdout + stop.stderr
        status = json.loads((logs / "supervisor.status.json").read_text(encoding="utf-8"))
        pids = [int(item["pid"]) for item in status["programs"].values() if item["pid"]]
        until(lambda: not any(pid_alive(pid) for pid in pids))
        after_pid, after_created = pid_record(MONITOR_PID)
        assert (after_pid, after_created) == (before_pid, before_created)
        assert same_process(after_pid, after_created)
    finally:
        powershell(STOP, "-Logs", str(logs), timeout=60)
