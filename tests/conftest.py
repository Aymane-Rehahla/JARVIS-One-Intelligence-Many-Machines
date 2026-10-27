"""Shared pytest setup.

Tests marked `deferred` are heavy (they load language or voice models): plain `pytest` skips them, and they run on
purpose, one at a time, with JARVIS_RUN_DEFERRED=1 (tests/DEFERRED.md lists each with its exact command). They were
deferred while the Phase 5 training ran; it ended on 27 Oct.

`nats_url` starts the project's own nats-server (third_party/nats-server/) on a free port of 127.0.0.1 for one test
module, and stops it afterwards: a few megabytes of RAM, no model, nothing outside the project.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
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
    skip = pytest.mark.skip(reason="heavy test: run it on purpose with JARVIS_RUN_DEFERRED=1 (tests/DEFERRED.md)")
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


def fixture_approved_rom(source: Path, destination: Path) -> Path:
    """Copy one real manual into a test ROM and approve only the exact temporary bytes."""
    destination.mkdir(parents=True)
    target = destination / source.name
    content = source.read_text(encoding="utf-8").replace('status = "draft"', 'status = "approved"', 1)
    target.write_text(content, encoding="utf-8")
    metadata = tomllib.loads(content.split("+++", 2)[1])
    project_root = destination.parents[3]
    for source_record in metadata["sources"]:
        copied_source = (source.parent / source_record["link"]).resolve()
        copied_target = project_root / copied_source.relative_to(ROOT)
        copied_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(copied_source, copied_target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (destination / "approvals.json").write_text(json.dumps({
        "schema_version": 1,
        "approved": {target.name: {"sha256": digest, "version": 1, "approved_utc": "2026-10-27T00:00:00Z",
                                   "by": "test fixture"}},
        "history": {},
    }), encoding="utf-8")
    return destination
