"""The farm runtime: ticks the Matrix and the four reasoning brains, and publishes what each node may see on NATS.

Two feeds (`--feed`, decision 0021). `simulated` (step 6A, the fast tests' default) is the read-only snapshot of the
farm (`_dev/farm_poc_snapshot/`) with its rule brains, labelled "live: simulated brains". `trained` (step 6D, the
launcher's default) is the release installed in `matrix/release_v31/` (decision 0015): its own Matrix code and the four
trained CTM brains built exactly as the release's exam built them, each node keeping a CMM in a fresh SQLite file per
farm run, labelled "live: trained brains". Published (contracts.py; plan section 4.3):
    farm.<node>.status, .assessment   every tick, at most MAX_PUBLISH_HZ times per real second (the latest wins)
    farm.<node>.events                every alarm raised or cleared, every safety-gate decision, offline/online
    farm.<node>.incident              when the node's reasoning opens, changes or closes an incident
    farm.links                        which radio links are cut and which machines are offline, on change and every 10 s
    farm.sim                          the farm clock, every real second and on every change
Only public telemetry and the reasoning's own outputs leave this program: no scenario label, fault schedule, hidden
truth or evaluator score (ground rule 4).

The Matrix API (127.0.0.1, `matrix_api` in launcher/ports.toml) is how the gateway drives the farm clock:
    GET  /health
    POST /sim/start · /sim/pause · /sim/reset · /sim/speed {"speed": S}
    POST /links/{node}/cut {"seconds": S} · /links/{node}/restore      (robustness tests: the Matrix cuts a radio link)
    POST /faults/{scenario_id}     one of the website's four demo faults, from the next tick (6W; decision 0020):
                                   404 unknown, 409 when one is already active in this farm run (a reset clears it).
                                   The answer goes to the caller only; nothing about it is published.
    POST /alerts/{node}/{incident_id}/ack   the owner acknowledged that node's alert (6C, P2 section 4 step 5): the
                                   Matrix's own owner acknowledgement, on every delivered and not yet acknowledged
                                   owner notification of this farm run from that node for that incident. 200
                                   {"node", "incident_id", "acknowledged": N, "farm_time"}; 404 when there is none.

The snapshot's NodeRuntime imports its CTM brain module, which needs torch; this environment has no torch and the test
feed runs rule brains only, so that one module is replaced by a stub that refuses to build a CTM (decision 0012).

Usage: python matrix/farm_runtime.py [--feed simulated|trained] [--seed N] [--speed S] [--paused] [--nats URL]
                                      [--port P] [--logs DIR | --no-logs]
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import shutil
import logging
import secrets
import sys
import time
import tomllib
import types
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PARENT = ROOT / "_dev"
REFERENCE_NODE = ROOT / "nodes" / "incubator-1"            # the four copies are identical (tests/test_nodes_in_sync.py)
for _path in (REFERENCE_NODE, SNAPSHOT_PARENT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
sys.dont_write_bytecode = True                              # the snapshot is read-only: no __pycache__ inside it

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException, Request, Response  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field, JsonValue  # noqa: E402

from comms.jsonlog import log, program_logger  # noqa: E402
from comms.nats_link import NatsLink  # noqa: E402
from contracts import (FARM_LINKS, FARM_SIM, NODE_IDS, SCENARIO_IDS, FarmAssessment, FarmEvent,  # noqa: E402
                       FarmEventType, FarmIncident, FarmStatus, FaultResult, FeedLabel, GateDecision, LinkStatus,
                       NodeId, ScenarioId, SimState, farm_subject)

MAX_PUBLISH_HZ = 10.0
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
SIM_EVERY_S = 1.0
LINKS_EVERY_S = 10.0
SPEED_WINDOW_S = 5.0
MAX_SPEED = 1000.0
LABEL: FeedLabel = "live: simulated brains"
Feed = Literal["simulated", "trained"]
LABELS: dict[str, FeedLabel] = {"simulated": "live: simulated brains", "trained": "live: trained brains"}
RELEASE = ROOT / "matrix" / "release_v31"
DATA = ROOT / "matrix" / "data"                         # gitignored: each farm run's CMM files (trained feed)
#: The trained feed's farm: an ordinary library day (decision 0021): "normal" variant 1, a training-split mild day whose
#: weather starts at 06:52 (start_hour 6.87); the brains get that start_hour, as in the exam.
DAY_FAMILY, DAY_VARIANT = "normal", 1
TORCH_THREADS = 1                                        # the exam's setting (decision 0021: measured, kept)
ENGINE_FILES = {"incubator": "reasoning_engine_INCv3.1.pt", "hvac": "reasoning_engine_HVACv3.1.pt",
                "animal_protection": "reasoning_engine_APv3.1.pt"}


def sent_by(sender: object, node: str) -> bool:
    """Whether an owner notification's `sender` is this node. The snapshot's safety gate names its caller
    `jarvis-<node>` (gate.py, `jarvis_id`); the bare node id is accepted too."""
    return sender in (node, f"jarvis-{node}")


class _NoCTM:
    """Stands in for the snapshot's CTM brain classes: the test feed runs rule brains only."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("CTM brains need torch and the trained weights; the 6A test feed runs rule brains only")


