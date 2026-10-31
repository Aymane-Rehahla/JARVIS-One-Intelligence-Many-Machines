"""launcher/supervisor.py and winjob.py (6A, Q6): start order and readiness, restarts with backoff, giving up,
restarting a program that stops answering, and ending every process a program started.

The programs here are small Python stand-ins (a TCP listener, an HTTP server, a program that crashes, one that starts
a grandchild); no JARVIS program, no language model, no nats-server. Everything they start ends with their job.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from argparse import Namespace
from pathlib import Path

import pytest

import supervisor as sv
import winjob
from contracts import NODE_IDS
from resource_monitor import pid_alive

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("test-supervisor")
PYTHON = sys.executable
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="job objects exist only on Windows")

LISTEN = """
import socket, sys, time
server = socket.socket(); server.bind(("127.0.0.1", int(sys.argv[1]))); server.listen()
quiet_after = float(sys.argv[2]) if len(sys.argv) > 2 else 1e9
time.sleep(quiet_after); server.close(); time.sleep(1e9)
"""
CRASH = "import time; time.sleep(0.3); raise SystemExit(3)"
GRANDCHILD = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1e9)"])
open(sys.argv[1], "w").write(str(child.pid)); time.sleep(1e9)
"""


def program(name: str, code: str, *args: str, health: sv.Health | None = None, **options: object) -> sv.Program:
    return sv.Program(name, (PYTHON, "-c", code, *args), ROOT, health or sv.Health("process"), **options)  # type: ignore[arg-type]


def supervisor(tmp_path: Path, programs: list[sv.Program], **options: object) -> sv.Supervisor:
    defaults: dict[str, object] = dict(backoff_s=(0.1, 0.2), max_restarts=3, restart_window_s=60.0,
                                       health_every_s=0.3, unhealthy_checks=2)
    return sv.Supervisor(programs, logs=tmp_path, logger=LOG, **(defaults | options))  # type: ignore[arg-type]


async def watch_until(sup: sv.Supervisor, check, timeout_s: float = 15.0) -> None:  # type: ignore[no-untyped-def]
    task = asyncio.create_task(sup.watch())
    deadline = time.monotonic() + timeout_s
    try:
        while not check():
            assert time.monotonic() < deadline, {n: r.state for n, r in sup.programs.items()}
            await asyncio.sleep(0.05)
    finally:
        sup.stopping = True
        await task


@pytest.mark.asyncio
async def test_programs_start_in_order_when_ready_and_stop_in_reverse(tmp_path: Path, new_port) -> None:  # type: ignore[no-untyped-def]
    tcp, web = new_port(), new_port()
    sup = supervisor(tmp_path, [
        program("first", LISTEN, str(tcp), health=sv.Health("tcp", port=tcp)),
        sv.Program("second", (PYTHON, "-m", "http.server", str(web), "--bind", "127.0.0.1"), tmp_path,
                   sv.Health("http", url=f"http://127.0.0.1:{web}/"), depends_on=("first",)),
        program("third", "import time; time.sleep(1e9)", depends_on=("second",))])
    assert await sup.start_all()
    assert [r.state for r in sup.programs.values()] == ["ready", "ready", "ready"]
    status = json.loads((tmp_path / "supervisor.status.json").read_text(encoding="utf-8"))
    assert status["programs"]["second"]["state"] == "ready" and status["programs"]["third"]["pid"]
    pids = [r.pid for r in sup.programs.values()]
    await sup.stop_all()
    assert all(r.state == "stopped" and r.process is None for r in sup.programs.values())
    assert not any(pid_alive(pid) for pid in pids if pid)


@pytest.mark.asyncio
async def test_a_program_that_never_gets_ready_stops_the_start(tmp_path: Path, new_port) -> None:  # type: ignore[no-untyped-def]
    sup = supervisor(tmp_path, [
        program("deaf", "import time; time.sleep(1e9)", health=sv.Health("tcp", port=new_port()), ready_timeout_s=1.0),
        program("after", "import time; time.sleep(1e9)", depends_on=("deaf",))])
    assert not await sup.start_all()
    assert sup.programs["deaf"].state == "failed" and sup.programs["after"].state == "waiting"
    await sup.stop_all()


