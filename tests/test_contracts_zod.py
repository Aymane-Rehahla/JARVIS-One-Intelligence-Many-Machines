"""The website's zod schemas accept and refuse exactly what the pydantic contracts accept and refuse (Q2).

The same sample messages, valid and broken, go through pydantic here and through interface/web/src/services/contracts.ts
under Node (scripts/check-contracts.ts, Node 24 type stripping, no build). Node is the portable one the website uses;
the test skips with a message when it is not there.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

import gateway as gw
from contracts import FaultResult, Heartbeat, utc_now
from test_contracts import SAMPLES

#: Messages that exist only between the website and the gateway (the NATS ones are in test_contracts.SAMPLES).
WEBSITE_SAMPLES: list[BaseModel] = [
    gw.ChatSend(client_msg_id="m-1", conversation_id=None, text="What's wrong with the HVAC?"),
    gw.ChatAccepted(client_msg_id="m-1", request_id="c-1"),
    gw.ChatError(client_msg_id="m-2", request_id=None, reason="one question at a time"),
    gw.AckResponse(node="hvac", incident_id="hvac-equipment-0001", ok=False, stage="token", reason="token expired"),
    gw.OkResponse(ok=True),
    FaultResult(scenario_id="supply_overvoltage", family="overvoltage", variant=1,
                devices=["incubator-1", "incubator-2"], tick=5, end_tick=226, time_s=5.0, farm_time="06:00",
                feed_id="0000000000a1"),
]
#: Models that the website only ever sees inside another message.
NESTED_ONLY = {"NodeView", "GateDecision", "Citation", "ToolUse", "ReplyTimings", "AckOffer"}

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "interface" / "web"
NODE = Path(os.environ.get("JARVIS_NODE", r"C:\Users\Aymane\node\node-v24.22.1-win-x64\node.exe"))


def snapshot() -> gw.FarmSnapshot:
    view = gw.FarmView()
    for message in SAMPLES:
        if isinstance(message, gw.FarmStatus):
            view.status[message.node] = message
        elif isinstance(message, gw.FarmAssessment):
            view.assessment[message.node] = message
        elif isinstance(message, gw.FarmEvent):
            view.events.append(message)
        elif isinstance(message, gw.SimState):
            view.sim = message
        elif isinstance(message, gw.LinkStatus):
            view.links = message
        elif isinstance(message, gw.Capabilities):
            view.capabilities[message.node] = message
        elif isinstance(message, Heartbeat):
            view.heartbeats[message.node] = (message, time.monotonic())
    return view.snapshot()


def broken(name: str, message: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Ways to break a valid message that both sides must refuse."""
    cases = [message | {"schema_version": 2}, message | {"surprise": 1}]
    cases += [message | {key: "25:00"} for key in ("farm_time",) if key in message]
    cases += [message | {key: "barn-9"} for key in ("node", "sender", "target") if key in message]
    cases += [message | {key: -1} for key in ("tick", "seq", "sequence") if key in message]
    if name == "AskReply":
        cases += [message | {"error": "timeout"}, message | {"ok": False}]          # neither ok xor error
    if name == "AskRequest":
        cases += [message | {"target": message["sender"]}, message | {"deadline_ms": 30_001},
                  message | {"request_id": "has spaces"}]
    if name == "FarmSnapshot":
        node = dict(message["nodes"]["hvac"]) | {"machine": "incubator"}
        cases += [message | {"nodes": message["nodes"] | {"hvac": node | {"alive": "yes"}}}]
    if name == "ChatDone":
        cases += [message | {"reason": "why"} if message["outcome"] == "answered" else message | {"reason": ""},
                  message | {"outcome": "fallback", "reason": "x", "farm_time": None},
                  message | {"outcome": "error", "reason": "x", "confirm": message["confirm"] or {}},
                  message | {"citations": [{"node": "hvac", "file": "", "section": ""}]},
                  message | {"timings": message["timings"] | {"total_ms": 1.5}}]
    if name == "ChatSend":
        cases += [message | {"text": "bad\x07bell"}, message | {"text": ""}, message | {"client_msg_id": "has space"}]
    if name in ("VoiceStatus", "VoiceSettings", "VoiceSettingsResult"):
        cases += [message | {"voice": "George"}, message | {"speed": 2.5}]
    if name in ("VoiceSettingsResult", "OkResponse"):
        cases += [message | {"ok": not message["ok"]}]
    if name == "AckResponse":
        cases += [message | {"ok": True}, message | {"stage": "done"}, message | {"reason": ""}]
    if name == "FarmSnapshot" and message["events"]:
        cases += [message | {"events": message["events"] * 51}]                  # over the 50 shown
    if name == "FaultResult":
        cases += [message | {"end_tick": 0}, message | {"end_tick": "soon"}, message | {"scenario_id": "fire_in_the_barn"}, message | {"devices": []}, message | {"devices": ["barn-9"]},
                  message | {"family": ""}]
    if name == "LlmFailureSwitches":
        cases += [message | {"server_crash": ["hvac", "hvac"]}, message | {"broken_json": ["barn-9"]}]
    if name == "OwnerAlert":
        cases += [message | {"level": "WATCHING"}, message | {"feed_id": "XYZ"}, message | {"text": ""}]
    if name == "AuditRecord":
        cases += [message | {"kind": "chat"}, message | {"outputs": {"big": "x" * 5000}}]
    return [(name, case) for case in cases]


