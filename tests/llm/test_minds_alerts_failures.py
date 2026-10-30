"""6C's smaller pieces: the peer minds' verified summaries, the alert path (watcher, desk, template) and the mentor's
LLM failure switches. Scripted engines; no model, no NATS."""
from __future__ import annotations

import ast
import asyncio
import json
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from contracts import AlertNotice, FarmAssessment, GateDecision, LlmFailureSwitches, OwnerAlert, utc_now
from llm.alerts import AlertDesk, AlertWatcher, template
from llm.engines import ChatResult, EngineError, StreamDelta
from llm.failure_injection import FailureSwitches, FaultyEngine
from llm.peer_minds import MIND_BUDGET_S, PeerMind, budget_s

ROOT = Path(__file__).resolve().parents[2]
COMMON: dict[str, Any] = dict(tick=8, time_s=480.0, farm_time="06:08", label="live: simulated brains",
                              feed_id="0000000000a1")


def assessment(level: str = "ESCALATE_TO_USER", node: str = "hvac", incident: str | None = "hvac-equipment-0001",
               **extra: Any) -> FarmAssessment:
    return FarmAssessment(node=node, **COMMON, assessment=level, diagnosis="SUPPLY_FAN_FAULT",  # type: ignore[arg-type]
                          confidence=0.9, brain="rule", incident_id=incident, evidence=["SUPPLY_FAN_FAILURE"], **extra)


class Engine:
    """chat() answers from a script; records each call's options."""

    def __init__(self, *answers: Any, delay: float = 0.0) -> None:
        self.answers = list(answers)
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict[str, Any]], **options: Any) -> ChatResult:
        self.calls.append(options)
        await asyncio.sleep(self.delay)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, ChatResult):
            return answer
        return ChatResult(text=answer, latency_s=self.delay)

    async def chat_stream(self, messages: list[dict[str, Any]], **options: Any) -> AsyncGenerator[StreamDelta, None]:
        self.calls.append(options)
        yield StreamDelta(tool_calls=[{"index": 0, "function": {"name": "get_trend", "arguments": '{"signal": "x"}'}}])
        yield StreamDelta(text='{"summary": "ok"}')

    async def health(self) -> Any:
        return "healthy"

    async def close(self) -> None:
        pass


# --- the peer minds ------------------------------------------------------------------------------------------------

ANSWER = {"get_status": {"readings": {"airflow_m3_h": 0.0, "supply_air_temperature_c": 24.1}, "farm_time": "06:08"}}


@pytest.mark.asyncio
async def test_a_verified_summary_joins_the_answer() -> None:
    engine = Engine('{"summary": "The supply fan has stopped: airflow 0 m³/h, supply air 24.1 °C."}')
    summary, status = await PeerMind(engine).summarize("how is the fan?", ANSWER, 1.0)
    assert status == "verified" and summary is not None and "24.1" in summary
    options = engine.calls[0]
    assert options["response_format"]["type"] == "json_object" and options["deadline_s"] == 1.0


@pytest.mark.parametrize(("reply", "why"), [('{"summary": "Airflow is 900 m³/h."}', "unverified"),
                                            ("not json", "not valid JSON"), ('{"summary": ""}', "not valid JSON"),
                                            (EngineError("server unreachable"), "unreachable")])
@pytest.mark.asyncio
async def test_an_unverified_or_broken_summary_is_omitted_with_its_reason(reply: Any, why: str) -> None:
    summary, status = await PeerMind(Engine(reply)).summarize("q", ANSWER, 1.0)
    assert summary is None and status.startswith("omitted") and why in status


@pytest.mark.asyncio
async def test_the_mind_never_makes_an_answer_late() -> None:
    engine = Engine('{"summary": "fine"}')
    summary, status = await PeerMind(engine).summarize("q", ANSWER, 0.2)
    assert summary is None and "no time left" in status and engine.calls == []
    assert budget_s(3000, 1.0, 0.1) == pytest.approx(0.6)          # 3.0 - 2 hops - 0.3 margin - 0.1 used
    assert budget_s(3000, 0.01, 0.0) == MIND_BUDGET_S                # never more than the mind's own budget


# --- alerts ---------------------------------------------------------------------------------------------------------

def test_the_template_uses_only_the_published_output() -> None:
    text = template(assessment("DANGER", action=GateDecision(proposed="SHUTDOWN", allowed=True, reason="evidence")))
    assert text.startswith("Sir, danger at the HVAC: supply fan fault.")
    assert "Evidence: supply fan failure." in text and "allowed the action shutdown" in text
    assert text.endswith("Farm time 06:08.")


@pytest.mark.asyncio
async def test_one_notice_per_incident_and_level_and_a_rising_level_alerts_again() -> None:
    sent: list[AlertNotice] = []

    async def send(notice: AlertNotice) -> None:
        sent.append(notice)
    watcher = AlertWatcher("hvac", send)
    for level in ("WATCHING", "ESCALATE_TO_USER", "ESCALATE_TO_USER", "DANGER", "ESCALATE_TO_USER", "DANGER"):
        await watcher.on_assessment(assessment(level))
    assert [n.assessment.assessment for n in sent] == ["ESCALATE_TO_USER", "DANGER"]
    await watcher.on_assessment(assessment("ESCALATE_TO_USER", incident="hvac-equipment-0002"))
    assert len(sent) == 3 and all(n.target == "incubator-1" and n.sender == "hvac" for n in sent)


