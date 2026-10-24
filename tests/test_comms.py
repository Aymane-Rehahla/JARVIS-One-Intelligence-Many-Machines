"""comms/: the NATS link (validated, bounded), the in-node event bus, and the ZigBee rules between nodes (S19-S22).

Uses a private nats-server on a free port (the `nats_url` fixture); no model and no other program.
"""
from __future__ import annotations

import asyncio
import logging
import time

import pytest

from comms.events import EventBus
from comms.nats_link import NatsLink, RequestFailed
from comms.peer_network import PeerNetwork, RadioRules
from contracts import AskReply, AskRequest, Heartbeat, LinkStatus, SimState, encode, utc_now

LOG = logging.getLogger("test-comms")
FAST = SimState(tick=1, time_s=1.0, farm_time="06:00", day=1, running=True, target_speed=10.0, actual_speed=10.0,
                label="live: simulated brains", episode_id="e")              # one tick of latency = 0.1 s


def beat(node: str = "hvac") -> Heartbeat:
    return Heartbeat(node=node, seq=1, sent_utc=utc_now(), started_utc=utc_now(), uptime_s=0.0,  # type: ignore[arg-type]
                     known=[], alive=[])


async def connected(url: str, name: str) -> NatsLink:
    link = NatsLink(name, url, logger=LOG)
    await link.connect(attempts=5)
    return link


# --- the NATS link -------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_messages_arrive_and_invalid_ones_are_dropped(nats_url: str) -> None:
    sender, receiver = await connected(nats_url, "a"), await connected(nats_url, "b")
    got: list[Heartbeat] = []

    async def handler(message: Heartbeat, subject: str) -> None:
        if message.seq == 2:
            raise RuntimeError("a buggy handler")                   # must not stop the subscription
        got.append(message)

    await receiver.subscribe("jarvis.*.heartbeat", Heartbeat, handler)
    await sender.publish("jarvis.hvac.heartbeat", beat())
    await sender.client.publish("jarvis.hvac.heartbeat", b'{"schema_version": 9}')
    await sender.publish("jarvis.hvac.heartbeat", beat().model_copy(update={"seq": 2}))
    await sender.publish("jarvis.hvac.heartbeat", beat().model_copy(update={"seq": 3}))
    await sender.client.flush()
    await asyncio.sleep(0.3)
    assert [m.seq for m in got] == [1, 3] and receiver.dropped == 1
    await sender.close()
    await receiver.close()


@pytest.mark.asyncio
async def test_request_reply_no_responders_and_timeouts(nats_url: str) -> None:
    server, client = await connected(nats_url, "server"), await connected(nats_url, "client")
    request = AskRequest(request_id="q-1", sender="incubator-1", target="hvac", topic="status", sent_utc=utc_now(),
                         deadline_ms=1000)

    async def answer(message: AskRequest, subject: str) -> AskReply | None:
        if message.request_id == "q-silent":
            return None                                               # like a lost radio answer
        return AskReply(request_id=message.request_id, sender="hvac", target="incubator-1", topic="status", ok=True)

    await server.serve("jarvis.hvac.ask", AskRequest, answer)
    reply = await client.request("jarvis.hvac.ask", request, AskReply, timeout_s=2.0)
    assert reply.ok and reply.request_id == "q-1"
    with pytest.raises(RequestFailed) as nobody:
        await client.request("jarvis.nobody.ask", request, AskReply, timeout_s=1.0)
    assert nobody.value.kind == "no_responders"
    with pytest.raises(RequestFailed) as silent:
        await client.request("jarvis.hvac.ask", request.model_copy(update={"request_id": "q-silent"}), AskReply,
                             timeout_s=0.3)
    assert silent.value.kind == "timeout"
    await server.close()
    await client.close()


@pytest.mark.asyncio
async def test_connect_gives_up_when_nats_is_not_there(new_port) -> None:  # type: ignore[no-untyped-def]
    from nats.errors import NoServersError

    link = NatsLink("lonely", f"nats://127.0.0.1:{new_port()}", logger=LOG, connect_timeout_s=1.0)
    with pytest.raises(NoServersError):
        await link.connect(attempts=2)
    assert not link.connected


# --- the event bus -------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_event_bus_is_bounded_and_never_blocks() -> None:
    bus = EventBus(maxsize=2)
    reader = bus.subscribe("farm.status")
    assert bus.publish("farm.status", 1) == 1 and bus.publish("nobody", 0) == 0
    bus.publish("farm.status", 2)
    bus.publish("farm.status", 3)                                      # full: the oldest (1) is dropped
    assert [await reader.get(timeout_s=0.1), await reader.get(timeout_s=0.1)] == [2, 3] and reader.dropped == 1
    assert await reader.get(timeout_s=0.05) is None
    bus.unsubscribe(reader)
    assert bus.publish("farm.status", 4) == 0
    with pytest.raises(ValueError):
        EventBus(maxsize=0)


