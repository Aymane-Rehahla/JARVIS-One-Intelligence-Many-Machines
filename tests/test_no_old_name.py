"""The project's old name must not appear anywhere inside JARVIS/ (owner's point 2).

Checked, in any capitalisation: every file and folder name, every file's content (logs, environment and website
packages included), and the git history (commit messages, diffs and file paths). The only files whose content is not
read are the model weights of 50 MB or more: they are byte-identical to their upstream releases, which
`models/MANIFEST.json` and `tools/hash_models.py check` prove.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = ("thir" + "duni").encode("ascii")            # written in two parts so this file doesn't contain the word
BIG_BYTES = 50 * 1024 * 1024
CACHE = ROOT / ".cache" / "old_name_scan.json"


def _walk(root: Path) -> tuple[list[Path], list[Path]]:
    """All folders and files under root, without following links, git internals listed separately."""
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


def test_no_file_or_folder_name_contains_it() -> None:
    folders, files = _walk(ROOT)
    offenders = [str(p.relative_to(ROOT)) for p in folders + files if OLD in str(p.relative_to(ROOT)).lower().encode()]
    assert offenders == []
    assert OLD not in str(ROOT).lower().encode(), "the project folder itself"


def test_no_file_content_contains_it() -> None:
    """Reads every file under 50 MB. Files already verified clean, with the same size and modification time, are
    skipped (a cache in .cache/, ignored by git); set JARVIS_FULL_NAME_SCAN=1 to read everything again."""
    full = os.environ.get("JARVIS_FULL_NAME_SCAN") == "1"
    cached: dict[str, list[int]] = {} if full else _load_cache()
    clean: dict[str, list[int]] = {}
    offenders: list[str] = []
    _, files = _walk(ROOT)
    for path in files:
        rel = path.relative_to(ROOT)
        if rel.parts[:1] in ((".git",), (".cache",)):
            continue                                      # git objects are compressed (history checked below); our cache
        stat = path.stat()
        if stat.st_size >= BIG_BYTES:
            continue                                      # model weights, proven identical to upstream by MANIFEST.json
        key, fingerprint = rel.as_posix(), [stat.st_size, stat.st_mtime_ns]
        if cached.get(key) == fingerprint:
            clean[key] = fingerprint
            continue
        with path.open("rb") as handle:
            if OLD in handle.read().lower():
                offenders.append(key)
            else:
                clean[key] = fingerprint
    _save_cache(clean)
    assert offenders == []


def _load_cache() -> dict[str, list[int]]:
    import json

    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(clean: dict[str, list[int]]) -> None:
    import json

    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(clean), encoding="utf-8")


def test_big_files_are_only_manifest_checked_model_weights() -> None:
    import json

    manifest = json.loads((ROOT / "models" / "MANIFEST.json").read_text(encoding="utf-8"))["files"]
    _, files = _walk(ROOT)
    big = [p for p in files if ".git" not in p.relative_to(ROOT).parts[:1] and p.stat().st_size >= BIG_BYTES]
    for path in big:
        rel = path.relative_to(ROOT).as_posix()
        assert rel.startswith("models/") and rel.removeprefix("models/") in manifest, f"{rel} is big but not a model"


def test_git_history_does_not_contain_it() -> None:
    head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=ROOT, capture_output=True, check=False)
    if head.returncode != 0:
        return                                            # no commits yet: nothing to search
    log = subprocess.run(["git", "log", "--all", "-p", "--name-status", "--format=%H%n%an%n%ae%n%B"], cwd=ROOT,
                         capture_output=True, check=True)
    assert OLD not in log.stdout.lower(), "a commit message, author, diff or path in the history contains it"
    refs = subprocess.run(["git", "for-each-ref", "--format=%(refname)"], cwd=ROOT, capture_output=True, check=True)
    assert OLD not in refs.stdout.lower(), "a branch or tag name contains it"
