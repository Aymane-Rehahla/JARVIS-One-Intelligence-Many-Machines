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
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NATS_SERVER = ROOT / "third_party" / "nats-server" / "bin" / "nats-server.exe"


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
