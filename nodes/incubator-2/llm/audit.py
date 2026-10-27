"""Measure and persist bounded tool and peer audit steps, publishing them on the node's NATS subject when available.

In: request ids, step metadata, inputs, outputs and optional farm coordinates. Out: durable `llm.sqlite` audit rows and
best-effort `jarvis.<node>.audit` messages; exceptions are recorded and re-raised. Tests: `tests/llm/test_audit.py`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Mapping

from contracts import REQUEST_ID, AuditRecord, NodeId, jarvis_subject, utc_now
from llm.conversation import LlmStore

MAX_AUDIT_JSON_BYTES = 4096


def _bounded(value: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Plain JSON of at most 4 KB: values JSON cannot hold (tuples, datetimes, models) become text or lists through
    a JSON round trip, and an oversized value is kept as a bounded preview."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text.encode("utf-8")) <= MAX_AUDIT_JSON_BYTES:
        plain: dict[str, Any] = json.loads(text)
        return plain, False
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
        self.failures = 0                                  # audit steps that could not be recorded (health checks)

    @asynccontextmanager
    async def step(self, request_id: str | None,
                   kind: Literal["tool_call", "peer_answer", "peer_question", "refusal", "answer"], tool: str | None,
                   inputs: Mapping[str, Any]) -> AsyncIterator[AuditStep]:
        """Record one scoped operation; record failures before re-raising their original exception.

        Recording never changes the outcome: an invalid request id is replaced by a fresh one (the given text is
        kept in the inputs as "given_request_id"), values that are not plain JSON are written as text, and a failed
        database write is logged and counted in `failures`; it never replaces the operation's own result or error."""
        actual_request_id = request_id if request_id and re.fullmatch(REQUEST_ID, request_id) else \
            f"r-{secrets.token_hex(6)}"
        if request_id and actual_request_id != request_id:
            inputs = {**inputs, "given_request_id": request_id[:80]}
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
        except asyncio.CancelledError:
            error_text = "CancelledError: operation cancelled or deadline exceeded"
            raise
        finally:
            duration_ms = max(0, round((time.perf_counter() - started) * 1000))
            try:
                input_json, input_truncated = _bounded(inputs)
                output_json, output_truncated = _bounded(outputs)
                truncated = input_truncated or output_truncated
                record = AuditRecord(request_id=actual_request_id, node=self.node, kind=kind, tool=tool, ok=ok,
                                     error=error_text, duration_ms=duration_ms, farm_time=step.farm_time,
                                     tick=step.tick, inputs=input_json, outputs=output_json, truncated=truncated,
                                     created_utc=utc_now())  # type: ignore[arg-type]
                await self.store.add_audit(record)
            except Exception:                              # never let recording replace the real result
                self.failures += 1
                self.logger.exception("could not record an audit step", extra={"fields": {
                    "request_id": actual_request_id, "tool": tool, "kind": kind}})
            else:
                if self.link is not None:
                    try:
                        await self.link.publish(jarvis_subject(self.node, "audit"), record)
                    except Exception:
                        self.logger.exception("could not publish audit record",
                                              extra={"fields": {"request_id": actual_request_id}})

    async def timings(self, request_id: str) -> list[dict[str, Any]]:
        """Return the recorded audit steps and their durations for one request."""
        return await self.store.audit_for(request_id)
