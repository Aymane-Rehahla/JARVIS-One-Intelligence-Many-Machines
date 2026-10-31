"""Fetch everything git does not carry: the model weights, llama.cpp, the NATS server and a portable Node.js.

    python tools/download.py              download what is missing, check every SHA-256, unpack into place
    python tools/download.py --assets ZIP_OR_URL   also the website's 3D farm models (tools/farm_assets.py unpack)

Every file is checked against a published or recorded SHA-256 (models/MANIFEST.json for the models, the publishers'
release pages for the programs) and is thrown away when it does not match. Archives write only their own folder;
a model archive keeps only the files the manifest lists. Files already in place with the right size are skipped,
so running it again resumes. Standard library only: it runs before anything is installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
HF = "https://huggingface.co"
SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
WAKE = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
BGE = f"{HF}/BAAI/bge-small-en-v1.5/resolve/main"
CHUNK = 1 << 20


class DownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class File:
    """One model file, checked against models/MANIFEST.json."""

    url: str
    target: str                       # relative to models/


@dataclass(frozen=True)
class Archive:
    """A zip or tar.bz2: a model package (manifest_prefix set) or a program (unpacked whole into target)."""

    url: str
    target: str                       # relative to the project folder
    sha256: str = ""                  # programs: the publisher's digest
    manifest_prefix: str = ""         # model packages: the manifest folder its files go to
    strip: str = ""                   # model packages: the archive's own top folder


DOWNLOADS: tuple[File | Archive, ...] = (
    File(f"{HF}/mradermacher/Qwen-3.5-2b-roleplay-tuned-GGUF/resolve/main/Qwen-3.5-2b-roleplay-tuned.Q8_0.gguf",
         "llm/Qwen-3.5-2b-roleplay-tuned.Q8_0.gguf"),
    File(f"{HF}/Qwen/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf", "llm/Qwen3-0.6B-Q8_0.gguf"),
    File(f"{BGE}/onnx/model.onnx", "embeddings/bge-small-en-v1.5/model.onnx"),
    *(File(f"{BGE}/{name}", f"embeddings/bge-small-en-v1.5/{name}") for name in
      ("config.json", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json", "vocab.txt")),
    *(File(f"{WAKE}/{name}", f"voice/openwakeword/{name}") for name in
      ("hey_jarvis_v0.1.onnx", "melspectrogram.onnx", "embedding_model.onnx")),
    File(f"{SHERPA}/asr-models/silero_vad.onnx", "voice/silero-vad/silero_vad.onnx"),
    Archive(f"{SHERPA}/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2", "models",
            manifest_prefix="voice/parakeet-tdt-0.6b-v2-int8/", strip="sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8/"),
    Archive(f"{SHERPA}/tts-models/kokoro-multi-lang-v1_0.tar.bz2", "models",
            manifest_prefix="voice/kokoro-multi-lang-v1_0/", strip="kokoro-multi-lang-v1_0/"),
    Archive("https://github.com/ggml-org/llama.cpp/releases/download/b11829/llama-b11829-bin-win-vulkan-x64.zip",
            "third_party/llama.cpp/bin", "839ff55ce9cbee6a099b403b640ca6b694ee5d74b815e39f77fc5d2b20962f0d"),
    Archive("https://github.com/nats-io/nats-server/releases/download/v2.15.1/nats-server-v2.15.1-windows-amd64.zip",
            "third_party/nats-server/bin", "4f7edfa376750b2720463bd22766ed469a5f13f378a643097866fcd953cbf7e3"),
    Archive("https://nodejs.org/dist/v24.22.1/node-v24.22.1-win-x64.zip",
            "third_party/node", "d2046d28c2c3dcd211ac5145c0bf30e2047ef9dfb306693c8c6e2eccd8b95fa5"),
)

# Big files inside the programs above, by their SHA-256 after unpacking (tests/test_no_old_name.py checks them)
PROGRAM_FILES = {
    "third_party/node/node-v24.22.1-win-x64/node.exe": "d702daa815eabbff763c7d3808e235cf031cf2ca70211b56847bc72f51e240e5",
}


def _get(url: str, out: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "jarvis-setup"})
    with urllib.request.urlopen(request, timeout=60) as response, out.open("wb") as file:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(CHUNK):
            file.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done * 100 // total:3d} %  {url.rsplit('/', 1)[-1]}", end="", flush=True)
    print()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_file(url: str, target: Path, expected: str) -> None:
    """Download url to target, keeping it only when its SHA-256 is the expected one."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    _get(url, partial)
    if sha256(partial) != expected:
        partial.unlink()
        raise DownloadError(f"{url}: the SHA-256 does not match, so the file was thrown away")
    partial.replace(target)


