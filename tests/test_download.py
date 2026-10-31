"""tools/download.py: what setup fetches, where it lands, and that nothing unchecked is kept. No network."""
from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

import download
from download import DOWNLOADS, Archive, File, fetch_file, unpack_archive


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_every_model_in_the_manifest_has_a_download() -> None:
    import json
    manifest = json.loads((download.ROOT / "models" / "MANIFEST.json").read_text(encoding="utf-8"))["files"]
    covered: set[str] = set()
    for item in DOWNLOADS:
        if isinstance(item, File):
            covered.add(item.target)
        elif item.manifest_prefix:
            covered |= {path for path in manifest if path.startswith(item.manifest_prefix)}
    assert set(manifest) <= covered
    assert all(item.url.startswith("https://") for item in DOWNLOADS)


def test_a_file_is_kept_only_when_its_hash_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(download, "_get", lambda url, out: out.write_bytes(b"good"))
    target = tmp_path / "a.onnx"
    fetch_file("https://x/a.onnx", target, _sha(b"good"))
    assert target.read_bytes() == b"good"
    monkeypatch.setattr(download, "_get", lambda url, out: out.write_bytes(b"tampered"))
    other = tmp_path / "b.onnx"
    with pytest.raises(download.DownloadError, match="SHA-256"):
        fetch_file("https://x/b.onnx", other, _sha(b"good"))
    assert not other.exists()


def test_a_model_archive_keeps_only_the_manifests_files_renamed(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        for name, data in (("pkg-v2/encoder.onnx", b"enc"), ("pkg-v2/README.md", b"extra"), ("../evil", b"x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    archive = tmp_path / "pkg.tar.bz2"
    archive.write_bytes(buffer.getvalue())
    manifest = {"voice/pkg/encoder.onnx": {"sha256": _sha(b"enc")}}
    unpack_archive(archive, tmp_path / "models", strip="pkg-v2/", into="voice/pkg/", manifest=manifest)
    assert (tmp_path / "models" / "voice" / "pkg" / "encoder.onnx").read_bytes() == b"enc"
    assert not (tmp_path / "models" / "voice" / "pkg" / "README.md").exists()
    assert not (tmp_path / "evil").exists()


def test_a_program_zip_unpacks_whole_into_its_bin_folder(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("llama-server.exe", b"exe")
        zf.writestr("sub/ggml.dll", b"dll")
    archive = tmp_path / "llama.zip"
    archive.write_bytes(buffer.getvalue())
    unpack_archive(archive, tmp_path / "bin")
    assert (tmp_path / "bin" / "llama-server.exe").read_bytes() == b"exe"
    assert (tmp_path / "bin" / "sub" / "ggml.dll").exists()


def test_the_programs_come_with_their_published_hashes() -> None:
    programs = [item for item in DOWNLOADS if isinstance(item, Archive) and not item.manifest_prefix]
    assert {Path(p.target).parts[1] for p in programs} == {"llama.cpp", "nats-server", "node"}
    assert all(len(p.sha256) == 64 for p in programs)
