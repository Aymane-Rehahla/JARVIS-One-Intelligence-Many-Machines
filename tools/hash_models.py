"""Write and check models/MANIFEST.json: the SHA-256, size and source of every model file (plan Q8).

Usage:
    python tools/hash_models.py write                   hash every file under models/ and write MANIFEST.json
    python tools/hash_models.py write --accept-changes  also accept files whose hash changed or that disappeared
    python tools/hash_models.py check                   re-hash every file and compare (the launcher's start check)
    python tools/hash_models.py check --quick           compare sizes only (tests and development; not a Q8 check)

`check` exits with 0 when everything matches, 1 when a file is missing, changed or unlisted, and 2 when the manifest
is missing or invalid or a file can't be read. `write` refuses (exit 1) to bless a changed or vanished file unless
`--accept-changes` is given. From the command line the tool lowers its own priority, so hashing never competes with
the training.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
MANIFEST_NAME = "MANIFEST.json"
CHUNK_BYTES = 8 * 1024 * 1024
# Top-level documentation files of models/ that are not model files.
NOT_MODEL_FILES = frozenset({"README.md", MANIFEST_NAME})
UNKNOWN_SOURCE = "unknown: add it to SOURCES in tools/hash_models.py"

# Where each file came from. Patterns are relative to models/ with forward slashes; the first match wins.
SOURCES: tuple[tuple[str, str], ...] = (
    ("llm/Qwen-3.5-2b-roleplay-tuned.Q8_0.gguf",
     "huggingface.co/mradermacher/Qwen-3.5-2b-roleplay-tuned-GGUF; hard link to Desktop/jarvis 0.3.4/predownloaded assets"),
    ("llm/Qwen3-0.6B-Q8_0.gguf",
     "huggingface.co/Qwen/Qwen3-0.6B-GGUF; hard link to Desktop/jarvis 0.3.4/predownloaded assets"),
    ("embeddings/bge-small-en-v1.5/*",
     "huggingface.co/BAAI/bge-small-en-v1.5 (model.onnx from onnx/); hard link to Desktop/jarvis 0.3.4/predownloaded assets"),
    ("voice/openwakeword/*", "github.com/dscripka/openWakeWord, release v0.5.1"),
    ("voice/silero-vad/*", "github.com/k2-fsa/sherpa-onnx, release asr-models: silero_vad.onnx"),
    ("voice/parakeet-tdt-0.6b-v2-int8/*",
     "github.com/k2-fsa/sherpa-onnx, release asr-models: sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2"),
    ("voice/kokoro-multi-lang-v1_0/*", "github.com/k2-fsa/sherpa-onnx, release tts-models: kokoro-multi-lang-v1_0.tar.bz2"),
    ("voice/emotion2vec-plus-base/*", "huggingface.co/emotion2vec/emotion2vec_plus_base"),
)


class ManifestEntry(BaseModel):
    """One model file as recorded in MANIFEST.json."""

    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    source: str = Field(min_length=1)


class Manifest(BaseModel):
    """The whole MANIFEST.json file."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    generated_utc: str
    files: dict[str, ManifestEntry]

    @field_validator("files")
    @classmethod
    def _paths_stay_inside_models(cls, files: dict[str, ManifestEntry]) -> dict[str, ManifestEntry]:
        for rel in files:
            parts = rel.split("/")
            if not rel or "\\" in rel or ":" in rel or rel.startswith("/") or any(p in ("", ".", "..") for p in parts):
                raise ValueError(f"manifest path must be relative to models/ with forward slashes: {rel!r}")
        return files


@dataclass
class CheckReport:
    """What `check_manifest` found. `ok` is True only when nothing is missing, changed or unlisted."""

    checked: int = 0
    missing: list[str] = field(default_factory=list[str])
    changed: list[str] = field(default_factory=list[str])
    unlisted: list[str] = field(default_factory=list[str])

    @property
    def ok(self) -> bool:
        return not (self.missing or self.changed or self.unlisted)


