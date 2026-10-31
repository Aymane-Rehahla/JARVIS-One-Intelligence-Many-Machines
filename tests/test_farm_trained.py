"""The farm runtime's trained feed (P3 step 5, decisions 0015 and 0021): the release installed in matrix/release_v31,
its four CTM brains built as the exam built them, a fresh CMM per farm run, every Matrix API route, and the owner
acknowledgement reaching the brains' own notifications. Real brains on the CPU (about 35 ms a tick); no NATS."""
from __future__ import annotations

import ast
import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import farm_runtime as fr
from contracts import FarmAssessment, FarmIncident, FarmStatus

pytest.importorskip("torch")
pytestmark = pytest.mark.skipif(not (fr.RELEASE / "PROVENANCE.json").is_file(),
                                reason="the release is not installed (tools/install_release.py)")
ROOT = Path(__file__).resolve().parents[1]


class Capture:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def publish(self, subject: str, message: Any, **_: Any) -> None:
        self.sent.append((subject, message))


@pytest.fixture(scope="module")
def farm(tmp_path_factory: pytest.TempPathFactory) -> Any:
    import logging
    runtime = fr.FarmRuntime(Capture(), logger=logging.getLogger("test-farm-trained"), feed="trained",  # type: ignore[arg-type]
                             running=False, data_dir=tmp_path_factory.mktemp("matrix-data"))
    yield runtime
    runtime.close_memories()


def test_the_trained_feed_runs_the_release_brains_on_an_ordinary_day(farm: Any) -> None:
    asyncio.run(farm.control("reset"))
    assert farm.label == "live: trained brains" and farm.feed == "trained"
    assert farm.start_hour == pytest.approx(6.87)                                  # decision 0021: normal, variant 1
    assert float(farm.engine.comms.hour_of_day()) == pytest.approx(farm.start_hour)   # the brains' hour = the farm's
    assert farm.farm_time() == ("06:52", 1)
    for node in farm.runtime.nodes.values():
        brain = node.brain
        assert type(brain).__name__ == "TrainedNodeBrain" and brain.hide_notes and brain.hold_calls
        assert brain.decider is not None and brain.start_hour == farm.start_hour
    one, two = farm.runtime.nodes["incubator-1"].brain, farm.runtime.nodes["incubator-2"].brain
    assert one.brain is not two.brain                                               # each incubator its own copy
    seen: dict[str, int] = defaultdict(int)
    for _ in range(20):
        for _, message in farm.messages_for_tick(farm.step(), include_state=True):
            if isinstance(message, (FarmStatus, FarmAssessment)):
                assert message.label == "live: trained brains" and message.feed_id == farm.feed_id
                seen[type(message).__name__] += 1
            if isinstance(message, FarmAssessment):
                assert message.brain == "ctm"
    assert seen == {"FarmStatus": 80, "FarmAssessment": 80}


def test_each_farm_run_has_fresh_cmm_files(farm: Any) -> None:
    asyncio.run(farm.control("reset"))
    for _ in range(3):
        farm.step()
    folder = farm.data_dir / "cmm" / farm.feed_id
    assert sorted(p.name for p in folder.glob("*.sqlite")) == [f"{n}.sqlite" for n in sorted(fr.NODE_IDS)]
    asyncio.run(farm.control("reset"))
    assert not folder.exists() and (farm.data_dir / "cmm" / farm.feed_id).is_dir()  # the old run's memory is gone


def test_every_matrix_api_route_works_on_the_trained_feed(farm: Any) -> None:
    with TestClient(fr.create_api(farm)) as client:
        assert client.get("/health").json()["label"] == "live: trained brains"
        assert client.post("/sim/reset").status_code == 200
        assert client.post("/sim/speed", json={"speed": 30}).json()["target_speed"] == 30
        assert client.post("/sim/pause").json()["running"] is False
        assert client.post("/links/hvac/cut", json={"seconds": 30}).json()["cut"] == ["hvac"]
        assert client.post("/links/hvac/restore").json()["cut"] == []
        fault = client.post("/faults/supply_overvoltage")
        assert fault.status_code == 200 and fault.json()["devices"] == ["incubator-1", "incubator-2"]
        assert client.post("/faults/heater_relay_stuck").status_code == 409
        assert client.post("/alerts/hvac/hvac-equipment-0001/ack", params={"feed_id": farm.feed_id}).status_code == 404
        assert client.post("/sim/reset").status_code == 200


def test_the_acknowledgement_reaches_the_trained_brains_notifications(farm: Any) -> None:
    """The incident id in each brain's owner notification is one its node published on farm.<node>.incident, so the
    website's acknowledgement (node + incident) reaches the right record (P3 step 5)."""
    asyncio.run(farm.control("reset"))
    asyncio.run(farm.inject_fault("supply_overvoltage"))
    published: dict[str, set[str]] = defaultdict(set)
    for _ in range(60):
        for _, message in farm.messages_for_tick(farm.step(), include_state=True):
            if isinstance(message, FarmIncident):
                published[message.node].add(message.incident_id)
    notes = farm.engine.comms.notifications
    assert notes, "no owner notification in 60 ticks of an overvoltage"
    for note in notes:
        node = note["sender"].removeprefix("jarvis-")
        assert fr.sent_by(note["sender"], node) and note["incident_id"] in published[node], note
    target = next(n for n in notes if n["status"] == "delivered")
    node = target["sender"].removeprefix("jarvis-")
    with TestClient(fr.create_api(farm)) as client:
        answer = client.post(f"/alerts/{node}/{target['incident_id']}/ack", params={"feed_id": farm.feed_id})
    assert answer.status_code == 200 and answer.json()["acknowledged"] >= 1
    assert target["status"] == "acknowledged"
    asyncio.run(farm.control("reset"))


def test_the_farm_runtime_never_imports_a_language_model() -> None:
    """The safety path never depends on the LLM (ground rule 4): no import from any node's llm/ package."""
    tree = ast.parse((ROOT / "matrix" / "farm_runtime.py").read_text(encoding="utf-8"))
    names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    names += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [name for name in names if name == "llm" or name.startswith("llm.") or "engines" in name]