def load_snapshot() -> tuple[Any, Any]:
    """The snapshot's MatrixEngine and NodeRuntime classes (with the CTM module stubbed, see the module docstring)."""
    name = "farm_poc_snapshot.nodes.ctm_brain"
    if name not in sys.modules:
        stub = types.ModuleType(name)
        stub.__dict__.update(NodeBrainConfig=_NoCTM, NodeCTMBrain=_NoCTM)
        sys.modules[name] = stub
    from farm_poc_snapshot.matrix.engine import MatrixEngine
    from farm_poc_snapshot.nodes.runtime import NodeRuntime
    return MatrixEngine, NodeRuntime


def _event_codes(readings: Mapping[str, Any]) -> dict[str, tuple[Literal["alarm", "fault"],
                                                                        Literal["info", "warning", "critical"]]]:
    """Collapse alarm and fault telemetry into one code with its strongest severity and source."""
    found: dict[str, tuple[Literal["alarm", "fault"], Literal["info", "warning", "critical"]]] = {}
    sources: tuple[tuple[str, Literal["alarm", "fault"], Literal["warning", "critical"]], ...] = (
        ("alarms", "alarm", "warning"), ("faults", "fault", "critical"),
        ("active_alarms", "alarm", "warning"), ("active_faults", "fault", "critical"))
    for key, source, default_severity in sources:
        for entry in readings.get(key) or []:
            if isinstance(entry, str):
                code, severity = entry, default_severity
            elif isinstance(entry, Mapping) and isinstance(entry.get("code"), str):
                code = str(entry["code"])
                severity_value = entry.get("severity", default_severity)
                severity: Literal["info", "warning", "critical"] = default_severity
                if severity_value in ("info", "warning", "critical"):
                    severity = severity_value
            else:
                continue
            current = found.get(code)
            order = SEVERITY_RANK
            if current is None or order[severity] > order[current[1]] or (
                    severity == current[1] and source == "fault"):
                found[code] = (source, severity)
    return found


