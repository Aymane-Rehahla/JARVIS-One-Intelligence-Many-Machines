"""Log CPU, RAM, commit, VRAM and free disk space every 10 s; warn near the limits; guard RAM (plan R2, ground rule 1).

Each sample is one JSON line in logs/resource_monitor.jsonl (rotated at 10 MB, one old file kept). Samples that cross a
limit carry "level": "warning". The monitor runs at below-normal priority and refuses to start twice.

**RAM guard.** Programs we start that may use a lot of memory (an LLM server, a voice model test) register themselves
in logs/guarded.json (`register_guarded`); only programs inside this project or this project's own Python can register.
When free RAM stays below 2 GiB for 30 s, or the commit headroom falls below 1 GiB, the monitor stops every registered
process (checked and stopped through one handle) and logs it. While anything is registered it samples every 2 s. It
never touches anything that is not registered: the Phase 5 training runs on another Python and can never qualify.

Usage:
    python tools/resource_monitor.py            run until stopped
    python tools/resource_monitor.py --once     print one sample as JSON and exit
    python tools/resource_monitor.py --stop     ask the running monitor to stop, and wait for it

Windows only; standard library only (GetSystemTimes, GlobalMemoryStatusEx, DXGI, PDH, GetProcessTimes).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sys
import time
from collections import deque
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
LOG_PATH = LOGS / "resource_monitor.jsonl"
PID_PATH = LOGS / "resource_monitor.pid"
STOP_PATH = LOGS / "resource_monitor.stop"
GUARD_PATH = LOGS / "guarded.json"
DEFAULT_DISKS = ("C:\\", "F:\\")
GIB = 1024 ** 3
MIN_INTERVAL_S = 1.0
GUARDED_INTERVAL_S = 2.0        # sample faster while a registered program could need stopping


@dataclass(frozen=True)
class Thresholds:
    """When a sample becomes a warning, and when the RAM guard acts (ground rule 1: about 2 GB free)."""

    min_free_ram_bytes: int = 2 * GIB
    ram_sustain_s: float = 30.0     # free RAM must stay low this long (the training's own dips last a few seconds)
    min_commit_free_bytes: int = 1 * GIB
    max_vram_fraction: float = 0.90
    min_free_disk_bytes: int = 3 * GIB
    max_cpu_pct: float = 95.0
    cpu_samples: int = 3


@dataclass
class GpuSample:
    """One display adapter's dedicated video memory."""

    name: str
    vram_total_bytes: int
    vram_used_bytes: int | None


@dataclass
class DiskSample:
    """Free space on one drive."""

    path: str
    free_bytes: int


@dataclass
class Sample:
    """One measurement of the whole machine."""

    time_utc: str
    cpu_pct: float | None
    ram_total_bytes: int
    ram_free_bytes: int
    commit_limit_bytes: int
    commit_free_bytes: int
    disks: list[DiskSample] = field(default_factory=list[DiskSample])
    gpus: list[GpuSample] = field(default_factory=list[GpuSample])
    gpu_error: str | None = None


@dataclass
class History:
    """The latest readings that sustained limits look at (bounded, Q4)."""

    ram_free: deque[int] = field(default_factory=lambda: deque[int](maxlen=16))
    cpu: deque[float] = field(default_factory=lambda: deque[float](maxlen=16))
    ram_low_since: datetime | None = None          # when free RAM last went below the limit, None while above it
    ram_low_for_s: float = 0.0


def _sample_time(sample: Sample) -> datetime:
    return datetime.fromisoformat(sample.time_utc)


def ram_guard_reason(sample: Sample, limits: Thresholds, history: History) -> str | None:
    """Why the RAM guard must act now, or None. Call after `evaluate` has added this sample to the history."""
    if history.ram_low_since is not None and history.ram_low_for_s >= limits.ram_sustain_s:
        return (f"free RAM below {limits.min_free_ram_bytes / GIB:.1f} GiB for {history.ram_low_for_s:.0f} s "
                f"(now {sample.ram_free_bytes / GIB:.2f} GiB)")
    if sample.commit_free_bytes < limits.min_commit_free_bytes:
        return f"commit headroom {sample.commit_free_bytes / GIB:.2f} GiB, below {limits.min_commit_free_bytes / GIB:.1f} GiB"
    return None


