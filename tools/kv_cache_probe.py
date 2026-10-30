"""Decision 0017's measurement: the four node LLM servers with an f16 and with a q8_0 KV cache (P2 section 0.1).

For each cache type, all four servers are started together (the supervisor's commands, below-normal priority, each
under the RAM guard, as D3 does), then measured: dedicated VRAM after all four have loaded, each server's generation
speed (the server's own predicted tokens per second over a fixed 128-token reply, 3 runs, median), and the 2B model's
replies to the first 10 lines of the persona test set (same system prompt, seed 7, temperature 0.7). Everything is
stopped before the next cache type starts. The result goes to logs/kv_cache_probe.json.

The rule (P2): adopt q8_0 if it frees at least 0.3 GiB and slows generation by at most 10 %; otherwise keep f16.
Whether the replies are "as good" is a judgement on the printed replies, not a number.

Usage: .venv\\Scripts\\python tools\\kv_cache_probe.py [--types f16,q8_0] [--out FILE]
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _extra in (ROOT / "tools", ROOT / "nodes" / "incubator-1", ROOT / "launcher", ROOT / "tests" / "llm"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from contracts import NODE_IDS  # noqa: E402
from llm.engines import server_spec  # noqa: E402
from persona_search.loading import load_data  # noqa: E402
from resource_monitor import GIB, Probe  # noqa: E402
from test_engines_live import (adapter_reading, start_server, stop_server,  # noqa: E402  # pyright: ignore[reportMissingImports]
                               wait_for_health)

SPEED_PROMPT = [{"role": "user", "content": "Describe, in plain prose, how an egg incubator keeps its temperature "
                 "and humidity steady. Write at least two hundred words."}]
SPEED_TOKENS = 128
SPEED_RUNS = 3
PERSONA_LINES = 10
SEED = 7


async def measure(cache_type: str) -> dict[str, Any]:
    """Start all four servers with this KV cache type, measure, stop them all."""
    flash = "on" if cache_type != "f16" else "auto"                 # a quantized V cache needs flash attention
    specs = [dataclasses.replace(server_spec(node), cache_type_k=cache_type, cache_type_v=cache_type,
                                 flash_attn=flash) for node in NODE_IDS]
    probe = Probe()
    before = adapter_reading(probe)
    started = [start_server(spec) for spec in specs]
    result: dict[str, Any] = {"cache_type": cache_type, "flash_attn": flash, "vram_before": before, "servers": {}}
    clients = []
    try:
        for spec, (process, _, _) in zip(specs, started):
            client, load_s = await wait_for_health(spec, process)
            clients.append(client)
            result["servers"][spec.node] = {"load_s": round(load_s, 2)}
        await asyncio.sleep(2.0)
        result["vram_after_all_loaded"] = adapter_reading(probe)
        for spec, client in zip(specs, clients):
            speeds = []
            for _ in range(SPEED_RUNS):
                reply = await client.chat(SPEED_PROMPT, max_tokens=SPEED_TOKENS, temperature=0.0, seed=SEED,
                                          deadline_s=120.0)
                speeds.append(reply.tokens_per_s or 0.0)
            result["servers"][spec.node]["tokens_per_s"] = speeds
            result["servers"][spec.node]["tokens_per_s_median"] = round(statistics.median(speeds), 2)
        data = load_data()
        system = next(iter(data.system_prompts.values())).replace("{address}", "sir").replace("{Address}", "Sir")
        replies = []
        for prompt in data.prompts[:PERSONA_LINES]:
            reply = await clients[0].chat([{"role": "system", "content": system},
                                           {"role": "user", "content": prompt.text}],
                                          max_tokens=120, temperature=0.7, seed=SEED, deadline_s=60.0)
            replies.append({"owner": prompt.text, "jarvis": reply.text})
        result["persona_replies"] = replies
    finally:
        for client in clients:
            await client.close()
        for process, job, output in started:
            stop_server(process, job, output)
    return result


def used_gib(reading: list[dict[str, Any]]) -> float | None:
    used = [entry["vram_used_bytes"] for entry in reading if entry.get("vram_used_bytes")]
    return round(max(used) / GIB, 3) if used else None


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The KV cache measurement of decision 0017.")
    parser.add_argument("--types", default="f16,q8_0")
    parser.add_argument("--out", type=Path, default=ROOT / "logs" / "kv_cache_probe.json")
    args = parser.parse_args(argv)
    results = []
    for cache_type in args.types.split(","):
        print(f"measuring {cache_type} ...", flush=True)
        started = time.perf_counter()
        try:
            measured = await measure(cache_type)
        except Exception as error:                              # a failed type is a result too (e.g. no flash attention)
            measured = {"cache_type": cache_type, "error": f"{type(error).__name__}: {error}"}
        measured["seconds"] = round(time.perf_counter() - started, 1)
        measured["vram_used_gib"] = used_gib(measured.get("vram_after_all_loaded", []))
        results.append(measured)
        print(json.dumps({k: v for k, v in measured.items() if k != "persona_replies"}, indent=1)[:2000], flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
