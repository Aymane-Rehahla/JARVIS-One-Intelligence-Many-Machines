"""The launcher's supervisor: starts every JARVIS program in order, waits for each one's health check, restarts any
program that ends (Q6), and stops them all in reverse order.

The programs (plan section 4.1), all on 127.0.0.1 with the ports of launcher/ports.toml:
    nats                 third_party/nats-server/bin/nats-server.exe -c nats.conf     ready when its port accepts
    llama-1, llama-2     the two llama.cpp servers (llm/engines.py); left out with --no-llm      GET /health is 200
    farm                 matrix/farm_runtime.py (the 6A test feed)                                GET /health is 200
    incubator-1 ...      nodes/<node>/runtime.py, one program per node                            running
    gateway              interface/gateway/gateway.py                                             GET /health is 200
    website              npm run dev in interface/web (only with --website)                       GET / is 200

Every program runs in its own Windows job object (winjob.py): ending or restarting a program ends everything it
started, and when the supervisor itself ends, however it ends, the operating system ends every program with it. The
supervisor registers itself with the resource monitor's RAM guard (decision 0011), so the guard stopping it stops all.
A program that ends, cannot start, or fails UNHEALTHY_CHECKS health checks in a row is restarted after 1, 2, 4 ... 30 s;
after MAX_RESTARTS restarts within RESTART_WINDOW_S it is given up (an error in the log) and stays stopped. Programs are ended by their job (a console signal cannot reach them
from a windowless supervisor); in step 6A none keeps anything that needs a clean shutdown. While the training runs the
programs start at below-normal priority (--normal-priority to change). The state of every program is in
logs/supervisor.status.json.

Usage (from the project folder):
    python launcher/supervisor.py [--no-llm] [--website] [--only nats,farm,...] [--ports FILE] [--logs DIR]
    python launcher/supervisor.py --stop          ask the running supervisor to stop everything, then wait
Exit codes: 0 stopped, 1 a program never became ready, 2 bad arguments or a supervisor already runs.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import socket
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Literal

LAUNCHER = Path(__file__).resolve().parent
ROOT = LAUNCHER.parent
REFERENCE_NODE = ROOT / "nodes" / "incubator-1"
for _path in (LAUNCHER, REFERENCE_NODE, ROOT / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import httpx  # noqa: E402

from comms.jsonlog import log, program_logger  # noqa: E402
from contracts import NODE_IDS, NodeId  # noqa: E402
from llm.engines import server_command, server_spec  # noqa: E402
from winjob import Job, start_in_job  # noqa: E402

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
NATS_SERVER = ROOT / "third_party" / "nats-server" / "bin" / "nats-server.exe"
NATS_CONF = ROOT / "third_party" / "nats-server" / "nats.conf"
NODE_JS = Path(r"C:\Users\Aymane\node\node-v24.22.1-win-x64")
MAX_RESTARTS = 5
RESTART_WINDOW_S = 600.0
BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
POLL_S = 0.5
HEALTH_EVERY_S = 5.0
UNHEALTHY_CHECKS = 3                    # health checks failed in a row before a program is restarted
OUTPUT_MAX_BYTES = 10_000_000
STOP_WAIT_S = 30.0

State = Literal["waiting", "starting", "ready", "unhealthy", "restarting", "failed", "stopped"]


@dataclass(frozen=True)
class Health:
    """How to tell a program is ready: its port accepts ("tcp"), a URL answers 200 ("http"), or it runs ("process")."""

    kind: Literal["tcp", "http", "process"]
    host: str = "127.0.0.1"
    port: int = 0
    url: str = ""


@dataclass(frozen=True)
class Program:
    """One program the supervisor runs."""

    name: str
    argv: tuple[str, ...]
    cwd: Path
    health: Health
    depends_on: tuple[str, ...] = ()
    ready_timeout_s: float = 30.0
    env: tuple[tuple[str, str], ...] = ()


@dataclass
class Running:
    """A program's live state."""

    program: Program
    state: State = "waiting"
    process: Any = None
    job: Job | None = None
    output: IO[bytes] | None = None
    pid: int | None = None
    started_at: float = 0.0
    since_utc: str = ""
    restarts: list[float] = field(default_factory=list[float])
    last_exit: int | None = None
    health_failures: int = 0


def utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_ports(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def build_programs(ports: dict[str, Any], *, llm: bool, website: bool, nodes: tuple[NodeId, ...] = NODE_IDS,
                   ports_file: Path = ROOT / "launcher" / "ports.toml") -> list[Program]:
    """Every program in start order, with explicit ports so no program has to read ports.toml itself."""
    host = str(ports["host"])
    nats_url = f"nats://{host}:{ports['nats']}"
    matrix_url = f"http://{host}:{ports['matrix_api']}"
    python = str(PYTHON)
    programs = [Program("nats", (str(NATS_SERVER), "-c", str(NATS_CONF), "-a", host, "-p", str(ports["nats"])), ROOT,
                        Health("tcp", host, int(ports["nats"])), ready_timeout_s=15.0)]
    if llm:
        servers: tuple[Literal[1, 2], ...] = (1, 2)
        for number in servers:
            spec = server_spec(number, ports_file)
            programs.append(Program(f"llama-{number}", tuple(server_command(spec)), ROOT,
                                    Health("http", url=f"{spec.url}/health"), ready_timeout_s=240.0))
    programs.append(Program("farm", (python, str(ROOT / "matrix" / "farm_runtime.py"), "--nats", nats_url, "--port",
                                     str(ports["matrix_api"])), ROOT, Health("http", url=f"{matrix_url}/health"),
                            depends_on=("nats",), ready_timeout_s=60.0))
    for node in nodes:
        programs.append(Program(node, (python, str(ROOT / "nodes" / node / "runtime.py"), "--nats", nats_url), ROOT,
                                Health("process"), depends_on=("nats",), ready_timeout_s=30.0))
    gateway_port = int(ports["gateway"])
    programs.append(Program("gateway", (python, str(ROOT / "interface" / "gateway" / "gateway.py"), "--nats", nats_url,
                                        "--matrix", matrix_url, "--port", str(gateway_port)), ROOT,
                            Health("http", url=f"http://{host}:{gateway_port}/health"), depends_on=("nats", "farm"),
                            ready_timeout_s=60.0))
    if website:
        npm = NODE_JS / "npm.cmd"
        programs.append(Program("website", (str(npm), "run", "dev", "--", "--host", host, "--port",
                                            str(ports["website"]), "--strictPort"), ROOT / "interface" / "web",
                                Health("http", url=f"http://{host}:{ports['website']}/"), depends_on=("gateway",),
                                ready_timeout_s=90.0, env=(("PATH", f"{NODE_JS};{os.environ.get('PATH', '')}"),)))
    return programs


class Supervisor:
    """Runs a list of programs (see the module docstring)."""

    def __init__(self, programs: list[Program], *, logs: Path, logger: logging.Logger, below_normal: bool = True,
                 backoff_s: tuple[float, ...] = BACKOFF_S, max_restarts: int = MAX_RESTARTS,
                 restart_window_s: float = RESTART_WINDOW_S, health_every_s: float = HEALTH_EVERY_S,
                 unhealthy_checks: int = UNHEALTHY_CHECKS) -> None:
        names = [p.name for p in programs]
        if len(names) != len(set(names)):
            raise ValueError("program names must be unique")
        for program in programs:
            unknown = set(program.depends_on) - set(names)
            if unknown:
                raise ValueError(f"{program.name} depends on unknown programs {sorted(unknown)}")
        self.programs = {p.name: Running(p) for p in programs}
        self.order = names
        self.logs = logs
        self.logger = logger
        self.below_normal = below_normal
        self.backoff_s = backoff_s
        self.max_restarts = max_restarts
        self.restart_window_s = restart_window_s
        self.health_every_s = health_every_s
        self.unhealthy_checks = max(1, unhealthy_checks)
        self._restart_at: dict[str, float] = {}
        self.status_path = logs / "supervisor.status.json"
        self.stop_path = logs / "supervisor.stop"
        self.stopping = False
        self._http = httpx.AsyncClient(timeout=2.0)

    # --- one program -------------------------------------------------------------------------------------------

    def _open_output(self, name: str) -> IO[bytes]:
        path = self.logs / f"{name}.out"
        if path.exists() and path.stat().st_size > OUTPUT_MAX_BYTES:
            path.replace(path.with_suffix(".out.1"))
        return path.open("ab")

    def _start(self, running: Running) -> None:
        program = running.program
        env = dict(os.environ)
        env.update(dict(program.env))
        output = self._open_output(program.name)
        try:
            process, job = start_in_job(program.argv, cwd=program.cwd, env=env, output=output,
                                        below_normal=self.below_normal)
        except OSError as error:
            output.close()
            running.state, running.last_exit = "restarting", None
            log(self.logger, logging.ERROR, "could not start", program=program.name, error=str(error))
            return
        running.process, running.job, running.output = process, job, output
        running.pid, running.started_at, running.since_utc = process.pid, time.monotonic(), utc_now()
        running.state, running.health_failures = "starting", 0
        log(self.logger, logging.INFO, "started", program=program.name, pid=process.pid)

    def _end(self, running: Running) -> None:
        """End the program and everything it started (its job)."""
        if running.job is not None:
            running.job.terminate()
            running.job.close()
            running.job = None
        if running.process is not None:
            with contextlib.suppress(Exception):
                running.process.wait(timeout=5)
            running.last_exit = running.process.returncode
            running.process = None
        if running.output is not None:
            running.output.close()
            running.output = None

    async def _healthy(self, program: Program) -> bool:
        health = program.health
        if health.kind == "process":
            return True
        if health.kind == "tcp":
            try:
                with socket.create_connection((health.host, health.port), timeout=1.0):
                    return True
            except OSError:
                return False
        try:
            return (await self._http.get(health.url)).status_code == 200
        except httpx.HTTPError:
            return False

    def _exited(self, running: Running) -> bool:
        return running.process is not None and running.process.poll() is not None

    def _give_up(self, running: Running) -> bool:
        now = time.monotonic()
        running.restarts = [t for t in running.restarts if now - t <= self.restart_window_s]
        return len(running.restarts) >= self.max_restarts

    def _backoff(self, running: Running) -> float:
        return self.backoff_s[min(len(running.restarts), len(self.backoff_s) - 1)]

    # --- the whole set -----------------------------------------------------------------------------------------

    def write_status(self) -> None:
        data = {"supervisor": {"pid": os.getpid(), "updated_utc": utc_now(), "stopping": self.stopping},
                "programs": {name: {"state": r.state, "pid": r.pid, "since_utc": r.since_utc,
                                    "restarts": len(r.restarts), "last_exit": r.last_exit}
                             for name, r in self.programs.items()}}
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.status_path)

    async def start_all(self) -> bool:
        """Start every program in order, each when its dependencies are ready. False if one never got ready."""
        for name in self.order:
            running = self.programs[name]
            for dependency in running.program.depends_on:
                if self.programs[dependency].state != "ready":
                    log(self.logger, logging.ERROR, "dependency not ready", program=name, dependency=dependency)
                    return False
            self._start(running)
            self.write_status()
            if not await self._wait_ready(running):
                return False
        return True

    async def _wait_ready(self, running: Running) -> bool:
        program = running.program
        deadline = time.monotonic() + program.ready_timeout_s
        while time.monotonic() < deadline:
            if self._stop_requested():
                return False
            if running.process is None or self._exited(running):
                log(self.logger, logging.ERROR, "ended before it was ready", program=program.name,
                    exit_code=running.process.returncode if running.process is not None else None)
                self._end(running)
                running.state = "failed"
                self.write_status()
                return False
            if await self._healthy(program):
                running.state = "ready"
                log(self.logger, logging.INFO, "ready", program=program.name,
                    after_s=round(time.monotonic() - running.started_at, 2))
                self.write_status()
                return True
            await asyncio.sleep(POLL_S)
        log(self.logger, logging.ERROR, "not ready in time", program=program.name, timeout_s=program.ready_timeout_s)
        running.state = "failed"
        self.write_status()
        return False

    def _schedule_restart(self, name: str, running: Running, why: str, **fields: Any) -> None:
        """Restart after the backoff, or give up if it keeps ending (MAX_RESTARTS within RESTART_WINDOW_S)."""
        now = time.monotonic()
        if self._give_up(running):
            running.state = "failed"
            log(self.logger, logging.ERROR, f"gave up: {why} again and again", program=name,
                restarts=len(running.restarts), window_s=self.restart_window_s, **fields)
        else:
            running.state = "restarting"
            self._restart_at[name] = now + self._backoff(running)
            log(self.logger, logging.WARNING, f"{why}; restarting", program=name,
                in_s=round(self._restart_at[name] - now, 2), **fields)
        self.write_status()

    async def watch(self) -> None:
        """Until a stop is asked for: restart programs that end or stay unhealthy, keep the status file current."""
        next_health = time.monotonic() + self.health_every_s
        while not self._stop_requested():
            now = time.monotonic()
            for name, running in self.programs.items():
                if running.state in ("ready", "unhealthy", "starting") and self._exited(running):
                    code = running.process.returncode if running.process is not None else None
                    self._end(running)
                    running.last_exit = code
                    self._schedule_restart(name, running, "it ended", exit_code=code)
                elif running.state == "restarting" and now >= self._restart_at.get(name, 0.0):
                    running.restarts.append(now)
                    self._start(running)
                    if running.state == "restarting":             # it could not even start
                        self._schedule_restart(name, running, "it could not start")
                    self.write_status()
                elif running.state == "starting" and await self._healthy(running.program):
                    running.state = "ready"
                    log(self.logger, logging.INFO, "ready again", program=name, restarts=len(running.restarts))
                    self.write_status()
            if now >= next_health:
                next_health = now + self.health_every_s
                await self._check_health()
            await asyncio.sleep(POLL_S)

    async def _check_health(self) -> None:
        """A program that fails UNHEALTHY_CHECKS health checks in a row is ended and restarted."""
        for name, running in self.programs.items():
            if running.state not in ("ready", "unhealthy"):
                continue
            healthy = await self._healthy(running.program)
            running.health_failures = 0 if healthy else running.health_failures + 1
            if running.health_failures >= self.unhealthy_checks:
                self._end(running)
                self._schedule_restart(name, running, "it stopped answering its health check",
                                       checks=running.health_failures)
                continue
            new: State = "ready" if healthy else "unhealthy"
            if new != running.state:
                log(self.logger, logging.WARNING if not healthy else logging.INFO,
                    "health changed", program=name, state=new)
                running.state = new
                self.write_status()

    def _stop_requested(self) -> bool:
        return self.stopping or self.stop_path.exists()

    async def stop_all(self) -> None:
        """End every program, the last started first."""
        self.stopping = True
        for name in reversed(self.order):
            running = self.programs[name]
            if running.process is not None or running.job is not None:
                self._end(running)
                log(self.logger, logging.INFO, "stopped", program=name)
            running.state = "stopped"
        self.write_status()
        await self._http.aclose()