def pydantic_accepts(name: str, message: dict[str, Any]) -> bool:
    model: type[BaseModel] = {type(m).__name__: type(m) for m in [*SAMPLES, *WEBSITE_SAMPLES, snapshot()]}[name]
    try:
        model.model_validate_json(json.dumps(message))
        return True
    except ValidationError:
        return False


def gateway_models() -> set[str]:
    """What the website receives or sends: FarmSnapshot, gateway.WEBSITE_MODELS and every model inside them (JsonValue
    is a type, not a model)."""
    names = {"FarmSnapshot", *gw.FarmSnapshot.model_json_schema(mode="serialization")["$defs"]}
    for model in gw.WEBSITE_MODELS:
        names |= {model.__name__, *model.model_json_schema(mode="serialization").get("$defs", {})}
    return names - {"JsonValue"}


def zod(samples: list[tuple[str, Any]], tmp_path: Path) -> list[dict[str, Any]]:
    """Each sample's result from the website's schemas."""
    if not NODE.is_file():
        pytest.skip(f"Node 24 not found at {NODE} (set JARVIS_NODE)")
    path = tmp_path / "samples.json"
    path.write_text(json.dumps([{"schema": name, "message": message} for name, message in samples]),
                    encoding="utf-8")
    run = subprocess.run([str(NODE), "--no-warnings", "scripts/check-contracts.ts", str(path)], cwd=WEB,
                         capture_output=True, text=True, timeout=60)
    results = [json.loads(line) for line in run.stdout.splitlines() if line.startswith("{")]
    assert len(results) == len(samples), run.stderr[-2000:]
    return results


def test_the_website_has_a_schema_for_everything_the_gateway_sends(tmp_path: Path) -> None:
    names = sorted(gateway_models())
    missing = [name for name, result in zip(names, zod([(name, {}) for name in names], tmp_path))
               if result["issues"] == ["unknown schema"]]
    assert missing == [], "add these to SCHEMAS in interface/web/src/services/contracts.ts"


def test_zod_and_pydantic_agree_on_every_sample(tmp_path: Path) -> None:
    valid = [(type(m).__name__, json.loads(m.model_dump_json())) for m in [*SAMPLES, *WEBSITE_SAMPLES, snapshot()]
             if type(m).__name__ in gateway_models()]
    assert {name for name, _ in valid} == gateway_models() - NESTED_ONLY
    samples = valid + [case for name, message in valid for case in broken(name, message)]
    results = zod(samples, tmp_path)
    disagree = [(name, result["ok"], result["issues"], message) for (name, message), result in zip(samples, results)
                if result["ok"] != pydantic_accepts(name, message)]
    assert disagree == []
    assert all(result["ok"] for result in results[:len(valid)])
    assert sum(not result["ok"] for result in results) == len(samples) - len(valid)
