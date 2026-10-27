"""Audit scopes persist success and failure timings and tolerate an unavailable NATS link."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from contracts import AuditRecord
from llm.audit import Auditor
from llm.conversation import LlmStore


class FakeLink:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[tuple[str, AuditRecord]] = []

    async def publish(self, subject: str, message: AuditRecord) -> None:
        if self.fail:
            raise ConnectionError("NATS is unavailable")
        self.messages.append((subject, message))


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[LlmStore]:
    result = LlmStore(tmp_path / "llm.sqlite", node="incubator-1", owner_conversations=True)
    yield result
    await result.close()


@pytest.mark.asyncio
async def test_success_writes_and_publishes_one_timed_row(store: LlmStore) -> None:
    link = FakeLink()
    auditor = Auditor(store, link, "incubator-1")
    async with auditor.step("same-id", "tool_call", "get_status", {"fields": ["online"]}) as step:
        step.done({"online": True}, "06:00", 9)
    rows = await auditor.timings("same-id")
    assert len(rows) == len(link.messages) == 1
    assert rows[0]["ok"] == 1 and rows[0]["duration_ms"] >= 0
    assert rows[0]["farm_time"] == "06:00" and rows[0]["tick"] == 9
    assert link.messages[0][0] == "jarvis.incubator-1.audit"


@pytest.mark.asyncio
async def test_failure_is_recorded_and_original_exception_is_reraised(store: LlmStore) -> None:
    auditor = Auditor(store, None, "incubator-1")
    with pytest.raises(ValueError, match="bad tool"):
        async with auditor.step("failed", "tool_call", "get_status", {}) as _:
            raise ValueError("bad tool")
    row = (await auditor.timings("failed"))[0]
    assert row["ok"] == 0 and "ValueError: bad tool" == row["error"]


@pytest.mark.asyncio
async def test_large_payload_is_cut_to_four_kilobytes(store: LlmStore) -> None:
    auditor = Auditor(store, None, "incubator-1")
    async with auditor.step("large", "tool_call", "search_documents", {}) as step:
        step.done({"text": "x" * 10_000})
    record = (await auditor.timings("large"))[0]
    assert record["truncated"] == 1
    assert len(record["outputs_json"].encode("utf-8")) <= 4096


@pytest.mark.asyncio
async def test_nats_failure_does_not_lose_the_local_row(store: LlmStore) -> None:
    auditor = Auditor(store, FakeLink(fail=True), "incubator-1")
    async with auditor.step("offline", "answer", None, {}) as step:
        step.done({"answer": "safe"})
    assert len(await auditor.timings("offline")) == 1


@pytest.mark.asyncio
async def test_missing_request_id_is_generated_in_the_audit_row(store: LlmStore) -> None:
    link = FakeLink()
    auditor = Auditor(store, link, "incubator-1")
    async with auditor.step(None, "peer_answer", None, {}) as step:
        step.done({})
    request_id = link.messages[0][1].request_id
    assert request_id.startswith("r-") and len(request_id) == 14
    assert (await auditor.timings(request_id))[0]["request_id"] == request_id
