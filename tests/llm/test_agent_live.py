"""6C's "Done when" integration test (P2): "What's wrong with the HVAC?" through the gateway's /ws/chat with the real
models (deferred: it loads the 2B and one 0.6B server).

Real nats-server (the `nats_url` fixture), the real llama.cpp servers of incubator-1 (Qwen 3.5 2B) and hvac
(Qwen3-0.6B) on free ports (a temporary ports file), both nodes in this process with their real engines and the
provisional persona, the hvac manual approved by a test fixture in a temporary folder, a synthetic supply-fan fault
published on NATS as the farm runtime would, the real gateway on a free port, and tools/chat_cli.py as the owner.
The transcript goes to logs/eval/integration_6c.json.

Run: $env:JARVIS_RUN_DEFERRED = "1"; .venv\\Scripts\\python -m pytest tests\\llm\\test_agent_live.py -s
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import pytest
import uvicorn
import websockets

import chat_cli
import gateway as gw
from comms.nats_link import NatsLink
from comms.peer_network import RadioRules
from conftest import fixture_approved_rom, free_port
from contracts import FarmAssessment, FarmIncident, FarmStatus, load_node_config
from llm.engines import server_spec
from llm.jarvis_speaker import load_persona
from node import Node
from test_engines_live import start_server, stop_server, wait_for_health  # pyright: ignore[reportMissingImports]

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger("test-agent-live")
TRANSCRIPT = ROOT / "logs" / "eval" / "integration_6c.json"
QUESTION = "What's wrong with the HVAC?"
COMMON: dict[str, Any] = dict(tick=128, time_s=128.0, farm_time="06:08", label="live: simulated brains",
                              feed_id="0000000000c6")


def ports_file(folder: Path) -> Path:
    """launcher/ports.toml with every port free (rule 10)."""
    path = folder / "ports.toml"
    llm = "\n".join(f'{node} = {free_port()}' for node in ("incubator-1", "incubator-2", "hvac", "animal-protection"))
    path.write_text(f'schema_version = 1\nhost = "127.0.0.1"\nnats = {free_port()}\nmatrix_api = {free_port()}\n'
                    f'gateway = {free_port()}\nwebsite = 5173\n\n[llm]\n{llm}\n', encoding="utf-8")
    return path


async def publish_fan_fault(link: NatsLink) -> None:
    """What the farm runtime publishes after a supply-fan failure (the readings of the 6W real run's shape)."""
    await link.publish("farm.incubator-1.status", FarmStatus(node="incubator-1", **COMMON, online=True, readings={
        "online": True, "health": "ok", "temperature_mean_c": 37.6, "humidity_mean_pct": 55.2, "heater_on": True,
        "alarms": [], "faults": []}))
    await link.publish("farm.incubator-1.assessment", FarmAssessment(
        node="incubator-1", **COMMON, assessment="NORMAL", diagnosis="NORMAL", confidence=0.95, brain="rule"))
    await link.publish("farm.hvac.status", FarmStatus(node="hvac", **COMMON, online=True, readings={
        "online": True, "health": "fault", "mode": "fault", "supply_air_temperature_c": 27.4,
        "return_air_temperature_c": 26.9, "supply_fan_state": "failed", "supply_fan_actual_rpm": 0.0,
        "airflow_m3_h": 0.0, "zone_mean_temperature_c": 26.1, "alarms": ["SUPPLY_FAN_FAILURE"],
        "faults": ["SUPPLY_FAN_FAILURE"]}))
    await link.publish("farm.hvac.assessment", FarmAssessment(
        node="hvac", **COMMON, assessment="ESCALATE_TO_USER", diagnosis="SUPPLY_FAN_FAULT", confidence=0.92,
        brain="rule", incident_kind="equipment", incident_id="hvac-equipment-0001",
        recommendation="Supply fan failed; no ventilation or cooling. Inspect the supply fan and its drive.",
        evidence=["SUPPLY_FAN_FAILURE", "airflow 0.0 m3/h"]))
    await link.publish("farm.hvac.incident", FarmIncident(
        node="hvac", **COMMON, incident_id="hvac-equipment-0001", state="opened", kind="equipment",
        summary={"code": "SUPPLY_FAN_FAILURE"}))


async def until(check: Any, timeout_s: float, what: str) -> None:
    deadline = time.monotonic() + timeout_s
    while not check():
        assert time.monotonic() < deadline, what
        await asyncio.sleep(0.05)


@pytest.mark.deferred
@pytest.mark.asyncio
async def test_whats_wrong_with_the_hvac_through_the_gateway_with_the_real_models(nats_url: str,
                                                                                tmp_path: Path) -> None:
    ports = ports_file(tmp_path)
    specs = {node: server_spec(node, ports) for node in ("incubator-1", "hvac")}
    started = {node: start_server(spec) for node, spec in specs.items()}
    engines: dict[str, Any] = {}
    nodes: list[Node] = []
    server: uvicorn.Server | None = None
    serving: asyncio.Task[None] | None = None
    gateway: gw.Gateway | None = None
    farm = NatsLink("farm", nats_url, logger=LOG)
    try:
        for node, spec in specs.items():
            engines[node], load_s = await wait_for_health(spec, started[node][0])
            LOG.info("%s ready in %.1f s", node, load_s)
        rom = fixture_approved_rom(ROOT / "nodes" / "hvac" / "memory" / "rom" / "hvac_manual.md",
                                   tmp_path / "copy" / "nodes" / "hvac" / "memory" / "rom")
        for node in ("hvac", "incubator-1"):
            link = NatsLink(node, nats_url, logger=LOG)
            await link.connect(attempts=5)
            started_node = Node(load_node_config(ROOT / "nodes" / node / "node.toml"), link, logger=LOG,
                                rules=RadioRules(latency_ticks=0), data_dir=tmp_path / "data" / node,
                                engine=engines[node], rom_dir=rom if node == "hvac" else None,
                                persona=load_persona() if node == "incubator-1" else None)
            await started_node.start()
            nodes.append(started_node)
        hvac, speaker = nodes
        await until(lambda: speaker.peers["hvac"].capabilities is not None
                    and "documents" in speaker.peers["hvac"].capabilities.topics, 20.0, "hvac announced documents")
        assert speaker.agent is not None
        await until(lambda: speaker.agent is not None and speaker.agent.warm_ms is not None, 60.0,
                    "the speaking node warmed its prompt cache")             # done at node start, as in real use
        await farm.connect(attempts=5)
        await publish_fan_fault(farm)
        await until(lambda: hvac.assessment is not None and speaker.assessment is not None, 10.0, "the feed arrived")

        link = NatsLink("gateway", nats_url, logger=LOG)
        await link.connect(attempts=5)
        gateway = gw.Gateway(link, "http://127.0.0.1:1", logger=LOG)
        await gateway.start()
        port = free_port()
        server = uvicorn.Server(uvicorn.Config(gw.create_app(gateway), host="127.0.0.1", port=port,
                                               log_level="warning", lifespan="off"))
        serving = asyncio.create_task(server.serve())
        await until(lambda: server is not None and server.started, 10.0, "the gateway started")

        asked_at = time.monotonic()
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws/chat") as socket:
            answer = await chat_cli.ask(socket, QUESTION, answer_s=chat_cli.ANSWER_S)
        seconds = time.monotonic() - asked_at
        done = answer.done or {}
        TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
        TRANSCRIPT.write_text(json.dumps({"question": QUESTION, "seconds": round(seconds, 2),
                                          "warm_ms": round(speaker.agent.warm_ms or 0),
                                          "chunks": answer.chunks, "done": done, "error": answer.error,
                                          "alerts": answer.alerts}, indent=1, ensure_ascii=False), encoding="utf-8")
        print("\n" + json.dumps({"question": QUESTION, "reply": done.get("text"), "outcome": done.get("outcome"),
                                 "reason": done.get("reason"), "citations": done.get("citations"),
                                 "tools": [(t["node"], t["tool"], t["ok"]) for t in done.get("tools", [])],
                                 "numbers_checked": done.get("numbers_checked"), "timings": done.get("timings"),
                                 "seconds": round(seconds, 2)}, indent=1, ensure_ascii=False))

        assert answer.error is None, answer.error
        assert done["outcome"] == "answered", (done.get("outcome"), done.get("reason"))
        assert any(t["node"] == "hvac" and t["tool"].startswith("ask_peer") and t["ok"] for t in done["tools"])
        assert done["numbers_checked"] >= 1                                       # verified numbers
        assert any(c["node"] == "hvac" and c["file"] == "hvac_manual.md" for c in done["citations"])
        assert done["farm_time"] == "06:08" and "06:08" in done["text"]           # the farm time, stated
        assert "".join(answer.chunks) == done["text"]
    finally:
        if server is not None:
            server.should_exit = True
        if serving is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(serving, timeout=10.0)
        if gateway is not None:
            await gateway.close()
        for started_node in nodes:
            await started_node.stop()                                             # closes its engine
        for node, engine in engines.items():
            if not any(n.id == node for n in nodes):
                await engine.close()
        with contextlib.suppress(Exception):
            await farm.close()
        for process, job, output in started.values():
            stop_server(process, job, output)
