"""Freeze the Phase 5 result into the release folder the Phase 6 build installs from (plan step 6D).

    python -m ctm_training.release --out F:/TestbedRuns/release-v2

Copies, never moves: the three XL brains (weights, decision layer, training and tuning records), the code
that trained and runs them (``code_snapshot/``), ``cmm.py``, the results and the design notes, plus a
manifest with the SHA-256 of every file. Refuses to overwrite an existing release folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time

REPO = Path(__file__).resolve().parents[1]
WORK = Path(r"F:\TestbedRuns\ctm-training-v2")
BRAINS = ("incubator", "hvac", "animal_protection")
SIZE = "xl"
BRAIN_FILES = ("best.pt", "decide.json", "result.json", "val_b1.json")
CODE = ("ctm_training", "farm_poc", "tests")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")

README = """# Phase 5 release: the XL CTM Jarvis brains

Frozen {date}. Installed by the Phase 6 build in its step 6D.

## What is inside

| Folder | Contents |
|---|---|
| `brains/<brain>/` | `best.pt` (XL CTM weights + config), `decide.json` (its decision layer), `result.json` (training record), `val_b1.json` (decision-layer tuning record) |
| `code_snapshot/` | the exact code that trained and runs the brains (`ctm_training/`, `farm_poc/`, `tests/`) |
| `memory/cmm.py` | CMM v3, the node memory (also inside the snapshot at `farm_poc/nodes/cmm.py`) |
| `RESULTS.md`, `docs/` | the exam results and the design notes |
| `MANIFEST.json` | SHA-256 of every file |

Brains: `incubator` (both incubators run their own instance of these weights), `hvac`, `animal_protection`.
Each is the XL size: 256 neurons, 16 thinking steps, 32-step trace.

## How the brains run (the owner's final design: "listen to rules, decide with the CTM")

- Each node runs its rule brain, which keeps making everything the trained brain reads (memory status, messages
  to the other nodes) and the farm's side effects (owner notifications, calls, protective actions, through the
  safety gate). This makes every input exactly as in the training data.
- The node's reported verdict, what JARVIS reports and what its CMM stores, is the CTM + decision layer + the
  DANGER safety net (the rules' DANGER passes through when the CTM does not see it).

```python
from ctm_training.live import TrainedNodeBrain
from farm_poc.nodes import NODE_IDS, NodeRuntime
from farm_poc.nodes.cmm import CognitiveMemory
from farm_poc.nodes.contracts import MACHINE_TYPES

advisors = {{n: TrainedNodeBrain.from_checkpoint(release / "brains" / MACHINE_TYPES[n] / "best.pt", packed_stats,
                                                start_hour, danger_net=True) for n in NODE_IDS}}
runtime = NodeRuntime(engine, advisors=advisors, memory_factory=lambda n: CognitiveMemory(n, path / f"{{n}}.sqlite"))
verdict = runtime.step()["hvac"]["verdict"]     # the node's reported verdict
```

`from_checkpoint` reads `decide.json` next to `best.pt`. `packed_stats` is a folder holding `<brain>/stats.npz`,
the normal statistics; they are copied here under `brains/<brain>/stats.npz`, so `release / "brains"` works.

## Notes for step 6D

- The checkpoints and the snapshot carry the old project name in metadata, docstrings and default paths. The plan
  leaves the handling to the owner (re-save weights-only with tensor-equality proof, or load from outside JARVIS).
- Torch is needed to run the brains (the training used Python 3.14 with torch 2.x on CPU).
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(out: Path) -> int:
    files_out = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "MANIFEST.json")
    manifest = {str(p.relative_to(out)).replace("\\", "/"): {"bytes": p.stat().st_size, "sha256": sha256(p)} for p in files_out}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return len(files_out)


