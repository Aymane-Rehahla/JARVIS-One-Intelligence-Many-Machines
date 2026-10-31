"""E6, latency, text part (plan section 8, P2 section 5): first chunk and whole answer, p50 and p95.

In: the live rig (live_rig.py: the real 2B and 0.6B models with a warm prompt cache, the four nodes, the trained farm
at 1x with the supply-fan fault, the gateway). 50 questions through `/ws/chat`, alternating 25 simple ones about
incubator-1's own machine and 25 that need a peer (and its manual). Per question: the agent's own timings from its
ChatDone (`first_chunk_ms`, `total_ms`, measured by incubator-1 from the moment the question arrived) and the
client's seconds to the ChatDone. Target: first chunk <= 1.5 s p50. The voice part is P4's (`e6_voice.py`); this
script keeps any voice numbers already in E6.json under their own key.
Out: `logs/eval/E6.json`.

Run (loads the models): `.venv\\Scripts\\python tests\\llm\\eval\\e6_text.py [--runs N]`
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_rig import LiveRig, rig_models  # noqa: E402

from eval_results import OUT, EvalResult, git_commit, utc_now, write_result  # noqa: E402

SIMPLE = ("How is incubator one?", "What is the temperature in incubator one?", "Is incubator one all right?",
          "What's the humidity in incubator one?", "Any problem with incubator one?")
PEERS = ("What's wrong with the HVAC?", "How is the barn's air?", "Is the animal protection unit fine?",
         "How is incubator two?", "Why is the HVAC alarming?")


def percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))]


def stats(values: list[float]) -> dict[str, float | None]:
    return {"p50": percentile(values, 0.5), "p95": percentile(values, 0.95), "n": float(len(values)),
            "mean": round(statistics.fmean(values), 1) if values else None}


async def run(runs: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    async with LiveRig() as rig:
        for index in range(runs):
            kind = "simple" if index % 2 == 0 else "peers"
            question = (SIMPLE if kind == "simple" else PEERS)[(index // 2) % 5]
            answer, seconds = await rig.ask(question)
            done = answer.done or {}
            timings = done.get("timings") or {}
            rows.append({"kind": kind, "question": question, "outcome": done.get("outcome"),
                         "first_chunk_ms": timings.get("first_chunk_ms"), "total_ms": timings.get("total_ms"),
                         "client_s": round(seconds, 2), "error": answer.error})
            print(f"{index:2} {kind:6} {done.get('outcome')} first {timings.get('first_chunk_ms')} ms "
                  f"total {timings.get('total_ms')} ms", flush=True)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=50)
    args = parser.parse_args(argv)
    started_utc, started = utc_now(), time.monotonic()
    rows = asyncio.run(run(args.runs))
    measured: dict[str, Any] = {}
    previous = OUT / "E6.json"
    if previous.is_file():
        measured = {k: v for k, v in json.loads(previous.read_text(encoding="utf-8"))["measured"].items()
                    if k.startswith("voice")}
    text: dict[str, Any] = {}
    for kind in ("all", "simple", "peers"):
        chosen = [r for r in rows if kind == "all" or r["kind"] == kind]
        answered = [r for r in chosen if r["outcome"] == "answered"]       # the target is about real answers
        text[kind] = {"first_chunk_ms": stats([float(r["first_chunk_ms"]) for r in answered if r["first_chunk_ms"] is not None]),
                      "total_ms": stats([float(r["total_ms"]) for r in answered if r["total_ms"] is not None]),
                      "first_chunk_ms_every_outcome": stats([float(r["first_chunk_ms"]) for r in chosen
                                                             if r["first_chunk_ms"] is not None]),
                      "answered": len(answered), "of": len(chosen)}
    measured["text"] = text
    measured["text_commit"] = git_commit()
    measured["text_rows"] = rows
    first_p50 = text["all"]["first_chunk_ms"]["p50"]
    measured["text_passed"] = None if first_p50 is None else first_p50 <= 1500.0
    path = write_result(EvalResult(
        id="E6", title="Latency", target="first chunk <= 1.5 s p50 (text); voice: see the voice keys",
        passed=None if first_p50 is None or measured.get("voice_passed") is None else bool(
            first_p50 <= 1500.0 and measured["voice_passed"]), measured=measured,
        command=f".venv\\Scripts\\python tests\\llm\\eval\\e6_text.py --runs {args.runs}", commit=git_commit(),
        seeds=[7], models=rig_models(), started_utc=started_utc, duration_s=round(time.monotonic() - started, 1),
        notes="Text part through the gateway on this PC (RX 580, Vulkan; f16 KV cache; warm prompt cache). The first "
              "chunk is a whole verified sentence, not a first token. The latencies are of answered replies; "
              "first_chunk_ms_every_outcome counts fallbacks and refusals too."))
    print(f"E6 text: first chunk p50 {first_p50} ms -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
