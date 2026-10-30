"""EngineClient.chat_stream() and response_format without a server: scripted server-sent events over a mock transport.

No llama.cpp server is started here. The byte streams below await asyncio.sleep to play a slow, silent or endless
server, so the deadline, the first-token watchdog and the byte limit are measured where they apply.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import pytest

from llm.engines import EngineClient, EngineError, StreamDelta, assemble_tool_calls

URL = "http://127.0.0.1:8081"
HI = [{"role": "user", "content": "status?"}]
Part = bytes | float                                   # bytes to send, or seconds to wait


class Scripted(httpx.AsyncByteStream):
    """A response body that sends its parts in order (waiting where a part is a number), then optionally repeats one
    part forever every `every_s` seconds; records whether it was closed."""

    def __init__(self, parts: Sequence[Part], *, forever: bytes | None = None, every_s: float = 0.3) -> None:
        self.parts = parts
        self.forever = forever
        self.every_s = every_s
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for part in self.parts:
            if isinstance(part, bytes):
                yield part
            else:
                await asyncio.sleep(part)
        while self.forever is not None:
            yield self.forever
            await asyncio.sleep(self.every_s)

    async def aclose(self) -> None:
        self.closed = True


def data(chunk: dict[str, Any] | str) -> bytes:
    return f"data: {chunk if isinstance(chunk, str) else json.dumps(chunk)}\n\n".encode()


def delta(content: str | None = None, *, finish: str | None = None, role: bool = False,
          tool_calls: list[dict[str, Any]] | None = None, timings: dict[str, Any] | None = None) -> dict[str, Any]:
    piece: dict[str, Any] = {}
    if role:
        piece["role"] = "assistant"
    if content is not None:
        piece["content"] = content
    if tool_calls is not None:
        piece["tool_calls"] = tool_calls
    chunk: dict[str, Any] = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "model": "local",
                             "choices": [{"index": 0, "delta": piece, "finish_reason": finish}]}
    if timings is not None:
        chunk["timings"] = timings
    return chunk


DONE = data("[DONE]")
TIMINGS = {"prompt_n": 120, "prompt_ms": 40.0, "predicted_n": 9, "predicted_ms": 90.0, "predicted_per_second": 100.0}


def serve(stream: Scripted, seen: list[dict[str, Any]] | None = None, *, status: int = 200) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(status, stream=stream, headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handle)


async def collect(client: EngineClient, **kwargs: Any) -> list[StreamDelta]:
    options: dict[str, Any] = {"deadline_s": 5.0, "first_token_s": 2.0} | kwargs
    return [piece async for piece in client.chat_stream(HI, **options)]


@pytest.mark.asyncio
async def test_a_stream_yields_the_text_and_sends_a_streaming_request_on_the_slot() -> None:
    frame = data(delta("at 37.6 °C, 06:00."))
    stream = Scripted([data(delta("", role=True)), b": keep-alive comment\n\n", b"\n", data(delta("Heater ")),
                       frame[:20], 0.05, frame[20:],                       # one event split across two reads
                       b"\r\n", data(delta(finish="stop", timings=TIMINGS)), DONE,
                       data(delta("after the end"))])                       # nothing after [DONE] is read
    seen: list[dict[str, Any]] = []
    schema = {"type": "json_object", "schema": {"type": "object", "properties": {"say": {"type": "string"}}}}
    client = EngineClient(URL, slot=1, transport=serve(stream, seen))
    pieces = await collect(client, max_tokens=60, seed=7, response_format=schema)
    await client.close()
    assert "".join(piece.text for piece in pieces) == "Heater at 37.6 °C, 06:00."
    assert len(pieces) == 3                                                # the role-only chunk carries nothing
    assert pieces[-1].finish_reason == "stop" and pieces[-1].timings == TIMINGS
    body = seen[0]
    assert body["stream"] is True and body["cache_prompt"] is True and body["id_slot"] == 1
    assert body["response_format"] == schema and body["max_tokens"] == 60 and body["seed"] == 7
    assert body["chat_template_kwargs"] == {"enable_thinking": False} and "tools" not in body
    assert stream.closed


@pytest.mark.asyncio
async def test_without_a_slot_or_a_response_format_the_body_has_neither() -> None:
    seen: list[dict[str, Any]] = []
    client = EngineClient(URL, transport=serve(Scripted([data(delta("ok", finish="stop")), DONE]), seen))
    assert [piece.text for piece in await collect(client)] == ["ok"]
    await client.close()
    assert "id_slot" not in seen[0] and "response_format" not in seen[0]


@pytest.mark.asyncio
async def test_tool_call_fragments_are_assembled_by_index() -> None:
    fragments = [
        [{"index": 0, "id": "call_a", "type": "function", "function": {"name": "get_status", "arguments": ""}}],
        [{"index": 0, "function": {"arguments": "{\"node\": "}}],
        [{"index": 1, "id": "call_b", "type": "function", "function": {"name": "get_events", "arguments": "{}"}}],
        [{"index": 0, "function": {"arguments": "\"hvac\"}"}}]]
    stream = Scripted([data(delta(role=True))] + [data(delta(tool_calls=item)) for item in fragments]
                      + [data(delta(finish="tool_calls", timings=TIMINGS)), DONE])
    tools = [{"type": "function", "function": {"name": "get_status", "parameters": {"type": "object"}}}]
    seen: list[dict[str, Any]] = []
    client = EngineClient(URL, transport=serve(stream, seen))
    pieces = await collect(client, tools=tools)
    await client.close()
    assert seen[0]["tools"] == tools
    raw = [item for piece in pieces for item in piece.tool_calls]
    assert raw == [item for fragment in fragments for item in fragment]   # the raw fragments, as received
    assert assemble_tool_calls(raw) == [
        {"id": "call_a", "type": "function", "function": {"name": "get_status", "arguments": "{\"node\": \"hvac\"}"}},
        {"id": "call_b", "type": "function", "function": {"name": "get_events", "arguments": "{}"}}]
    assert pieces[-1].finish_reason == "tool_calls"
    assert assemble_tool_calls([]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "parts", "reason"), [
    (500, [b"{\"error\": \"slot unavailable\"}"], "HTTP 500: {\"error\": \"slot unavailable\"}"),
    (200, [data(delta("Heat")), b"data: {not json\n\n", DONE], "malformed stream data"),
    (200, [data({"id": "chatcmpl-1", "object": "chat.completion.chunk"}), DONE], "without choices"),
    (200, [data({"error": {"code": 500, "message": "boom"}})], "server error in the stream"),
    (200, [b"error: {\"message\": \"context full\"}\n\n"], "server error in the stream"),
    (200, [data(delta("Heat"))], "ended without \\[DONE\\]"),
])
async def test_bad_streams_are_engine_errors(status: int, parts: list[Part], reason: str) -> None:
    stream = Scripted(parts)
    client = EngineClient(URL, transport=serve(stream, status=status))
    with pytest.raises(EngineError, match=reason):
        await collect(client)
    await client.close()
    assert stream.closed


@pytest.mark.asyncio
async def test_an_unreachable_server_is_an_engine_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)
    client = EngineClient(URL, transport=httpx.MockTransport(refuse))
    with pytest.raises(EngineError, match="unreachable"):
        await collect(client)
    await client.close()


@pytest.mark.asyncio
async def test_the_first_token_watchdog_fires_when_the_server_says_nothing() -> None:
    stream = Scripted([data(delta("", role=True)), 30.0, data(delta("late")), DONE])  # a role chunk is no token
    client = EngineClient(URL, transport=serve(stream))
    started = time.monotonic()
    with pytest.raises(EngineError, match="no first token within 0.5 s"):
        await collect(client, deadline_s=20.0, first_token_s=0.5)
    elapsed = time.monotonic() - started
    await client.close()
    assert 0.45 <= elapsed < 0.5 + 0.5 and stream.closed


@pytest.mark.asyncio
async def test_the_whole_stream_has_a_deadline_even_while_tokens_keep_coming() -> None:
    stream = Scripted([], forever=data(delta("more ")), every_s=0.3)       # a server trickling forever
    client = EngineClient(URL, transport=serve(stream))
    received: list[StreamDelta] = []
    started = time.monotonic()
    with pytest.raises(EngineError, match="did not end within the deadline of 1.0 s"):
        async for piece in client.chat_stream(HI, deadline_s=1.0, first_token_s=0.5):
            received.append(piece)
    elapsed = time.monotonic() - started
    await client.close()
    assert 0.95 <= elapsed < 1.5 and len(received) >= 3 and stream.closed


@pytest.mark.asyncio
async def test_a_stream_longer_than_max_bytes_is_an_engine_error() -> None:
    stream = Scripted([], forever=data(delta("x" * 400)), every_s=0.0)
    client = EngineClient(URL, transport=serve(stream))
    with pytest.raises(EngineError, match="more than 2000 bytes"):
        await collect(client, max_bytes=2000)
    await client.close()
    assert stream.closed


@pytest.mark.asyncio
async def test_a_consumer_stopping_early_closes_the_response() -> None:
    stream = Scripted([data(delta("Heater ")), data(delta("at ")), 30.0, DONE])
    client = EngineClient(URL, transport=serve(stream))
    pieces = client.chat_stream(HI, deadline_s=60.0, first_token_s=5.0)
    first = await anext(pieces)
    assert first.text == "Heater " and not stream.closed
    await pieces.aclose()
    assert stream.closed
    await client.close()


@pytest.mark.asyncio
async def test_a_slow_consumer_is_not_cancelled_by_the_first_token_watchdog() -> None:
    stream = Scripted([data(delta("Heater ")), data(delta("on", finish="stop")), DONE])
    client = EngineClient(URL, transport=serve(stream))
    texts: list[str] = []
    async for piece in client.chat_stream(HI, deadline_s=5.0, first_token_s=0.2):
        await asyncio.sleep(0.3)                                           # longer than the watchdog, per piece
        texts.append(piece.text)
    await client.close()
    assert texts == ["Heater ", "on"]


@pytest.mark.asyncio
async def test_chat_puts_the_response_format_in_the_body_only_when_given() -> None:
    seen: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"say\": \"ok\"}"},
                                                      "finish_reason": "stop"}]})
    client = EngineClient(URL, slot=0, transport=httpx.MockTransport(handle))
    schema = {"type": "json_object", "schema": {"type": "object", "properties": {"say": {"type": "string"}}}}
    result = await client.chat(HI, response_format=schema)
    await client.chat(HI)
    await client.close()
    assert seen[0]["response_format"] == schema and seen[0]["stream"] is False and seen[0]["id_slot"] == 0
    assert "response_format" not in seen[1]
    assert json.loads(result.text) == {"say": "ok"}
