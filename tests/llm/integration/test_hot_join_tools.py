"""H1: a late node publishes its topics and approved document, then answers the speaker through audited tools.

This exercises real rule-brain farm messages and the in-process node tools on private NATS. It does not load a language
model: the LLM-worded answer comes in 6C.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any
import tomllib

import pytest

import farm_runtime as fr
from comms.nats_link import NatsLink
from comms.peer_network import RadioRules
from contracts import NODE_IDS, NodeId, load_node_config
from node import Node

ROOT = Path(__file__).resolve().parents[3]
LOG = logging.getLogger("test-h1-hot-join")
INITIAL_NODES: tuple[NodeId, ...] = ("incubator-1", "incubator-2", "hvac")


def fixture_approved_rom(source: Path, destination: Path) -> Path:
    """Copy one real manual into a test ROM and approve only the exact temporary bytes."""
    destination.mkdir(parents=True)
    target = destination / source.name
    content = source.read_text(encoding="utf-8").replace('status = "draft"', 'status = "approved"', 1)
    target.write_text(content, encoding="utf-8")
    metadata = tomllib.loads(content.split("+++", 2)[1])
    project_root = destination.parents[3]
    for source_record in metadata["sources"]:
        copied_source = (source.parent / source_record["link"]).resolve()
        copied_target = project_root / copied_source.relative_to(ROOT)
        copied_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(copied_source, copied_target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (destination / "approvals.json").write_text(json.dumps({
        "schema_version": 1,
        "approved": {target.name: {"sha256": digest, "version": 1, "approved_utc": "2026-10-27T00:00:00Z",
                                   "by": "test fixture"}},
        "history": {},
    }), encoding="utf-8")
    return destination


async def until(check: Any, timeout_s: float) -> None:
    """Wait for a distributed-node condition using a finite deadline."""
    deadline = time.monotonic() + timeout_s
    while not check():
        if time.monotonic() >= deadline:
            raise AssertionError("H1 condition did not become true before its deadline")
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_hot_join_publishes_document_capability_and_answers_with_audit_pair(
    nats_url: str, tmp_path: Path,
) -> None:
    farm_link = NatsLink("h1-farm", nats_url, logger=LOG)
    await farm_link.connect(attempts=10)
    farm = fr.FarmRuntime(farm_link, logger=LOG, speed=10.0)
    stop = asyncio.Event()
    farm_task = asyncio.create_task(farm.run(stop), name="h1-rule-farm")
    nodes: dict[NodeId, Node] = {}
    try:
        for node_id in INITIAL_NODES:
            link = NatsLink(node_id, nats_url, logger=LOG)
            await link.connect(attempts=10)
            rom_dir = None
            if node_id == "incubator-1":
                rom_dir = fixture_approved_rom(ROOT / "nodes" / node_id / "memory" / "rom" / "incubator_manual.md",
                                               tmp_path / "nodes" / node_id / "memory" / "rom")
            node = Node(load_node_config(ROOT / "nodes" / node_id / "node.toml"), link, logger=LOG,
                        rules=RadioRules(latency_ticks=1), heartbeat_s=0.1, silent_after_s=1.0,
                        data_dir=tmp_path / "data" / node_id, rom_dir=rom_dir)
            nodes[node_id] = node
            async with asyncio.timeout(30.0):
                await node.start()

        await until(lambda: all(node.status is not None and node.sim is not None for node in nodes.values()), 20.0)
        await until(lambda: all(node.known() == list(INITIAL_NODES) for node in nodes.values()), 10.0)

        source_manual = ROOT / "nodes" / "animal-protection" / "memory" / "rom" / "animal_protection_manual.md"
        animal_rom = fixture_approved_rom(source_manual,
                                          tmp_path / "nodes" / "animal-protection" / "memory" / "rom")
        animal_link = NatsLink("animal-protection", nats_url, logger=LOG)
        await animal_link.connect(attempts=10)
        animal = Node(load_node_config(ROOT / "nodes" / "animal-protection" / "node.toml"), animal_link,
                      logger=LOG, rules=RadioRules(latency_ticks=1), heartbeat_s=0.1, silent_after_s=1.0,
                      data_dir=tmp_path / "data" / "animal-protection", rom_dir=animal_rom)
        speaker = nodes["incubator-1"]
        hot_join_started = time.monotonic()
        nodes["animal-protection"] = animal
        async with asyncio.timeout(30.0):
            await animal.start()
        object_ids: dict[NodeId, int] = {node_id: id(node) for node_id, node in nodes.items()}
        await until(lambda: all(node.known() == list(NODE_IDS) for node in nodes.values()), 10.0)
        hot_join_s = time.monotonic() - hot_join_started
        assert hot_join_s < 10.0
        await until(lambda: animal.status is not None, 5.0)
        assert "animal-protection" in speaker.capability_registry.who_knows("hazard_check")
        documents = speaker.capability_registry.documents_of("animal-protection")
        assert len(documents) == 1 and documents[0].file == "animal_protection_manual.md"

        assert speaker.tools is not None
        ask_started = time.monotonic()
        outcome = await speaker.tools.call("ask_peer", {
            "node": "animal-protection", "question": "is there any smoke or flood?",
        }, request_id="h1-hot-join-ask", caller="owner")
        ask_s = time.monotonic() - ask_started
        assert outcome.ok, outcome.error
        assert ask_s < 3.0
        assert outcome.data["topic"] == "hazard_check"
        assert outcome.data["farm_time"] and outcome.data["label"] == "live: simulated brains"
        assert outcome.data["answer"]

        assert speaker.store is not None and animal.store is not None
        speaker_audit = await speaker.store.audit_for("h1-hot-join-ask")
        animal_audit = await animal.store.audit_for("h1-hot-join-ask")
        assert any(row["kind"] == "peer_question" and row["ok"] == 1 for row in speaker_audit)
        assert any(row["kind"] == "peer_answer" and row["ok"] == 1 for row in animal_audit)
        assert all(row["request_id"] == "h1-hot-join-ask" for row in speaker_audit + animal_audit)
        assert all(id(nodes[node_id]) == original_id for node_id, original_id in object_ids.items())
        print(f"H1 hot_join_s={hot_join_s:.3f} peer_ask_s={ask_s:.3f}")
    finally:
        stop.set()
        await asyncio.wait_for(farm_task, timeout=5.0)
        for node in nodes.values():
            await asyncio.wait_for(node.stop(), timeout=10.0)
        await asyncio.wait_for(farm_link.close(), timeout=5.0)