@pytest.mark.asyncio
async def test_a_crashing_program_is_restarted_with_backoff_then_given_up(tmp_path: Path) -> None:
    sup = supervisor(tmp_path, [program("crasher", CRASH)])
    assert await sup.start_all()
    crasher = sup.programs["crasher"]
    await watch_until(sup, lambda: crasher.state == "failed")
    assert len(crasher.restarts) == 3 and crasher.last_exit == 3
    gaps = [b - a for a, b in zip(crasher.restarts, crasher.restarts[1:])]
    assert all(gap >= 0.3 for gap in gaps)                                     # it ran, ended, then waited
    assert (tmp_path / "crasher.out").exists()                                 # its output is kept
    await sup.stop_all()


@pytest.mark.asyncio
async def test_a_program_that_cannot_start_is_retried_then_given_up(tmp_path: Path) -> None:
    missing = sv.Program("missing", (str(tmp_path / "no-such-program.exe"),), tmp_path, sv.Health("process"))
    sup = supervisor(tmp_path, [missing])
    assert not await sup.start_all()                                            # it never ran
    sup.programs["missing"].state = "restarting"
    await watch_until(sup, lambda: sup.programs["missing"].state == "failed", timeout_s=10.0)
    assert len(sup.programs["missing"].restarts) == 3
    await sup.stop_all()


@pytest.mark.asyncio
async def test_a_program_that_stops_answering_is_restarted(tmp_path: Path, new_port) -> None:  # type: ignore[no-untyped-def]
    port = new_port()
    sup = supervisor(tmp_path, [program("quiet", LISTEN, str(port), "0.8", health=sv.Health("tcp", port=port))])
    assert await sup.start_all()
    first = sup.programs["quiet"].pid
    await watch_until(sup, lambda: len(sup.programs["quiet"].restarts) >= 1 and sup.programs["quiet"].state == "ready")
    assert sup.programs["quiet"].pid != first and first is not None and not pid_alive(first)
    await sup.stop_all()