def sha256_file(path: Path, chunk_bytes: int = CHUNK_BYTES) -> str:
    """Return the hex SHA-256 of a file, read in chunks so large models never load into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def source_for(relative: str) -> str:
    """Return the recorded origin of a model file (path relative to models/, forward slashes)."""
    for pattern, source in SOURCES:
        if fnmatch.fnmatchcase(relative, pattern):
            return source
    return UNKNOWN_SOURCE


def model_files(models_dir: Path) -> list[str]:
    """List every model file under models_dir, relative and sorted, leaving out the top-level docs."""
    found = (p.relative_to(models_dir).as_posix() for p in models_dir.rglob("*") if p.is_file())
    return sorted(rel for rel in found if rel not in NOT_MODEL_FILES)


def build_manifest(models_dir: Path) -> Manifest:
    """Hash every model file and return the manifest (not yet written)."""
    files = {
        rel: ManifestEntry(sha256=sha256_file(models_dir / rel), size=(models_dir / rel).stat().st_size,
                           source=source_for(rel))
        for rel in model_files(models_dir)
    }
    return Manifest(generated_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"), files=files)


def changes_since(old: Manifest, new: Manifest) -> list[str]:
    """Files whose hash changed, or that vanished, between two manifests (new files are not changes)."""
    changed = [rel for rel, entry in old.files.items() if rel in new.files and new.files[rel].sha256 != entry.sha256]
    vanished = [rel for rel in old.files if rel not in new.files]
    return sorted(changed + vanished)


def write_manifest(models_dir: Path, manifest: Manifest | None = None) -> Manifest:
    """Write models_dir/MANIFEST.json, hashing every model file unless an already built `manifest` is given."""
    manifest = manifest or build_manifest(models_dir)
    text = json.dumps(manifest.model_dump(), indent=2, sort_keys=True) + "\n"
    (models_dir / MANIFEST_NAME).write_text(text, encoding="utf-8", newline="\n")
    return manifest


def load_manifest(models_dir: Path) -> Manifest:
    """Read and validate models_dir/MANIFEST.json. Raises FileNotFoundError or pydantic.ValidationError."""
    raw = (models_dir / MANIFEST_NAME).read_text(encoding="utf-8")
    return Manifest.model_validate_json(raw)


def check_manifest(models_dir: Path, quick: bool = False) -> CheckReport:
    """Compare the files on disk with the manifest; `quick` compares sizes only."""
    manifest = load_manifest(models_dir)
    report = CheckReport()
    for rel, entry in sorted(manifest.files.items()):
        path = models_dir / rel
        if not path.is_file():
            report.missing.append(rel)
            continue
        report.checked += 1
        if path.stat().st_size != entry.size or (not quick and sha256_file(path) != entry.sha256):
            report.changed.append(rel)
    report.unlisted = [rel for rel in model_files(models_dir) if rel not in manifest.files]
    return report


def lower_priority() -> None:
    """Best effort: run at below-normal priority on Windows (ground rule 1: never compete with the training)."""
    if sys.platform == "win32":
        import ctypes

        below_normal = 0x00004000
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), below_normal)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("write", "check"))
    parser.add_argument("--quick", action="store_true", help="check sizes only, without hashing")
    parser.add_argument("--accept-changes", action="store_true", help="write: accept changed or vanished files")
    parser.add_argument("--models", type=Path, default=MODELS_DIR, help="models folder (default: the project's)")
    args = parser.parse_args(argv)
    if args.command == "write":
        try:
            new = build_manifest(args.models)
            old = load_manifest(args.models) if (args.models / MANIFEST_NAME).exists() else None
        except OSError as error:
            print(f"cannot read the model files: {error}")
            return 2
        except ValidationError as error:
            print(f"invalid existing manifest: {error}")
            return 2
        differences = changes_since(old, new) if old else []
        if differences and not args.accept_changes:
            print("refusing to write: these files changed or vanished since the last manifest "
                  "(check them, then use --accept-changes):")
            for rel in differences:
                print(f"  {rel}")
            return 1
        manifest = write_manifest(args.models, new)                       # hashed once, above
        total = sum(entry.size for entry in manifest.files.values())
        unknown = [rel for rel, entry in manifest.files.items() if entry.source == UNKNOWN_SOURCE]
        print(f"wrote {args.models / MANIFEST_NAME}: {len(manifest.files)} files, {total:,} bytes")
        for rel in unknown:
            print(f"  source unknown: {rel}")
        return 0
    try:
        report = check_manifest(args.models, quick=args.quick)
    except FileNotFoundError:
        print(f"no manifest at {args.models / MANIFEST_NAME}; run: python tools/hash_models.py write")
        return 2
    except ValidationError as error:
        print(f"invalid manifest: {error}")
        return 2
    except OSError as error:
        print(f"cannot read the model files: {error}")
        return 2
    mode = "sizes" if args.quick else "SHA-256"
    print(f"checked {report.checked} files ({mode}): missing {len(report.missing)}, changed {len(report.changed)}, "
          f"unlisted {len(report.unlisted)}")
    for label, items in (("missing", report.missing), ("changed", report.changed), ("unlisted", report.unlisted)):
        for rel in items:
            print(f"  {label}: {rel}")
    print("OK" if report.ok else "MISMATCH")
    return 0 if report.ok else 1


if __name__ == "__main__":
    lower_priority()
    sys.exit(main())
