"""Copies the shared code of incubator-1 to the other three nodes (plan section 1.3: one fix reaches all four).

Shared is everything in `nodes/incubator-1/` except each node's own files: `node.toml` and `README.md` at the node's
top level, `memory/rom/` (its knowledge, managed by tools/knowledge.py) and `data/` (its databases). Caches
(`__pycache__`, `*.pyc`) are ignored. A shared file that exists only in another node is removed there, so the copies
end up identical; tests/test_nodes_in_sync.py fails if they are not.

Usage (from the project folder):
    python tools/sync_nodes.py            copy incubator-1's shared files to the other three
    python tools/sync_nodes.py --check    list the differences and exit 1 if there are any (changes nothing)
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
NODES = ROOT / "nodes"
SOURCE = "incubator-1"
TARGETS = ("incubator-2", "hvac", "animal-protection")
OWN_FILES = frozenset({"node.toml", "README.md"})          # at the node's top level only
OWN_DIRS = ("memory/rom", "data")
CACHE_PARTS = frozenset({"__pycache__", ".pytest_cache"})

Difference = tuple[str, Literal["missing", "different", "extra"]]


def is_shared(rel: Path) -> bool:
    """True for a path (relative to a node folder) that must be the same in all four nodes."""
    posix = rel.as_posix()
    if len(rel.parts) == 1 and rel.name in OWN_FILES:
        return False
    if any(posix == own or posix.startswith(own + "/") for own in OWN_DIRS):
        return False
    return not (CACHE_PARTS.intersection(rel.parts) or rel.suffix == ".pyc")


def shared_files(node_dir: Path) -> dict[str, Path]:
    """The node's shared files, by path relative to the node folder (POSIX form)."""
    return {path.relative_to(node_dir).as_posix(): path for path in sorted(node_dir.rglob("*"))
            if path.is_file() and is_shared(path.relative_to(node_dir))}


def differences(source: Path, target: Path) -> list[Difference]:
    """How `target`'s shared files differ from `source`'s."""
    mine, theirs = shared_files(source), shared_files(target)
    found: list[Difference] = []
    for rel, path in mine.items():
        if rel not in theirs:
            found.append((rel, "missing"))
        elif theirs[rel].read_bytes() != path.read_bytes():
            found.append((rel, "different"))
    extras: list[Difference] = [(rel, "extra") for rel in theirs if rel not in mine]
    found += extras
    return sorted(found)


def sync(source: Path, target: Path) -> list[Difference]:
    """Make `target`'s shared files equal to `source`'s; returns what it changed."""
    changes = differences(source, target)
    for rel, kind in changes:
        destination = target / rel
        if kind == "extra":
            destination.unlink()
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / rel, destination)
    return changes


def main(argv: list[str] | None = None, *, nodes: Path = NODES) -> int:
    """Command-line entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only list differences; exit 1 if there are any")
    args = parser.parse_args(argv)
    source = nodes / SOURCE
    total = 0
    for name in TARGETS:
        target = nodes / name
        changes = differences(source, target) if args.check else sync(source, target)
        total += len(changes)
        for rel, kind in changes:
            print(f"{name}: {rel} {kind}" + ("" if args.check else " -> synced"))
    if args.check:
        print("the four nodes hold the same shared code" if total == 0 else f"{total} differences")
        return 1 if total else 0
    print(f"synced {total} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
