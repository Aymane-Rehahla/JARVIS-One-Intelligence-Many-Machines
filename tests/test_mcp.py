"""The node exposes exactly its six audited read-only tools over MCP Streamable HTTP."""
from __future__ import annotations

import asyncio
import logging
import socket
import time
from pathlib import Path
from typing import Any, cast

import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from comms.mcp import NodeMCP
from comms.nats_link import NatsLink
from contracts import FarmStatus, NodeId, load_node_config
from node import Node
import runtime

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("test-mcp")


def free_port() -> int:
    """Return a currently free localhost TCP port for the in-process server."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def until(check: Any, timeout_s: float = 5.0) -> None:
    """Wait for a test condition with a finite deadline."""
    deadline = time.monotonic() + timeout_s
    while not check():
        if time.monotonic() >= deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(0.02)


async def make_node(url: str, data_dir: Path, port: int) -> Node:
    """Start one node on private test NATS and an isolated data directory."""
    node_id: NodeId = "incubator-1"
    link = NatsLink(node_id, url, logger=LOG)
    await link.connect(attempts=10)
    node = Node(load_node_config(ROOT / "nodes" / node_id / "node.toml"), link, logger=LOG,
                data_dir=data_dir, mcp_enabled=True, mcp_port=port)
    await node.start()
    return node


@pytest.mark.asyncio
async def test_mcp_lists_six_tools_audits_calls_and_returns_clean_errors(
    nats_url: str, tmp_path: Path,
) -> None:
    port = free_port()
    node = await make_node(nats_url, tmp_path / "node-data", port)
    farm_link = NatsLink("mcp-test-farm", nats_url, logger=LOG)
    await farm_link.connect(attempts=10)
    try:
        await farm_link.publish("farm.incubator-1.status", FarmStatus(
            node="incubator-1", tick=9, time_s=90.0, farm_time="06:09", label="live: simulated brains",
            online=True, readings={"temperature_mean_c": 37.2, "alarms": []}))
        await until(lambda: node.status is not None)
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "get_status", "get_assessment", "get_trend", "list_events", "get_incident", "search_documents"}

                status_result = await client.call_tool("get_status", {"fields": ["temperature_mean_c"]})
                assert not status_result.is_error
                status = cast(dict[str, Any], status_result.structured_content)
                assert status["ok"] is True
                assert status["data"]["farm_time"] == "06:09"
                assert node.store is not None
                audit_rows = await node.store.audit_for(status["request_id"])
                assert len(audit_rows) == 1 and audit_rows[0]["kind"] == "tool_call"
                assert audit_rows[0]["tool"] == "get_status" and audit_rows[0]["ok"] == 1

                bad_args = await client.call_tool("get_trend", {"signal": "", "minutes": 1})
                bad = cast(dict[str, Any], bad_args.structured_content)
                assert bad["ok"] is False and "invalid arguments" in bad["error"]
                unknown = await client.call_tool("not_a_tool", {})
                assert unknown.is_error and unknown.content
    finally:
        await farm_link.close()
        await node.stop()


@pytest.mark.asyncio
async def test_taken_mcp_port_leaves_node_running(nats_url: str, tmp_path: Path) -> None:
    port = free_port()
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", port))
        occupied.listen()
        node = await make_node(nats_url, tmp_path / "node-data", port)
        try:
            assert node.tools is not None
            assert node.mcp is not None
            assert node.link.connected
            assert node.mcp._task is None
        finally:
            await node.stop()


def test_mcp_refuses_non_loopback_hosts() -> None:
    class Registry:
        pass

    with pytest.raises(ValueError, match="127.0.0.1"):
        NodeMCP("incubator-1", cast(Any, Registry()), 8101, host="0.0.0.0")


def test_runtime_can_disable_mcp() -> None:
    assert runtime.parse_args(["--no-mcp"]).no_mcp is True