def _plain(value: Any) -> Any:
    """JSON-ready copy: sets become sorted lists, tuples lists."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


class SpeedRequest(BaseModel):
    """POST /sim/speed."""

    model_config = ConfigDict(extra="forbid")
    speed: float = Field(gt=0.0, le=MAX_SPEED)


class CutRequest(BaseModel):
    """POST /links/{node}/cut."""

    model_config = ConfigDict(extra="forbid")
    seconds: float = Field(default=60.0, gt=0.0, le=86_400.0)


VARIANTS_SEARCHED = 500


def _first(fault: Mapping[str, Any], **wanted: Any) -> bool:
    return all(fault.get(key) == value for key, value in wanted.items())


#: The website's demo faults (plan 4.4) → the scenario library's family, and which ordinary (not novel) variant to take:
#: the first one whose fault matches the website's description (decision 0020). Its own parameters are used unchanged.
DEMO_FAULTS: dict[ScenarioId, tuple[str, Callable[[Any], bool]]] = {
    "heater_relay_stuck": ("heater_relay_stuck",
                           lambda spec: _first(spec.faults[0], device="incubator-1", end_tick=None)),
    "supply_fan_failure": ("supply_fan_failure", lambda spec: _first(spec.faults[0], device="hvac", end_tick=None)),
    "humidity_sensor_drift": ("sensor_fault",
                              lambda spec: _first(spec.faults[0], channel="incubator-2.humidity_1", mode="drift",
                                                  end_tick=None) and float(spec.faults[0]["severity"]) > 0.0),
    "supply_overvoltage": ("overvoltage", lambda spec: spec.tags.get("pattern") == "shared"),
}


def load_fault_tools() -> tuple[Any, Any]:
    """The snapshot library's episode builder (`_make`) and its fault injector (`_faults`): the same code that made
    the training episodes, so a demo fault is one the brains were trained on."""
    load_snapshot()
    from farm_poc_snapshot.scenarios.episode import _faults
    from farm_poc_snapshot.scenarios.library import _make
    return _make, _faults


def load_release(release: Path = RELEASE) -> types.SimpleNamespace:
    """The installed release's own classes (decision 0015), imported from `matrix/release_v31` (its packages are
    `farm_poc` and `ctm_training`; the snapshot's is `farm_poc_snapshot`, so the two never mix)."""
    if not (release / "PROVENANCE.json").is_file():
        raise FileNotFoundError(f"the release is not installed in {release} (tools/install_release.py)")
    if str(release) not in sys.path:
        sys.path.insert(0, str(release))
    import torch
    from ctm_training.decide import Decider
    from ctm_training.live import TrainedNodeBrain
    from ctm_training.train import load_brain
    from farm_poc.matrix.engine import MatrixEngine
    from farm_poc.nodes.cmm import CognitiveMemory
    from farm_poc.nodes.contracts import MACHINE_TYPES
    from farm_poc.nodes.runtime import NodeRuntime
    from farm_poc.scenarios.episode import _faults
    from farm_poc.scenarios.library import _make
    return types.SimpleNamespace(torch=torch, Decider=Decider, TrainedNodeBrain=TrainedNodeBrain, load_brain=load_brain,
                                 MatrixEngine=MatrixEngine, CognitiveMemory=CognitiveMemory,
                                 MACHINE_TYPES=MACHINE_TYPES, NodeRuntime=NodeRuntime, faults=_faults, make=_make,
                                 brains=release / "brains")


class DemoFaultRefused(Exception):
    """A demo fault that cannot start now (one is already active in this farm run)."""


