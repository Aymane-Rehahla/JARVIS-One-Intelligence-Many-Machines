"""The nine node tools validate inputs, obey deadlines and permissions, and audit every result."""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from contracts import FarmAssessment, FarmEvent, FarmIncident, FarmStatus, load_node_config
from llm.audit import Auditor
from llm.capabilities import classify_topic
from llm.conversation import LlmStore
from llm.documents import Documents, HashEmbedder
from llm.tools import ConfirmTokens, ToolRegistry

ROOT = Path(__file__).resolve().parents[2]


class FakePeers:
    def __init__(self) -> None:
        self.available = True
        self.alive = True
        self.delay = 0.0
        self.calls: list[tuple[str, str, str | None]] = []

    def capabilities_of(self, node: str) -> Any:
        return SimpleNamespace(topics=["hazard_check", "readings", "status"]) if self.available else None

    def is_alive(self, node: str) -> bool:
        return self.alive

    async def ask(self, node: str, topic: str, question: str, *, deadline_s: float, request_id: str | None) -> Any:
        self.calls.append((node, topic, request_id))
        await asyncio.sleep(self.delay)
        return SimpleNamespace(ok=True, answer={"smoke": False, "label": "live: simulated brains"},
                               farm_time="06:00", tick=7, detail="", error=None)


