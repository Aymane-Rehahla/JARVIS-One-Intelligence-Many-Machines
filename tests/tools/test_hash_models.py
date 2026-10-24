"""tools/hash_models.py: writes and checks models/MANIFEST.json."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import hash_models
from hash_models import MANIFEST_NAME, UNKNOWN_SOURCE, check_manifest, main, sha256_file, source_for, write_manifest


@pytest.fixture
def models(tmp_path: Path) -> Path:
    root = tmp_path / "models"
    (root / "llm").mkdir(parents=True)
    (root / "llm" / "Qwen3-0.6B-Q8_0.gguf").write_bytes(b"GGUF" + bytes(range(256)) * 10)
    nested = root / "voice" / "kokoro-multi-lang-v1_0" / "espeak-ng-data"
    nested.mkdir(parents=True)
    (nested / "phontab").write_bytes(b"\x00\x01\x02")
    (root / "voice" / "kokoro-multi-lang-v1_0" / "README.md").write_text("part of the model package")
    (root / "README.md").write_text("documentation, not a model")
    return root


def test_write_lists_every_model_file_with_its_hash_and_source(models: Path) -> None:
    manifest = write_manifest(models)
    assert sorted(manifest.files) == ["llm/Qwen3-0.6B-Q8_0.gguf", "voice/kokoro-multi-lang-v1_0/README.md",
                                      "voice/kokoro-multi-lang-v1_0/espeak-ng-data/phontab"]
    gguf = models / "llm" / "Qwen3-0.6B-Q8_0.gguf"
    entry = manifest.files["llm/Qwen3-0.6B-Q8_0.gguf"]
    assert entry.sha256 == hashlib.sha256(gguf.read_bytes()).hexdigest()
    assert entry.size == gguf.stat().st_size
    assert entry.source.startswith("huggingface.co/Qwen/Qwen3-0.6B-GGUF")
    on_disk = json.loads((models / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 1 and set(on_disk["files"]) == set(manifest.files)


def test_the_write_command_hashes_each_file_once(models: Path, monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    hashed: list[Path] = []
    real = hash_models.sha256_file

    def counting(path: Path, chunk_bytes: int = 1 << 20) -> str:
        hashed.append(path)
        return real(path, chunk_bytes=chunk_bytes)

    monkeypatch.setattr(hash_models, "sha256_file", counting)
    assert main(["write", "--models", str(models)]) == 0
    assert len(hashed) == 3 == len(set(hashed)) and "3 files" in capsys.readouterr().out


def test_check_passes_on_an_untouched_folder(models: Path) -> None:
    write_manifest(models)
    report = check_manifest(models)
    assert report.ok and report.checked == 3


def test_check_catches_a_changed_byte_that_a_size_check_misses(models: Path) -> None:
    write_manifest(models)
    gguf = models / "llm" / "Qwen3-0.6B-Q8_0.gguf"
    data = bytearray(gguf.read_bytes())
    data[100] ^= 0xFF
    gguf.write_bytes(bytes(data))
    assert check_manifest(models).changed == ["llm/Qwen3-0.6B-Q8_0.gguf"]
    assert check_manifest(models, quick=True).ok          # same size: only the hash notices


def test_check_reports_missing_and_unlisted_files(models: Path) -> None:
    write_manifest(models)
    (models / "voice" / "kokoro-multi-lang-v1_0" / "espeak-ng-data" / "phontab").unlink()
    (models / "llm" / "surprise.gguf").write_bytes(b"not in the manifest")
    report = check_manifest(models)
    assert report.missing == ["voice/kokoro-multi-lang-v1_0/espeak-ng-data/phontab"]
    assert report.unlisted == ["llm/surprise.gguf"]
    assert not report.ok


def test_cli_exit_codes(models: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--models", str(models)]) == 2              # no manifest yet
    assert main(["write", "--models", str(models)]) == 0
    assert main(["check", "--models", str(models)]) == 0
    assert "OK" in capsys.readouterr().out
    (models / "llm" / "Qwen3-0.6B-Q8_0.gguf").write_bytes(b"truncated")
    assert main(["check", "--models", str(models), "--quick"]) == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_write_refuses_to_bless_a_changed_file_unless_told(models: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["write", "--models", str(models)]) == 0
    (models / "llm" / "Qwen3-0.6B-Q8_0.gguf").write_bytes(b"corrupted")
    (models / "llm" / "new-model.gguf").write_bytes(b"a new file is not a change")
    assert main(["write", "--models", str(models)]) == 1
    out = capsys.readouterr().out
    assert "refusing to write" in out and "llm/Qwen3-0.6B-Q8_0.gguf" in out and "new-model" not in out
    assert check_manifest(models).changed == ["llm/Qwen3-0.6B-Q8_0.gguf"]       # the manifest was not rewritten
    assert main(["write", "--models", str(models), "--accept-changes"]) == 0
    assert check_manifest(models).ok


@pytest.mark.parametrize("bad", ["../outside.bin", "/etc/passwd", "C:/x.bin", r"llm\x.gguf", "llm//x.gguf", "./x"])
def test_manifest_paths_cannot_escape_models(models: Path, bad: str, capsys: pytest.CaptureFixture[str]) -> None:
    write_manifest(models)
    raw = json.loads((models / MANIFEST_NAME).read_text(encoding="utf-8"))
    raw["files"][bad] = {"sha256": "0" * 64, "size": 1, "source": "x"}
    (models / MANIFEST_NAME).write_text(json.dumps(raw), encoding="utf-8")
    assert main(["check", "--models", str(models)]) == 2
    assert "invalid manifest" in capsys.readouterr().out


def test_an_invalid_manifest_is_rejected(models: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_manifest(models)
    raw = json.loads((models / MANIFEST_NAME).read_text(encoding="utf-8"))
    raw["files"]["llm/Qwen3-0.6B-Q8_0.gguf"]["sha256"] = "not-a-hash"
    (models / MANIFEST_NAME).write_text(json.dumps(raw), encoding="utf-8")
    assert main(["check", "--models", str(models)]) == 2
    assert "invalid manifest" in capsys.readouterr().out


def test_sources_cover_every_planned_model_folder() -> None:
    assert source_for("embeddings/bge-small-en-v1.5/model.onnx").startswith("huggingface.co/BAAI/bge-small-en-v1.5")
    assert "openWakeWord" in source_for("voice/openwakeword/hey_jarvis_v0.1.onnx")
    assert "parakeet" in source_for("voice/parakeet-tdt-0.6b-v2-int8/encoder.int8.onnx")
    assert "kokoro" in source_for("voice/kokoro-multi-lang-v1_0/espeak-ng-data/en_dict")
    assert source_for("somewhere/else.bin") == UNKNOWN_SOURCE


@settings(max_examples=60, deadline=None, derandomize=True)        # fixed examples (Q8: fixed seeds)
@given(data=st.binary(max_size=5000), chunk=st.integers(min_value=1, max_value=700))
def test_chunked_hash_equals_hashlib_for_any_content(tmp_path_factory: pytest.TempPathFactory, data: bytes,
                                                     chunk: int) -> None:
    path = tmp_path_factory.mktemp("blob") / "blob.bin"
    path.write_bytes(data)
    assert sha256_file(path, chunk_bytes=chunk) == hashlib.sha256(data).hexdigest()


def test_the_project_manifest_matches_the_models_on_disk() -> None:
    """The real models/ folder: sizes must match MANIFEST.json (the full hash check is `hash_models.py check`)."""
    models = hash_models.MODELS_DIR
    if not (models / MANIFEST_NAME).exists():
        pytest.fail("models/MANIFEST.json is missing; run: python tools/hash_models.py write")
    if not hash_models.model_files(models):
        pytest.skip("no model files on this machine yet (a fresh clone): install them as models/README.md says")
    report = check_manifest(models, quick=True)
    assert report.ok, f"missing {report.missing}, changed {report.changed}, unlisted {report.unlisted}"
