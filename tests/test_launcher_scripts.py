"""The PowerShell launchers (6A): isolated ports, gateway readiness, clean supervisor stop and monitor ownership."""
from __future__ import annotations

import json
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


def write_ports(path: Path, new_port: Callable[[], int]) -> int:
    """A complete temporary launcher ports file; return its gateway port."""
    ports = {name: new_port() for name in ("nats", "matrix_api", "gateway", "website", "llm_incubator_1",
                                            "llm_incubator_2", "llm_hvac", "llm_animal_protection", "incubator_1",
                                            "incubator_2", "hvac", "animal_protection")}
    path.write_text("\n".join(("schema_version = 1", 'host = "127.0.0.1"', *(f"{name} = {port}" for name, port in ports.items()),
                                "", "[llm]", f"incubator-1 = {ports['llm_incubator_1']}",
                                f"incubator-2 = {ports['llm_incubator_2']}", f"hvac = {ports['llm_hvac']}",
                                f"animal-protection = {ports['llm_animal_protection']}", "", "[mcp]",
                                f"incubator-1 = {ports['incubator_1']}",
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


def test_start_and_stop_with_temporary_ports_leaves_the_monitor_running(tmp_path: Path,
                                                                         new_port: Callable[[], int]) -> None:
    """The launcher reaches the gateway then leaves an independently started monitor alone."""
    before = MONITOR_PID.read_bytes() if MONITOR_PID.is_file() else None
    before_identity = pid_record(MONITOR_PID) if before is not None else None
    before_alive = before_identity is not None and same_process(*before_identity)
    ports, logs = tmp_path / "ports.toml", tmp_path / "logs"
    gateway = write_ports(ports, new_port)
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
        if before is None:
            assert not MONITOR_PID.exists()
        else:
            assert MONITOR_PID.read_bytes() == before
        if before_alive:
            assert before_identity is not None and same_process(*before_identity)
    finally:
        powershell(STOP, "-Logs", str(logs), timeout=60)
