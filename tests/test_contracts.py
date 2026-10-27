"""contracts.py: the wire messages and node.toml are strict, versioned and validated at the border (Q1, Q2)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import export_contracts
from contracts import (NODE_IDS, AskReply, AskRequest, Capabilities, ContractError, DocumentInfo, FarmAssessment, FarmEvent,
                       FarmIncident, FarmStatus, Heartbeat, LinkStatus, ProbeRequest, SimState, decode, encode,
                       farm_subject, jarvis_subject, load_node_config, node_of_subject, utc_now)

ROOT = Path(__file__).resolve().parents[1]
FARM: dict[str, Any] = dict(node="hvac", tick=12, time_s=12.0, farm_time="06:00", label="live: simulated brains", feed_id="0000000000a1")

SAMPLES = [
    FarmStatus(**FARM, online=True, readings={"supply_air_temperature_c": 14.2, "alarms": []}),
    FarmEvent(**FARM, sequence=3, type="alarm_raised", severity="warning",
              data={"code": "SUPPLY_FAN_FAILURE", "source": "alarm"}),
    DocumentInfo(file="hvac_manual.md", title="HVAC manual", kind="machine manual", version=1, sha256="a" * 64),
    FarmAssessment(**FARM, assessment="WATCHING", diagnosis="SUPPLY_FAN_FAULT", confidence=0.7, brain="rule"),
    FarmIncident(**FARM, incident_id="hvac-equipment-0001", state="opened", kind="equipment", summary={"x": 1}),
    LinkStatus(tick=12, time_s=12.0, cut=["hvac"], offline=[]),
    SimState(tick=12, time_s=12.0, farm_time="06:00", day=1, running=True, target_speed=1.0, actual_speed=1.0,
             label="live: simulated brains", feed_id="0000000000a1", episode_id="e1"),
    Heartbeat(node="hvac", seq=1, sent_utc=utc_now(), started_utc=utc_now(), uptime_s=1.5, known=["hvac"],
              alive=["hvac"]),
    Capabilities(node="hvac", machine="hvac", speaks=False, topics=["status", "readings"], documents=[
        DocumentInfo(file="hvac_manual.md", title="HVAC manual", kind="machine manual", version=1, sha256="a" * 64)],
                 started_utc=utc_now(), reason="start"),
    AskRequest(request_id="q-1", sender="incubator-1", target="hvac", topic="readings", sent_utc=utc_now(),
               deadline_ms=3000),
    AskReply(request_id="q-1", sender="hvac", target="incubator-1", topic="readings", ok=True, answer={"a": 1},
             farm_time="06:00", tick=12),
    ProbeRequest(target="hvac", topic="status"),
]


@pytest.mark.parametrize("node", NODE_IDS)
def test_every_node_toml_is_valid_for_its_folder(node: str) -> None:
    config = load_node_config(ROOT / "nodes" / node / "node.toml")
    assert config.id == node and config.speaks == (node == "incubator-1")


@pytest.mark.parametrize("message", SAMPLES, ids=[type(m).__name__ for m in SAMPLES])
def test_every_message_round_trips_through_json(message: object) -> None:
    model = type(message)
    assert decode(model, encode(message)) == message                     # type: ignore[arg-type]


def test_invalid_messages_are_refused_with_a_reason() -> None:
    good = json.loads(encode(SAMPLES[0]))
    for change, where in ((dict(schema_version=2), "schema_version"), (dict(farm_time="25:00"), "farm_time"),
                          (dict(surprise=1), "surprise"), (dict(node="barn-9"), "node"),
                          (dict(online="yes"), "online"), (dict(tick="12"), "tick")):     # strict: no coercion
        with pytest.raises(ContractError, match=f"FarmStatus: {where}"):
            decode(FarmStatus, json.dumps(good | change))
    with pytest.raises(ContractError, match="FarmStatus"):
        decode(FarmStatus, b"not json")


def test_questions_and_answers_are_consistent() -> None:
    with pytest.raises(ValueError, match="does not ask itself"):
        AskRequest(request_id="q-2", sender="hvac", target="hvac", topic="status", sent_utc=utc_now(), deadline_ms=1)
    with pytest.raises(ValueError, match="either ok or has an error"):
        AskReply(request_id="q-2", sender="hvac", target="incubator-1", topic="status", ok=True, error="timeout")
    with pytest.raises(ValueError, match="either ok or has an error"):
        AskReply(request_id="q-2", sender="hvac", target="incubator-1", topic="status", ok=False)
    with pytest.raises(ValueError):
        ProbeRequest(target="hvac", topic="status", question="bad\x07bell")


def test_node_config_rejects_a_wrong_machine_or_server(tmp_path: Path) -> None:
    text = (ROOT / "nodes" / "hvac" / "node.toml").read_text(encoding="utf-8")
    bad = tmp_path / "node.toml"
    bad.write_text(text.replace('machine = "hvac"', 'machine = "incubator"'), encoding="utf-8")
    with pytest.raises(ContractError, match="is a hvac"):
        load_node_config(bad)
    bad.write_text(text.replace('server = "hvac"', 'server = "incubator-1"'), encoding="utf-8")
    with pytest.raises(ContractError, match="own LLM server"):
        load_node_config(bad)


def test_subjects() -> None:
    assert farm_subject("hvac", "status") == "farm.hvac.status"
    assert jarvis_subject("*", "heartbeat") == "jarvis.*.heartbeat"
    assert node_of_subject("jarvis.animal-protection.ask") == "animal-protection"
    assert node_of_subject("farm.links") is None


def test_the_exported_json_schemas_are_up_to_date() -> None:
    assert export_contracts.main(["--check"]) == 0, "run: python tools/export_contracts.py"
    for path in (ROOT / "interface" / "contracts").glob("*.schema.json"):
        assert "do not edit" in json.loads(path.read_text(encoding="utf-8"))["$comment"]


def test_the_export_writes_and_checks_a_folder(tmp_path: Path) -> None:
    assert export_contracts.main(["--check"], out=tmp_path) == 1
    assert export_contracts.main([], out=tmp_path) == 0
    assert export_contracts.main(["--check"], out=tmp_path) == 0
    names = {p.name for p in tmp_path.iterdir()}
    assert {"FarmStatus.schema.json", "AskReply.schema.json", "FarmSnapshot.schema.json"} <= names
    (tmp_path / "Retired.schema.json").write_text("{}", encoding="utf-8")         # a model that no longer exists
    assert export_contracts.main(["--check"], out=tmp_path) == 1
    assert export_contracts.main([], out=tmp_path) == 0
    assert not (tmp_path / "Retired.schema.json").exists()
