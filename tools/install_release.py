"""Install the trained brains of release v3.1 inside JARVIS (P3 step 3, decision 0015), with proofs.

    .venv\\Scripts\\python tools\\install_release.py --release DIR --upstream DIR --modules FILE [--out DIR]

The release folder and the upstream CTM folder are given on the command line only (they are never written anywhere:
PROVENANCE.json names files by their place inside the release); --modules is release_probe.py's output. Into --out
(default matrix/release_v31/):
- **code:** every module the probe saw (`.py` files only), at the same relative path: `code_snapshot/<path>` becomes
  `<path>`; the upstream modules go under `base ctm/continuous-thought-machines-main/`, so `ctm_training/model.py`
  finds them through its own unchanged path; plus the upstream LICENSE (and NOTICE if there is one);
- **the old name** (`name_guard.OLD`) is replaced by a reversible word map (all lower case -> `testbed`, capitalised ->
  `Testbed`, all upper case -> `TESTBED`); any other spelling of the name, or "testbed" already in a file, stops the
  install. Proof per file: the reverse map gives the release file byte for byte;
- **engines:** each `brains/<machine>/reasoning_engine_*.pt` is loaded (`weights_only=False`, as the training's
  `load_brain` does) and re-saved as only `{"config", "state_dict"}`. Proof: the same tensor names, `torch.equal` for
  every tensor, an equal config, and no old name in the config. `decide.json` and `stats.npz` are copied byte for
  byte. Nothing else from `brains/` is copied;
- `PROVENANCE.json` (version, and per installed file: where it came from inside the release, both SHA-256s and how it
  was installed) and `README.md`.
Running it twice gives byte-identical files (tests/test_install_release.py).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from name_guard import OLD  # noqa: E402

VERSION = "v3.1"
UPSTREAM_PREFIX = "base ctm/continuous-thought-machines-main"
NEW = "testbed"
FORWARD = {OLD: NEW, OLD.capitalize(): NEW.capitalize(), OLD.upper(): NEW.upper()}
REVERSE = {new: old for old, new in FORWARD.items()}
ENGINE_FILES = {"incubator": "reasoning_engine_INCv3.1.pt", "hvac": "reasoning_engine_HVACv3.1.pt",
                "animal_protection": "reasoning_engine_APv3.1.pt"}
BRAIN_DATA = ("decide.json", "stats.npz")
LICENSES = ("LICENSE", "NOTICE")
README = """# matrix/release_v31: the trained brains of release v3.1, installed inside JARVIS

Written by `tools/install_release.py` (P3 step 3, decision 0015). **Never edit anything here by hand**: run the
installer again on the release folder instead; it gives byte-identical files.

