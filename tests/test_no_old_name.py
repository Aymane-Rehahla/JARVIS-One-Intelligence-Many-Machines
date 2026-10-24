"""The project's old name must not appear anywhere inside JARVIS/ (owner's point 2).

Checked, in any capitalisation and as ASCII/UTF-8 or UTF-16 (`tools/name_guard.py`): every file and folder name, every
file's content (logs, the environment, the website packages and git's own plain files included), the staged tree,
and every object in the git repository (commit messages, authors and committers, trees, every file version, tags)
and every ref name. Git's compressed object files are read through git rather than as raw bytes. The only files whose
content is not read are model weights of 50 MB or more: they are byte-identical to their upstream releases, which
`models/MANIFEST.json` and `tools/hash_models.py check` prove.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from name_guard import NEEDLES, OLD, contains, history_offenders, identity_offenders, staged_offenders
from name_guard import main as name_guard_main

ROOT = Path(__file__).resolve().parents[1]
BIG_BYTES = 50 * 1024 * 1024
CACHE = ROOT / ".cache" / "old_name_scan.json"
SKIPPED = (".git/objects/",)                          # compressed: read through git by history_offenders()


def _walk(root: Path) -> tuple[list[Path], list[Path]]:
    """All folders and files under root, without following links."""
    folders: list[Path] = []
    files: list[Path] = []
    stack = [root]
    while stack:
        with os.scandir(stack.pop()) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    folders.append(path)
                    stack.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
    return folders, files


def test_the_check_finds_every_spelling_and_encoding() -> None:
    word = OLD.encode("ascii")
    for text in (word, word.upper(), word.capitalize(), b"x" + word.swapcase() + b"y"):
        assert contains(text) and contains(text.decode().encode("utf-16-le")) and contains(text.decode().encode("utf-16"))
    assert not contains(b"third uni") and not contains("JARVIS".encode("utf-16-le"))
    assert len(NEEDLES) == 3


def test_no_file_or_folder_name_contains_it() -> None:
    folders, files = _walk(ROOT)
    offenders = [str(p.relative_to(ROOT)) for p in folders + files if contains(str(p.relative_to(ROOT)).encode())]
    assert offenders == []
    assert not contains(str(ROOT).encode()), "the project folder itself"


def test_no_file_content_contains_it() -> None:
    """Reads every file under 50 MB. Files already verified clean, with the same size and modification time, are
    skipped (a cache in .cache/, ignored by git); set JARVIS_FULL_NAME_SCAN=1 to read everything again."""
    full = os.environ.get("JARVIS_FULL_NAME_SCAN") == "1"
    cached: dict[str, list[int]] = {} if full else _load_cache()
    clean: dict[str, list[int]] = {}
    offenders: list[str] = []
    _, files = _walk(ROOT)
    for path in files:
        key = path.relative_to(ROOT).as_posix()
        if path == CACHE or key.startswith(SKIPPED):
            continue
        stat = path.stat()
        if stat.st_size >= BIG_BYTES:
            continue                                      # model weights, proven identical to upstream by MANIFEST.json
        fingerprint = [stat.st_size, stat.st_mtime_ns]
        if cached.get(key) == fingerprint:
            clean[key] = fingerprint
            continue
        with path.open("rb") as handle:
            if contains(handle.read()):
                offenders.append(key)
            else:
                clean[key] = fingerprint
    _save_cache(clean)
    assert offenders == []


def _load_cache() -> dict[str, list[int]]:
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(clean: dict[str, list[int]]) -> None:
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(clean), encoding="utf-8")


def test_big_files_are_only_manifest_checked_model_weights() -> None:
    manifest = json.loads((ROOT / "models" / "MANIFEST.json").read_text(encoding="utf-8"))["files"]
    _, files = _walk(ROOT)
    big = [p for p in files if not p.relative_to(ROOT).as_posix().startswith(SKIPPED) and p.stat().st_size >= BIG_BYTES]
    for path in big:
        rel = path.relative_to(ROOT).as_posix()
        assert rel.startswith("models/") and rel.removeprefix("models/") in manifest, f"{rel} is big but not a model"


def test_the_staged_tree_and_the_git_identity_do_not_contain_it() -> None:
    assert staged_offenders() == [] and identity_offenders() == []


def test_git_history_does_not_contain_it() -> None:
    """Every object in .git, reachable or not: commits (message, author, committer), trees, file versions, tags."""
    assert history_offenders() == []


def test_the_command_line_checks_a_commit_message_and_the_history(tmp_path: Path,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    clean = tmp_path / "clean.txt"
    clean.write_text("Add the gateway\n", encoding="utf-8")
    assert name_guard_main(["staged", "--message", str(clean)]) == 0
    bad = tmp_path / "bad.txt"
    bad.write_bytes(f"Fix the {OLD.upper()} paths\n".encode("utf-16"))           # UTF-16, as some editors write
    assert name_guard_main(["staged", "--message", str(bad)]) == 1
    assert "FOUND in commit message" in capsys.readouterr().out
    assert name_guard_main(["history"]) == 0
    assert name_guard_main(["staged", "--message", str(tmp_path / "missing.txt")]) == 2
