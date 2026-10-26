"""D3: live llama.cpp measurements for the four independent node servers (deferred until training ends).

Each server is started through the same `server_command()` and Windows-job path as the supervisor. The RAM guard owns
each native process while it is alive; the test records its working set and the resource monitor's adapter reading in
logs/d3_engines.json. This module must never run while the Phase 5 training is active.
"""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import IO, Any

import pytest

from contracts import NODE_IDS
from llm.engines import LLAMA_SERVER, EngineClient, ServerSpec, server_command, server_spec
from resource_monitor import GIB, Probe, guarded_processes, pid_alive, register_guarded, unregister_guarded
from winjob import Job, start_in_job

ROOT = Path(__file__).resolve().parents[2]
D3_LOG = ROOT / "logs" / "d3_engines.json"
LOAD_TIMEOUT_S = 240.0


class _ProcessMemoryCounters(ctypes.Structure):
    """The Windows working-set counters needed for D3's per-server RAM measurement."""

    _fields_ = [("cb", wintypes.DWORD), ("page_fault_count", wintypes.DWORD),
                ("peak_working_set_bytes", ctypes.c_size_t), ("working_set_bytes", ctypes.c_size_t),
                ("quota_peak_paged_pool_bytes", ctypes.c_size_t), ("quota_paged_pool_bytes", ctypes.c_size_t),
                ("quota_peak_nonpaged_pool_bytes", ctypes.c_size_t), ("quota_nonpaged_pool_bytes", ctypes.c_size_t),
                ("pagefile_bytes", ctypes.c_size_t), ("peak_pagefile_bytes", ctypes.c_size_t),
                ("private_bytes", ctypes.c_size_t)]


def working_set_bytes(pid: int) -> int:
    """The current working set of this test's live llama-server process."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessMemoryCounters), wintypes.DWORD]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        raise OSError(ctypes.get_last_error(), f"could not inspect process {pid}")
    try:
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), f"could not read RAM for process {pid}")
        return int(counters.working_set_bytes)
    finally:
        kernel32.CloseHandle(handle)


async def wait_for_health(spec: ServerSpec, process: subprocess.Popen[bytes]) -> tuple[EngineClient, float]:
    """Wait until this process's model is ready, returning its client and measured load time."""
    started = time.monotonic()
    client = EngineClient(spec.url, slot=0, model=spec.alias, timeout_s=30.0)
    deadline = started + LOAD_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            await client.close()
            raise AssertionError(f"{spec.node} ended while loading (exit {process.returncode})")
        if (await client.health()).ready:
            return client, time.monotonic() - started
        await asyncio.sleep(0.5)
    await client.close()
    raise AssertionError(f"{spec.node} was not ready within {LOAD_TIMEOUT_S:g} s")


def adapter_reading(probe: Probe) -> list[dict[str, int | str | None]]:
    """The resource monitor's current VRAM reading for every adapter."""
    return [{"name": gpu.name, "vram_total_bytes": gpu.vram_total_bytes, "vram_used_bytes": gpu.vram_used_bytes}
            for gpu in probe.sample().gpus]


def tool_call_capability(props: dict[str, Any]) -> dict[str, Any]:
    """What `/props` says about chat-template tool calls, preserving an absent explicit capability honestly."""
    for key in ("chat_template_capabilities", "chat_template_caps"):
        if key in props:
            return {"source": f"/props.{key}", "value": props[key]}
    template = props.get("chat_template")
    if not isinstance(template, str):
        settings = props.get("default_generation_settings")
        template = settings.get("chat_template") if isinstance(settings, dict) else ""
    return {"source": "template text", "handles_tools": "tools" in template.lower() if isinstance(template, str) else False}


def start_server(spec: ServerSpec) -> tuple[subprocess.Popen[bytes], Job, IO[bytes]]:
    """Start one server in a kill-on-close job and place its native process under the RAM guard."""
    D3_LOG.parent.mkdir(parents=True, exist_ok=True)
    output = (D3_LOG.parent / f"d3_{spec.node}.out").open("wb")
    process: subprocess.Popen[bytes] | None = None
    job: Job | None = None
    try:
        process, job = start_in_job(server_command(spec), cwd=ROOT, env=None, output=output, below_normal=True)
        register_guarded(process.pid, f"d3-llama-{spec.node}")
        assert any(entry["pid"] == process.pid for entry in guarded_processes())
        return process, job, output
    except Exception:
        if job is not None:
            job.terminate()
            job.close()
        elif process is not None:
            process.terminate()
            process.wait(timeout=30)
        if process is not None:
            with contextlib.suppress(OSError, TimeoutError):
                unregister_guarded(process.pid)
        output.close()
        raise


def stop_server(process: subprocess.Popen[bytes], job: Job, output: IO[bytes]) -> None:
    """End one server started by this test and prove its PID no longer exists."""
    try:
        job.terminate()
        process.wait(timeout=30)
        assert not pid_alive(process.pid), f"llama-server PID {process.pid} remained alive"
    finally:
        job.close()
        output.close()
        with contextlib.suppress(OSError, TimeoutError):
            unregister_guarded(process.pid)


@pytest.mark.deferred
@pytest.mark.asyncio
async def test_each_node_llm_server_loads_chats_and_records_resources() -> None:
    """D3: load each node server in turn, retaining all four to measure the complete independent topology."""
    if sys.platform != "win32":
        pytest.skip("D3's llama.cpp server and resource monitor run on Windows")
    specs = [server_spec(node) for node in NODE_IDS]
    if not LLAMA_SERVER.is_file() or not all(spec.model.is_file() for spec in specs):
        pytest.skip("llama.cpp or one of the approved model files is not installed")
    probe = Probe()
    running: list[tuple[subprocess.Popen[bytes], Job, IO[bytes]]] = []
    try:
        free_ram = probe.sample().ram_free_bytes
        if free_ram < 4 * GIB:
            pytest.skip(f"D3 needs at least 4 GiB free RAM; only {free_ram / GIB:.2f} GiB is free")
        measurements: list[dict[str, Any]] = []
        for spec in specs:
            process, job, output = start_server(spec)
            running.append((process, job, output))
            client, load_s = await wait_for_health(spec, process)
            try:
                props = await client.props()
                assert spec.model.name in json.dumps(props), f"{spec.node} /props did not name {spec.model.name}"
                result = await client.chat([{"role": "user", "content": "Reply only: ready."}], max_tokens=8,
                                           temperature=0.0, deadline_s=30.0)
            finally:
                await client.close()
            assert result.prompt_tokens is not None and result.prompt_ms is not None and result.prompt_ms > 0
            assert result.tokens_per_s is not None
            measurements.append({"node": spec.node, "pid": process.pid, "model": spec.model.name,
                                 "load_s": load_s, "server_working_set_bytes": working_set_bytes(process.pid),
                                 "prompt_tokens_per_s": result.prompt_tokens / (result.prompt_ms / 1_000),
                                 "generation_tokens_per_s": result.tokens_per_s,
                                 "tool_call_capability": tool_call_capability(props),
                                 "vram_after_load": adapter_reading(probe)})
        D3_LOG.write_text(json.dumps({"servers": measurements}, indent=2) + "\n", encoding="utf-8")
    finally:
        for process, job, output in reversed(running):
            stop_server(process, job, output)
        probe.close()