# --- the command line --------------------------------------------------------------------------------------------

def _claim_single_instance(pid_path: Path) -> bool:
    from resource_monitor import process_created, same_process
    if pid_path.exists():
        with contextlib.suppress(ValueError, OSError, KeyError):
            data = json.loads(pid_path.read_text(encoding="utf-8"))
            if same_process(int(data["pid"]), int(data["created"])):
                return False
    created = process_created(os.getpid())
    pid_path.write_text(json.dumps({"pid": os.getpid(), "created": created}) + "\n", encoding="utf-8")
    return True


def request_stop(logs: Path) -> int:
    """`--stop`: ask the running supervisor to stop, and wait until it has."""
    pid_path = logs / "supervisor.pid"
    if not pid_path.exists():
        print("no supervisor is running")
        return 0
    (logs / "supervisor.stop").write_text(utc_now() + "\n", encoding="utf-8")
    deadline = time.monotonic() + STOP_WAIT_S
    while time.monotonic() < deadline and pid_path.exists():
        time.sleep(0.25)
    if pid_path.exists():
        print(f"the supervisor did not stop within {STOP_WAIT_S:g} s")
        return 1
    print("stopped")
    return 0


async def amain(args: argparse.Namespace) -> int:
    logs: Path = args.logs
    logs.mkdir(parents=True, exist_ok=True)
    logger = program_logger("supervisor", logs, echo=args.echo)
    pid_path = logs / "supervisor.pid"
    if not _claim_single_instance(pid_path):
        print("a supervisor is already running (logs/supervisor.pid)")
        return 2
    (logs / "supervisor.stop").unlink(missing_ok=True)
    try:
        from resource_monitor import register_guarded, unregister_guarded
        with contextlib.suppress(OSError, PermissionError):
            register_guarded(os.getpid(), "supervisor")
        ports = read_ports(args.ports)
        nodes: tuple[NodeId, ...] = tuple(node for node in NODE_IDS if not args.only or node in args.only)
        programs = [p for p in build_programs(ports, llm=not args.no_llm, website=args.website, nodes=nodes,
                                              ports_file=args.ports)
                    if not args.only or p.name in args.only]
        supervisor = Supervisor(programs, logs=logs, logger=logger, below_normal=not args.normal_priority)
        log(logger, logging.INFO, "supervisor started", programs=[p.name for p in programs])
        code = 0
        try:
            if await supervisor.start_all():
                await supervisor.watch()
            elif not supervisor._stop_requested():
                code = 1
        finally:
            await supervisor.stop_all()
            with contextlib.suppress(OSError):
                unregister_guarded(os.getpid())
            log(logger, logging.INFO, "supervisor stopped", exit_code=code)
        return code
    finally:
        (logs / "supervisor.stop").unlink(missing_ok=True)
        pid_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stop", action="store_true", help="stop the running supervisor and everything it runs")
    parser.add_argument("--no-llm", action="store_true", help="leave out the two llama.cpp servers")
    parser.add_argument("--website", action="store_true", help="also run the website (npm run dev)")
    parser.add_argument("--only", type=lambda s: {x.strip() for x in s.split(",") if x.strip()}, default=set(),
                        help="run only these programs, e.g. nats,farm,incubator-1,hvac,gateway")
    parser.add_argument("--ports", type=Path, default=ROOT / "launcher" / "ports.toml", help="ports file")
    parser.add_argument("--logs", type=Path, default=ROOT / "logs", help="log folder (default: logs/)")
    parser.add_argument("--normal-priority", action="store_true", help="do not lower the programs' priority")
    parser.add_argument("--echo", action="store_true", help="also print the log to stderr")
    args = parser.parse_args(argv)
    if args.stop:
        return request_stop(args.logs)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
