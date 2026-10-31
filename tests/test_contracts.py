"""contracts.py: the wire messages and node.toml are strict, versioned and validated at the border (Q1, Q2)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import export_contracts
from contracts import (NODE_IDS, AckCommand, AlertNotice, AckOffer, AckResult, AskReply, AskRequest, AuditRecord, Capabilities,
                       ChatChunk, ChatDone, ChatRequest, Citation, ContractError, DocumentInfo, FarmAssessment, FarmEvent,
                       FarmIncident, FarmStatus, Heartbeat, LinkStatus, LlmFailureSwitches, OwnerAlert, ProbeRequest,
                       PushToTalk, ReplyTimings, SimState, ToolUse, VoiceSettings, VoiceSettingsResult, VoiceStatus,
                       decode, encode, farm_subject, jarvis_subject, load_node_config, node_of_subject, utc_now)

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
    AuditRecord(request_id="q-1", node="hvac", kind="tool_call", tool="get_status", ok=True, error=None,
                duration_ms=3, farm_time="06:00", tick=12, inputs={"fields": ["online"]}, outputs={"online": True},
                truncated=False, created_utc=utc_now()),
    # 6C and 6E (fixed by the senior before they were built)
    ChatRequest(request_id="c-1", text="What's wrong with the HVAC?", source="chat", sent_utc=utc_now()),
    ChatChunk(request_id="c-1", seq=0, text="The HVAC's supply fan has stopped (farm time 06:00). "),
    ChatDone(request_id="c-1", conversation_id="conv-1", text="The HVAC's supply fan has stopped (farm time 06:00). ",
             chunks=1, outcome="answered", numbers_checked=1, farm_time="06:00", label="live: simulated brains",
             citations=[Citation(node="hvac", file="hvac_manual.md", section="Supply fan")],
             tools=[ToolUse(node="hvac", tool="get_status", ok=True, duration_ms=4)],
             timings=ReplyTimings(first_chunk_ms=900, total_ms=2100, llm_ms=1700, tools_ms=240),
             confirm=AckOffer(node="hvac", incident_id="hvac-equipment-0001", feed_id="0000000000a1", token="t0k3n", expires_utc=utc_now(),
                              summary="hvac incident hvac-equipment-0001 (supply fan failure)")),
    ChatDone(request_id="c-2", conversation_id="conv-1", text="I only talk; the machines act through their safety gate.",
             chunks=0, outcome="refused", reason="asked for a machine action", numbers_checked=0,
             timings=ReplyTimings(total_ms=12)),
    OwnerAlert(alert_id="a-1", node="hvac", level="DANGER", incident_id="hvac-equipment-0001",
               text="Sir, the HVAC reports DANGER: the supply fan has failed. Farm time 06:00.", farm_time="06:00",
               feed_id="0000000000a1", label="live: simulated brains", reworded=False, sent_utc=utc_now()),
    AlertNotice(notice_id="n-1", sender="hvac", target="incubator-1", sent_utc=utc_now(),
                assessment=FarmAssessment(**FARM, assessment="ESCALATE_TO_USER", diagnosis="SUPPLY_FAN_FAULT",
                                          confidence=0.9, brain="rule")),
    AckCommand(request_id="k-1", node="hvac", incident_id="hvac-equipment-0001", feed_id="0000000000a1",
               confirm_token="t0k3n",
               sent_utc=utc_now()),
    AckResult(request_id="k-1", node="hvac", incident_id="hvac-equipment-0001", ok=False, reason="token already used"),
    LlmFailureSwitches(llm_timeout=["hvac"], silent_peer=["animal-protection"], set_utc=utc_now()),
    VoiceStatus(state="listening", mic="ok", voice="bm_george", speed=1.0, push_to_talk=True, sent_utc=utc_now()),
    VoiceSettings(voice="bm_george", speed=1.1),
    VoiceSettingsResult(ok=True, voice="bm_george", speed=1.1, voices=["bm_george", "bf_emma"]),
    PushToTalk(pressed=True, sent_utc=utc_now()),
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


def test_chat_replies_and_results_are_consistent() -> None:
    timings = ReplyTimings(total_ms=5)
    done: dict[str, Any] = dict(request_id="c-9", conversation_id="conv-9", text="", chunks=0, numbers_checked=0,
                                timings=timings)
    with pytest.raises(ValueError, match="has no reason"):
        ChatDone(**done, outcome="answered", reason="why not", farm_time="06:00", label="live: simulated brains")
    with pytest.raises(ValueError, match="has no reason"):
        ChatDone(**done, outcome="error")                                       # every other outcome says why
    with pytest.raises(ValueError, match="states the farm time"):
        ChatDone(**done, outcome="fallback", reason="hvac is silent")
    offer = AckOffer(node="hvac", incident_id="i-1", feed_id="0000000000a1", token="t", expires_utc=utc_now(), summary="hvac incident i-1")
    with pytest.raises(ValueError, match="only an answered reply"):
        ChatDone(**done, outcome="refused", reason="no", confirm=offer)
    with pytest.raises(ValueError, match="ok, or refused"):
        AckResult(request_id="k-9", node="hvac", incident_id="i-1", ok=True, reason="but")
    with pytest.raises(ValueError, match="names a node twice"):
        LlmFailureSwitches(broken_json=["hvac", "hvac"], set_utc=utc_now())
    with pytest.raises(ValueError, match="control characters"):
        ChatRequest(request_id="c-9", text="bad\x07bell", source="chat", sent_utc=utc_now())
    with pytest.raises(ValueError):
        VoiceSettings(voice="George", speed=1.0)                               # not a Kokoro voice name
    with pytest.raises(ValueError):
        VoiceSettings(voice="bm_george", speed=2.5)


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
    assert jarvis_subject("incubator-1", "reply") == "jarvis.incubator-1.reply"
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
