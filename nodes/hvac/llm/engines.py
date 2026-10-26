"""The language-model engines (S2, S15): one llama.cpp server per node, and a node's connection to its own one.

Four independent servers on the RX 580 (Vulkan), every layer on the GPU (plan section 4.1, decision 0013):
    incubator-1        Qwen 3.5 2B roleplay, 1 slot
    incubator-2, hvac, animal-protection     Qwen3-0.6B, 1 slot each
`server_spec()` and `server_command()` give each server's command line (the launcher's supervisor starts them),
`EngineClient.health()` asks a server whether its model is loaded, and `EngineClient.chat()` sends one chat
completion to the node's own server and slot, with a deadline (Q4). Pinning the slot (`id_slot`) and starting every
prompt with the same fixed text let llama.cpp reuse its prompt cache (S15). The thinking mode is off unless asked for.
"""
from __future__ import annotations

import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from contracts import NodeId, load_node_config

NODE_DIR = Path(__file__).resolve().parents[1]
ROOT = NODE_DIR.parents[1]
LLAMA_SERVER = ROOT / "third_party" / "llama.cpp" / "bin" / "llama-server.exe"
CTX_PER_SLOT = 8192                     # the persona spec's context size (docs/persona-spec.md, section 4)
CHAT_PATH = "/v1/chat/completions"


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
    Jinja chat templates (for tool calls), Prometheus metrics, listening on 127.0.0.1 only (Q7)."""
    return [str(exe), "-m", str(spec.model), "--host", spec.host, "--port", str(spec.port),
            "-ngl", str(spec.gpu_layers), "-c", str(spec.ctx_per_slot * spec.slots), "-np", str(spec.slots),
            "-t", str(spec.threads), "--jinja", "--metrics", "-a", spec.alias]


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

    async def chat(self, messages: list[dict[str, Any]], *, max_tokens: int = 120, temperature: float = 0.7,
                   top_p: float = 0.9, min_p: float = 0.05, top_k: int = 40, repeat_penalty: float = 1.08,
                   seed: int | None = None, tools: list[dict[str, Any]] | None = None, think: bool = False,
                   deadline_s: float | None = None) -> ChatResult:
        """One chat completion on this node's slot. Raises EngineError."""
        body: dict[str, Any] = {"model": self.model or "local", "messages": messages, "max_tokens": max_tokens,
                                "temperature": temperature, "top_p": top_p, "min_p": min_p, "top_k": top_k,
                                "repeat_penalty": repeat_penalty, "stream": False, "cache_prompt": True,
                                "chat_template_kwargs": {"enable_thinking": think}}
        if seed is not None:
            body["seed"] = seed
        if self.slot is not None:
            body["id_slot"] = self.slot
        if tools:
            body["tools"] = tools
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
