"""The website's 3D models, carried beside the code (too large for git: hvac.glb alone is over GitHub's 100 MB limit).

    .venv\\Scripts\\python tools\\farm_assets.py check              every file in interface/web/assets-manifest.json is
                                                                 present with its SHA-256 (exit 1 otherwise)
    .venv\\Scripts\\python tools\\farm_assets.py pack OUT.zip       the manifest's files and the manifest, into one zip
    .venv\\Scripts\\python tools\\farm_assets.py unpack IN.zip      put them back under interface/web/public/, then check

Unpacking writes only the manifest's own paths (nothing else in the zip is extracted), so a hand-in zip cannot write
outside the assets folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "interface" / "web" / "assets-manifest.json"
CHUNK = 1 << 20


def _manifest() -> tuple[Path, list[dict[str, object]]]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return ROOT / str(data["root"]), list(data["files"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def check() -> list[str]:
    """The problems, one line each (empty when every file is present and unchanged)."""
    root, files = _manifest()
    problems = []
    for entry in files:
        path = root / str(entry["path"])
        if not path.is_file():
            problems.append(f"missing: {entry['path']}")
        elif _sha256(path) != entry["sha256"]:
            problems.append(f"changed: {entry['path']}")
    return problems


def pack(out: Path) -> None:
    root, files = _manifest()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as archive:   # .glb and .png are compressed already
        archive.write(MANIFEST, "assets-manifest.json")
        for entry in files:
            archive.write(root / str(entry["path"]), str(entry["path"]))


def unpack(source: Path) -> None:
    root, files = _manifest()
    with zipfile.ZipFile(source) as archive:
        for entry in files:
            target = root / str(entry["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(str(entry["path"])) as stream, target.open("wb") as sink:
                while block := stream.read(CHUNK):
                    sink.write(block)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("pack").add_argument("out", type=Path)
    sub.add_parser("unpack").add_argument("source", type=Path)
    args = parser.parse_args(argv)
    if args.command == "pack":
        problems = check()
        if problems:
            print("\n".join(problems))
            return 1
        pack(args.out)
        print(f"wrote {args.out}")
        return 0
    if args.command == "unpack":
        unpack(args.source)
    problems = check()
    print("\n".join(problems) if problems else "every 3D model is present and unchanged")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
