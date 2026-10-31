"""llm/engines.py without a server: the four independent servers' command lines, and a mock client.

No llama.cpp server is started here (the training runs; the live test is tests/llm/test_engines_live.py, deferred D3).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from contracts import NODE_IDS
from llm.engines import LLAMA_SERVER, EngineClient, EngineError, server_command, server_spec

ROOT = Path(__file__).resolve().parents[2]


def test_each_node_has_its_own_local_server_with_every_layer_on_the_gpu() -> None:
    specs = {node: server_spec(node) for node in NODE_IDS}
    assert {node: (spec.port, spec.slots) for node, spec in specs.items()} == {
        "incubator-1": (8081, 2), "incubator-2": (8082, 1), "hvac": (8083, 1), "animal-protection": (8084, 1)}
    for node, spec in specs.items():
        argv = server_command(spec)
        flag = {argv[i]: argv[i + 1] for i in range(1, len(argv) - 1) if argv[i].startswith("-")}
        assert argv[0] == str(LLAMA_SERVER) and flag["-m"] == str(spec.model)
        assert flag["--host"] == "127.0.0.1" and flag["-ngl"] == "99"
        assert int(flag["-c"]) == 8192 * spec.slots and flag["-np"] == str(spec.slots)
        assert "--jinja" in argv and "--metrics" in argv and flag["-a"] == f"jarvis-{node}"
    assert server_spec("incubator-1", slots=4).slots == 4                       # the persona search's extra slots


def test_the_models_and_the_server_are_where_the_commands_say() -> None:
    if not LLAMA_SERVER.exists():
        pytest.skip("llama.cpp is not installed on this machine")
    assert all(server_spec(node).model.is_file() for node in NODE_IDS)


def mock(seen: list[dict[str, Any]], *, status: int = 200, body: object = None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(status, text="loading model" if status == 503 else "ok")
        seen.append(json.loads(request.content))
        if body is not None:
            return httpx.Response(status, json=body)
        return httpx.Response(status, json={
            "choices": [{"message": {"content": "Heater at 37.6 °C, 06:00.", "tool_calls": []},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 9},
            "timings": {"prompt_ms": 40.0, "predicted_ms": 90.0, "predicted_per_second": 100.0}})
    return httpx.MockTransport(handle)


@pytest.mark.asyncio
async def test_a_chat_pins_the_slot_keeps_thinking_off_and_reads_the_timings() -> None:
    seen: list[dict[str, Any]] = []
    client = EngineClient("http://127.0.0.1:8082", slot=2, transport=mock(seen))
    result = await client.chat([{"role": "user", "content": "status?"}], seed=7, max_tokens=60)
    await client.close()
    body = seen[0]
    assert body["id_slot"] == 2 and body["cache_prompt"] is True and body["stream"] is False
    assert body["chat_template_kwargs"] == {"enable_thinking": False} and body["seed"] == 7
    assert result.text.startswith("Heater") and result.completion_tokens == 9 and result.tokens_per_s == 100.0
    assert "tools" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "body", "reason"), [(500, {"error": "x"}, "HTTP 500"),
                                                        (200, {"choices": []}, "unexpected response")])
async def test_bad_answers_are_engine_errors(status: int, body: object, reason: str) -> None:
    client = EngineClient("http://127.0.0.1:8082", transport=mock([], status=status, body=body))
    with pytest.raises(EngineError, match=reason):
        await client.chat([{"role": "user", "content": "hi"}])
    await client.close()


@pytest.mark.asyncio
async def test_health_tells_loading_from_down() -> None:
    loading = EngineClient("http://127.0.0.1:8082", transport=mock([], status=503))
    assert (await loading.health()).status == "loading"
    await loading.close()

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)
    down = EngineClient("http://127.0.0.1:8082", transport=httpx.MockTransport(refuse))
    health = await down.health()
    assert not health.ready and health.status == "unreachable"
    with pytest.raises(EngineError, match="unreachable"):
        await down.chat([{"role": "user", "content": "hi"}])
    await down.close()