- `ctm_training/`, `farm_poc/`: the exact code that trained the brains (the release's `code_snapshot/`), only the
  modules the brains need (found by `tools/release_probe.py`).
- `base ctm/continuous-thought-machines-main/`: the upstream Continuous Thought Machine library modules the brains
  import, with its LICENSE, at the path `ctm_training/model.py` expects.
- `brains/<machine>/`: each reasoning engine re-saved as plain weights (`config` and `state_dict` only, every tensor
  proven equal), with its `decide.json` and `stats.npz` copied byte for byte.
- The project's old name is replaced by the word `testbed` (same case) everywhere in this code; the reverse map gives
  the release's files byte for byte. `PROVENANCE.json` lists every file with both SHA-256s and how it was installed.

The farm runtime's trained feed (`matrix/farm_runtime.py --feed trained`, decision 0021) runs these brains exactly as
the release's exam did. This folder is excluded from pyright and coverage (frozen code) but not from the old-name scan.
"""


class InstallError(RuntimeError):
    """The install must stop (another spelling of the old name, "testbed" already present, a failed proof)."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rename(data: bytes, where: str) -> bytes:
    """The forward word map, refusing anything it could not reverse exactly."""
    text = data.decode("utf-8")
    lowered = text.lower()
    if NEW in lowered:
        raise InstallError(f"{where}: already contains {NEW!r} in some case, so the map would not be reversible")
    for match in re.finditer(re.escape(OLD), lowered):
        found = text[match.start():match.end()]
        if found not in FORWARD:
            raise InstallError(f"{where}: the old name is spelled {found!r} at character {match.start()}, "
                               "which the word map does not cover")
    for old, new in FORWARD.items():
        text = text.replace(old, new)
    renamed = text.encode("utf-8")
    if reverse(renamed) != data:
        raise InstallError(f"{where}: the reverse map does not give the release file back")
    return renamed


def reverse(data: bytes) -> bytes:
    text = data.decode("utf-8")
    for new, old in REVERSE.items():
        text = text.replace(new, old)
    return text.encode("utf-8")


def has_old_name(data: bytes) -> bool:
    return OLD.encode() in data.lower()


def resave_engine(source: Path) -> tuple[bytes, dict[str, Any]]:
    """The engine as plain weights, with the proof that nothing in it changed."""
    import torch

    saved = torch.load(source, map_location="cpu", weights_only=False)
    config, state = saved["config"], saved["state_dict"]
    if has_old_name(json.dumps(config, sort_keys=True, default=str).encode()):
        raise InstallError(f"{source.name}: its config names the old project")
    buffer = io.BytesIO()
    torch.save({"config": config, "state_dict": state}, buffer)
    data = buffer.getvalue()
    again = torch.load(io.BytesIO(data), map_location="cpu", weights_only=True)
    if set(again) != {"config", "state_dict"} or again["config"] != config:
        raise InstallError(f"{source.name}: the re-saved config differs")
    if list(again["state_dict"]) != list(state):
        raise InstallError(f"{source.name}: the re-saved tensor names differ")
    for name, tensor in state.items():
        copy = again["state_dict"][name]
        if copy.dtype != tensor.dtype or copy.shape != tensor.shape or not torch.equal(copy, tensor):
            raise InstallError(f"{source.name}: tensor {name} differs after re-saving")
    proof = {"tensors": len(state), "parameters": int(sum(t.numel() for t in state.values())),
             "dropped_keys": sorted(k for k in saved if k not in ("config", "state_dict")),
             "loads_with_weights_only": True}
    return data, proof


def install(release: Path, upstream: Path, modules: dict[str, list[str]], out: Path) -> dict[str, Any]:
    """Write everything into `out` (emptied first, so a second run gives the same files) and return the provenance.
    `out` is emptied only when it is an earlier install (it has PROVENANCE.json) and never overlaps the release or the
    upstream CTM folder (rule 2: they are read only)."""
    where = out.resolve()
    for source in (release.resolve(), upstream.resolve()):
        if where == source or source in where.parents or where in source.parents:
            raise InstallError("--out is inside the release or the upstream CTM folder, or holds one of them")
    if where.exists() and any(where.iterdir()) and not (where / "PROVENANCE.json").is_file():
        raise InstallError("--out is not an earlier install (it has files but no PROVENANCE.json): never emptied")
    plan: list[tuple[str, str, bytes]] = []                   # (installed path, source inside the release, bytes)
    for path in modules["code_snapshot"]:
        if path.endswith(".py") and "__pycache__" not in path:
            plan.append((path, f"code_snapshot/{path}", (release / "code_snapshot" / path).read_bytes()))
    for path in modules["upstream"]:
        if path.endswith(".py") and "__pycache__" not in path:
            plan.append((f"{UPSTREAM_PREFIX}/{path}", f"upstream/{path}", (upstream / path).read_bytes()))
    for name in LICENSES:
        if (upstream / name).is_file():
            plan.append((f"{UPSTREAM_PREFIX}/{name}", f"upstream/{name}", (upstream / name).read_bytes()))
    if not any(p.startswith("ctm_training/") for p, _, _ in plan) or not any("models/ctm_rl.py" in p for p, _, _ in plan):
        raise InstallError("the module list misses the training package or the upstream CTM: run release_probe.py")

    if out.exists():
        shutil.rmtree(out)
    files: list[dict[str, Any]] = []
    for installed, source, data in sorted(plan):
        written = rename(data, source)
        target = out / installed
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(written)
        files.append({"path": installed, "source": source, "release_sha256": sha256(data),
                      "installed_sha256": sha256(written), "how": "renamed" if written != data else "copied"})
    engines: dict[str, Any] = {}
    for machine, engine in sorted(ENGINE_FILES.items()):
        folder = release / "brains" / machine
        data, proof = resave_engine(folder / engine)
        target = out / "brains" / machine / engine
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        source_bytes = (folder / engine).read_bytes()
        files.append({"path": f"brains/{machine}/{engine}", "source": f"brains/{machine}/{engine}",
                      "release_sha256": sha256(source_bytes), "installed_sha256": sha256(data), "how": "re-saved"})
        engines[machine] = proof
        for name in BRAIN_DATA:
            raw = (folder / name).read_bytes()
            if has_old_name(raw):
                raise InstallError(f"brains/{machine}/{name} contains the old name")
            (out / "brains" / machine / name).write_bytes(raw)
            files.append({"path": f"brains/{machine}/{name}", "source": f"brains/{machine}/{name}",
                          "release_sha256": sha256(raw), "installed_sha256": sha256(raw), "how": "copied"})
    cmm = release / "memory" / "cmm.py"
    provenance = {"release": VERSION, "installer": "tools/install_release.py", "word_map": {"old name": "testbed"},
                  "engines": engines,
                  "memory_cmm_identical_to_code": cmm.is_file()
                  and cmm.read_bytes() == (release / "code_snapshot" / "farm_poc" / "nodes" / "cmm.py").read_bytes(),
                  "files": sorted(files, key=lambda f: f["path"])}
    (out / "PROVENANCE.json").write_text(json.dumps(provenance, indent=1) + "\n", encoding="utf-8", newline="\n")
    (out / "README.md").write_text(README, encoding="utf-8", newline="\n")
    return provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--modules", type=Path, required=True, help="release_probe.py's output")
    parser.add_argument("--out", type=Path, default=ROOT / "matrix" / "release_v31")
    args = parser.parse_args(argv)
    modules = json.loads(args.modules.read_text(encoding="utf-8"))["files"]
    try:
        provenance = install(args.release, args.upstream, modules, args.out)
    except InstallError as error:
        print(f"stopped: {error}", file=sys.stderr)
        return 1
    hows: dict[str, int] = {}
    for item in provenance["files"]:
        hows[item["how"]] = hows.get(item["how"], 0) + 1
    print(f"installed {len(provenance['files'])} files ({hows}); engines: "
          + ", ".join(f"{m} {p['tensors']} tensors equal" for m, p in provenance["engines"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
