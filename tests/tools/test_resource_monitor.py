"""tools/resource_monitor.py: samples, limits, the RAM guard, the bounded log, single instance and stopping."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import resource_monitor
from resource_monitor import (GIB, ROOT, Adapter, DiskSample, GpuSample, History, JsonlLog, Probe, Sample, Thresholds,
                              _GuardLock, evaluate, guarded_processes, image_allowed, pid_alive, process_created,
                              record, register_guarded, request_stop, run, same_process, stop_guarded,
                              unregister_guarded, vram_used)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the monitor reads Windows counters")
BASE_PYTHON = getattr(sys, "_base_executable", sys.executable)    # the real interpreter, not the venv's launcher
SLEEPER = [BASE_PYTHON, "-c", "import time; time.sleep(60)"]
T0 = datetime(2026, 10, 23, tzinfo=timezone.utc)


def _sample(seconds: float = 0.0, **changes: object) -> Sample:
    base: dict[str, object] = dict(time_utc=(T0 + timedelta(seconds=seconds)).isoformat(timespec="seconds"),
                                   cpu_pct=40.0, ram_total_bytes=16 * GIB, ram_free_bytes=6 * GIB,
                                   commit_limit_bytes=30 * GIB, commit_free_bytes=10 * GIB,
                                   disks=[DiskSample("C:\\", 12 * GIB), DiskSample("F:\\", 200 * GIB)],
                                   gpus=[GpuSample("AMD Radeon RX 580 2048SP", 8 * GIB, 1 * GIB)])
    base.update(changes)
    return Sample(**base)  # type: ignore[arg-type]


def test_a_calm_sample_gives_no_warning() -> None:
    assert evaluate(_sample(), Thresholds(), History()) == []


def test_brief_ram_dips_are_not_a_warning_but_30_seconds_low_is() -> None:
    limits, history = Thresholds(), History()
    low, high = int(1.0 * GIB), int(6.5 * GIB)
    pattern = [(0, low), (10, high), (20, low), (30, low), (40, low), (50, low)]   # the training dips, then a squeeze
    results = [evaluate(_sample(t, ram_free_bytes=free), limits, history) for t, free in pattern]
    assert [bool(r) for r in results] == [False, False, False, False, False, True]
    assert "for 30 s" in results[-1][0]


def test_fast_sampling_needs_the_same_30_seconds() -> None:
    limits, history = Thresholds(), History()
    results = [evaluate(_sample(t, ram_free_bytes=GIB), limits, history) for t in range(0, 31, 2)]
    assert not any(results[:-1]) and results[-1]                       # 2 s samples: still 30 s, not 3 samples


def test_low_commit_headroom_warns_at_once() -> None:
    warnings = evaluate(_sample(commit_free_bytes=int(0.5 * GIB)), Thresholds(), History())
    assert len(warnings) == 1 and "commit headroom" in warnings[0]


def test_full_vram_and_low_disk_each_warn() -> None:
    warnings = evaluate(_sample(disks=[DiskSample("C:\\", 2 * GIB), DiskSample("F:\\", 1 * GIB)],
                                gpus=[GpuSample("RX 580", 8 * GIB, int(7.6 * GIB))]), Thresholds(), History())
    assert len(warnings) == 3
    assert any("VRAM 95%" in w for w in warnings) and sum("free space" in w for w in warnings) == 2


def test_cpu_warns_only_after_three_high_samples_in_a_row() -> None:
    limits, history = Thresholds(), History()
    results = [evaluate(_sample(cpu_pct=cpu), limits, history) for cpu in (99.0, 99.0, 50.0, 99.0, 99.0, 99.0)]
    assert [bool(r) for r in results] == [False, False, False, False, False, True]


def test_history_is_bounded() -> None:
    history = History()
    for _ in range(100):
        evaluate(_sample(), Thresholds(), history)
    assert len(history.ram_free) == 16 and len(history.cpu) == 16


def test_an_unknown_vram_reading_never_warns() -> None:
    assert evaluate(_sample(gpus=[GpuSample("RX 580", 8 * GIB, None)]), Thresholds(), History()) == []


def test_vram_is_matched_to_its_adapter_by_luid() -> None:
    adapter = Adapter("AMD Radeon RX 580 2048SP", 8 * GIB, luid_high=0, luid_low=0xD1C3)
    readings = {"luid_0x00000000_0x0000D1C3_phys_0": 1_000, "luid_0x00000000_0x0000AAAA_phys_0": 5_000}
    assert vram_used(adapter, readings) == 1_000
    assert vram_used(Adapter("other", 1, 0, 0x1234), readings) is None


def test_the_log_rotates_and_stays_bounded(tmp_path: Path) -> None:
    log = JsonlLog(tmp_path / "logs" / "monitor.jsonl", max_bytes=400)
    for k in range(40):
        log.write({"k": k, "padding": "x" * 30})
    current, old = tmp_path / "logs" / "monitor.jsonl", tmp_path / "logs" / "monitor.jsonl.1"
    assert old.exists() and current.stat().st_size < 500 and old.stat().st_size < 500


def test_a_record_is_flat_json_with_its_level() -> None:
    line = record(_sample(), ["free RAM low"])
    assert line["level"] == "warning" and line["warnings"] == ["free RAM low"]
    json.dumps(line)


def test_only_project_programs_and_its_own_python_are_allowed() -> None:
    roots = (Path(r"C:\Proj\JARVIS"), Path(r"C:\Py312"))
    assert image_allowed(r"C:\Proj\JARVIS\third_party\llama.cpp\bin\llama-server.exe", roots)
    assert image_allowed(r"c:\py312\python.exe", roots)
    assert not image_allowed(r"C:\Py314\python.exe", roots)                 # another Python, such as the training's
    assert not image_allowed(r"C:\Proj\JARVIS-other\x.exe", roots)          # a prefix of the name is not inside
    assert not image_allowed("", roots)


def test_the_guard_lock_waits_and_breaks_stale_locks(tmp_path: Path) -> None:
    registry = tmp_path / "guarded.json"
    with _GuardLock(registry):
        with pytest.raises(TimeoutError):
            with _GuardLock(registry, timeout_s=0.2):
                pass
    lock = registry.with_name("guarded.json.lock")
    lock.write_text("")
    old = lock.stat().st_mtime - 120
    os.utime(lock, (old, old))
    with _GuardLock(registry, timeout_s=0.5, stale_s=30.0):                # a crashed writer's lock is cleared
        pass
    assert not lock.exists()


@windows_only
def test_a_real_sample_of_this_machine() -> None:
    probe = Probe()
    sample = probe.sample()
    probe.close()
    assert sample.ram_total_bytes > 4 * GIB and 0 < sample.ram_free_bytes < sample.ram_total_bytes
    assert sample.commit_limit_bytes >= sample.ram_total_bytes and sample.commit_free_bytes > 0
    assert sample.disks and sample.disks[0].path == "C:\\" and sample.disks[0].free_bytes > 0
    assert sample.cpu_pct is None or 0.0 <= sample.cpu_pct <= 100.0
    if sample.gpu_error is None:
        assert all(gpu.vram_total_bytes > 0 for gpu in sample.gpus)


@windows_only
def test_process_identity_survives_pid_reuse_checks() -> None:
    me = process_created(os.getpid())
    assert me is not None and same_process(os.getpid(), me) and not same_process(os.getpid(), me + 1)
    child = subprocess.Popen([BASE_PYTHON, "-c", "pass"])
    child.wait()
    assert not pid_alive(child.pid) and process_created(child.pid) is None


@windows_only
def test_the_ram_guard_stops_only_registered_processes(tmp_path: Path) -> None:
    guard = tmp_path / "guarded.json"
    ours, bystander = subprocess.Popen(SLEEPER), subprocess.Popen(SLEEPER)
    try:
        register_guarded(ours.pid, "test llm server", guard)
        assert [e["name"] for e in guarded_processes(guard)] == ["test llm server"]
        stopped = stop_guarded("test", guard)
        assert [e["pid"] for e in stopped] == [ours.pid]
        assert ours.wait(timeout=10) is not None
        assert bystander.poll() is None                      # never registered: untouched
        assert guarded_processes(guard) == []
    finally:
        for process in (ours, bystander):
            process.kill()
            process.wait()


@windows_only
def test_a_program_outside_the_project_cannot_register(tmp_path: Path) -> None:
    outsider = subprocess.Popen(["cmd.exe", "/c", "ping -n 30 127.0.0.1 >nul"])
    try:
        with pytest.raises(PermissionError):
            register_guarded(outsider.pid, "not ours", tmp_path / "guarded.json")
        assert guarded_processes(tmp_path / "guarded.json") == []
    finally:
        outsider.kill()
        outsider.wait()


@windows_only
def test_a_reused_pid_or_a_foreign_program_is_never_stopped(tmp_path: Path) -> None:
    guard = tmp_path / "guarded.json"
    ours, outsider = subprocess.Popen(SLEEPER), subprocess.Popen(["cmd.exe", "/c", "ping -n 30 127.0.0.1 >nul"])
    try:
        created, foreign = process_created(ours.pid), process_created(outsider.pid)
        assert created is not None and foreign is not None
        guard.write_text(json.dumps({"processes": [
            {"pid": ours.pid, "created": created - 1, "name": "stale"},          # wrong creation time: a reused id
            {"pid": outsider.pid, "created": foreign, "name": "forged"}]}))       # right identity, wrong program
        assert stop_guarded("test", guard) == []
        assert ours.poll() is None and outsider.poll() is None
    finally:
        for process in (ours, outsider):
            process.kill()
            process.wait()


@windows_only
def test_unregister_and_malformed_entries(tmp_path: Path) -> None:
    guard = tmp_path / "guarded.json"
    other = subprocess.Popen(SLEEPER)
    try:
        register_guarded(other.pid, "x", guard)
        unregister_guarded(other.pid, guard)
        assert guarded_processes(guard) == []
        guard.write_text('{"processes": [{"pid": "nope"}, 7, {"pid": 1, "created": 2, "name": "ok"}]}')
        assert [e["name"] for e in guarded_processes(guard)] == ["ok"]
        guard.write_text("not json")
        assert guarded_processes(guard) == []
    finally:
        other.kill()
        other.wait()


class _FakeProbe:
    """Stands in for Probe: one sample per call, from a list."""

    def __init__(self, samples: list[Sample | Exception]) -> None:
        self.samples = samples

    def sample(self) -> Sample:
        item = self.samples.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        pass


@windows_only
def test_run_survives_a_failed_sample_guards_ram_and_stops_on_request(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    guard, stop, log_path = tmp_path / "guarded.json", tmp_path / "monitor.stop", tmp_path / "log.jsonl"
    ours = subprocess.Popen(SLEEPER)
    try:
        register_guarded(ours.pid, "test llm server", guard)
        low = int(1.0 * GIB)
        samples: list[Sample | Exception] = [_sample(0), OSError("disk vanished"), _sample(20, ram_free_bytes=low),
                                             _sample(30, ram_free_bytes=low), _sample(40, ram_free_bytes=low),
                                             _sample(50, ram_free_bytes=low)]
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) == 7:
                stop.write_text("stop")

        monkeypatch.setattr(resource_monitor.time, "sleep", fake_sleep)
        run(10.0, JsonlLog(log_path), Thresholds(), guard_path=guard, stop_path=stop,
            probe_factory=lambda: _FakeProbe(samples))  # type: ignore[arg-type, return-value]
        lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert [line["level"] for line in lines] == ["info", "error", "info", "info", "info", "warning", "action", "info"]
        assert lines[1]["error"].startswith("OSError") and lines[6]["stopped"][0]["pid"] == ours.pid
        assert lines[-1]["event"] == "stopped on request" and not stop.exists()
        assert sleeps[:6] == [2.0] * 6 and sleeps[6] == 10.0     # fast while something is registered, then normal
        assert ours.wait(timeout=10) is not None
    finally:
        ours.kill()
        ours.wait()


def test_a_too_short_interval_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        resource_monitor.main(["--interval", "0"])
    assert "at least 1" in capsys.readouterr().err


@windows_only
def test_main_refuses_to_start_twice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    pid_file = tmp_path / "resource_monitor.pid"
    monkeypatch.setattr(resource_monitor, "PID_PATH", pid_file)
    other = subprocess.Popen(SLEEPER)
    try:
        pid_file.write_text(json.dumps({"pid": other.pid, "created": process_created(other.pid)}))
        assert resource_monitor.main(["--log", str(tmp_path / "log.jsonl")]) == 3
        assert "already running" in capsys.readouterr().err
    finally:
        other.kill()
        other.wait()


@windows_only
def test_stop_request_with_no_monitor_running_cleans_a_stale_pid_file(tmp_path: Path) -> None:
    pid_file = tmp_path / "resource_monitor.pid"
    pid_file.write_text(json.dumps({"pid": os.getpid(), "created": 1}))     # a stale record: wrong creation time
    assert request_stop(1.0, pid_path=pid_file, stop_path=tmp_path / "stop") and not pid_file.exists()


@windows_only
def test_a_real_monitor_starts_logs_and_stops_on_request(tmp_path: Path) -> None:
    """End to end in a temporary copy of the tools folder, so the project's own monitor is never touched."""
    tools = tmp_path / "tools"
    tools.mkdir()
    script = Path(resource_monitor.__file__)
    (tools / "resource_monitor.py").write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    monitor = subprocess.Popen([sys.executable, str(tools / "resource_monitor.py"), "--interval", "1"])
    try:
        pid_file, log = tmp_path / "logs" / "resource_monitor.pid", tmp_path / "logs" / "resource_monitor.jsonl"
        for _ in range(100):
            if log.exists() and log.read_text(encoding="utf-8").count("\n") >= 2:
                break
            subprocess.run([BASE_PYTHON, "-c", "import time; time.sleep(0.1)"], check=True)
        assert pid_file.exists() and log.read_text(encoding="utf-8").count("\n") >= 2
        stop = subprocess.run([sys.executable, str(tools / "resource_monitor.py"), "--stop", "--interval", "1"],
                              capture_output=True, text=True, timeout=60)
        assert stop.returncode == 0, stop.stderr
        assert monitor.wait(timeout=30) == 0 and not pid_file.exists()
    finally:
        monitor.kill()
        monitor.wait()


@windows_only
def test_once_prints_one_json_sample() -> None:
    script = ROOT / "tools" / "resource_monitor.py"
    result = subprocess.run([sys.executable, str(script), "--once"], capture_output=True, text=True, timeout=60,
                            check=True)
    data = json.loads(result.stdout)
    assert data["level"] in ("info", "warning") and "commit_free_bytes" in data and isinstance(data["disks"], list)
