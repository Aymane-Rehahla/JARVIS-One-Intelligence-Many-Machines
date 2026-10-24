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
from contracts import Heartbeat, utc_now
from test_contracts import SAMPLES

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
    return [(name, case) for case in cases]


def pydantic_accepts(name: str, message: dict[str, Any]) -> bool:
    model: type[BaseModel] = {type(m).__name__: type(m) for m in [*SAMPLES, snapshot()]}[name]
    try:
        model.model_validate_json(json.dumps(message))
        return True
    except ValidationError:
        return False


def gateway_models() -> set[str]:
    """What the website receives: FarmSnapshot and every model inside it (JsonValue is a type, not a model)."""
    return {"FarmSnapshot", *gw.FarmSnapshot.model_json_schema(mode="serialization")["$defs"]} - {"JsonValue"}


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
    valid = [(type(m).__name__, json.loads(m.model_dump_json())) for m in [*SAMPLES, snapshot()]
             if type(m).__name__ in gateway_models()]
    assert {name for name, _ in valid} == gateway_models() - {"NodeView", "GateDecision"}   # those two live inside
    samples = valid + [case for name, message in valid for case in broken(name, message)]
    results = zod(samples, tmp_path)
    disagree = [(name, result["ok"], result["issues"], message) for (name, message), result in zip(samples, results)
                if result["ok"] != pydantic_accepts(name, message)]
    assert disagree == []
    assert all(result["ok"] for result in results[:len(valid)])
    assert sum(not result["ok"] for result in results) == len(samples) - len(valid)
