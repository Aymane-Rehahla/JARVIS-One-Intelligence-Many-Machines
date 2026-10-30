"""The mentor's LLM failure switches (test only; E7), applied to this node's own engine and peer answers.

The gateway publishes `LlmFailureSwitches` on `mentor.llm_failures`; each node applies only the switches that name it:
    llm_timeout    its language-model calls never answer in time (they wait out their deadline, then fail)
    broken_json    its tool-call arguments and JSON answers come back corrupted
    silent_peer    it stops answering other nodes' questions (they time out, as a dead node would)
    server_crash   its language-model server is treated as down (unreachable at once)
**Only this module reads the switches.** The agent, the minds, the prompts and the tools never import it or name them
(a static test proves it); they meet the failures only as the ordinary errors the wrapped engine raises. The switches
never reach the farm or the reasoning (ground rule 4): only the node programs subscribe to them.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from comms.jsonlog import log
from contracts import MENTOR_LLM_FAILURES, LlmFailureSwitches, NodeId
from llm.engines import ChatResult, EngineError, EngineHealth, StreamDelta

SwitchName = str


def corrupt(text: str) -> str:
    """JSON that no parser accepts: half of it, then a stray brace."""
    return text[: len(text) // 2] + '}{"broken'


class FailureSwitches:
    """The switches that are on for this node (none until the gateway says so)."""

    def __init__(self, node: NodeId, *, logger: logging.Logger | None = None) -> None:
        self.node: NodeId = node
        self.logger = logger or logging.getLogger(f"jarvis.failures.{node}")
        self.current: LlmFailureSwitches | None = None

    def on(self, name: SwitchName) -> bool:
        return self.current is not None and self.node in getattr(self.current, name)

    async def update(self, switches: LlmFailureSwitches, subject: str = MENTOR_LLM_FAILURES) -> None:
        if switches != self.current:
            mine = [name for name in ("llm_timeout", "broken_json", "silent_peer", "server_crash")
                    if self.node in getattr(switches, name)]
            log(self.logger, logging.WARNING if mine else logging.INFO, "LLM failure switches", node=self.node,
                on=mine)
        self.current = switches

    async def serve(self, link: Any) -> None:
        """Follow `mentor.llm_failures` on this node's NATS link."""
        await link.subscribe(MENTOR_LLM_FAILURES, LlmFailureSwitches, self.update)


class FaultyEngine:
    """An EngineClient with the switches applied; the same methods and signatures as the engine it wraps."""

    def __init__(self, engine: Any, switches: FailureSwitches) -> None:
        self.engine = engine
        self.switches = switches

    async def close(self) -> None:
        await self.engine.close()

    async def health(self) -> EngineHealth:
        if self.switches.on("server_crash"):
            return EngineHealth(ready=False, status="unreachable", detail="server crash (mentor test switch)")
        result: EngineHealth = await self.engine.health()
        return result

    async def _before(self, deadline_s: float | None) -> None:
        if self.switches.on("server_crash"):
            raise EngineError("server unreachable: ConnectError (mentor test switch)")
        if self.switches.on("llm_timeout"):
            await asyncio.sleep(deadline_s if deadline_s is not None else 30.0)
            raise EngineError("no answer within the deadline (mentor test switch)")

    async def chat(self, messages: list[dict[str, Any]], **options: Any) -> ChatResult:
        await self._before(options.get("deadline_s"))
        result: ChatResult = await self.engine.chat(messages, **options)
        if self.switches.on("broken_json"):
            calls = [{**call, "function": {**call.get("function", {}),
                                           "arguments": corrupt(str(call.get("function", {}).get("arguments", "")))}}
                     for call in result.tool_calls]
            text = corrupt(result.text) if options.get("response_format") else result.text
            result = result.model_copy(update={"tool_calls": calls, "text": text})
        return result

    async def chat_stream(self, messages: list[dict[str, Any]], **options: Any) -> AsyncGenerator[StreamDelta, None]:
        if self.switches.on("server_crash"):
            raise EngineError("server unreachable: ConnectError (mentor test switch)")
        if self.switches.on("llm_timeout"):
            await asyncio.sleep(float(options.get("first_token_s", 4.0)))
            raise EngineError("no first token within the deadline (mentor test switch)")
        broken = self.switches.on("broken_json")
        stream = self.engine.chat_stream(messages, **options)
        try:
            async for delta in stream:
                if broken and delta.tool_calls:
                    delta = delta.model_copy(update={"tool_calls": [
                        {**call, "function": {**call.get("function", {}),
                                              "arguments": corrupt(str(call.get("function", {}).get("arguments", "")))}}
                        if "function" in call else call for call in delta.tool_calls]})
                if broken and options.get("response_format") and delta.text:
                    delta = delta.model_copy(update={"text": corrupt(delta.text)})
                yield delta
        finally:
            await stream.aclose()