# --- the ZigBee rules ----------------------------------------------------------------------------------------------

async def radio_pair(nats_url: str, rules: RadioRules | None = None) -> tuple[PeerNetwork, PeerNetwork, list[str]]:
    asker = PeerNetwork("incubator-1", await connected(nats_url, "incubator-1"), rules=rules, logger=LOG)
    answerer = PeerNetwork("hvac", await connected(nats_url, "hvac"), rules=rules, logger=LOG)
    heard: list[str] = []

    async def answer(request: AskRequest) -> AskReply:
        heard.append(request.request_id)
        return AskReply(request_id=request.request_id, sender="hvac", target=request.sender, topic=request.topic,
                        ok=True, answer={"supply_air_temperature_c": 14.2}, farm_time="06:00", tick=1)

    await answerer.serve(answer)
    for radio in (asker, answerer):
        radio.update_sim(FAST)
    return asker, answerer, heard


async def close(*radios: PeerNetwork) -> None:
    for radio in radios:
        await radio.link.close()


@pytest.mark.asyncio
async def test_a_question_and_its_answer_each_take_one_radio_hop(nats_url: str) -> None:
    asker, answerer, heard = await radio_pair(nats_url)
    assert asker.latency_s == pytest.approx(0.1)
    started = time.monotonic()
    reply = await asker.ask("hvac", "readings", deadline_s=3.0)
    elapsed = time.monotonic() - started
    assert reply.ok and reply.answer == {"supply_air_temperature_c": 14.2} and len(heard) == 1
    assert 0.2 <= elapsed < 1.5 and reply.elapsed_ms is not None and reply.elapsed_ms >= 200
    await close(asker, answerer)


@pytest.mark.asyncio
async def test_a_cut_link_makes_the_question_time_out_cleanly(nats_url: str) -> None:
    asker, answerer, heard = await radio_pair(nats_url)
    cut = LinkStatus(tick=2, time_s=2.0, cut=["hvac"], offline=[])
    asker.update_links(cut)
    answerer.update_links(cut)
    started = time.monotonic()
    reply = await asker.ask("hvac", "status", deadline_s=0.8)
    assert not reply.ok and reply.error == "timeout" and "link is cut" in reply.detail
    assert 0.75 <= time.monotonic() - started < 1.5 and heard == []
    own = PeerNetwork("incubator-1", asker.link, logger=LOG)
    own.update_links(LinkStatus(tick=2, time_s=2.0, cut=["incubator-1"], offline=[]))
    mine = await own.ask("hvac", "status", deadline_s=0.5)
    assert mine.error == "link_cut"                                    # its own radio is cut: it knows at once
    asker.update_links(LinkStatus(tick=3, time_s=3.0, cut=[], offline=[]))
    answerer.update_links(LinkStatus(tick=3, time_s=3.0, cut=[], offline=[]))
    assert (await asker.ask("hvac", "status", deadline_s=2.0)).ok      # the link is back
    await close(asker, answerer)


@pytest.mark.asyncio
async def test_lost_messages_unanswered_peers_and_bad_questions(nats_url: str) -> None:
    lossy, answerer, _ = await radio_pair(nats_url, RadioRules(latency_ticks=0, loss=0.99, seed="test"))
    reply = await lossy.ask("hvac", "status", deadline_s=0.3)
    assert reply.error == "timeout" and "lost" in reply.detail
    asker = PeerNetwork("incubator-1", lossy.link, rules=RadioRules(latency_ticks=0), logger=LOG)
    nobody = await asker.ask("animal-protection", "status", deadline_s=1.0)
    assert nobody.error == "unavailable" and "no animal-protection program" in nobody.detail
    itself = await asker.ask("incubator-1", "status")
    assert itself.error == "bad_request"
    for bad in (dict(question="x" * 2001), dict(deadline_s=31.0), dict(request_id="no spaces")):
        refused = await asker.ask("hvac", "status", **bad)  # type: ignore[arg-type]
        assert refused.error == "bad_request" and "not a valid question" in refused.detail, bad
    with pytest.raises(ValueError):
        RadioRules(loss=1.0)
    await close(lossy, answerer)


@pytest.mark.asyncio
async def test_the_answering_node_ignores_questions_for_others_and_answers_late_never(nats_url: str) -> None:
    client = await connected(nats_url, "probe")
    asker, answerer, heard = await radio_pair(nats_url)
    stray = AskRequest(request_id="q-stray", sender="incubator-1", target="incubator-2", topic="status",
                       sent_utc=utc_now(), deadline_ms=500)
    await client.client.publish("jarvis.hvac.ask", encode(stray))       # addressed to someone else
    await client.client.flush()
    await asyncio.sleep(0.3)
    assert heard == []
    await client.close()
    await close(asker, answerer)
