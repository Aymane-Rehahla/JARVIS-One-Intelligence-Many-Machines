"""E7, robustness, language-model side (plan section 8): every injected failure still gets a graceful answer.

In: the live rig (live_rig.py: the real models, the four nodes, the trained farm with the supply-fan fault, the
gateway). Each case sets one failure, asks one question through `/ws/chat`, and clears the failure:
- the mentor's four switches (llm_timeout, broken_json, silent_peer, server_crash) on incubator-1 and on hvac;
- the Matrix cutting hvac's radio link (`FarmRuntime.cut`);
- NATS restarted (the server process stopped and started again on its port; the question is asked once the
  programs are back);
- incubator-1's llama-server killed for real (the question meets the dead server), then started again as the
  supervisor does (the next question finds it again).
**Graceful:** a ChatDone (never a ChatError, never silence) within the gateway's 30 s, with an outcome and, unless
answered, its reason; an answered reply's numbers were verified by the agent (numbers_checked is reported).
The brains-keep-working part of E7 (the safety path without any language model) is P3's real run in WORKLOG.md.
Out: `logs/eval/E7.json`.

Run (loads the models): `.venv\\Scripts\\python tests\\llm\\eval\\e7_failures.py`
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_rig import NATS_SERVER, LiveRig  # noqa: E402

from eval_results import EvalResult, git_commit, utc_now, write_result  # noqa: E402

PEER_QUESTION = "What's wrong with the HVAC?"
OWN_QUESTION = "How is incubator one doing?"
CHAT_DEADLINE_S = 30.0


def row(case: str, question: str, answer: Any, seconds: float) -> dict[str, Any]:
    done = answer.done or {}
    graceful = answer.error is None and bool(done) and seconds <= CHAT_DEADLINE_S + 1.0 and (
        done.get("outcome") == "answered" or bool(done.get("reason")))
    result = {"case": case, "question": question, "graceful": graceful, "outcome": done.get("outcome"),
              "reason": done.get("reason"), "numbers_checked": done.get("numbers_checked"),
              "seconds": round(seconds, 1), "error": answer.error, "text": str(done.get("text", ""))[:400]}
    print(f"{case:34} graceful={graceful} {done.get('outcome')} {seconds:.1f}s {str(done.get('reason'))[:80]}",
          flush=True)
    return result


async def run() -> list[dict[str, Any]]:
    from test_engines_live import start_server, stop_server, wait_for_health  # pyright: ignore[reportMissingImports]
    rows: list[dict[str, Any]] = []
    async with LiveRig() as rig:
        answer, seconds = await rig.ask(PEER_QUESTION)
        rows.append(row("baseline", PEER_QUESTION, answer, seconds))
        for node, question in (("incubator-1", OWN_QUESTION), ("hvac", PEER_QUESTION)):
            for switch in ("llm_timeout", "broken_json", "silent_peer", "server_crash"):
                if node == "incubator-1" and switch == "silent_peer":
                    continue                                          # incubator-1 is the one asking
                await rig.set_failures(**{switch: [node]})
                answer, seconds = await rig.ask(question)
                rows.append(row(f"{switch} on {node}", question, answer, seconds))
                await rig.set_failures()
        await rig.farm.cut("hvac", 120.0)
        answer, seconds = await rig.ask(PEER_QUESTION)
        rows.append(row("radio link to hvac cut", PEER_QUESTION, answer, seconds))
        await rig.farm.restore("hvac")

        rig._nats.terminate()
        rig._nats.wait(timeout=10)
        await asyncio.sleep(2.0)
        rig._nats = subprocess.Popen([str(NATS_SERVER), "-a", "127.0.0.1", "-p", str(rig.nats_port)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await rig._wait_port(rig.nats_port)
        deadline = time.monotonic() + 60
        while not (rig.gateway.link.connected and all(n.link.connected for n in rig.nodes.values())):
            if time.monotonic() > deadline:
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(3.0)                                     # heartbeats and capabilities again
        answer, seconds = await rig.ask(PEER_QUESTION)
        rows.append(row("NATS restarted", PEER_QUESTION, answer, seconds))

        process, job, output = rig.servers["incubator-1"]
        stop_server(process, job, output)                            # killed for real
        answer, seconds = await rig.ask(OWN_QUESTION)
        rows.append(row("incubator-1 llama-server killed", OWN_QUESTION, answer, seconds))
        rig.servers["incubator-1"] = start_server(rig.specs["incubator-1"])
        client, _ = await wait_for_health(rig.specs["incubator-1"], rig.servers["incubator-1"][0])
        await client.close()
        agent = rig.nodes["incubator-1"].agent
        if agent is not None:
            agent.rewarm()
            await asyncio.sleep(1.0)
            deadline = time.monotonic() + 60
            while agent._warming is not None and not agent._warming.done() and time.monotonic() < deadline:
                await asyncio.sleep(0.5)
        answer, seconds = await rig.ask(OWN_QUESTION)
        rows.append(row("incubator-1 llama-server restarted", OWN_QUESTION, answer, seconds))
    return rows


def main() -> int:
    started_utc, started = utc_now(), time.monotonic()
    rows = asyncio.run(run())
    graceful = sum(r["graceful"] for r in rows)
    path = write_result(EvalResult(
        id="E7", title="Robustness: every injected LLM failure still gets a graceful answer",
        target="100 % graceful answers", passed=graceful == len(rows),
        measured=json.loads(json.dumps({"cases": len(rows), "graceful": graceful, "rows": rows})),
        command=".venv\\Scripts\\python tests\\llm\\eval\\e7_failures.py", commit=git_commit(), seeds=[7], models={},
        started_utc=started_utc, duration_s=round(time.monotonic() - started, 1),
        notes="Language-model side. The brains part (faults caught and the safety gate acting with no language model "
              "at all) is the 6D real run with start_all.ps1 -NoLlm, in WORKLOG.md."))
    print(f"E7 {graceful}/{len(rows)} graceful -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