def evaluate(sample: Sample, limits: Thresholds, history: History) -> list[str]:
    """Add the sample to the history and return its warnings."""
    history.ram_free.append(sample.ram_free_bytes)
    if sample.ram_free_bytes < limits.min_free_ram_bytes:
        now = _sample_time(sample)
        history.ram_low_since = history.ram_low_since or now
        history.ram_low_for_s = (now - history.ram_low_since).total_seconds()
    else:
        history.ram_low_since, history.ram_low_for_s = None, 0.0
    if sample.cpu_pct is not None:
        history.cpu.append(sample.cpu_pct)
    warnings: list[str] = []
    guard = ram_guard_reason(sample, limits, history)
    if guard:
        warnings.append(guard)
    for gpu in sample.gpus:
        if gpu.vram_used_bytes is not None and gpu.vram_total_bytes > 0:
            fraction = gpu.vram_used_bytes / gpu.vram_total_bytes
            if fraction > limits.max_vram_fraction:
                warnings.append(f"{gpu.name}: VRAM {fraction:.0%} used, above {limits.max_vram_fraction:.0%}")
    for disk in sample.disks:
        if disk.free_bytes < limits.min_free_disk_bytes:
            warnings.append(f"{disk.path} free space {disk.free_bytes / GIB:.2f} GiB is below "
                            f"{limits.min_free_disk_bytes / GIB:.1f} GiB")
    last_cpu = list(history.cpu)[-limits.cpu_samples:]
    if sample.cpu_pct is not None and len(last_cpu) == limits.cpu_samples and \
            all(value > limits.max_cpu_pct for value in last_cpu):
        warnings.append(f"CPU above {limits.max_cpu_pct:.0f} % for {limits.cpu_samples} samples in a row")
    return warnings


class JsonlLog:
    """Append-only JSON-lines file, rotated to `<name>.1` when it passes `max_bytes` (Q4: nothing grows without limit)."""

    def __init__(self, path: Path, max_bytes: int = 10 * 1024 * 1024) -> None:
        self.path, self.max_bytes = path, max_bytes
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, object]) -> None:
        if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
            os.replace(self.path, self.path.with_name(self.path.name + ".1"))
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


