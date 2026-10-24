"""Record the owner's blind ratings of a persona search run and reveal the winner.

Usage:
    python tools/persona_search/rate.py set RUN_DIR PROMPT_ID A=5 B=3 C=4    score each reply of one prompt (1-5)
    python tools/persona_search/rate.py status RUN_DIR                        which prompts are still unrated
    python tools/persona_search/rate.py reveal RUN_DIR [--partial]            map ratings to configurations; winner

Ratings go to RUN_DIR/ratings.json, the result to RUN_DIR/winner.json. Exit codes: 0 done, 2 bad input or files.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tools/, so `persona_search` imports

from pydantic import ValidationError

from persona_search.report import load_key, load_ratings, record_ratings, reveal


def parse_scores(items: list[str]) -> dict[str, int]:
    """Turn ["A=5", "B=3"] into {"A": 5, "B": 3}. Raises ValueError on a malformed item."""
    scores: dict[str, int] = {}
    for item in items:
        label, sep, value = item.partition("=")
        if not sep or not label.strip() or not value.strip().lstrip("-").isdigit():
            raise ValueError(f"expected LABEL=SCORE such as A=4, got {item!r}")
        scores[label.strip().upper()] = int(value)
    return scores


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    set_cmd = commands.add_parser("set", help="score every reply of one prompt")
    set_cmd.add_argument("run_dir", type=Path)
    set_cmd.add_argument("prompt_id")
    set_cmd.add_argument("scores", nargs="+", help="A=5 B=3 C=4")
    status_cmd = commands.add_parser("status", help="list unrated prompts")
    status_cmd.add_argument("run_dir", type=Path)
    reveal_cmd = commands.add_parser("reveal", help="reveal the winner")
    reveal_cmd.add_argument("run_dir", type=Path)
    reveal_cmd.add_argument("--partial", action="store_true", help="reveal before every prompt is rated")
    args = parser.parse_args(argv)
    try:
        if args.command == "set":
            ratings = record_ratings(args.run_dir, args.prompt_id, parse_scores(args.scores))
            total = len(load_key(args.run_dir).labels)
            print(f"recorded {args.prompt_id}: {len(ratings.ratings)}/{total} prompts rated")
        elif args.command == "status":
            key = load_key(args.run_dir)
            ratings = load_ratings(args.run_dir)
            unrated = [p for p in key.labels if p not in ratings.ratings]
            print(f"{len(key.labels) - len(unrated)}/{len(key.labels)} prompts rated")
            for prompt_id in unrated:
                print(f"  unrated: {prompt_id}")
        else:
            winner = reveal(args.run_dir, allow_partial=args.partial)
            print(f"winner: {winner.winner} ({winner.rated_prompts} prompts rated)")
            for config, mean in sorted(winner.mean_ratings.items(), key=lambda kv: -kv[1]):
                print(f"  {mean:.2f}  {config}  (automatic score {winner.auto_scores.get(config, 0.0):.1f})")
            print(f"  written to {args.run_dir / 'winner.json'}")
    except FileNotFoundError as error:
        print(f"error: {error.filename or error} not found; is RUN_DIR a persona search run with a blind sheet?")
        return 2
    except (ValueError, ValidationError) as error:
        print(f"error: {error}")
        return 2
    return 0


if __name__ == "__main__":
    import io

    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")   # a Windows console may not encode every character
    sys.exit(main())