def _safe(name: str) -> PurePosixPath | None:
    path = PurePosixPath(name)
    return None if path.is_absolute() or ".." in path.parts else path


def unpack_archive(archive: Path, dest: Path, *, strip: str = "", into: str = "",
                   manifest: dict[str, Any] | None = None) -> None:
    """Unpack into dest. With a manifest, only the members it lists are written (renamed from strip to into)."""
    dest.mkdir(parents=True, exist_ok=True)

    def place(name: str) -> Path | None:
        member = _safe(name)
        if member is None:
            return None
        rel = str(member)
        if manifest is not None:
            if not rel.startswith(strip):
                return None
            rel = into + rel[len(strip):]
            if rel not in manifest:
                return None
        return dest / rel

    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                target = place(info.filename)
                if target is None or info.is_dir():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source, target.open("wb") as out:
                    shutil.copyfileobj(source, out)
        return
    with tarfile.open(archive, "r:*") as tar:
        for info in tar:
            target = place(info.name)
            if target is None or not info.isfile():
                continue
            source = tar.extractfile(info)
            if source is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as out:
                shutil.copyfileobj(source, out)


def _missing(manifest: dict[str, Any], paths: list[str]) -> list[str]:
    return [p for p in paths if not (MODELS / p).is_file() or (MODELS / p).stat().st_size != manifest[p]["size"]]


def run(assets: str | None = None) -> int:
    manifest: dict[str, Any] = json.loads((MODELS / "MANIFEST.json").read_text(encoding="utf-8"))["files"]
    with tempfile.TemporaryDirectory(dir=ROOT) as scratch:
        for item in DOWNLOADS:
            if isinstance(item, File):
                if _missing(manifest, [item.target]):
                    fetch_file(item.url, MODELS / item.target, manifest[item.target]["sha256"])
                continue
            if item.manifest_prefix:
                wanted = [p for p in manifest if p.startswith(item.manifest_prefix)]
                if not _missing(manifest, wanted):
                    continue
                archive = Path(scratch) / item.url.rsplit("/", 1)[-1]
                _get(item.url, archive)
                unpack_archive(archive, MODELS, strip=item.strip, into=item.manifest_prefix, manifest=manifest)
                archive.unlink()
                continue
            dest = ROOT / item.target
            if dest.is_dir() and any(dest.iterdir()):
                continue
            archive = Path(scratch) / item.url.rsplit("/", 1)[-1]
            fetch_file(item.url, archive, item.sha256)
            unpack_archive(archive, dest)
            archive.unlink()
        if assets:
            zip_path = Path(assets)
            if assets.startswith("https://"):
                zip_path = Path(scratch) / "farm-assets.zip"
                _get(assets, zip_path)
            from farm_assets import main as farm_assets
            if farm_assets(["unpack", str(zip_path)]) != 0:
                return 1
    bad = [p for p in manifest if sha256(MODELS / p) != manifest[p]["sha256"]] if not _missing(manifest, list(manifest)) \
        else _missing(manifest, list(manifest))
    if bad:
        print(f"{len(bad)} model files are missing or wrong, for example {bad[0]}")
        return 1
    print("everything downloaded and checked")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--assets", help="the 3D farm models: a local zip or an https URL (a GitHub release asset)")
    args = parser.parse_args(argv)
    try:
        return run(args.assets)
    except (DownloadError, OSError) as error:
        print(f"download failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