# ---------------------------------------------------------------------------------------------- Windows probes
class _FileTime(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    def value(self) -> int:
        return (self.high << 32) | self.low


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _Luid(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _AdapterDesc1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", _Luid), ("Flags", ctypes.c_uint)]


class _Guid(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class _PdhValue(ctypes.Union):
    _fields_ = [("longValue", ctypes.c_long), ("doubleValue", ctypes.c_double), ("largeValue", ctypes.c_longlong)]


class _PdhFmtValue(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("value", _PdhValue)]


class _PdhItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _PdhFmtValue)]


_IID_IDXGIFactory1 = _Guid(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
_DXGI_ERROR_NOT_FOUND = ctypes.c_long(0x887A0002).value
_DXGI_ADAPTER_FLAG_SOFTWARE = 2
_PDH_FMT_LARGE = 0x00000400
_PDH_MORE_DATA = 0x800007D2
_VRAM_COUNTER = "\\GPU Adapter Memory(*)\\Dedicated Usage"
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_STILL_ACTIVE = 259
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def _kernel32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _com_method(obj: ctypes.c_void_p, index: int, *argtypes: Any) -> Callable[..., int]:
    """Return method `index` of a COM object's vtable, callable as f(obj, *args) -> HRESULT."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return cast(Callable[..., int], prototype(vtable[index]))


@dataclass(frozen=True)
class Adapter:
    """A hardware display adapter as DXGI reports it."""

    name: str
    vram_total_bytes: int
    luid_high: int
    luid_low: int

    @property
    def pdh_prefix(self) -> str:
        """The start of this adapter's instance name in the GPU performance counters."""
        return f"luid_0x{self.luid_high & 0xFFFFFFFF:08x}_0x{self.luid_low:08x}"


def list_adapters() -> list[Adapter]:
    """Hardware display adapters with their dedicated video memory (DXGI; software adapters left out)."""
    dxgi = ctypes.WinDLL("dxgi")
    factory = ctypes.c_void_p()
    if dxgi.CreateDXGIFactory1(ctypes.byref(_IID_IDXGIFactory1), ctypes.byref(factory)) != 0:
        raise OSError("CreateDXGIFactory1 failed")
    adapters: list[Adapter] = []
    try:
        enum_adapters1 = _com_method(factory, 12, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        index = 0
        while True:
            adapter = ctypes.c_void_p()
            result = enum_adapters1(factory, index, ctypes.byref(adapter))
            if result == _DXGI_ERROR_NOT_FOUND:
                break
            if result != 0:
                raise OSError(f"EnumAdapters1 failed with HRESULT {result & 0xFFFFFFFF:#010x}")
            try:
                desc = _AdapterDesc1()
                if _com_method(adapter, 10, ctypes.POINTER(_AdapterDesc1))(adapter, ctypes.byref(desc)) == 0 \
                        and not desc.Flags & _DXGI_ADAPTER_FLAG_SOFTWARE:
                    adapters.append(Adapter(desc.Description, int(desc.DedicatedVideoMemory),
                                            int(desc.AdapterLuid.HighPart), int(desc.AdapterLuid.LowPart)))
            finally:
                _com_method(adapter, 2)(adapter)      # Release
            index += 1
    finally:
        _com_method(factory, 2)(factory)              # Release
    return adapters


class VramCounter:
    """Reads dedicated VRAM in use per adapter from the Windows GPU performance counters (PDH)."""

    def __init__(self) -> None:
        self._pdh = ctypes.WinDLL("pdh")
        for name in ("PdhOpenQueryW", "PdhAddEnglishCounterW", "PdhCollectQueryData", "PdhGetFormattedCounterArrayW",
                     "PdhCloseQuery"):
            getattr(self._pdh, name).restype = wintypes.DWORD
        self._query, self._counter = wintypes.HANDLE(), wintypes.HANDLE()
        if self._pdh.PdhOpenQueryW(None, None, ctypes.byref(self._query)) != 0:
            raise OSError("PdhOpenQueryW failed")
        if self._pdh.PdhAddEnglishCounterW(self._query, _VRAM_COUNTER, None, ctypes.byref(self._counter)) != 0:
            raise OSError(f"cannot read the counter {_VRAM_COUNTER}")

    def read(self) -> dict[str, int]:
        """Return {counter instance name: dedicated bytes in use}."""
        if self._pdh.PdhCollectQueryData(self._query) != 0:
            return {}
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        status = self._pdh.PdhGetFormattedCounterArrayW(self._counter, _PDH_FMT_LARGE, ctypes.byref(size),
                                                        ctypes.byref(count), None)
        if status != _PDH_MORE_DATA or size.value == 0:
            return {}
        buffer = (ctypes.c_byte * size.value)()
        if self._pdh.PdhGetFormattedCounterArrayW(self._counter, _PDH_FMT_LARGE, ctypes.byref(size),
                                                  ctypes.byref(count), buffer) != 0:
            return {}
        items = ctypes.cast(buffer, ctypes.POINTER(_PdhItem))
        return {str(items[k].szName): int(items[k].FmtValue.value.largeValue) for k in range(count.value)}

    def close(self) -> None:
        self._pdh.PdhCloseQuery(self._query)


def vram_used(adapter: Adapter, readings: dict[str, int]) -> int | None:
    """Sum the counter instances that belong to this adapter; None when the adapter has no counter."""
    matches = [value for name, value in readings.items() if name.lower().startswith(adapter.pdh_prefix)]
    return sum(matches) if matches else None


def cpu_times() -> tuple[int, int]:
    """Return (idle, total) CPU time in 100 ns units, summed over all cores."""
    idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
    if not _kernel32().GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        raise OSError("GetSystemTimes failed")
    return idle.value(), kernel.value() + user.value()      # kernel time includes idle time


def cpu_percent(before: tuple[int, int], after: tuple[int, int]) -> float | None:
    """CPU use between two `cpu_times()` readings, in percent."""
    idle, total = after[0] - before[0], after[1] - before[1]
    return None if total <= 0 else round(100.0 * (1.0 - idle / total), 1)


def memory() -> tuple[int, int, int, int]:
    """Return (total RAM, available RAM, commit limit, commit available) in bytes."""
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not _kernel32().GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    return (int(status.ullTotalPhys), int(status.ullAvailPhys), int(status.ullTotalPageFile),
            int(status.ullAvailPageFile))


def disks(paths: Iterable[str]) -> list[DiskSample]:
    """Free space of each existing drive in `paths`."""
    return [DiskSample(path, shutil.disk_usage(path).free) for path in paths if os.path.exists(path)]


class Probe:
    """Takes samples. GPU problems are reported in the sample (gpu_error), never raised."""

    def __init__(self, disk_paths: Iterable[str] = DEFAULT_DISKS) -> None:
        self.disk_paths = tuple(disk_paths)
        self._previous_cpu = cpu_times()
        self._adapters: list[Adapter] = []
        self._vram: VramCounter | None = None
        self._gpu_error: str | None = None
        try:
            self._adapters = list_adapters()
            self._vram = VramCounter()
        except OSError as error:
            self._gpu_error = str(error)

    def sample(self) -> Sample:
        now = cpu_times()
        cpu, self._previous_cpu = cpu_percent(self._previous_cpu, now), now
        total, free, commit_limit, commit_free = memory()
        readings = self._vram.read() if self._vram else {}
        gpus = [GpuSample(a.name, a.vram_total_bytes, vram_used(a, readings)) for a in self._adapters]
        return Sample(datetime.now(timezone.utc).isoformat(timespec="seconds"), cpu, total, free, commit_limit,
                      commit_free, disks(self.disk_paths), gpus, self._gpu_error)

    def close(self) -> None:
        if self._vram:
            self._vram.close()


# ---------------------------------------------------------------------------------------------- processes
def _identity(handle: Any) -> tuple[int, str] | None:
    """(creation time, image path) of a live process, read through one open handle; None if it has exited."""
    kernel32 = _kernel32()
    code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
        return None
    created, exited, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
    if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel),
                                    ctypes.byref(user)):
        return None
    buffer, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
    image = buffer.value if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else ""
    return created.value(), image


def _process_identity(pid: int) -> tuple[int, str] | None:
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
    if not handle:
        return None
    try:
        return _identity(handle)
    finally:
        kernel32.CloseHandle(handle)


def process_created(pid: int) -> int | None:
    """The process's creation time (FILETIME units), or None when no such process is running."""
    identity = _process_identity(pid)
    return identity[0] if identity else None


def same_process(pid: int, created: int) -> bool:
    """True when `pid` is running and is the very process created at `created` (not a reused id)."""
    return process_created(pid) == created


def pid_alive(pid: int) -> bool:
    """True when a process with this id is running."""
    return process_created(pid) is not None


def allowed_image_roots() -> tuple[Path, ...]:
    """Where the programs the RAM guard may stop live: this project, and the base interpreter of its own .venv.

    The Phase 5 training runs on a different Python, so none of its processes can ever qualify.
    """
    roots = [ROOT]
    config = ROOT / ".venv" / "pyvenv.cfg"
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "home" and value.strip():
                roots.append(Path(value.strip()))
    except OSError:
        pass
    return tuple(roots)


def image_allowed(image: str, roots: tuple[Path, ...] | None = None) -> bool:
    """True when an executable lives under one of the allowed roots (case-insensitive, Windows paths)."""
    if not image:
        return False
    normalised = os.path.normcase(os.path.abspath(image))
    for root in roots if roots is not None else allowed_image_roots():
        prefix = os.path.normcase(os.path.abspath(root)).rstrip("\\/") + os.sep
        if normalised.startswith(prefix):
            return True
    return False


def terminate(pid: int, created: int) -> bool:
    """Stop the process only if it is still the registered one and an allowed program.

    The identity check and the stop use one handle, so a process id reused in between can never be hit.
    """
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        identity = _identity(handle)
        if identity is None or identity[0] != created or not image_allowed(identity[1]):
            return False
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


class _GuardLock:
    """A lock file next to the registry, so concurrent registrations can't lose entries (bounded wait, Q4)."""

    def __init__(self, path: Path, timeout_s: float = 5.0, stale_s: float = 30.0) -> None:
        self.lock = path.with_name(path.name + ".lock")
        self.timeout_s, self.stale_s = timeout_s, stale_s

    def __enter__(self) -> "_GuardLock":
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                os.close(os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - self.lock.stat().st_mtime > self.stale_s:
                        self.lock.unlink(missing_ok=True)          # left behind by a crashed writer
                        continue
                except OSError:
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"the RAM-guard registry is locked: {self.lock}") from None
                time.sleep(0.05)

    def __exit__(self, *_: object) -> None:
        self.lock.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def register_guarded(pid: int, name: str, path: Path | None = None) -> None:
    """Put a process we started under the RAM guard (stopped if RAM stays too low).

    Raises ProcessLookupError if it isn't running and PermissionError if its program lives outside the allowed roots
    (`allowed_image_roots`): a mistaken entry can never point at the training. A Python program registers itself with
    `os.getpid()`: the venv's `python.exe` is only a launcher, and stopping it would leave the real interpreter running.
    The 6A launcher starts native programs (llama-server, nats-server) directly and registers those.
    """
    path = path or GUARD_PATH
    identity = _process_identity(pid)
    if identity is None:
        raise ProcessLookupError(f"no running process {pid}")
    created, image = identity
    if not image_allowed(image):
        raise PermissionError(f"process {pid} ({image or 'unknown program'}) is not a JARVIS program")
    with _GuardLock(path):
        entries = [e for e in guarded_processes(path) if e["pid"] != pid]
        entries.append({"pid": pid, "created": created, "name": name, "image": image})
        _write_json(path, {"processes": entries})


def unregister_guarded(pid: int, path: Path | None = None) -> None:
    """Take a process off the RAM guard (it ended normally)."""
    path = path or GUARD_PATH
    with _GuardLock(path):
        _write_json(path, {"processes": [e for e in guarded_processes(path) if e["pid"] != pid]})


def guarded_processes(path: Path | None = None) -> list[dict[str, Any]]:
    """The registered processes; malformed entries are ignored."""
    raw = _read_json(path or GUARD_PATH).get("processes", [])
    entries = raw if isinstance(raw, list) else []
    return [e for e in entries if isinstance(e, dict) and isinstance(e.get("pid"), int)
            and isinstance(e.get("created"), int) and isinstance(e.get("name"), str)]


def stop_guarded(reason: str, path: Path | None = None) -> list[dict[str, Any]]:
    """Stop every registered process that is still the one registered; return what was stopped."""
    path = path or GUARD_PATH
    with _GuardLock(path):
        stopped = [entry | {"reason": reason} for entry in guarded_processes(path)
                   if terminate(entry["pid"], entry["created"])]
        _write_json(path, {"processes": [e for e in guarded_processes(path) if same_process(e["pid"], e["created"])]})
    return stopped


# ---------------------------------------------------------------------------------------------- running
def record(sample: Sample, warnings: Iterable[str]) -> dict[str, object]:
    """The JSON-lines record for one sample."""
    warning_list = list(warnings)
    return {"level": "warning" if warning_list else "info", "warnings": warning_list, **asdict(sample)}


def run(interval_s: float, log: JsonlLog, limits: Thresholds, *, guard_path: Path | None = None,
        stop_path: Path | None = None, probe_factory: Callable[[], Probe] = Probe) -> None:
    """Sample every `interval_s` seconds until the stop file appears. One failed sample never ends the monitor."""
    guard_path, stop_path = guard_path or GUARD_PATH, stop_path or STOP_PATH
    probe = probe_factory()
    history = History()
    try:
        while True:
            time.sleep(min(interval_s, GUARDED_INTERVAL_S) if guarded_processes(guard_path) else interval_s)
            if stop_path.exists():
                stop_path.unlink(missing_ok=True)
                log.write({"level": "info", "event": "stopped on request",
                           "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
                return
            try:
                sample = probe.sample()
                warnings = evaluate(sample, limits, history)
                log.write(record(sample, warnings))
                reason = ram_guard_reason(sample, limits, history)
                if reason:
                    stopped = stop_guarded(reason, guard_path)
                    if stopped:
                        log.write({"level": "action", "event": "RAM guard stopped processes", "reason": reason,
                                   "stopped": stopped, "time_utc": sample.time_utc})
            except Exception as error:  # noqa: BLE001 - the monitor must survive any single failure
                try:
                    log.write({"level": "error", "error": f"{type(error).__name__}: {error}",
                               "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
                except OSError:
                    pass
    finally:
        probe.close()


def request_stop(timeout_s: float, pid_path: Path | None = None, stop_path: Path | None = None) -> bool:
    """Ask the running monitor to stop (stop file) and wait until it has; True when no monitor is left running."""
    pid_path, stop_path = pid_path or PID_PATH, stop_path or STOP_PATH
    info = _read_json(pid_path)
    pid, created = info.get("pid"), info.get("created")
    if not (isinstance(pid, int) and isinstance(created, int) and same_process(pid, created)):
        pid_path.unlink(missing_ok=True)
        return True
    stop_path.parent.mkdir(parents=True, exist_ok=True)
    stop_path.write_text("stop", encoding="ascii")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not same_process(pid, created):
            return True
        time.sleep(0.5)
    return False


def lower_priority() -> None:
    """Run at below-normal priority, so the monitor never competes with the training."""
    kernel32 = _kernel32()
    kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), _BELOW_NORMAL_PRIORITY_CLASS)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="print one sample (CPU measured over 1 s) and exit")
    group.add_argument("--stop", action="store_true", help="stop the running monitor and wait for it")
    parser.add_argument("--interval", type=float, default=10.0, help="seconds between samples (at least 1, default 10)")
    parser.add_argument("--log", type=Path, default=LOG_PATH, help="JSON-lines log file")
    args = parser.parse_args(argv)
    if args.interval < MIN_INTERVAL_S:
        parser.error(f"--interval must be at least {MIN_INTERVAL_S:g} s")
    if sys.platform != "win32":
        print("resource_monitor.py runs on Windows only", file=sys.stderr)
        return 2
    limits = Thresholds()
    if args.once:
        probe = Probe()
        time.sleep(1.0)
        sample = probe.sample()
        probe.close()
        print(json.dumps(record(sample, evaluate(sample, limits, History())), indent=2))
        return 0
    if args.stop:
        stopped = request_stop(args.interval + 10.0)
        print("monitor stopped" if stopped else "the monitor did not stop in time", file=sys.stderr)
        return 0 if stopped else 1
    info = _read_json(PID_PATH)
    pid, created = info.get("pid"), info.get("created")
    if isinstance(pid, int) and isinstance(created, int) and pid != os.getpid() and same_process(pid, created):
        print(f"resource monitor already running (pid {pid})", file=sys.stderr)
        return 3
    own_created = process_created(os.getpid())
    _write_json(PID_PATH, {"pid": os.getpid(), "created": own_created})
    STOP_PATH.unlink(missing_ok=True)
    try:
        run(args.interval, JsonlLog(args.log), limits)
    except KeyboardInterrupt:
        pass
    finally:
        if _read_json(PID_PATH).get("pid") == os.getpid():
            PID_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        lower_priority()
    sys.exit(main())