def point_release(base: Path, out: Path, engines: dict, readme: str, docs=(), deciders: dict | None = None) -> int:
    """A point release built from ``base`` (left untouched): each brain's ``best.pt`` renamed to ``engines[brain]``,
    optionally a new ``decide.json`` (``deciders[brain]``), today's code snapshot, extra docs, a new manifest."""
    base, out = Path(base), Path(out)
    if out.exists():
        raise SystemExit(f"{out} exists: a release is never overwritten")
    shutil.copytree(base, out, ignore=shutil.ignore_patterns("code_snapshot", "MANIFEST.json", "README.md"))
    for brain, name in engines.items():
        (out / "brains" / brain / "best.pt").rename(out / "brains" / brain / name)
        if deciders and brain in deciders:
            shutil.copy2(deciders[brain], out / "brains" / brain / "decide.json")
    for folder in CODE:
        shutil.copytree(REPO / folder, out / "code_snapshot" / folder, ignore=IGNORE)
    for doc in docs:
        shutil.copy2(REPO / "docs" / doc, out / "docs" / doc)
    (out / "README.md").write_text(readme.format(date=time.strftime("%Y-%m-%d %H:%M")), encoding="utf-8")
    return write_manifest(out)


def freeze(out: Path, checkpoints: dict, results: Path, packed: Path, readme: str = None):
    """Copy each brain's checkpoint with its decision layer, training record and tuning records, the code,
    cmm.py, ``results`` and the docs into ``out``, plus the manifest. Never overwrites."""
    out = Path(out)
    if out.exists():
        raise SystemExit(f"{out} exists: a release is never overwritten")
    for brain, checkpoint in checkpoints.items():
        target = out / "brains" / brain
        target.mkdir(parents=True)
        folder = Path(checkpoint).parent
        for source in [Path(checkpoint), folder / "decide.json", folder / "result.json", *sorted(folder.glob("*_b1.json"))]:
            if source.exists():
                shutil.copy2(source, target / ("best.pt" if source == Path(checkpoint) else source.name))
        shutil.copy2(Path(packed) / brain / "stats.npz", target / "stats.npz")
    for folder in CODE:
        shutil.copytree(REPO / folder, out / "code_snapshot" / folder, ignore=IGNORE)
    (out / "memory").mkdir()
    shutil.copy2(REPO / "farm_poc" / "nodes" / "cmm.py", out / "memory" / "cmm.py")
    shutil.copy2(results, out / "RESULTS.md")
    (out / "docs").mkdir()
    for doc in ("phase5-v2-plan.md", "phase5-6-design.md", "jarvis-nodes.md"):
        if (REPO / "docs" / doc).exists():
            shutil.copy2(REPO / "docs" / doc, out / "docs" / doc)
    (out / "README.md").write_text((readme or README).format(date=time.strftime("%Y-%m-%d %H:%M")), encoding="utf-8")
    return write_manifest(out)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="F:/TestbedRuns/release-v2")
    parser.add_argument("--packed", default="F:/TestbedRuns/ctm-packed-v3")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"{out} exists: a release is never overwritten")
    for brain in BRAINS:
        target = out / "brains" / brain
        target.mkdir(parents=True)
        for name in BRAIN_FILES:
            shutil.copy2(WORK / "runs" / brain / SIZE / name, target / name)
        shutil.copy2(Path(args.packed) / brain / "stats.npz", target / "stats.npz")
    for folder in CODE:
        shutil.copytree(REPO / folder, out / "code_snapshot" / folder, ignore=IGNORE)
    (out / "memory").mkdir()
    shutil.copy2(REPO / "farm_poc" / "nodes" / "cmm.py", out / "memory" / "cmm.py")
    shutil.copy2(WORK / "RESULTS.md", out / "RESULTS.md")
    (out / "docs").mkdir()
    for doc in ("phase5-v2-plan.md", "phase5-6-design.md", "jarvis-nodes.md"):
        if (REPO / "docs" / doc).exists():
            shutil.copy2(REPO / "docs" / doc, out / "docs" / doc)
    (out / "README.md").write_text(README.format(date=time.strftime("%Y-%m-%d %H:%M")), encoding="utf-8")
    files = sorted(p for p in out.rglob("*") if p.is_file())
    manifest = {str(p.relative_to(out)).replace("\\", "/"): {"bytes": p.stat().st_size, "sha256": sha256(p)} for p in files}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"released {len(files)} files to {out}")


if __name__ == "__main__":
    main()
