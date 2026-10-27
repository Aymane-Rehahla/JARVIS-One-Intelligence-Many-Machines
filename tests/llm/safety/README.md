# tests/llm/safety/

The 6B static boundary test scans node LLM modules and the MCP adapter. It rejects imports of memory, safety, reasoning,
Matrix internals or the farm snapshot; CMM/working-memory/command strings; and publishing to any `farm.*` subject. It
also checks the tool permission levels and that only alert acknowledgement is owner-confirmed.

- **In:** the AST of `nodes/incubator-1/llm/*.py` and `nodes/incubator-1/comms/mcp.py`.
- **Out:** a pass only when no prohibited access or publish boundary is present.
- **Test:** `.venv\Scripts\python -m pytest -q -p no:cacheprovider -o faulthandler_timeout=120 tests\llm\safety\test_static_boundaries.py`.