class FarmRuntime:
    """The farm, its four brains and their publication (see the module docstring)."""

    def __init__(self, link: NatsLink, *, logger: logging.Logger, seed: int = 7, speed: float = 1.0,
                 running: bool = True, config: Mapping[str, Any] | None = None,
                 max_publish_hz: float = MAX_PUBLISH_HZ, feed: Feed = "simulated", release: Path = RELEASE,
                 data_dir: Path = DATA, threads: int = TORCH_THREADS) -> None:
        if not 0 < speed <= MAX_SPEED or max_publish_hz <= 0:
            raise ValueError("speed and publish rate must be positive")
        if feed not in LABELS or threads < 1:
            raise ValueError("the feed is simulated or trained, with at least one torch thread")
        self.link = link
        self.logger = logger
        self.seed = seed
        self.speed = speed
        self.running = running
        self.max_publish_hz = max_publish_hz
        self.feed: Feed = feed
        self.label: FeedLabel = LABELS[feed]
        self.data_dir = data_dir
        self._memories: list[Any] = []
        self._memory_folder: Path | None = None
        if feed == "trained":
            self._release = load_release(release)
            self._release.torch.set_num_threads(threads)
            self._engine_cls, self._runtime_cls = self._release.MatrixEngine, self._release.NodeRuntime
            self._make_spec, self._inject = self._release.make, self._release.faults
            day = self._make_spec(DAY_FAMILY, DAY_VARIANT, False)
            self.config = copy.deepcopy(dict(config or day.config))
            self.start_hour = float(self.config.get("weather", {}).get("start_hour", 0.0))
            self._loaded = {machine: self._release.load_brain(self._release.brains / machine / name,
                                                               self._release.brains, "cpu")
                            for machine, name in ENGINE_FILES.items()}
        else:
            self.config = dict(config or {})
            self._engine_cls, self._runtime_cls = load_snapshot()
            self._make_spec, self._inject = load_fault_tools()
        self._demo_specs: dict[ScenarioId, tuple[int, Any]] = {}
        self.engine: Any = None
        self.runtime: Any = None
        self._publish_lock = asyncio.Lock()
        self.reset()

    # --- the farm ----------------------------------------------------------------------------------------------

    def reset(self) -> None:
        """A fresh farm and fresh brains, as a new farm run with its own feed_id (ticks, event sequences and incident
        ids start again, so the nodes must not mix them with the previous run's)."""
        self.feed_id = secrets.token_hex(6)
        self.close_memories()
        if self.feed == "trained":
            self.engine = self._engine_cls(seed=self.seed, config=copy.deepcopy(self.config))
            self.runtime = self._trained_runtime()
        else:
            self.engine = self._engine_cls(seed=self.seed, config=self.config or None)
            self.runtime = self._runtime_cls(self.engine)
        self._alarms: dict[str, dict[str, tuple[Literal["alarm", "fault"], Literal["info", "warning", "critical"]]]] = {
            node: {} for node in NODE_IDS}
        self._online: dict[str, bool] = {node: True for node in NODE_IDS}
        self._incidents: dict[str, tuple[str | None, str]] = {node: (None, "") for node in NODE_IDS}
        self._event_seq = 0
        self._last_publish = float("-inf")
        self._last_sim = float("-inf")
        self._last_links_sent = float("-inf")
        self._links: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self._speed_samples: list[tuple[float, float]] = []
        self._pending: dict[str, Any] = {}
        self.demo_fault: FaultResult | None = None            # one demo fault per farm run; a reset clears it
        log(self.logger, logging.INFO, "farm reset", seed=self.seed, episode=self.engine.world.episode_id,
            feed_id=self.feed_id)

    @property
    def tick(self) -> int:
        return int(self.engine.clock.tick)

    @property
    def time_s(self) -> float:
        return float(self.engine.clock.time)

    def _trained_runtime(self) -> Any:
        """The four trained brains as the release's exam built them (`ctm_training.practice.CheckpointBrains`): one
        loaded copy per machine, a deep copy per node (each incubator its own), the tuned decision layer,
        hide_notes=True, hold_calls=True, the day's start_hour; each node's CMM in this run's own folder."""
        release = self._release
        brains = {}
        for node in NODE_IDS:
            machine = release.MACHINE_TYPES[node]
            decider = release.Decider.for_checkpoint(release.brains / machine / ENGINE_FILES[machine])
            brains[node] = release.TrainedNodeBrain(copy.deepcopy(self._loaded[machine]), self.start_hour, decider,
                                                    False, True, True)
        folder = self.data_dir / "cmm" / self.feed_id
        folder.mkdir(parents=True, exist_ok=True)
        self._memory_folder = folder

        def memory(node: str) -> Any:
            made = release.CognitiveMemory(node, folder / f"{node}.sqlite")
            self._memories.append(made)
            return made
        return self._runtime_cls(self.engine, modes={node: "real" for node in NODE_IDS}, brains=brains,
                                 memory_factory=memory)

    def close_memories(self) -> None:
        """Close this run's CMM files and delete their folder (a reset is a new run with fresh memories)."""
        for memory in self._memories:
            with contextlib.suppress(Exception):
                memory.close()
        self._memories = []
        if self._memory_folder is not None:
            shutil.rmtree(self._memory_folder, ignore_errors=True)
            self._memory_folder = None

    @property
    def step_seconds(self) -> float:
        return float(self.engine.clock.step_seconds)

    def farm_time(self) -> tuple[str, int]:
        """("HH:MM", day): the farm's own clock, the same one the Matrix's phone service uses."""
        hour = float(self.engine.comms.hour_of_day())
        minutes = int(hour * 60) % 1440
        start_hour = (hour - self.time_s / 3600.0) % 24.0
        day = 1 + int((start_hour * 3600.0 + self.time_s) // 86_400)
        return f"{minutes // 60:02d}:{minutes % 60:02d}", day

    def actual_speed(self) -> float:
        """Farm seconds per real second over the last few seconds (0 while paused)."""
        if not self.running or len(self._speed_samples) < 2:
            return 0.0
        (t0, s0), (t1, s1) = self._speed_samples[0], self._speed_samples[-1]
        return (s1 - s0) / (t1 - t0) if t1 > t0 else 0.0

    def cut_links(self) -> list[NodeId]:
        """Machines whose radio link the Matrix has cut right now."""
        tick = self.tick
        return [node for node in NODE_IDS
                if (window := self.runtime.bus.isolated.get(node)) is not None and window[0] <= tick < window[1]]

    def step(self) -> dict[str, Any]:
        """One farm tick: physics, devices, the four brains, the safety gate."""
        results: dict[str, Any] = self.runtime.step()
        now = time.monotonic()
        self._speed_samples.append((now, self.time_s))
        while len(self._speed_samples) > 2 and now - self._speed_samples[0][0] > SPEED_WINDOW_S:
            self._speed_samples.pop(0)
        return results

    # --- publication -------------------------------------------------------------------------------------------

    def _common(self, node: NodeId) -> dict[str, Any]:
        farm_time, _ = self.farm_time()
        return {"node": node, "tick": self.tick, "time_s": self.time_s, "farm_time": farm_time, "label": self.label,
                "feed_id": self.feed_id}

    def messages_for_tick(self, results: Mapping[str, Any], *, include_state: bool) -> list[tuple[str, BaseModel]]:
        """Everything to publish after one tick (status and assessment only when `include_state`)."""
        out: list[tuple[str, BaseModel]] = []
        for node in NODE_IDS:
            result = results.get(node, {})
            device = self.engine.world.devices[node]
            online = bool(device.online)
            readings: dict[str, JsonValue] = _plain(self.engine.device(node)) if online else {}
            common = self._common(node)
            for event in self._events(node, online, readings, result):
                out.append((farm_subject(node, "events"), event))
            if include_state:
                out.append((farm_subject(node, "status"), FarmStatus(**common, online=online, readings=readings)))
                output = result.get("output")
                if output is not None:
                    out.append((farm_subject(node, "assessment"), self._assessment(node, output, result, common)))
            incident = self._incident(node, common)
            if incident is not None:
                out.append((farm_subject(node, "incident"), incident))
        return out

    def _events(self, node: NodeId, online: bool, readings: Mapping[str, Any],
                result: Mapping[str, Any]) -> list[FarmEvent]:
        found: list[tuple[FarmEventType, Literal["info", "warning", "critical"], dict[str, JsonValue]]] = []
        if online != self._online[node]:
            kind: FarmEventType = "machine_online" if online else "machine_offline"
            found.append((kind, "info" if online else "critical", {}))
            self._online[node] = online
        if online:
            current = _event_codes(readings)
            before = self._alarms[node]
            # raised: a new code, or a known code whose severity rose (warning -> critical is news for the owner)
            raised: list[tuple[FarmEventType, Literal["info", "warning", "critical"], dict[str, JsonValue]]] = [
                ("alarm_raised", severity, {"code": code, "source": source})
                for code, (source, severity) in sorted(current.items())
                if code not in before or SEVERITY_RANK[severity] > SEVERITY_RANK[before[code][1]]]
            cleared: list[tuple[FarmEventType, Literal["info", "warning", "critical"], dict[str, JsonValue]]] = [
                ("alarm_cleared", "info", {"code": code, "source": before[code][0]})
                for code in sorted(before.keys() - current.keys())]
            found += raised + cleared
            self._alarms[node] = dict(current)
        gate_kinds: tuple[tuple[str, FarmEventType], ...] = (("action", "safety_action"),
                                                             ("communication", "safety_communication"))
        for key, kind in gate_kinds:
            decision = result.get(key)
            if isinstance(decision, Mapping):
                allowed = bool(decision.get("allowed"))
                found.append((kind, "critical" if allowed else "warning",
                              {"proposed": str(decision.get(key, "")), "allowed": allowed,
                               "reason": str(decision.get("reason", ""))}))
        events: list[FarmEvent] = []
        for kind, severity, data in found:
            self._event_seq += 1
            events.append(FarmEvent(**self._common(node), sequence=self._event_seq, type=kind, severity=severity,
                                    data=data))
        return events

    @staticmethod
    def _assessment(node: NodeId, output: Any, result: Mapping[str, Any], common: Mapping[str, Any]) -> FarmAssessment:
        def gate(key: str) -> GateDecision | None:
            decision = result.get(key)
            if not isinstance(decision, Mapping):
                return None
            return GateDecision(proposed=str(decision.get(key, "")), allowed=bool(decision.get("allowed")),
                                reason=str(decision.get("reason", "")))

        return FarmAssessment(**common, assessment=output.assessment, diagnosis=output.diagnosis,
                              confidence=float(output.confidence), incident_kind=output.incident_kind,
                              incident_id=result.get("incident_id"), recommendation=str(output.recommendation),
                              evidence=[str(e) for e in output.evidence],
                              brain="ctm" if output.brain == "ctm" else "rule",
                              action=gate("action"), communication=gate("communication"))

    def _incident(self, node: NodeId, common: Mapping[str, Any]) -> FarmIncident | None:
        memory = self.runtime.nodes[node].memory
        active = memory.active()
        before_id, before_hash = self._incidents[node]
        if active is None:
            if before_id is None:
                return None
            self._incidents[node] = (None, "")
            closed = memory.incidents.get(before_id)
            return FarmIncident(**common, incident_id=before_id, state="closed",
                                kind=closed.get("kind") if closed else None, summary=_plain(closed or {}))
        summary = _plain(active)
        digest = repr(sorted(summary.items()))
        if before_id == active["incident_id"] and digest == before_hash:
            return None
        state: Literal["opened", "changed"] = "changed" if before_id == active["incident_id"] else "opened"
        self._incidents[node] = (active["incident_id"], digest)
        return FarmIncident(**common, incident_id=active["incident_id"], state=state, kind=active.get("kind"),
                            summary=summary)

    def sim_state(self) -> SimState:
        farm_time, day = self.farm_time()
        return SimState(tick=self.tick, time_s=self.time_s, farm_time=farm_time, day=day, running=self.running,
                        target_speed=self.speed, actual_speed=round(self.actual_speed(), 3), label=self.label,
                        episode_id=str(self.engine.world.episode_id), feed_id=self.feed_id)

    def link_state(self) -> LinkStatus:
        offline: list[NodeId] = [node for node in NODE_IDS if not self.engine.world.devices[node].online]
        return LinkStatus(tick=self.tick, time_s=self.time_s, cut=self.cut_links(), offline=offline)

    async def publish(self, messages: list[tuple[str, BaseModel]]) -> None:
        for subject, message in messages:
            await self.link.publish(subject, message)

    async def publish_clock(self, *, force: bool = False) -> None:
        """farm.sim every SIM_EVERY_S; farm.links on change and every LINKS_EVERY_S."""
        now = time.monotonic()
        if force or now - self._last_sim >= SIM_EVERY_S:
            self._last_sim = now
            await self.link.publish(FARM_SIM, self.sim_state())
        links = self.link_state()
        key = (tuple(links.cut), tuple(links.offline))
        if force or key != self._links or now - self._last_links_sent >= LINKS_EVERY_S:
            self._links, self._last_links_sent = key, now
            await self.link.publish(FARM_LINKS, links)

    # --- the loop ----------------------------------------------------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        """Tick at the target speed until `stop` is set; publish after every tick."""
        await self.publish_clock(force=True)
        next_tick = time.monotonic()
        while not stop.is_set():
            if not self.running:
                await self.publish_clock()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=0.1)
                next_tick = time.monotonic()
                continue
            async with self._publish_lock:
                results = self.step()
                now = time.monotonic()
                include = now - self._last_publish >= 1.0 / self.max_publish_hz
                if include:
                    self._last_publish = now
                await self.publish(self.messages_for_tick(results, include_state=include))
                await self.publish_clock()
            next_tick += self.step_seconds / self.speed
            delay = next_tick - time.monotonic()
            if delay < -1.0:                               # cannot keep up: do not try to catch up in a burst
                next_tick = time.monotonic()
                delay = 0.0
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=max(0.0, delay))

    # --- control (the Matrix API) --------------------------------------------------------------------------------

    async def control(self, action: str, *, speed: float | None = None) -> SimState:
        async with self._publish_lock:
            if action == "start":
                self.running = True
            elif action == "pause":
                self.running = False
            elif action == "reset":
                self.reset()
            elif action == "speed" and speed is not None:
                self.speed = speed
                self._speed_samples.clear()
            else:
                raise ValueError(f"unknown action {action!r}")
            log(self.logger, logging.INFO, "farm clock", action=action, speed=self.speed, running=self.running)
            await self.publish_clock(force=True)
            return self.sim_state()

    def demo_spec(self, scenario_id: ScenarioId) -> tuple[int, Any]:
        """(variant, EpisodeSpec) of the library episode a demo fault takes its parameters from (DEMO_FAULTS)."""
        if scenario_id not in self._demo_specs:
            family, wanted = DEMO_FAULTS[scenario_id]
            for variant in range(VARIANTS_SEARCHED):
                spec = self._make_spec(family, variant, False)
                if wanted(spec):
                    self._demo_specs[scenario_id] = (variant, spec)
                    break
            else:
                raise LookupError(f"no ordinary {family} variant matches {scenario_id}")
        return self._demo_specs[scenario_id]

    async def inject_fault(self, scenario_id: ScenarioId) -> FaultResult:
        """Start a demo fault at the next tick with the library's own parameters, shifted in time only. Publishes
        nothing: the nodes learn of it only through its physical effects (ground rule 4)."""
        async with self._publish_lock:
            if self.demo_fault is not None:
                raise DemoFaultRefused(f"a demo fault ({self.demo_fault.scenario_id}) is already active in this farm "
                                       "run; reset the farm to start another")
            variant, spec = self.demo_spec(scenario_id)
            start = self.tick                                   # due at the next engine step
            shift = start - int(spec.faults[0]["inject_tick"])
            faults = []
            for fault in spec.faults:
                moved = dict(fault)
                moved["inject_tick"] = int(fault["inject_tick"]) + shift
                if fault.get("end_tick") is not None:
                    moved["end_tick"] = int(fault["end_tick"]) + shift
                faults.append(moved)
            self._inject(self.engine, self.runtime, types.SimpleNamespace(faults=faults))
            fault = faults[0]
            devices: list[NodeId] = ([fault["device"]] if "device" in fault else
                                     ["incubator-1", "incubator-2"] if fault.get("shared") else ["incubator-1"])
            farm_time, _ = self.farm_time()
            self.demo_fault = FaultResult(scenario_id=scenario_id, family=DEMO_FAULTS[scenario_id][0],
                                          variant=variant, devices=devices, tick=start,
                                          end_tick=max((f["end_tick"] for f in faults if f.get("end_tick") is not None),
                                                       default=None),
                                          time_s=start * self.step_seconds, farm_time=farm_time, feed_id=self.feed_id)
            log(self.logger, logging.INFO, "demo fault injected", scenario_id=scenario_id,
                family=self.demo_fault.family, variant=variant, devices=devices, tick=start, feed_id=self.feed_id,
                faults=_plain(faults))
            return self.demo_fault

    async def acknowledge(self, node: NodeId, incident_id: str) -> dict[str, Any]:
        """The owner's acknowledgement (the click the gateway passed on after incubator-1 checked its token): the
        Matrix's phone service marks that node's delivered owner notifications for this incident as acknowledged now,
        as its simulated owner would have later. The brains see it only as they always see an acknowledgement.
        LookupError when this farm run has none to mark."""
        async with self._publish_lock:
            comms = self.engine.comms
            now = float(self.engine.clock.time)
            marked = []
            for record in comms.notifications:
                if (record.get("incident_id") == incident_id and sent_by(record.get("sender"), node)
                        and record.get("status") == "delivered"):
                    record["status"], record["acknowledged_s"] = "acknowledged", now
                    comms.log.append({"time_s": now, "event": "owner_acknowledged",
                                      "notification_id": record["notification_id"]})
                    marked.append(record["notification_id"])
            if not marked:
                raise LookupError(f"this farm run has no delivered, unacknowledged owner notification from {node} "
                                  f"for incident {incident_id}")
            farm_time, _ = self.farm_time()
            log(self.logger, logging.INFO, "owner acknowledged", node=node, incident_id=incident_id,
                notifications=marked, tick=self.tick, feed_id=self.feed_id)
            return {"node": node, "incident_id": incident_id, "acknowledged": len(marked), "farm_time": farm_time}

    async def cut(self, node: NodeId, seconds: float) -> LinkStatus:
        async with self._publish_lock:
            ticks = max(1, int(round(seconds / self.step_seconds)))
            self.runtime.bus.isolate(node, self.tick, self.tick + ticks)
            log(self.logger, logging.INFO, "radio link cut", node=node, ticks=ticks)
            await self.publish_clock(force=True)
            return self.link_state()

    async def restore(self, node: NodeId) -> LinkStatus:
        async with self._publish_lock:
            self.runtime.bus.isolated.pop(node, None)
            log(self.logger, logging.INFO, "radio link restored", node=node)
            await self.publish_clock(force=True)
            return self.link_state()


