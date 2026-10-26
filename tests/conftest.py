"""Shared pytest setup.

Tests marked `deferred` could disturb the Phase 5 training (plan ground rule 1). They are written now, listed in
tests/DEFERRED.md with their exact command, and run only when JARVIS_RUN_DEFERRED=1.

`nats_url` starts the project's own nats-server (third_party/nats-server/) on a free port of 127.0.0.1 for one test
module, and stops it afterwards: a few megabytes of RAM, no model, nothing outside the project.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
NATS_SERVER = ROOT / "third_party" / "nats-server" / "bin" / "nats-server.exe"
PORTS = ROOT / "launcher" / "ports.toml"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("JARVIS_RUN_DEFERRED") == "1":
        return
    skip = pytest.mark.skip(reason="deferred until the Phase 5 training ends; see tests/DEFERRED.md")
    for item in items:
        if "deferred" in item.keywords:
            item.add_marker(skip)


def free_port() -> int:
    """A TCP port nothing listens on right now (127.0.0.1)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def write_test_ports(path: Path, new_port: Callable[[], int]) -> dict[str, Any]:
    """Copy launcher/ports.toml with fresh service ports, retaining website as the allowed gateway origin."""
    configured: dict[str, Any] = tomllib.loads(PORTS.read_text(encoding="utf-8"))
    ports: dict[str, Any] = {}
    for name, value in configured.items():
        if isinstance(value, dict):
            ports[name] = {nested: new_port() if isinstance(port, int) else port
                           for nested, port in value.items()}
        elif isinstance(value, int) and name not in ("schema_version", "website"):
            ports[name] = new_port()
        else:
            ports[name] = value
    lines = [f'{name} = {value!r}' if isinstance(value, str) else f"{name} = {value}"
             for name, value in ports.items() if not isinstance(value, dict)]
    for table, values in ports.items():
        if not isinstance(values, dict):
            continue
        lines.extend(("", f"[{table}]", *(f"{name} = {value}" for name, value in values.items())))
    path.write_text("\n".join((*lines, "")), encoding="utf-8")
    return ports


def wait_for_port(port: int, timeout_s: float = 10.0) -> None:
    """Wait until 127.0.0.1:port accepts connections. Raises TimeoutError."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"nothing listens on 127.0.0.1:{port} after {timeout_s} s")


@pytest.fixture
def new_port() -> Callable[[], int]:
    """Gives a fresh free port each time it is called."""
    return free_port


@pytest.fixture(scope="module")
def nats_url() -> Iterator[str]:
    """A private nats-server for one test module."""
    if not NATS_SERVER.is_file():
        pytest.skip("nats-server is not installed (third_party/nats-server/README.md)")
    port = free_port()
    process = subprocess.Popen([str(NATS_SERVER), "-a", "127.0.0.1", "-p", str(port)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    try:
        wait_for_port(port)
        yield f"nats://127.0.0.1:{port}"
    finally:
        process.terminate()
        process.wait(timeout=10)
