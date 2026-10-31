"""The website's chat logic (P4 section 3, items 1-3), on message sequences made by the Python contracts, under
Node 24 (interface/web/scripts/check-chat.ts): the chunks joined equal the done text; a lost chunk is shown as such;
ChatError; an alert in the middle of a reply; a confirm that expires; one question at a time; late pieces dropped.
Skips with a message when Node is not there, as tests/test_contracts_zod.py does."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from contracts import AckOffer, ChatChunk, ChatDone, OwnerAlert, ReplyTimings, utc_now

import gateway as gw

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "interface" / "web"
NODE = Path(os.environ.get("JARVIS_NODE", r"C:\Users\Aymane\node\node-v24.22.1-win-x64\node.exe"))
pytestmark = pytest.mark.skipif(not NODE.is_file(), reason=f"Node 24 is not at {NODE} (set JARVIS_NODE)")
TEXT = "The supply fan has failed, sir. That is as of farm time 06:08."
PIECES = ["The supply fan has failed, sir. ", "That is as of farm time 06:08."]


def raw(message: Any) -> dict[str, str]:
    return {"raw": message.model_dump_json()}


def accepted(client_msg_id: str = "m1", request_id: str = "c-1") -> dict[str, str]:
    return raw(gw.ChatAccepted(client_msg_id=client_msg_id, request_id=request_id))


def chunk(seq: int, text: str, request_id: str = "c-1") -> dict[str, str]:
    return raw(ChatChunk(request_id=request_id, seq=seq, text=text))


def done(chunks: int = 2, request_id: str = "c-1", **extra: Any) -> dict[str, str]:
    fields: dict[str, Any] = dict(request_id=request_id, conversation_id="conv-7", text=TEXT, chunks=chunks,
                                  outcome="answered", numbers_checked=1, farm_time="06:08",
                                  label="live: simulated brains", timings=ReplyTimings(first_chunk_ms=900, total_ms=1800))
    fields.update(extra)
    return raw(ChatDone(**fields))


ALERT = OwnerAlert(alert_id="a-1", node="hvac", level="DANGER", incident_id="hvac-equipment-0001",
                   text="Sir, danger at the HVAC: supply fan fault. Farm time 06:09.", farm_time="06:09",
                   feed_id="0000000000a1", label="live: simulated brains", reworded=False, sent_utc=utc_now())
SEND = {"send": "What's wrong with the HVAC?", "id": "m1"}


def check() -> dict[str, Any]:
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    offer = AckOffer(node="hvac", incident_id="hvac-equipment-0001", feed_id="0000000000a1", token="1.abc",
                     expires_utc=(now + timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
                     summary="Acknowledge the HVAC's supply fan alert")
    cases = {
        "streams": [SEND, accepted(), chunk(0, PIECES[0]), chunk(1, PIECES[1]), done()],
        "out_of_order": [SEND, accepted(), chunk(1, PIECES[1]), chunk(0, PIECES[0]), done()],
        "gap_in_seq": [SEND, accepted(), chunk(0, PIECES[0]), chunk(2, "Extra."), done(chunks=3)],
        "fewer_than_done_says": [SEND, accepted(), chunk(0, PIECES[0]), done(chunks=2)],
        "error_for_the_question": [SEND, raw(gw.ChatError(client_msg_id="m1", reason="incubator-1 did not answer"))],
        "timeout_then_late_pieces": [SEND, accepted(), raw(gw.ChatError(client_msg_id="m1", request_id="c-1",
                                                                         reason="did not answer within 30 s")),
                                     chunk(0, PIECES[0]), done()],
        "alert_mid_reply": [SEND, accepted(), chunk(0, PIECES[0]), raw(ALERT), chunk(1, PIECES[1]), done(),
                            raw(ALERT)],
        "one_at_a_time": [SEND, {"send": "and another?", "id": "m2"}, accepted(), chunk(0, PIECES[0]),
                          chunk(1, PIECES[1]), done(), {"send": "now?", "id": "m3"}],
        "fallback_and_refused": [SEND, accepted(), chunk(0, "Here is the data itself."),
                                 done(chunks=1, text="Here is the data itself.", outcome="fallback",
                                      reason="language model unavailable"),
                                 {"send": "Turn off the heater.", "id": "m4"}, accepted("m4", "c-2"),
                                 done(chunks=0, request_id="c-2", text="JARVIS talks; the machines act.",
                                      outcome="refused", reason="act", farm_time=None, label=None)],
        "invalid_and_disconnect": [SEND, {"raw": "not json"}, {"raw": json.dumps({"type": "chat_chunk"})},
                                   accepted(), {"disconnect": True}],
    }
    source = {"cases": cases, "offer": json.loads(offer.model_dump_json()), "now_ms": int(now.timestamp() * 1000)}
    path = ROOT / "logs" / "check_chat_input.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(source), encoding="utf-8")
    result = subprocess.run([str(NODE), "scripts/check-chat.ts", str(path)], cwd=WEB, capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, result.stderr
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def test_the_chat_logic_on_recorded_sequences() -> None:
    out = check()
    cases = out["cases"]
    streams = cases["streams"]
    reply = streams["replies"][0]
    assert streams["kinds"] == ["owner", "reply"] and streams["owner"][0]["requestId"] == "c-1"
    assert reply["joined"] == reply["text"] == TEXT and reply["lostChunks"] == 0 and not reply["mismatch"]
    assert reply["outcome"] == "answered" and not reply["streaming"]
    assert streams["inFlight"] is None and streams["conversationId"] == "conv-7"
    assert cases["out_of_order"]["replies"][0]["joined"] == TEXT                     # joined in seq order

    gap = cases["gap_in_seq"]["replies"][0]
    assert gap["missingSeqs"] == [1] and gap["lostChunks"] == 1                         # a lost piece is shown
    fewer = cases["fewer_than_done_says"]["replies"][0]
    assert fewer["missingSeqs"] == [1] and fewer["text"] == TEXT                        # the final text still shown

    error = cases["error_for_the_question"]
    assert error["kinds"] == ["owner", "error"] and error["inFlight"] is None
    assert error["errors"] == ["incubator-1 did not answer"]                            # shown, never swallowed
    late = cases["timeout_then_late_pieces"]
    assert late["kinds"] == ["owner", "error"] and late["late"] == 2 and late["replies"] == []

    alert = cases["alert_mid_reply"]
    assert alert["kinds"] == ["owner", "reply", "alert"] and alert["banner"] == ["a-1"]   # once, though sent twice
    assert alert["replies"][0]["text"] == TEXT and alert["replies"][0]["lostChunks"] == 0

    once = cases["one_at_a_time"]
    assert [o["text"] for o in once["owner"]] == ["What's wrong with the HVAC?", "now?"]  # the 2nd while busy: ignored
    assert once["inFlight"] == {"clientMsgId": "m3", "requestId": None}

    outcomes = cases["fallback_and_refused"]
    assert [r["outcome"] for r in outcomes["replies"]] == ["fallback", "refused"]
    assert outcomes["replies"][1]["text"] == "JARVIS talks; the machines act."

    broken = cases["invalid_and_disconnect"]
    assert broken["invalid"] == 2 and broken["inFlight"] is None and broken["kinds"] == ["owner", "error"]
    assert "connection to the gateway closed" in broken["errors"][0]

    assert out["confirm"] == {"fresh": 60, "later": 1, "expired": 0, "unreadable": 0}   # the countdown, and expiry
