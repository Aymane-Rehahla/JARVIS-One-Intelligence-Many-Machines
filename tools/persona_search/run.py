"""Persona search for JARVIS's talking model: find the best system prompt, example exchanges and sampling settings.

Usage:
    python tools/persona_search/run.py --server http://127.0.0.1:8081 [--limit N] [--prompts M] [--seed S] [--out DIR]
    python tools/persona_search/run.py --fake                 smoke run with canned replies (no model, no server)
    python tools/persona_search/run.py --plan [--limit N]     print what a run would do and its rough duration

Every (configuration × prompt) is asked once; each reply is scored (scoring.py) and the run folder gets
results.jsonl, summary.json, leaderboard.md, blind_rating.md and blind_key.json (search.py, report.py). Then the
owner rates the blind sheet with rate.py. Exit codes: 0 done, 1 the run stopped on backend failures, 2 bad data,
bad arguments, a busy --out folder or a server that is not ready.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tools/, so `persona_search` imports

from persona_search.backend import Backend, BackendError, FakeBackend, OpenAICompatibleBackend
from persona_search.fake import PersonaFake
from persona_search.loading import DATA_DIR, DataError, load_data
from persona_search.search import (MAX_REQUESTS, SECONDS_PER_REQUEST_ESTIMATE, RunOptions, default_run_dir,
                                   run_search)
from persona_search.selection import build_configs, select_configs, select_prompts

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERVER = "http://127.0.0.1:8081"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--server", default=DEFAULT_SERVER, help=f"OpenAI-compatible server (default {DEFAULT_SERVER})")
    where.add_argument("--fake", action="store_true", help="canned replies, no server (smoke run)")
    parser.add_argument("--plan", action="store_true", help="print the plan and its rough duration, then stop")
    parser.add_argument("--limit", type=int, help="number of configurations (seeded subset spread over the files)")
    parser.add_argument("--prompts", type=int, help="number of prompts (seeded subset spread over the categories)")
    parser.add_argument("--seed", type=int, default=7, help="seed for subsets, shuffling and sampling (default 7)")
    parser.add_argument("--out", type=Path, help="run folder (default logs/persona_search/<timestamp>)")
    parser.add_argument("--data", type=Path, default=DATA_DIR, help="data folder (default: the tool's data/)")
    parser.add_argument("--address", default="sir", help='how JARVIS addresses the owner (default "sir")')
    parser.add_argument("--evidence-mode", choices=("user", "tool"), default="user",
                        help="tool results inside the user message (default) or as OpenAI tool messages")
    parser.add_argument("--max-tokens", type=int, default=160, help="reply length cap in tokens (default 160)")
    parser.add_argument("--timeout", type=float, default=90.0, help="seconds per request before retrying (default 90)")
    parser.add_argument("--retries", type=int, default=2, help="retries per request (default 2, at most 5)")
    parser.add_argument("--think", action="store_true", help="leave the model's thinking mode on (off by default)")
    parser.add_argument("--blind-prompts", type=int, default=12, help="prompts on the blind sheet (default 12)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    args = parse_args(argv)
    try:
        data = load_data(args.data)
        configs = select_configs(build_configs(list(data.system_prompts), list(data.example_sets), data.grid),
                                 args.limit, args.seed)
        prompts = select_prompts(data.prompts, args.prompts, args.seed)
        options = RunOptions(seed=args.seed, address=args.address, max_tokens=args.max_tokens,
                             evidence_mode=args.evidence_mode, blind_prompts=args.blind_prompts)
    except (DataError, ValueError) as error:
        print(f"error: {error}")
        return 2
    requests = len(configs) * len(prompts)
    minutes = requests * SECONDS_PER_REQUEST_ESTIMATE / 60
    print(f"{len(configs)} configurations x {len(prompts)} prompts = {requests} requests "
          f"(about {minutes:.0f} min at {SECONDS_PER_REQUEST_ESTIMATE:g} s each; limit {MAX_REQUESTS})")
    if args.plan:
        for config in configs:
            print(f"  {config.id}")
        return 0
    backend: Backend
    if args.fake:
        backend = FakeBackend(PersonaFake(args.address), latency_s=0.05)
    else:
        extra = {} if args.think else {"chat_template_kwargs": {"enable_thinking": False}}
        try:
            server = OpenAICompatibleBackend(args.server, timeout_s=args.timeout, retries=args.retries,
                                             extra_body=extra)
        except ValueError as error:
            print(f"error: {error}")
            return 2
        try:
            server.health()
        except BackendError as error:
            server.close()
            print(f"error: {error}. Start llama-server with the roleplay model first (deferred until the training "
                  "ends; see tools/persona_search/README.md).")
            return 2
        backend = server
    out = args.out or default_run_dir(ROOT, "-fake" if args.fake else "")
    try:
        result = run_search(backend, data, configs, prompts, out, options=options, progress=print)
    except FileExistsError as error:
        print(f"error: {error}")
        return 2
    except BackendError as error:
        print(f"stopped: {error}. Partial results are in {out}")
        return 1
    finally:
        backend.close()
    best = result.summaries[0]
    print(f"done: {result.meta.requests} replies, {result.meta.errors} errors. Best: {best.config.id} "
          f"(score {best.score:.1f}, hard fails {best.hard_fails}/{best.replies})")
    for name in ("leaderboard.md", "blind_rating.md", "results.jsonl"):
        if (out / name).exists():
            print(f"  {out / name}")
    return 0


if __name__ == "__main__":
    import io

    from hash_models import lower_priority

    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")   # a Windows console may not encode every character
    lower_priority()
    sys.exit(main())
