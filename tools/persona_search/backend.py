"""Chat backends for the persona search: llama-server's OpenAI-compatible API, and a fake for tests and smoke runs.

Every network call has a timeout and a bounded number of retries (Q4); a failure raises BackendError with the cause.
"""
from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from persona_search.models import Category, Completion, Sampling, ToolEvidence

CHAT_PATH = "/v1/chat/completions"
HEALTH_PATHS = ("/health", "/v1/models")
MAX_RETRIES = 5
MAX_BACKOFF_S = 8.0
RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})
FAKE_CALL_HISTORY = 10_000


class BackendError(RuntimeError):
    """A backend could not produce a reply (server down, timeout, bad response)."""


class ToolCallFunction(BaseModel):
    """The function part of an OpenAI tool call."""

    model_config = ConfigDict(extra="forbid")
    name: str
    arguments: str = "{}"


class ToolCall(BaseModel):
    """An OpenAI-style tool call made by the assistant."""

    model_config = ConfigDict(extra="forbid")
    id: str
    type: Literal["function"] = "function"
    function: ToolCallFunction


class ChatMessage(BaseModel):
    """One chat message in OpenAI format."""

    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def to_openai(self) -> dict[str, Any]:
        """The message as the API expects it (unset fields left out)."""
        return self.model_dump(exclude_none=True)


class ChatRequest(BaseModel):
    """One request. `config_id`, `prompt_id`, `category` and `evidence` are for logs and fakes, never sent."""

    model_config = ConfigDict(extra="forbid")
    config_id: str
    prompt_id: str
    category: Category
    messages: list[ChatMessage] = Field(min_length=1)
    sampling: Sampling
    max_tokens: int = Field(ge=1, le=4096)
    seed: int
    evidence: list[ToolEvidence] = Field(default_factory=list[ToolEvidence])


class Backend(Protocol):
    """Anything that turns a ChatRequest into a Completion."""

    label: str

    def complete(self, request: ChatRequest) -> Completion:
        """Return the model's reply, or raise BackendError."""
        ...

    def close(self) -> None:
        """Release connections."""
        ...


class _Message(BaseModel):
    content: str | None = None


class _Choice(BaseModel):
    message: _Message
    finish_reason: str | None = None


class _Usage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class _Timings(BaseModel):
    predicted_per_second: float | None = None


class _ChatResponse(BaseModel):
    """The parts of a chat completion response we read; anything else is ignored."""

    choices: list[_Choice] = Field(min_length=1)
    usage: _Usage | None = None
    timings: _Timings | None = None


class OpenAICompatibleBackend:
    """llama-server (or any OpenAI-compatible server) over HTTP, with timeouts and bounded retries."""

    def __init__(self, base_url: str, *, timeout_s: float = 90.0, connect_timeout_s: float = 5.0, retries: int = 2,
                 backoff_s: float = 1.0, model: str = "jarvis-speaker", extra_body: Mapping[str, Any] | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        if not 0 <= retries <= MAX_RETRIES:
            raise ValueError(f"retries must be between 0 and {MAX_RETRIES}, got {retries}")
        if timeout_s <= 0 or connect_timeout_s <= 0 or backoff_s < 0:
            raise ValueError("timeouts must be positive and the backoff not negative")
        root = base_url.rstrip("/")
        root = root.removesuffix("/v1")
        self.label = f"openai-compatible {root}"
        self._retries = retries
        self._backoff_s = backoff_s
        self._model = model
        self._extra = dict(extra_body or {})
        self._client = httpx.Client(base_url=root, transport=transport,
                                    timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s))

    def health(self) -> str:
        """Check that the server answers; returns the path that worked. Raises BackendError otherwise."""
        problems: list[str] = []
        for path in HEALTH_PATHS:
            try:
                response = self._client.get(path)
            except httpx.HTTPError as error:
                problems.append(f"{path}: {type(error).__name__}: {error}")
                continue
            if response.status_code == 200:
                return path
            problems.append(f"{path}: HTTP {response.status_code}")
        raise BackendError("server not ready at " + self.label.split(" ", 1)[1] + " (" + "; ".join(problems) + ")")

    def _body(self, request: ChatRequest) -> dict[str, Any]:
        sampling = request.sampling
        return {
            "model": self._model,
            "messages": [message.to_openai() for message in request.messages],
            "temperature": sampling.temperature,
            "top_p": sampling.top_p,
            "min_p": sampling.min_p,
            "repeat_penalty": sampling.repeat_penalty,
            "max_tokens": request.max_tokens,
            "seed": request.seed,
            "stream": False,
            "cache_prompt": True,
            **self._extra,
        }

    def complete(self, request: ChatRequest) -> Completion:
        """POST one chat completion; retries transport errors and 408/429/5xx up to `retries` times."""
        body = self._body(request)
        last = "no attempt made"
        for attempt in range(self._retries + 1):
            started = time.perf_counter()
            try:
                response = self._client.post(CHAT_PATH, json=body)
            except httpx.HTTPError as error:
                last = f"{type(error).__name__}: {error}"
            else:
                latency = time.perf_counter() - started
                if response.status_code == 200:
                    return self._parse(response, latency)
                last = f"HTTP {response.status_code}: {response.text[:300]}"
                if response.status_code not in RETRY_STATUS:
                    raise BackendError(f"{request.config_id}/{request.prompt_id}: {last}")
            if attempt < self._retries:
                time.sleep(min(self._backoff_s * 2 ** attempt, MAX_BACKOFF_S))
        raise BackendError(f"{request.config_id}/{request.prompt_id}: gave up after {self._retries + 1} attempts: {last}")

    @staticmethod
    def _parse(response: httpx.Response, latency: float) -> Completion:
        try:
            parsed = _ChatResponse.model_validate(response.json())
        except (ValidationError, json.JSONDecodeError, ValueError) as error:
            raise BackendError(f"unexpected response: {str(error)[:300]}") from error
        choice = parsed.choices[0]
        usage = parsed.usage or _Usage()
        return Completion(text=choice.message.content or "", latency_s=latency, prompt_tokens=usage.prompt_tokens,
                          completion_tokens=usage.completion_tokens, finish_reason=choice.finish_reason,
                          tokens_per_s=parsed.timings.predicted_per_second if parsed.timings else None)

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self._client.close()


Responder = Callable[[ChatRequest], str]


class FakeBackend:
    """Returns canned replies from `responder` without any model; latency is simulated, never slept."""

    def __init__(self, responder: Responder, *, latency_s: float = 0.0, label: str = "fake") -> None:
        self.label = label
        self._responder = responder
        self._latency_s = latency_s
        self.calls: deque[ChatRequest] = deque(maxlen=FAKE_CALL_HISTORY)

    def complete(self, request: ChatRequest) -> Completion:
        """Record the request and return the responder's text (BackendError from the responder passes through)."""
        self.calls.append(request)
        text = self._responder(request)
        return Completion(text=text, latency_s=self._latency_s, completion_tokens=len(text.split()),
                          finish_reason="stop")

    def close(self) -> None:
        """Nothing to release."""
