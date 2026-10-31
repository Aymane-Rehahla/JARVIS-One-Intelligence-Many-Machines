"""tools/install_release.py on a small fake release in a temporary folder (P3 step 3): the reversible word map, the
re-saved engines with every tensor equal, PROVENANCE without outside paths, byte-identical second runs, and every
refusal. The old name is never written here: it comes from name_guard.OLD."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import install_release as ir
from name_guard import OLD

torch = pytest.importorskip("torch")


def fake_release(root: Path, *, model_text: str | None = None, config_name: str = "incubator",
                 decide_text: str = '{"thresholds": [0.5]}') -> tuple[Path, Path, dict[str, list[str]]]:
    release, upstream = root / "release", root / "upstream"
    code = release / "code_snapshot"
    (code / "ctm_training").mkdir(parents=True)
    (code / "farm_poc" / "nodes").mkdir(parents=True)
    (code / "ctm_training" / "__init__.py").write_text("", encoding="utf-8")
    (code / "ctm_training" / "model.py").write_text(model_text if model_text is not None else (
        f'"""The {OLD.capitalize()} brain."""\nclass {OLD.capitalize()}Brain:\n    NAME = "{OLD}"\n'
        f'    KEY = "{OLD.upper()}_X"\n'), encoding="utf-8")
    (code / "ctm_training" / "__pycache__").mkdir()
    (code / "ctm_training" / "__pycache__" / "model.cpython-314.pyc").write_bytes(b"\x00compiled")
    (code / "farm_poc" / "__init__.py").write_text("", encoding="utf-8")
    (code / "farm_poc" / "nodes" / "cmm.py").write_text("MEMORY = 1\n", encoding="utf-8")
    (release / "memory").mkdir()
    (release / "memory" / "cmm.py").write_text("MEMORY = 1\n", encoding="utf-8")
    (upstream / "models").mkdir(parents=True)
    (upstream / "models" / "ctm_rl.py").write_text("class ContinuousThoughtMachineRL: pass\n", encoding="utf-8")
    (upstream / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    for machine, engine in ir.ENGINE_FILES.items():
        folder = release / "brains" / machine
        folder.mkdir(parents=True)
        generator = torch.Generator().manual_seed(len(machine))
        torch.save({"config": {"machine": config_name if machine == "incubator" else machine, "d_model": 8},
                    "state_dict": {"w": torch.randn(3, 4, generator=generator), "b": torch.zeros(4)},
                    "notes": f"trained by {OLD}"}, folder / engine)
        (folder / "decide.json").write_text(decide_text, encoding="utf-8")
        (folder / "stats.npz").write_bytes(b"PK fake stats")
        (folder / "result.json").write_text("{}", encoding="utf-8")            # never copied
    modules = {"code_snapshot": ["ctm_training/__init__.py", "ctm_training/model.py",
                                 "ctm_training/__pycache__/model.cpython-314.pyc", "farm_poc/__init__.py",
                                 "farm_poc/nodes/cmm.py"],
               "upstream": ["models/ctm_rl.py"]}
    return release, upstream, modules


def tree(folder: Path) -> dict[str, str]:
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def test_the_install_is_reversible_proven_and_repeatable(tmp_path: Path) -> None:
    release, upstream, modules = fake_release(tmp_path)
    out = tmp_path / "jarvis" / "release_v31"
    provenance = ir.install(release, upstream, modules, out)
    model = (out / "ctm_training" / "model.py").read_text(encoding="utf-8")
    assert OLD not in model.lower() and "TestbedBrain" in model and '"testbed"' in model and "TESTBED_X" in model
    assert ir.reverse((out / "ctm_training" / "model.py").read_bytes()) == \
        (release / "code_snapshot" / "ctm_training" / "model.py").read_bytes()          # byte for byte
    assert (out / "base ctm" / "continuous-thought-machines-main" / "models" / "ctm_rl.py").is_file()
    assert (out / "base ctm" / "continuous-thought-machines-main" / "LICENSE").is_file()
    assert not list(out.rglob("*.pyc")) and not list(out.rglob("result.json"))
    for machine, engine in ir.ENGINE_FILES.items():
        saved = torch.load(out / "brains" / machine / engine, weights_only=True)
        original = torch.load(release / "brains" / machine / engine, weights_only=False)
        assert set(saved) == {"config", "state_dict"} and saved["config"] == original["config"]
        assert all(torch.equal(saved["state_dict"][k], original["state_dict"][k]) for k in original["state_dict"])
        assert provenance["engines"][machine]["dropped_keys"] == ["notes"]
    text = (out / "PROVENANCE.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in text and str(tmp_path).replace("\\", "/") not in text and OLD not in text.lower()
    hows = {item["path"]: item["how"] for item in json.loads(text)["files"]}
    assert hows["ctm_training/model.py"] == "renamed" and hows["farm_poc/nodes/cmm.py"] == "copied"
    assert hows["brains/hvac/reasoning_engine_HVACv3.1.pt"] == "re-saved" and hows["brains/hvac/decide.json"] == "copied"
    assert provenance["memory_cmm_identical_to_code"] is True
    first = tree(out)
    ir.install(release, upstream, modules, out)
    assert tree(out) == first                                                           # byte-identical again


@pytest.mark.parametrize(("kind", "why"), [("spelling", "spelled"), ("testbed", "already contains"),
                                           ("config", "config names"), ("decide", "contains the old name")])
def test_the_install_stops_on_anything_it_could_not_reverse_or_clean(tmp_path: Path, kind: str, why: str) -> None:
    mixed = OLD[:4] + OLD[4:].upper()
    options = {"spelling": {"model_text": f"x = '{mixed}'\n"}, "testbed": {"model_text": "x = 'TestBed'\n"},
               "config": {"config_name": OLD}, "decide": {"decide_text": f'{{"by": "{OLD}"}}'}}[kind]
    release, upstream, modules = fake_release(tmp_path, **options)  # type: ignore[arg-type]
    with pytest.raises(ir.InstallError, match=why):
        ir.install(release, upstream, modules, tmp_path / "out")


def test_the_install_never_empties_a_folder_it_did_not_make(tmp_path: Path) -> None:
    """Review (31 Oct): the install emptied whatever --out named; a mistaken --out (the release itself, a folder with
    other files) would have been deleted (rule 2). It empties only an earlier install."""
    release, upstream, modules = fake_release(tmp_path)
    before = tree(release)
    for out in (release, release / "brains", upstream, tmp_path):
        with pytest.raises(ir.InstallError, match="not an earlier install|inside the release"):
            ir.install(release, upstream, modules, out)
    other = tmp_path / "other"
    other.mkdir()
    (other / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(ir.InstallError, match="not an earlier install"):
        ir.install(release, upstream, modules, other)
    assert tree(release) == before and (other / "keep.txt").read_text(encoding="utf-8") == "mine"
    out = tmp_path / "jarvis" / "release_v31"
    ir.install(release, upstream, modules, out)
    ir.install(release, upstream, modules, out)                       # an earlier install is replaced
