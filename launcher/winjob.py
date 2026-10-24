"""Windows job objects for the supervisor: each program, and everything it starts, ends together (Q6).

A job made with `Job()` kills every process in it when the job is closed, including when the supervisor itself dies,
however it dies (the operating system closes its handles). `start_in_job()` starts a program suspended, puts it in
its job and only then lets it run, so even a child it starts at once (the venv's python.exe starts the real
interpreter) is inside the job. Ending a program means terminating its job: nothing of it is left behind.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
from collections.abc import Mapping, Sequence
from ctypes import wintypes
from pathlib import Path
from typing import IO

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
THREAD_SUSPEND_RESUME = 0x0002
TH32CS_SNAPTHREAD = 0x00000004
CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount",
                                                        "OtherOperationCount", "ReadTransferCount",
                                                        "WriteTransferCount", "OtherTransferCount")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]


def _kernel32() -> ctypes.WinDLL:
    if sys.platform != "win32":
        raise OSError("job objects exist only on Windows")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.TerminateJobObject.restype = wintypes.BOOL
    k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Thread32First.restype = wintypes.BOOL
    k32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    k32.Thread32Next.restype = wintypes.BOOL
    k32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    k32.OpenThread.restype = wintypes.HANDLE
    k32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.ResumeThread.restype = wintypes.DWORD
    k32.ResumeThread.argtypes = [wintypes.HANDLE]
    return k32


class Job:
    """One Windows job object whose processes all end when it is terminated or closed."""

    def __init__(self) -> None:
        self._k32 = _kernel32()
        handle = self._k32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._k32.SetInformationJobObject(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                                                 ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            self._k32.CloseHandle(handle)
            raise ctypes.WinError(error)
        self._handle: int | None = handle

    def assign(self, pid: int) -> None:
        """Put the process `pid` (and every process it starts from now on) in this job."""
        if self._handle is None:
            raise OSError("the job is closed")
        process = self._k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._k32.AssignProcessToJobObject(self._handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._k32.CloseHandle(process)

    def terminate(self, exit_code: int = 1) -> None:
        """End every process in the job now (the job itself stays open)."""
        if self._handle is not None:
            self._k32.TerminateJobObject(self._handle, exit_code)

    def close(self) -> None:
        """Close the job; any process still in it ends."""
        if self._handle is not None:
            self._k32.CloseHandle(self._handle)
            self._handle = None


def resume_process(pid: int) -> int:
    """Resume every thread of a process started suspended; returns how many threads were resumed."""
    k32 = _kernel32()
    snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    resumed = 0
    try:
        entry = _ThreadEntry()
        entry.dwSize = ctypes.sizeof(_ThreadEntry)
        more = k32.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                thread = k32.OpenThread(THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if thread:
                    try:
                        if k32.ResumeThread(thread) != 0xFFFFFFFF:
                            resumed += 1
                    finally:
                        k32.CloseHandle(thread)
            more = k32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snapshot)
    return resumed


def start_in_job(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None, output: IO[bytes],
                 below_normal: bool = False) -> tuple[subprocess.Popen[bytes], Job]:
    """Start `argv` in a new job (suspended until it is inside the job), writing its output to `output`.
    Raises OSError if the program cannot start; nothing is left running in that case."""
    flags = CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    if below_normal:
        flags |= BELOW_NORMAL_PRIORITY_CLASS
    process = subprocess.Popen(list(argv), cwd=str(cwd), env=dict(env) if env is not None else None,
                               stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, creationflags=flags)
    job: Job | None = None
    try:
        job = Job()
        job.assign(process.pid)
        if resume_process(process.pid) == 0:
            raise OSError(f"could not resume {argv[0]} (pid {process.pid})")
    except OSError:
        process.kill()
        process.wait(timeout=5)
        if job is not None:
            job.close()
        raise
    return process, job