class Link:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def publish(self, subject: str, message: Any, **_: Any) -> None:
        self.sent.append((subject, message))


def notice(level: str = "ESCALATE_TO_USER") -> AlertNotice:
    return AlertNotice(notice_id="n-1", sender="hvac", target="incubator-1", assessment=assessment(level),
                       sent_utc=utc_now())


@pytest.mark.asyncio
async def test_the_desk_publishes_one_owner_alert_fast_and_tells_the_listeners() -> None:
    link, heard = Link(), []

    async def deliver(alert: OwnerAlert) -> None:
        heard.append(alert)
    desk = AlertDesk(link, deliver=deliver)
    started = time.monotonic()
    alert = await desk.receive(notice())
    assert alert is not None and time.monotonic() - started < 0.5                # the template, within 0.5 s
    assert link.sent == [("jarvis.incubator-1.alert", alert)] and heard == [alert]
    assert alert.reworded is False and alert.level == "ESCALATE_TO_USER" and alert.farm_time == "06:08"
    assert await desk.receive(notice()) is None                                   # once per incident and level
    assert (await desk.receive(notice("DANGER"))) is not None                     # a rising level alerts again


@pytest.mark.asyncio
async def test_rewording_is_used_only_when_verified_and_in_time() -> None:
    good = "Sir, the HVAC needs you: its supply fan has failed. Farm time 06:08."
    desk = AlertDesk(Link(), engine=Engine(good), reword=True)
    assert (alert := await desk.receive(notice())) is not None and alert.reworded and alert.text == good
    invented = AlertDesk(Link(), engine=Engine("Sir, the HVAC is at 41 °C. Farm time 06:08."), reword=True)
    assert (alert := await invented.receive(notice())) is not None and not alert.reworded
    slow = AlertDesk(Link(), engine=Engine(good, delay=5.0), reword=True)
    started = time.monotonic()
    assert (alert := await slow.receive(notice())) is not None and not alert.reworded
    assert time.monotonic() - started < 2.5                                       # the reword deadline, 2.0 s


# --- the failure switches -------------------------------------------------------------------------------------------

def switches_for(**on: list[str]) -> LlmFailureSwitches:
    return LlmFailureSwitches(set_utc=utc_now(), **on)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_only_the_switches_naming_this_node_apply() -> None:
    switches = FailureSwitches("hvac")
    await switches.update(switches_for(server_crash=["incubator-2"], silent_peer=["hvac"]))
    assert switches.on("silent_peer") and not switches.on("server_crash")


@pytest.mark.asyncio
async def test_server_crash_and_timeout_are_ordinary_engine_errors() -> None:
    switches = FailureSwitches("hvac")
    engine = FaultyEngine(Engine("fine"), switches)
    await switches.update(switches_for(server_crash=["hvac"]))
    with pytest.raises(EngineError, match="unreachable"):
        await engine.chat([], deadline_s=1.0)
    assert (await engine.health()).ready is False
    await switches.update(switches_for(llm_timeout=["hvac"]))
    started = time.monotonic()
    with pytest.raises(EngineError, match="deadline"):
        await engine.chat([], deadline_s=0.3)
    assert 0.25 <= time.monotonic() - started < 1.0
    with pytest.raises(EngineError):
        async for _ in engine.chat_stream([], first_token_s=0.2, deadline_s=1.0):
            pass
    await switches.update(switches_for())
    assert (await engine.chat([], deadline_s=1.0)).text == "fine"


@pytest.mark.asyncio
async def test_broken_json_corrupts_tool_arguments_and_json_answers() -> None:
    switches = FailureSwitches("hvac")
    await switches.update(switches_for(broken_json=["hvac"]))
    engine = FaultyEngine(Engine('{"summary": "fine"}', ChatResult(
        text="", tool_calls=[{"function": {"name": "get_trend", "arguments": '{"signal": "x"}'}}], latency_s=0.0)),
        switches)
    result = await engine.chat([], response_format={"type": "json_object"})
    with pytest.raises(ValueError):
        json.loads(result.text)
    result = await engine.chat([])
    with pytest.raises(ValueError):
        json.loads(result.tool_calls[0]["function"]["arguments"])
    deltas = [d async for d in engine.chat_stream([], response_format={"type": "json_object"})]
    with pytest.raises(ValueError):
        json.loads(deltas[0].tool_calls[0]["function"]["arguments"])


def test_only_the_failure_injector_knows_the_switches() -> None:
    """The agent, the minds, the prompts and the tools never import the injector or name a switch (E7's rule)."""
    names = ("llm_timeout", "broken_json", "silent_peer", "server_crash", "failure_injection", "MENTOR_LLM_FAILURES",
             "LlmFailureSwitches")
    for module in ("agent", "peer_minds", "jarvis_speaker", "tools", "personality", "recovery", "routing", "alerts",
                   "verify", "documents", "context_first", "capabilities"):
        source = (ROOT / "nodes" / "incubator-1" / "llm" / f"{module}.py").read_text(encoding="utf-8")
        ast.parse(source)
        assert not [name for name in names if name in source], module