def create_api(farm: FarmRuntime) -> FastAPI:
    """The Matrix API (see the module docstring); every route answers with the new farm or link state."""
    app = FastAPI(title="JARVIS Matrix API (farm runtime)", version="0.6.0", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def programs_only(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """Only the gateway (a program) calls this API. A request with an Origin header comes from a browser page,
        which could start a fault or reset the farm without asking (a POST without a body needs no CORS preflight),
        so it is refused before anything changes (Q7)."""
        if request.headers.get("origin") is not None:
            return JSONResponse({"detail": "the Matrix API answers the gateway only, never a browser page"},
                                status_code=403)
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, JsonValue]:
        return {"status": "ok", "tick": farm.tick, "running": farm.running, "label": farm.label, "feed": farm.feed}

    @app.post("/sim/{action}")
    async def sim(action: Literal["start", "pause", "reset", "speed"], body: SpeedRequest | None = None) -> SimState:
        if action == "speed" and body is None:
            raise HTTPException(422, "speed needs a body: {\"speed\": S}")
        return await farm.control(action, speed=body.speed if body else None)

    @app.post("/faults/{scenario_id}")
    async def fault(scenario_id: str) -> FaultResult:
        if scenario_id not in SCENARIO_IDS:
            raise HTTPException(404, f"unknown demo fault {scenario_id!r}; known: {', '.join(SCENARIO_IDS)}")
        try:
            return await farm.inject_fault(scenario_id)  # type: ignore[arg-type]  # checked against SCENARIO_IDS
        except DemoFaultRefused as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/links/{node}/cut")
    async def cut(node: NodeId, body: CutRequest | None = None) -> LinkStatus:
        return await farm.cut(node, (body or CutRequest()).seconds)

    @app.post("/links/{node}/restore")
    async def restore(node: NodeId) -> LinkStatus:
        return await farm.restore(node)

    @app.post("/alerts/{node}/{incident_id}/ack")
    async def acknowledge(node: NodeId, incident_id: str) -> dict[str, Any]:
        try:
            return await farm.acknowledge(node, incident_id)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error

    return app


def read_ports(path: Path = ROOT / "launcher" / "ports.toml") -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


async def amain(args: argparse.Namespace) -> int:
    logger = program_logger("farm_runtime", None if args.no_logs else args.logs, echo=args.echo)
    ports = read_ports()
    link = NatsLink("farm-runtime", args.nats or f"nats://{ports['host']}:{ports['nats']}", logger=logger)
    try:
        await link.connect()
    except Exception as error:                                 # NoServersError and friends
        log(logger, logging.ERROR, "NATS unreachable", error=str(error))
        return 2
    farm = FarmRuntime(link, logger=logger, seed=args.seed, speed=args.speed, running=not args.paused,
                       feed=args.feed, threads=args.threads)
    server = uvicorn.Server(uvicorn.Config(create_api(farm), host=ports["host"], port=args.port or ports["matrix_api"],
                                           log_level="warning", lifespan="off"))
    stop = asyncio.Event()
    api = asyncio.create_task(server.serve())
    api.add_done_callback(lambda _: stop.set())
    try:
        await farm.run(stop)
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(api, timeout=5.0)
        await link.close()
        farm.close_memories()
        log(logger, logging.INFO, "farm runtime stopped", tick=farm.tick)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--feed", choices=("simulated", "trained"), default="simulated",
                        help="simulated: the snapshot's rule brains (default); trained: the release's CTMs (6D)")
    parser.add_argument("--threads", type=int, default=TORCH_THREADS, help="torch threads, trained feed (default 1)")
    parser.add_argument("--seed", type=int, default=7, help="farm seed (default 7)")
    parser.add_argument("--speed", type=float, default=1.0, help="farm seconds per real second (default 1)")
    parser.add_argument("--paused", action="store_true", help="start paused")
    parser.add_argument("--nats", help="NATS URL (default: from launcher/ports.toml)")
    parser.add_argument("--port", type=int, help="Matrix API port (default: from launcher/ports.toml)")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--logs", type=Path, default=ROOT / "logs", help="log folder (default: logs/)")
    where.add_argument("--no-logs", action="store_true", help="no log file")
    parser.add_argument("--echo", action="store_true", help="also print the log to stderr")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
