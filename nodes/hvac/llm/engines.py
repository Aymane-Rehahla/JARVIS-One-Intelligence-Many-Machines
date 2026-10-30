"""The language-model engines (S2, S15): one llama.cpp server per node, and a node's connection to its own one.

Four independent servers on the RX 580 (Vulkan), every layer on the GPU (plan section 4.1, decision 0013):
    incubator-1        Qwen 3.5 2B roleplay, 1 slot
    incubator-2, hvac, animal-protection     Qwen3-0.6B, 1 slot each
`server_spec()` and `server_command()` give each server's command line (the launcher's supervisor starts them),
`EngineClient.health()` asks a server whether its model is loaded, and `EngineClient.chat()` sends one chat
completion to the node's own server and slot, with a deadline (Q4); `EngineClient.chat_stream()` streams one, with a
deadline, a first-token watchdog and a byte limit. Pinning the slot (`id_slot`) and starting every
prompt with the same fixed text let llama.cpp reuse its prompt cache (S15). The thinking mode is off unless asked for.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
import tomllib
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contracts import NodeId, load_node_config

NODE_DIR = Path(__file__).resolve().parents[1]
ROOT = NODE_DIR.parents[1]
LLAMA_SERVER = ROOT / "third_party" / "llama.cpp" / "bin" / "llama-server.exe"
CTX_PER_SLOT = 8192                     # the persona spec's context size (docs/persona-spec.md, section 4)
CHAT_PATH = "/v1/chat/completions"
STREAM_MAX_BYTES = 200_000             # the most a streamed answer may send (Q4)
KV_CACHE_TYPE = "f16"                  # K and V cache type (decision 0017 measures q8_0 against it)
FLASH_ATTENTION = "auto"                # llama.cpp's own default; a quantized V cache needs it on


@dataclass(frozen=True)
class ServerSpec:
    """One llama.cpp server: its model, address, slots and context."""

    node: NodeId
    model: Path
    host: str
    port: int
    slots: int
    ctx_per_slot: int = CTX_PER_SLOT
    gpu_layers: int = 99
    threads: int = 4
    alias: str = ""
    cache_type_k: str = KV_CACHE_TYPE
    cache_type_v: str = KV_CACHE_TYPE
    flash_attn: str = FLASH_ATTENTION

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def server_spec(node: NodeId, ports_file: Path = ROOT / "launcher" / "ports.toml", *,
                slots: int | None = None) -> ServerSpec:
    """The spec of one node's server, from that node's `node.toml` and launcher/ports.toml.

    `slots` overrides the normal one slot for the persona search's parallel prompts.
    """
    ports = tomllib.loads(ports_file.read_text(encoding="utf-8"))
    config = load_node_config(ROOT / "nodes" / node / "node.toml")
    return ServerSpec(node=node, model=ROOT / config.llm.model, host=str(ports["host"]),
                      port=int(ports["llm"][node]), slots=slots or 1, alias=f"jarvis-{node}")


def server_command(spec: ServerSpec, exe: Path = LLAMA_SERVER) -> list[str]:
    """The llama-server command line: all layers on the GPU, `slots` parallel slots of `ctx_per_slot` tokens each,
    the KV cache types and flash attention, Jinja chat templates (for tool calls), Prometheus metrics, listening on
    127.0.0.1 only (Q7)."""
    return [str(exe), "-m", str(spec.model), "--host", spec.host, "--port", str(spec.port),
            "-ngl", str(spec.gpu_layers), "-c", str(spec.ctx_per_slot * spec.slots), "-np", str(spec.slots),
            "-t", str(spec.threads), "-ctk", spec.cache_type_k, "-ctv", spec.cache_type_v, "-fa", spec.flash_attn,
            "--jinja", "--metrics", "-a", spec.alias]


class EngineHealth(BaseModel):
    """What a server said about itself."""

    model_config = ConfigDict(extra="forbid")
    ready: bool
    status: str
    detail: str = ""


class ChatResult(BaseModel):
    """One chat completion: the text, any tool calls, and the server's own timings."""

    model_config = ConfigDict(extra="forbid")
    text: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    prompt_ms: float | None = None
    predicted_ms: float | None = None
    tokens_per_s: float | None = None
    latency_s: float


