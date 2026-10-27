# 0014 · MCP exposes only node-local read tools

**Status:** accepted · 27 Oct 2026

## Context

Each node's typed tools are useful to local developer tools and MCP clients. The language model must not gain a route to
machine actions, another node's tools, owner conversation recall, or alert acknowledgement through MCP.

## Decision

Each node may serve tools 1–6 over Streamable HTTP on `127.0.0.1` and its own `[mcp]` port in
`launcher/ports.toml`. Every call passes through that node's `ToolRegistry` with a fresh request id and is audited.
Runtime enables MCP by default; `--no-mcp` disables the listener. A port bind failure is logged and does not stop the
node.

## Consequences

MCP clients can read local status, assessment, trend, events, incidents and approved documents. Peer questions,
conversation recall and owner-confirmed acknowledgement remain unavailable over MCP. The listener rejects any host
other than `127.0.0.1`.