@pytest_asyncio.fixture
async def rig(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    store = LlmStore(tmp_path / "llm.sqlite", node="incubator-1", owner_conversations=True)
    documents = Documents(tmp_path / "rom", tmp_path / "docs_index.sqlite", HashEmbedder(), tokenizer_path=None)
    documents.rom.mkdir()
    status = FarmStatus(node="incubator-1", tick=7, time_s=120.0, farm_time="06:02",
                        label="live: simulated brains", online=True,
                        readings={"supply_air_temperature_c": 20.0, "relative_humidity_pct": 55.0,
                                  "fan_running": True, "alarms": []})
    assessment = FarmAssessment(node="incubator-1", tick=7, time_s=120.0, farm_time="06:02",
                               label="live: simulated brains", assessment="NORMAL", diagnosis="NORMAL",
                               confidence=0.9, brain="rule")
    view = SimpleNamespace(status=status, assessment=assessment, sim=None,
                           trend_buffer=deque([FarmStatus(node="incubator-1", tick=i, time_s=i * 60.0,
                                                         farm_time=f"06:{i:02d}", label="live: simulated brains",
                                                         online=True, readings={"temperature": float(10 + 2 * i)})
                                               for i in range(3)], maxlen=7200))
    auditor = Auditor(store, None, "incubator-1")
    confirm = ConfirmTokens(b"test-secret")
    delivered: list[str] = []

    async def deliver(incident_id: str) -> None:
        delivered.append(incident_id)

    peers = FakePeers()
    registry = ToolRegistry(load_node_config(ROOT / "nodes" / "incubator-1" / "node.toml"), view, store,
                            documents, peers, auditor, confirm, deliver)
    yield {"store": store, "documents": documents, "view": view, "auditor": auditor, "confirm": confirm,
           "delivered": delivered, "peers": peers, "registry": registry}
    documents.close()
    await store.close()


@pytest.mark.asyncio
async def test_each_tool_succeeds_with_typed_data_and_audit(rig: dict[str, Any]) -> None:
    store: LlmStore = rig["store"]
    registry: ToolRegistry = rig["registry"]
    await store.add_event(FarmEvent(node="incubator-1", tick=7, time_s=120.0, farm_time="06:02",
                                    label="live: simulated brains", sequence=7, type="alarm_raised",
                                    severity="warning", data={"code": "FILTER", "source": "alarm"}))
    await store.upsert_incident(FarmIncident(node="incubator-1", tick=7, time_s=120.0, farm_time="06:02",
                                             label="live: simulated brains", incident_id="incident-1", state="opened",
                                             kind="equipment", summary={"code": "FILTER"}))
    conversation_id = await store.new_conversation("HVAC")
    await store.add_turn(conversation_id, "history-1", "owner", "Check the airflow")
    confirm: ConfirmTokens = rig["confirm"]
    token = confirm.issue("incident-1")
    calls = [
        ("get_status", {"fields": ["supply_air_temperature_c"]}),
        ("get_assessment", {}),
        ("get_trend", {"signal": "temperature", "minutes": 10}),
        ("list_events", {"since_minutes": 60, "severity": "warning", "limit": 10}),
        ("get_incident", {"incident_id": "incident-1"}),
        ("search_documents", {"query": "compressor", "k": 4}),
        ("ask_peer", {"node": "animal-protection", "question": "is there smoke or flood?"}),
        ("recall_conversation", {"query": "airflow", "limit": 5}),
        ("acknowledge_alert", {"incident_id": "incident-1", "confirm_token": token}),
    ]
    for index, (name, args) in enumerate(calls):
        result = await registry.call(name, args, request_id=f"tool-{index}", caller="owner")
        assert result.ok, (name, result.error)
        assert result.tool_result(name).name == name
        rows = await rig["auditor"].timings(f"tool-{index}")
        assert sum(row["kind"] == "tool_call" for row in rows) == 1
        if name == "ask_peer":
            assert sum(row["kind"] == "peer_question" for row in rows) == 1
    assert registry.tools["get_status"].permission == "read"
    assert rig["peers"].calls == [("animal-protection", "hazard_check", "tool-6")]
    assert rig["delivered"] == ["incident-1"]


@pytest.mark.asyncio
async def test_bad_arguments_and_unknown_tools_are_outcomes_and_audited(rig: dict[str, Any]) -> None:
    registry: ToolRegistry = rig["registry"]
    for index, name in enumerate(registry.tools):
        result = await registry.call(name, {"unexpected": True}, request_id=f"bad-{index}", caller="owner")
        assert not result.ok and result.error and result.error.startswith("invalid arguments:")
        assert len(await rig["auditor"].timings(f"bad-{index}")) == 1
    unknown = await registry.call("invented", {}, request_id="unknown", caller="owner")
    assert not unknown.ok and unknown.error == "unknown tool: invented"
    row = (await rig["auditor"].timings("unknown"))[0]
    assert row["kind"] == "refusal" and row["ok"] == 0


@pytest.mark.asyncio
async def test_empty_data_paths_are_explicit(rig: dict[str, Any]) -> None:
    registry: ToolRegistry = rig["registry"]
    registry.view.status = None
    registry.view.assessment = None
    registry.view.trend_buffer.clear()
    registry.peers.available = False
    for name, args, phrase in (("get_status", {}, "no status"), ("get_assessment", {}, "no assessment"),
                               ("get_trend", {"signal": "temperature", "minutes": 5}, "not enough data"),
                               ("get_incident", {"incident_id": "missing"}, "no incident"),
                               ("ask_peer", {"node": "hvac", "question": "read status"}, "unknown")):
        result = await registry.call(name, args, request_id=f"empty-{name}", caller="owner")
        assert not result.ok and phrase in (result.error or "")
    events = await registry.call("list_events", {"since_minutes": 60}, request_id="empty-events", caller="owner")
    documents = await registry.call("search_documents", {"query": "manual"}, request_id="empty-docs", caller="owner")
    recall = await registry.call("recall_conversation", {"query": "nothing"}, request_id="empty-recall", caller="owner")
    assert events.ok and events.data["events"] == []
    assert documents.ok and documents.data["results"] == [] and documents.data["status"] == "no approved documents"
    assert recall.ok and recall.data["turns"] == []
    token = rig["confirm"].issue("missing")
    ack = await registry.call("acknowledge_alert", {"incident_id": "missing", "confirm_token": token},
                              request_id="empty-ack", caller="owner")
    assert not ack.ok and ack.error == "no active incident to acknowledge"


@pytest.mark.asyncio
async def test_speaker_only_tools_are_refused_on_silent_nodes(rig: dict[str, Any]) -> None:
    registry: ToolRegistry = rig["registry"]
    registry.node_config = load_node_config(ROOT / "nodes" / "hvac" / "node.toml")
    for index, (name, args) in enumerate((
            ("ask_peer", {"node": "hvac", "question": "status"}),
            ("recall_conversation", {"query": "past"}),
            ("acknowledge_alert", {"incident_id": "i1", "confirm_token": "forged"}))):
        result = await registry.call(name, args, request_id=f"silent-{index}", caller="owner")
        assert not result.ok and "not the speaking node" in (result.error or "")
        assert (await rig["auditor"].timings(f"silent-{index}"))[0]["kind"] == "refusal"


@pytest.mark.asyncio
async def test_confirmation_forgery_expiry_and_reuse_are_refused(rig: dict[str, Any]) -> None:
    confirm: ConfirmTokens = rig["confirm"]
    token = confirm.issue("incident-1")
    assert not confirm.verify("other-incident", token)
    expired = confirm.issue("incident-1", now_s=100)
    assert not confirm.verify("incident-1", expired, now_s=161)
    valid = confirm.issue("incident-1")
    assert confirm.verify("incident-1", valid)
    assert not confirm.verify("incident-1", valid)
    result = await rig["registry"].call("acknowledge_alert", {"incident_id": "incident-1", "confirm_token": "bad"},
                                        request_id="refused-token", caller="owner")
    assert not result.ok and result.error == "invalid confirmation token"
    assert (await rig["auditor"].timings("refused-token"))[0]["kind"] == "refusal"


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args"), [
    ("get_status", {"fields": []}), ("get_assessment", {}),
    ("get_trend", {"signal": "temperature", "minutes": 10}),
    ("list_events", {"since_minutes": 10}), ("get_incident", {"incident_id": "incident-1"}),
    ("search_documents", {"query": "manual"}),
    ("ask_peer", {"node": "animal-protection", "question": "is there smoke?"}),
    ("recall_conversation", {"query": "history"}),
    ("acknowledge_alert", {"incident_id": "incident-1", "confirm_token": "placeholder"}),
])
async def test_every_tool_deadline_returns_timeout_and_preserves_request_id(rig: dict[str, Any], name: str,
                                                                            args: dict[str, Any]) -> None:
    registry: ToolRegistry = rig["registry"]

    async def slow(raw: Any, request_id: str, caller: str) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {}

    registry.tools[name] = replace(registry.tools[name], deadline_s=0.001, handler=slow)
    if name == "acknowledge_alert":
        args["confirm_token"] = rig["confirm"].issue("incident-1")
        await rig["store"].upsert_incident(FarmIncident(node="incubator-1", tick=7, time_s=120.0, farm_time="06:02",
                                                        label="live: simulated brains", incident_id="incident-1",
                                                        state="opened", summary={}))
    result = await registry.call(name, args, request_id=f"slow-{name}", caller="owner")
    assert not result.ok and result.error == "timeout"
    rows = await rig["auditor"].timings(f"slow-{name}")
    assert len(rows) == 1 and rows[0]["request_id"] == f"slow-{name}"
    assert str(rows[0]["error"]).startswith("TimeoutError")