class EngineError(RuntimeError):
    """The server could not give an answer (down, loading, timed out, bad response)."""


class StreamDelta(BaseModel):
    """One piece of a streamed chat completion: new text, raw tool-call fragments, the finish reason, the timings."""

    model_config = ConfigDict(extra="forbid")
    text: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    finish_reason: str | None = None
    timings: dict[str, Any] | None = None


class EngineClient:
    """A node's connection to its llama.cpp server and slot (async, bounded)."""

    def __init__(self, base_url: str, *, slot: int | None = None, timeout_s: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None, model: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.slot = slot
        self.model = model
        self._client = httpx.AsyncClient(base_url=self.base_url, transport=transport,
                                         timeout=httpx.Timeout(timeout_s, connect=3.0))

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> EngineHealth:
        """Ready when /health answers 200; llama-server answers 503 while it is still loading the model."""
        try:
            response = await self._client.get("/health")
        except httpx.HTTPError as error:
            return EngineHealth(ready=False, status="unreachable", detail=type(error).__name__)
        if response.status_code == 200:
            return EngineHealth(ready=True, status="ok")
        return EngineHealth(ready=False, status="loading" if response.status_code == 503 else
                            f"HTTP {response.status_code}", detail=response.text[:200])

    async def props(self) -> dict[str, Any]:
        """The server's properties: the model, its chat template and what that template can do."""
        response = await self._client.get("/props")
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    def _body(self, messages: list[dict[str, Any]], *, stream: bool, max_tokens: int, temperature: float,
              top_p: float, min_p: float, top_k: int, repeat_penalty: float, seed: int | None,
              tools: list[dict[str, Any]] | None, think: bool,
              response_format: dict[str, Any] | None) -> dict[str, Any]:
        """The request body shared by `chat()` and `chat_stream()`."""
        body: dict[str, Any] = {"model": self.model or "local", "messages": messages, "max_tokens": max_tokens,
                                "temperature": temperature, "top_p": top_p, "min_p": min_p, "top_k": top_k,
                                "repeat_penalty": repeat_penalty, "stream": stream, "cache_prompt": True,
                                "chat_template_kwargs": {"enable_thinking": think}}
        if seed is not None:
            body["seed"] = seed
        if self.slot is not None:
            body["id_slot"] = self.slot
        if tools:
            body["tools"] = tools
        if response_format is not None:
            body["response_format"] = response_format
        return body

    async def chat(self, messages: list[dict[str, Any]], *, max_tokens: int = 120, temperature: float = 0.7,
                   top_p: float = 0.9, min_p: float = 0.05, top_k: int = 40, repeat_penalty: float = 1.08,
                   seed: int | None = None, tools: list[dict[str, Any]] | None = None, think: bool = False,
                   deadline_s: float | None = None, response_format: dict[str, Any] | None = None) -> ChatResult:
        """One chat completion on this node's slot. Raises EngineError.

        `response_format` (llama.cpp's OpenAI-compatible constraint, e.g. `{"type": "json_object", "schema": {...}}`)
        is sent as is when given.
        """
        body = self._body(messages, stream=False, max_tokens=max_tokens, temperature=temperature, top_p=top_p,
                          min_p=min_p, top_k=top_k, repeat_penalty=repeat_penalty, seed=seed, tools=tools,
                          think=think, response_format=response_format)
        started = time.perf_counter()
        try:
            response = await self._client.post(CHAT_PATH, json=body,
                                                timeout=deadline_s if deadline_s is not None else httpx.USE_CLIENT_DEFAULT)
        except httpx.TimeoutException as error:
            raise EngineError(f"no answer within the deadline ({type(error).__name__})") from error
        except httpx.HTTPError as error:
            raise EngineError(f"server unreachable: {type(error).__name__}: {error}") from error
        latency = time.perf_counter() - started
        if response.status_code != 200:
            raise EngineError(f"HTTP {response.status_code}: {response.text[:300]}")
        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise EngineError(f"unexpected response: {str(error)[:200]}") from error
        usage = data.get("usage") or {}
        timings = data.get("timings") or {}
        return ChatResult(text=message.get("content") or "", tool_calls=list(message.get("tool_calls") or []),
                          finish_reason=choice.get("finish_reason"), prompt_tokens=usage.get("prompt_tokens"),
                          completion_tokens=usage.get("completion_tokens"), prompt_ms=timings.get("prompt_ms"),
                          predicted_ms=timings.get("predicted_ms"),
                          tokens_per_s=timings.get("predicted_per_second"), latency_s=latency)

    async def chat_stream(self, messages: list[dict[str, Any]], *, max_tokens: int = 120, temperature: float = 0.7,
                          top_p: float = 0.9, min_p: float = 0.05, top_k: int = 40, repeat_penalty: float = 1.08,
                          seed: int | None = None, tools: list[dict[str, Any]] | None = None, think: bool = False,
                          response_format: dict[str, Any] | None = None, deadline_s: float, first_token_s: float,
                          max_bytes: int = STREAM_MAX_BYTES) -> AsyncGenerator[StreamDelta, None]:
        """One streamed chat completion on this node's slot (server-sent events), as StreamDeltas. Raises EngineError.

        Bounded (Q4): `max_tokens` is sent to the server; at most `max_bytes` are read; the whole stream must end
        within `deadline_s` and the first text, tool-call or reasoning token must come within `first_token_s` of
        sending (the watchdog). Both are measured with `time.monotonic()`, and no timeout spans a `yield`, so a slow
        consumer is never cancelled by them. The response is always closed, also when the consumer stops early
        (`aclose()` of this generator).
        """
        body = self._body(messages, stream=True, max_tokens=max_tokens, temperature=temperature, top_p=top_p,
                          min_p=min_p, top_k=top_k, repeat_penalty=repeat_penalty, seed=seed, tools=tools,
                          think=think, response_format=response_format)
        started = time.monotonic()
        stream_end = started + deadline_s
        first_token_end = started + first_token_s
        got_token = False
        finished = False
        read = 0

        def budget() -> tuple[float, str]:
            """The time left for the next wait, and what it means when it runs out."""
            now = time.monotonic()
            if not got_token and first_token_end < stream_end:
                return first_token_end - now, f"no first token within {first_token_s} s"
            return stream_end - now, f"the stream did not end within the deadline of {deadline_s} s"

        http_timeout = httpx.Timeout(deadline_s, connect=min(3.0, deadline_s))
        try:
            async with contextlib.AsyncExitStack() as stack:
                response = await _within(lambda: stack.enter_async_context(
                    self._client.stream("POST", CHAT_PATH, json=body, timeout=http_timeout)), *budget())
                if response.status_code != 200:
                    detail = await _within(lambda: _read_some(response, 1200), *budget())
                    raise EngineError(f"HTTP {response.status_code}: {detail[:300]}")
                chunks = cast(AsyncGenerator[bytes, None], response.aiter_bytes())
                stack.push_async_callback(chunks.aclose)
                buffer = b""
                while not finished:
                    piece = await _within(lambda: anext(chunks, None), *budget())
                    if piece is None:
                        break
                    read += len(piece)
                    if read > max_bytes:
                        raise EngineError(f"the stream sent more than {max_bytes} bytes")
                    buffer += piece
                    *lines, buffer = buffer.split(b"\n")
                    for raw in lines:
                        event = _parse_sse_line(raw)
                        if event is None:
                            continue
                        if isinstance(event, str):
                            finished = True
                            break
                        delta, token = event
                        got_token = got_token or token
                        if delta is not None:
                            yield delta
                if not finished and buffer.strip():                # a last line without its newline
                    event = _parse_sse_line(buffer)
                    if isinstance(event, str):
                        finished = True
                    elif event is not None and event[0] is not None:
                        yield event[0]
                if not finished:
                    raise EngineError("the stream ended without [DONE]")
        except httpx.TimeoutException as error:
            raise EngineError(f"no answer within the deadline ({type(error).__name__})") from error
        except httpx.HTTPError as error:
            raise EngineError(f"server unreachable: {type(error).__name__}: {error}") from error


_T = TypeVar("_T")


async def _within(start: Callable[[], Awaitable[_T]], limit_s: float, reason: str) -> _T:
    """Await `start()` for at most `limit_s` seconds (monotonic); EngineError(reason) when the time runs out."""
    if limit_s <= 0:
        raise EngineError(reason)
    try:
        async with asyncio.timeout(limit_s):
            return await start()
    except TimeoutError as error:
        raise EngineError(reason) from error


async def _read_some(response: httpx.Response, limit: int) -> str:
    """At most about `limit` bytes of a response body, as text (for error messages; never the whole body)."""
    data = b""
    async for piece in response.aiter_bytes():
        data += piece
        if len(data) >= limit:
            break
    return data[:limit].decode("utf-8", errors="replace")


def _parse_sse_line(raw: bytes) -> tuple[StreamDelta | None, bool] | str | None:
    """One server-sent-event line: None to skip it, "done" for `data: [DONE]`, else (the delta or None, whether it
    carried a token). Raises EngineError for a malformed line, a server error event or a chunk without choices."""
    try:
        line = raw.rstrip(b"\r").decode("utf-8")
    except UnicodeDecodeError as error:
        raise EngineError(f"malformed stream line: {error}") from error
    if not line.strip() or line.startswith(":"):
        return None                                        # keep-alive or comment
    if line.startswith("error:"):
        raise EngineError(f"server error in the stream: {line[6:].strip()[:300]}")
    if not line.startswith("data:"):
        return None                                        # other SSE fields (event:, id:, retry:)
    payload = line[5:].strip()
    if payload == "[DONE]":
        return "done"
    try:
        chunk = json.loads(payload)
    except ValueError as error:
        raise EngineError(f"malformed stream data: {payload[:200]!r}") from error
    if not isinstance(chunk, dict):
        raise EngineError(f"malformed stream data: {payload[:200]!r}")
    chunk = cast(dict[str, Any], chunk)
    choices = chunk.get("choices")
    if not isinstance(choices, list):
        if "error" in chunk:
            raise EngineError(f"server error in the stream: {str(chunk['error'])[:300]}")
        raise EngineError(f"stream chunk without choices: {payload[:200]!r}")
    choices = cast(list[Any], choices)
    timings = chunk.get("timings")
    try:
        if not choices:                                    # a usage-only chunk (llama.cpp may put the timings here)
            return (StreamDelta(timings=timings) if timings else None), False
        choice = cast(dict[str, Any], choices[0])
        delta = cast(dict[str, Any], choice.get("delta") or {})
        text = delta.get("content") or ""
        tool_calls = delta.get("tool_calls") or []
        token = bool(text) or bool(tool_calls) or bool(delta.get("reasoning_content"))
        finish_reason = choice.get("finish_reason")
        if not (text or tool_calls or finish_reason or timings):
            return None, token                             # e.g. the first role-only chunk, or reasoning only
        return StreamDelta(text=text, tool_calls=tool_calls, finish_reason=finish_reason, timings=timings), token
    except (ValidationError, AttributeError, TypeError) as error:
        raise EngineError(f"malformed stream chunk: {str(error)[:200]}") from error


def assemble_tool_calls(deltas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge streamed tool-call fragments (OpenAI `delta.tool_calls` items) by `index` into complete tool calls
    `{"id", "type": "function", "function": {"name", "arguments"}}`, in index order; the arguments are concatenated
    as a string, not parsed. A fragment without an index belongs to the call before it (or index 0)."""
    calls: dict[int, dict[str, Any]] = {}
    last = 0
    for fragment in deltas:
        index = fragment.get("index")
        last = index if isinstance(index, int) else last
        call = calls.setdefault(last, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
        if fragment.get("id"):
            call["id"] = str(fragment["id"])
        function = fragment.get("function") or {}
        if function.get("name"):
            call["function"]["name"] += str(function["name"])
        if function.get("arguments"):
            call["function"]["arguments"] += str(function["arguments"])
    return [calls[index] for index in sorted(calls)]
