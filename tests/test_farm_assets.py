"""tools/farm_assets.py: pack, unpack and check the website's 3D models against their manifest."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import farm_assets


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    model = tmp_path / "public" / "assets" / "farm" / "m.glb"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"glTF model")
    manifest = tmp_path / "assets-manifest.json"
    manifest.write_text(json.dumps({"root": "public", "files": [
        {"path": "assets/farm/m.glb", "bytes": 10, "sha256": hashlib.sha256(b"glTF model").hexdigest()}]}))
    monkeypatch.setattr(farm_assets, "ROOT", tmp_path)
    monkeypatch.setattr(farm_assets, "MANIFEST", manifest)
    return model


def test_pack_then_unpack_restores_every_model(project: Path, tmp_path: Path) -> None:
    assert farm_assets.main(["pack", str(tmp_path / "a.zip")]) == 0
    project.unlink()
    assert farm_assets.check() == ["missing: assets/farm/m.glb"]
    assert farm_assets.main(["unpack", str(tmp_path / "a.zip")]) == 0
    assert project.read_bytes() == b"glTF model"


def test_a_changed_model_fails_the_check_and_the_pack(project: Path, tmp_path: Path) -> None:
    project.write_bytes(b"other")
    assert farm_assets.main(["check"]) == 1
    assert farm_assets.main(["pack", str(tmp_path / "a.zip")]) == 1


def test_unpack_writes_only_the_manifests_paths(project: Path, tmp_path: Path) -> None:
    farm_assets.main(["pack", str(tmp_path / "a.zip")])
    with zipfile.ZipFile(tmp_path / "a.zip", "a") as archive:
        archive.writestr("../../evil.txt", "x")
    farm_assets.main(["unpack", str(tmp_path / "a.zip")])
    assert not (tmp_path.parent / "evil.txt").exists()