@pytest.mark.asyncio
async def test_peer_refusals_do_not_use_radio(rig: dict[str, Any]) -> None:
    peers: FakePeers = rig["peers"]
    peers.available = False
    result = await rig["registry"].call("ask_peer", {"node": "hvac", "question": "status"},
                                        request_id="unknown-peer", caller="owner")
    assert not result.ok and "unknown" in (result.error or "")
    assert peers.calls == []
    peers.available = True
    peers.alive = False
    result = await rig["registry"].call("ask_peer", {"node": "hvac", "question": "status"},
                                        request_id="dead-peer", caller="owner")
    assert not result.ok and "not alive" in (result.error or "") and peers.calls == []


@pytest.mark.parametrize(("question", "topic"), [
    ("is there smoke?", "hazard_check"), ("what about fire?", "hazard_check"),
    ("any flood warning?", "hazard_check"), ("prepare for a storm", "hazard_check"),
    ("is there a hazard?", "hazard_check"), ("do you detect sparks?", "hazard_check"),
    ("show the manual", "documents"), ("find a document", "documents"),
    ("which guide applies?", "documents"), ("what are the sources?", "documents"),
    ("show citations", "documents"), ("give instructions", "documents"),
    ("what happened in the incident?", "incident_history"), ("list alarms", "incident_history"),
    ("which fault occurred?", "incident_history"), ("show recent events", "incident_history"),
    ("what was the previous alert?", "incident_history"), ("was that cleared?", "incident_history"),
    ("how are the eggs?", "incubation"), ("how does hatching work?", "incubation"),
    ("incubator incubation status", "incubation"), ("embryo development?", "incubation"),
    ("what is the egg temperature?", "incubation"), ("hatch progress", "incubation"),
    ("what is the temperature?", "readings"), ("read the humidity", "readings"),
    ("check pressure readings", "readings"), ("is the fan running?", "readings"),
    ("compressor state", "readings"), ("supply airflow", "readings"),
    ("hello jarvis", "status"), ("how are you?", "status"), ("tell me the status", "status"),
    ("what is happening?", "status"), ("give an overview", "status"), ("are you online?", "status"),
])
def test_topic_classifier_examples(question: str, topic: str) -> None:
    assert classify_topic(question) == topic
