"""Serve this node's six read-only tools over local Streamable HTTP.

In: a node-local ToolRegistry and its configured MCP port. Out: audited tool results to localhost MCP clients. Tests:
`tests/test_mcp.py` exercises the client, tool list and occupied-port fallback.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import socket
from typing import Any, cast

import uvicorn
from mcp.server.mcpserver import MCPServer

from contracts import NodeId
from llm.tools import ToolRegistry

MCP_HOST = "127.0.0.1"
START_TIMEOUT_S = 5.0
STOP_TIMEOUT_S = 5.0


class NodeMCP:
    """Run the node's public read-only tool registry on one localhost port."""

    def __init__(self, node: NodeId, registry: ToolRegistry, port: int, *, host: str = MCP_HOST,
                 logger: logging.Logger | None = None) -> None:
        if host != MCP_HOST:
            raise ValueError("MCP host must be 127.0.0.1")
        if not 1 <= port <= 65535:
            raise ValueError("MCP port must be between 1 and 65535")
        self.node = node
        self.registry = registry
        self.port = port
        self.logger = logger or logging.getLogger(f"jarvis.{node}.mcp")
        self.server = MCPServer(name=f"jarvis-{node}-tools", instructions="Read-only access to this node's farm tools.")
        self._uvicorn: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None
        self._register_tools()

    def _register_tools(self) -> None:
        """Expose precisely tools one through six; every call receives its own audit request id."""
        @self.server.tool(description="Read this node's selected public farm readings.")
        async def get_status(fields: list[str] | None = None) -> dict[str, Any]:
            return await self._call("get_status", {"fields": fields})

        @self.server.tool(description="Read this node's latest rule-brain assessment.")
        async def get_assessment() -> dict[str, Any]:
            return await self._call("get_assessment", {})

        @self.server.tool(description="Read a numeric signal trend from this node's rolling farm feed.")
        async def get_trend(signal: str, minutes: int) -> dict[str, Any]:
            return await self._call("get_trend", {"signal": signal, "minutes": minutes})

        @self.server.tool(description="List this node's recent farm events.")
        async def list_events(since_minutes: int, severity: str | None = None, limit: int = 50) -> dict[str, Any]:
            return await self._call("list_events", {"since_minutes": since_minutes, "severity": severity,
                                                       "limit": limit})

        @self.server.tool(description="Read one incident or the current incident for this node.")
        async def get_incident(incident_id: str) -> dict[str, Any]:
            return await self._call("get_incident", {"incident_id": incident_id})

        @self.server.tool(description="Search this node's owner-approved ROM documents.")
        async def search_documents(query: str, k: int = 4) -> dict[str, Any]:
            return await self._call("search_documents", {"query": query, "k": k})

    async def _call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Validate, execute and audit one MCP request through the shared tool registry."""
        request_id = f"r-{secrets.token_hex(6)}"
        outcome = await self.registry.call(name, args, request_id=request_id, caller="mcp")
        return {"request_id": request_id, **outcome.model_dump(mode="json")}

    async def start(self) -> bool:
        """Bind Streamable HTTP; a bind failure is logged and leaves the node's other work available."""
        if self._task is not None:
            return True
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind((MCP_HOST, self.port))
            listener.listen()
            listener.setblocking(False)
        except OSError as error:
            listener.close()
            self.logger.error("MCP server failed to bind; node continues", extra={"fields": {
                "node": self.node, "port": self.port, "error": str(error)}})
            return False
        app = self.server.streamable_http_app(host=MCP_HOST)
        config = uvicorn.Config(app, host=MCP_HOST, port=self.port, log_level="warning")
        server = uvicorn.Server(config)
        cast(Any, server).install_signal_handlers = lambda: None
        task = asyncio.create_task(server.serve(sockets=[listener]), name=f"mcp-{self.node}")
        self._uvicorn, self._task = server, task
        try:
            async with asyncio.timeout(START_TIMEOUT_S):
                while not server.started:
                    if task.done():
                        task.result()
                        break
                    await asyncio.sleep(0.02)
                if not server.started:
                    raise RuntimeError("MCP server did not become ready")
        except (OSError, RuntimeError, TimeoutError, SystemExit) as error:
            server.should_exit = True                      # a server that never started must not keep running
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            listener.close()
            self.logger.error("MCP server failed to start; node continues", extra={"fields": {
                "node": self.node, "port": self.port, "error": str(error)}})
            self._task = None
            self._uvicorn = None
            return False
        return True

    async def stop(self) -> None:
        """Stop the HTTP task before the node closes its stores and NATS link."""
        server, task = self._uvicorn, self._task
        if server is None or task is None:
            return
        server.should_exit = True
        try:
            async with asyncio.timeout(STOP_TIMEOUT_S):
                await task
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.logger.warning("MCP server shutdown timed out", extra={"fields": {"node": self.node}})
        finally:
            self._uvicorn, self._task = None, None