@pytest.mark.asyncio
async def test_ending_a_program_ends_what_it_started(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    sup = supervisor(tmp_path, [program("parent", GRANDCHILD, str(pid_file))])
    assert await sup.start_all()
    deadline = time.monotonic() + 10
    while not pid_file.exists() or not pid_file.read_text(encoding="utf-8"):
        assert time.monotonic() < deadline
        await asyncio.sleep(0.05)
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    assert pid_alive(grandchild)
    await sup.stop_all()
    time.sleep(0.3)
    assert not pid_alive(grandchild)                                            # the job took it with the parent


def test_a_job_ends_its_processes_when_closed(tmp_path: Path) -> None:
    with (tmp_path / "out.txt").open("wb") as output:
        process, job = winjob.start_in_job([PYTHON, "-c", "import time; time.sleep(1e9)"], cwd=tmp_path, env=None,
                                           output=output, below_normal=True)
        assert process.poll() is None
        job.close()
        assert process.wait(timeout=10) is not None
    with (tmp_path / "out2.txt").open("wb") as output, pytest.raises(OSError):
        winjob.start_in_job([str(tmp_path / "nothing.exe")], cwd=tmp_path, env=None, output=output)


def test_bad_program_lists_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unique"):
        supervisor(tmp_path, [program("a", "pass"), program("a", "pass")])
    with pytest.raises(ValueError, match="unknown"):
        supervisor(tmp_path, [program("a", "pass", depends_on=("ghost",))])


def test_the_jarvis_programs_and_their_order() -> None:
    ports = sv.read_ports(ROOT / "launcher" / "ports.toml")
    plain = sv.build_programs(ports, llm=False, website=False)
    assert [p.name for p in plain] == ["nats", "farm", *NODE_IDS, "gateway"]
    nats = plain[0]
    assert nats.argv[nats.argv.index("-a") + 1] == "127.0.0.1" and str(ports["nats"]) in nats.argv
    assert all("127.0.0.1" in " ".join(p.argv) for p in plain[1:])
    full = sv.build_programs(ports, llm=True, website=True)
    assert [p.name for p in full][:5] == ["nats", *(f"llama-{node}" for node in NODE_IDS)]
    assert full[-1].name == "website"
    assert {p.name: p.depends_on for p in full if p.name in NODE_IDS} == {
        node: ("nats", f"llama-{node}") for node in NODE_IDS}
    assert sv.build_programs(ports, llm=False, website=False, nodes=("hvac",))[2].name == "hvac"
    only_hvac = sv.select_programs(sv.build_programs(ports, llm=True, website=False, nodes=("hvac",)),
                                   {"nats", "hvac"})
    assert [program.name for program in only_hvac] == ["nats", "llama-hvac", "hvac"]


def test_a_recorded_question_reaches_only_incubator_1(tmp_path: Path) -> None:
    """6F rehearsals: start_all.ps1 starts the supervisor through WMI, so the shell's environment never reaches the
    nodes; -VoiceWav passes the recorded question's file to incubator-1's voice box, and to no other program."""
    ports = sv.read_ports(ROOT / "launcher" / "ports.toml")
    wav = tmp_path / "question.wav"
    programs = sv.build_programs(ports, llm=False, website=False, voice_wav=wav)
    assert {p.name: dict(p.env) for p in programs if p.env} == {"incubator-1": {"JARVIS_VOICE_WAV": str(wav.resolve())}}
    assert not any(p.env for p in sv.build_programs(ports, llm=False, website=False))


def test_node_programs_get_the_runs_own_ports_file(tmp_path: Path) -> None:
    """Review H6: a node read its MCP port from the real launcher/ports.toml, so a test run bound 8101-8104."""
    other = tmp_path / "ports.toml"
    other.write_text((ROOT / "launcher" / "ports.toml").read_text(encoding="utf-8"), encoding="utf-8")
    for program in sv.build_programs(sv.read_ports(other), llm=False, website=False, ports_file=other):
        if program.name in NODE_IDS:
            assert program.argv[program.argv.index("--ports") + 1] == str(other.resolve()), program.name


@pytest.mark.asyncio
async def test_bad_dependencies_exit_two_with_a_logged_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    """A selection/configuration error is visible even when the supervisor runs through pythonw."""
    monkeypatch.setattr(sv, "build_programs", lambda *args, **kwargs: [program("bad", "pass", depends_on=("missing",))])
    import resource_monitor
    monkeypatch.setattr(resource_monitor, "register_guarded", lambda *args, **kwargs: None)
    monkeypatch.setattr(resource_monitor, "unregister_guarded", lambda *args, **kwargs: None)
    args = Namespace(no_llm=True, website=False, only=set(), ports=ROOT / "launcher" / "ports.toml", logs=tmp_path,
                     normal_priority=False, echo=False, feed="trained",     # 6D: the CLI's new --feed
                     voice_wav=None)                                         # 6F: --voice-wav
    assert await sv.amain(args) == 2
    assert "depends on unknown programs" in capsys.readouterr().out
    records = (tmp_path / "supervisor.jsonl").read_text(encoding="utf-8")
    assert "invalid program selection" in records and "depends on unknown programs" in records


def test_only_one_supervisor_and_stop_without_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pid_file = tmp_path / "supervisor.pid"
    assert sv._claim_single_instance(pid_file)                                 # this process claims it
    assert not sv._claim_single_instance(pid_file)                             # a second claim, while it lives
    pid_file.write_text(json.dumps({"pid": 4, "created": 1}), encoding="utf-8")  # a stale file
    assert sv._claim_single_instance(pid_file)
    pid_file.unlink()
    assert sv.request_stop(tmp_path) == 0 and "no supervisor" in capsys.readouterr().out


def test_the_website_uses_the_projects_own_node_then_the_one_on_path(tmp_path: Path) -> None:
    import supervisor
    bundled = tmp_path / "third_party" / "node" / "node-v24.22.1-win-x64"
    assert supervisor.find_node_js(tmp_path, which=lambda _: r"C:\tools\node\npm.cmd") == Path(r"C:\tools\node")
    bundled.mkdir(parents=True)
    (bundled / "npm.cmd").write_text("")
    assert supervisor.find_node_js(tmp_path, which=lambda _: None) == bundled
