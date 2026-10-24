"""The project's old name must not appear anywhere inside JARVIS/ (owner's point 2): the git checks behind
tests/test_no_old_name.py, and the check to run before every commit and tag.

The name is searched in any capitalisation, as ASCII/UTF-8 and as UTF-16 (little and big endian), so a Windows tool
that writes UTF-16 text cannot hide it.

Usage (from the project folder):
    python tools/name_guard.py staged [--message FILE]   before a commit: staged paths and contents, the commit
                                                         message in FILE, and the git identity
    python tools/name_guard.py history                   every object in .git (commits with their authors,
                                                         committers and messages, trees, file versions, tags) and
                                                         every ref name
Exit codes: 0 clean, 1 the name was found (each place is printed), 2 git failed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = "thir" + "duni"                                  # written in two parts so this file doesn't contain the word
NEEDLES = (OLD.encode("ascii"), OLD.encode("utf-16-le"), OLD.encode("utf-16-be"))


def contains(data: bytes) -> bool:
    """True when `data` holds the old name in any capitalisation, as ASCII/UTF-8 or UTF-16."""
    lowered = data.lower()                             # bytes.lower() folds ASCII letters, also inside UTF-16
    return any(needle in lowered for needle in NEEDLES)


def _git(*args: str, root: Path = ROOT) -> bytes:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout


def has_commits(root: Path = ROOT) -> bool:
    """True once the repository has a first commit."""
    return subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=root, capture_output=True,
                          check=False).returncode == 0


def _batch(object_ids: list[bytes], root: Path) -> Iterator[tuple[bytes, bytes, bytes]]:
    """(id, type, content) of each object, read through one `git cat-file --batch` process."""
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=root, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE)
    assert process.stdin is not None and process.stdout is not None
    try:
        for object_id in object_ids:
            process.stdin.write(object_id + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().split()
            if len(header) < 3:                        # "<id> missing"
                continue
            size = int(header[2])
            content = process.stdout.read(size)
            process.stdout.read(1)                     # the newline after each object
            yield header[0], header[1], content
    finally:
        process.stdin.close()
        process.wait()


def staged_offenders(root: Path = ROOT) -> list[str]:
    """Places in the index (the next commit) holding the name: a staged path or a staged file's content."""
    found: list[str] = []
    blobs: dict[bytes, list[str]] = {}
    for entry in filter(None, _git("ls-files", "-s", "-z", root=root).split(b"\0")):
        meta, path = entry.split(b"\t", 1)
        name = path.decode("utf-8", "replace")
        if contains(path):
            found.append(f"staged path: {name}")
        blobs.setdefault(meta.split()[1], []).append(name)
    for object_id, _, content in _batch(list(blobs), root):
        if contains(content):
            found.append("staged content: " + ", ".join(blobs[object_id]))
    return found


def history_offenders(root: Path = ROOT) -> list[str]:
    """Every object in the repository (reachable or not) and every ref name that holds the name."""
    if not has_commits(root):
        return []
    found: list[str] = []
    listing = _git("cat-file", "--batch-all-objects", "--batch-check=%(objectname)", root=root)
    for object_id, kind, content in _batch(listing.split(), root):
        if contains(content):
            found.append(f"{kind.decode()} {object_id.decode()}")
    refs = _git("for-each-ref", "--format=%(refname)", root=root)
    found += [f"ref {line.decode()}" for line in refs.splitlines() if contains(line)]
    return found


def identity_offenders(root: Path = ROOT) -> list[str]:
    """The git identity that the next commit will carry."""
    found: list[str] = []
    for key in ("user.name", "user.email"):
        value = subprocess.run(["git", "config", key], cwd=root, capture_output=True, check=False).stdout
        if contains(value):
            found.append(f"git config {key}")
    return found


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    staged = commands.add_parser("staged", help="check the staged tree, a commit message and the identity")
    staged.add_argument("--message", type=Path, help="the commit message file")
    commands.add_parser("history", help="check every git object and ref")
    args = parser.parse_args(argv)
    try:
        if args.command == "staged":
            found = staged_offenders() + identity_offenders()
            if args.message is not None and contains(args.message.read_bytes()):
                found.append(f"commit message {args.message}")
        else:
            found = history_offenders()
    except (subprocess.CalledProcessError, OSError) as error:
        print(f"error: {error}")
        return 2
    for place in found:
        print(f"FOUND in {place}")
    print(f"the old name is in {len(found)} places" if found else "clean: the old name is not there")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
