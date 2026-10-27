"""Measure and persist bounded tool and peer audit steps, publishing them on the node's NATS subject when available.

In: request ids, step metadata, inputs, outputs and optional farm coordinates. Out: durable `llm.sqlite` audit rows and
best-effort `jarvis.<node>.audit` messages; exceptions are recorded and re-raised. Tests: `tests/llm/test_audit.py`.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Mapping

from contracts import AuditRecord, NodeId, jarvis_subject, utc_now
from llm.conversation import LlmStore

MAX_AUDIT_JSON_BYTES = 4096


def _bounded(value: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Keep audit JSON valid and at most 4 KB, preserving a bounded preview when it must be shortened."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text.encode("utf-8")) <= MAX_AUDIT_JSON_BYTES:
        return dict(value), False
    prefix = text.encode("utf-8")[:MAX_AUDIT_JSON_BYTES - 64].decode("utf-8", errors="ignore")
    bounded = {"truncated_preview": prefix}
    while len(json.dumps(bounded, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_AUDIT_JSON_BYTES:
        prefix = prefix[:-64]
        bounded["truncated_preview"] = prefix
    return bounded, True


@dataclass
class AuditStep:
    """Mutable success values collected by one `Auditor.step` scope."""

    outputs: Mapping[str, Any] | None = None
    farm_time: str | None = None
    tick: int | None = None

    def done(self, outputs: Mapping[str, Any], farm_time: str | None = None, tick: int | None = None) -> None:
        """Mark the step successful and attach its result and optional farm coordinates."""
        self.outputs = outputs
        self.farm_time = farm_time
        self.tick = tick


class Auditor:
    """Persist each measured step locally and publish it to the matching audit subject best-effort."""

    def __init__(self, store: LlmStore, link: Any | None, node: NodeId, *, logger: logging.Logger | None = None) -> None:
        self.store = store
        self.link = link
        self.node: NodeId = node
        self.logger = logger or logging.getLogger(f"jarvis.audit.{node}")

    @asynccontextmanager
    async def step(self, request_id: str | None,
                   kind: Literal["tool_call", "peer_answer", "peer_question", "refusal", "answer"], tool: str | None,
                   inputs: Mapping[str, Any]) -> AsyncIterator[AuditStep]:
        """Record one scoped operation; record failures before re-raising their original exception."""
        actual_request_id = request_id or f"r-{secrets.token_hex(6)}"
        step = AuditStep()
        started = time.perf_counter()
        error_text: str | None = None
        ok = False
        truncated = False
        outputs: Mapping[str, Any] = {}
        try:
            yield step
            if step.outputs is None:
                error_text = "step completed without done()"
            else:
                ok = True
                outputs = step.outputs
        except Exception as error:
            error_text = f"{type(error).__name__}: {error}"
            raise
        finally:
            input_json, input_truncated = _bounded(inputs)
            output_json, output_truncated = _bounded(outputs)
            truncated = input_truncated or output_truncated
            record = AuditRecord(request_id=actual_request_id, node=self.node, kind=kind, tool=tool, ok=ok,
                                 error=error_text, duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
                                 farm_time=step.farm_time, tick=step.tick, inputs=input_json, outputs=output_json,
                                 truncated=truncated, created_utc=utc_now())  # type: ignore[arg-type]
            await self.store.add_audit(record)
            if self.link is not None:
                try:
                    await self.link.publish(jarvis_subject(self.node, "audit"), record)
                except Exception:
                    self.logger.exception("could not publish audit record", extra={"request_id": actual_request_id})

    async def timings(self, request_id: str) -> list[dict[str, Any]]:
        """Return the recorded audit steps and their durations for one request."""
        return await self.store.audit_for(request_id)
