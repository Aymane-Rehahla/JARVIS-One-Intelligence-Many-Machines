"""Build the labelled scenario dataset.

    python -m farm_poc.scenarios.build --out F:/TestbedRuns/scenario-library-v2 --workers 6

For every episode: run the fault run and its twin, derive labels, and write
``episodes/<id>.npz`` (inputs, validity, labels, rule-baseline outputs, evidence
flags, and the twin's inputs) plus ``episodes/<id>.json`` (spec, onsets, label
counts, outcomes) and ``episodes/<id>.truth.json.gz`` (the evaluator-only truth
timeline). A top-level ``manifest.json`` records the library hash, encoder
schema hashes, code fingerprint, and the split of every episode. Existing
episodes are skipped, so an interrupted build resumes.

    python -m farm_poc.scenarios.build --out ... --relabel

recomputes every label from the stored runs without simulating again, for when
the label policy changes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from ..nodes.contracts import NODE_IDS
from ..nodes.encoders import NodeEncoderV1
from .episode import run
from .labels import REASONS, label_episode
from .library import EpisodeSpec, build_library, library_hash

LABEL_NAMES = ("assessment", "diagnosis", "action", "communication", "reason", "source")

CODE_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def code_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted(CODE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        digest.update(str(path.relative_to(CODE_ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@lru_cache(maxsize=1)
def label_code_fingerprint() -> str:
    """The label policy alone: when only this changes, ``--relabel`` is enough."""
    here = Path(__file__).resolve().parent
    return hashlib.sha256(b"".join((here / name).read_bytes() for name in ("labels.py", "visibility.py"))).hexdigest()


def build_episode(spec: EpisodeSpec, out_dir: Path, brain_factory=None) -> dict:
    """``brain_factory``: run the nodes with trained brains instead of the rules (practice and exam runs)."""
    target = out_dir / "episodes" / f"{spec.episode_id}.npz"
    meta_path = target.with_suffix(".json")
    if target.exists() and meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    started = time.perf_counter()
    main = run(spec, with_faults=True, brain_factory=brain_factory)
    twin = run(spec, with_faults=False, record_truth=False, brain_factory=brain_factory) if spec.faults else None
    labels = label_episode(main, twin)
    arrays = {}
    meta = {"episode_id": spec.episode_id, "spec": spec.as_dict(), "split": spec.split, "family": spec.family,
            "ticks": spec.duration_ticks, "schema_hash": main["schema_hash"], "outcome": main["outcome"],
            "code_fingerprint": code_fingerprint(), "reasons": list(REASONS), "nodes": {}}
    for node in labels:
        key = node.replace("-", "_")
        arrays[f"{key}__values"] = main["values"][node]
        arrays[f"{key}__validity"] = np.packbits(main["validity"][node], axis=1)
        arrays[f"{key}__rule"] = main["rule"][node]
        if twin is not None:
            arrays[f"{key}__twin_values"] = twin["values"][node]
            arrays[f"{key}__twin_validity"] = np.packbits(twin["validity"][node], axis=1)
        meta["nodes"][node] = {"features": len(main["feature_names"][node]),
                               "rule_counts": _counts(main["rule"][node][:, 0])}
    _store_labels(arrays, meta, labels)
    truth = main["truth"]
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(target.with_name(f"{spec.episode_id}.truth.json.gz"), "wt", encoding="utf-8") as handle:
        json.dump(truth, handle)
    arrays["truth__fire"] = np.array([row["fire"] for row in truth], dtype=bool)
    arrays["truth__flood_cm"] = np.array([row["flood_cm"] for row in truth], dtype=np.float32)
    arrays["truth__floor_water_cm"] = np.array([row["floor_water_cm"] for row in truth], dtype=np.float32)
    arrays["truth__storm"] = np.array([row["storm"] for row in truth], dtype=bool)
    arrays["truth__any_fault"] = np.array([any(row["faults"].values()) for row in truth], dtype=bool)
    temporary = target.with_name(target.stem + ".tmp.npz")
    np.savez_compressed(temporary, **arrays)
    temporary.replace(target)
    meta["seconds"] = round(time.perf_counter() - started, 2)
    meta_path.write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    return meta


def _counts(values):
    return {int(k): int(v) for k, v in zip(*np.unique(values, return_counts=True))}


def _store_labels(arrays, meta, labels):
    for node, node_labels in labels.items():
        key = node.replace("-", "_")
        for name in LABEL_NAMES:
            arrays[f"{key}__label_{name}"] = node_labels[name]
        arrays[f"{key}__visible"] = node_labels["visible"]
        meta["nodes"][node].update(onset=node_labels["onset"], label_counts=_counts(node_labels["assessment"]))
    meta["reasons"] = list(REASONS)
    meta["label_code"] = label_code_fingerprint()


def load_runs(npz_path: Path):
    """The stored fault run (with its truth timeline) and twin, in the shape ``label_episode`` takes."""
    data = dict(np.load(npz_path))
    with gzip.open(npz_path.with_name(npz_path.stem + ".truth.json.gz"), "rt", encoding="utf-8") as handle:
        truth = json.load(handle)
    encoders = {node: NodeEncoderV1(node) for node in NODE_IDS}
    shape = {"feature_names": {n: e.feature_names for n, e in encoders.items()},
             "feature_kinds": {n: e.feature_kinds for n, e in encoders.items()}}

    def unpack(name, node):
        width = len(encoders[node].feature_names)
        return np.unpackbits(data[name], axis=1)[:, :width]
    keys = {node: node.replace("-", "_") for node in NODE_IDS}
    main = {"values": {n: data[f"{k}__values"] for n, k in keys.items()},
            "validity": {n: unpack(f"{k}__validity", n) for n, k in keys.items()}, "truth": truth, **shape}
    twin = None
    if f"{keys['hvac']}__twin_values" in data:
        twin = {"values": {n: data[f"{k}__twin_values"] for n, k in keys.items()},
                "validity": {n: unpack(f"{k}__twin_validity", n) for n, k in keys.items()}, **shape}
    return data, main, twin


def relabel_episode(npz_path: Path) -> dict:
    """Recompute the labels of one stored episode in place (no simulation)."""
    meta_path = npz_path.with_suffix(".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    data, main, twin = load_runs(npz_path)
    _store_labels(data, meta, label_episode(main, twin))
    temporary = npz_path.with_name(npz_path.stem + ".tmp.npz")
    np.savez_compressed(temporary, **data)
    temporary.replace(npz_path)
    meta_path.write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    return meta


def _worker(spec_dict, out_dir):
    spec = EpisodeSpec(**spec_dict)
    meta = build_episode(spec, Path(out_dir))
    return {"episode_id": spec.episode_id, "split": spec.split, "family": spec.family, "seconds": meta.get("seconds")}


def _relabel_worker(npz_path):
    relabel_episode(Path(npz_path))
    return npz_path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="F:/TestbedRuns/scenario-library-v2")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--only", default=None, help="comma-separated families to build")
    parser.add_argument("--limit", type=int, default=None, help="build at most this many episodes (smoke test)")
    parser.add_argument("--relabel", action="store_true", help="recompute labels of stored episodes, no simulation")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.relabel:
        return _relabel_all(out_dir, args.workers)
    library = build_library()
    specs = library
    if args.only:
        wanted = set(args.only.split(","))
        specs = [s for s in specs if s.family in wanted]
    if args.limit:
        specs = specs[: args.limit]
    # The manifest always lists the whole library; the report skips episodes not built yet.
    manifest = {"library_hash": library_hash(library), "code_fingerprint": code_fingerprint(),
                "label_code": label_code_fingerprint(),
                "schema_hash": {n: NodeEncoderV1(n).schema_hash for n in NODE_IDS},
                "episodes": [{"episode_id": s.episode_id, "family": s.family, "split": s.split,
                              "ticks": s.duration_ticks} for s in library]}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    started = time.perf_counter()
    done = 0
    # Longest episodes first, so the slow floods do not finish last on one worker.
    ordered = sorted(specs, key=lambda s: -s.duration_ticks)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_worker, s.as_dict(), str(out_dir)) for s in ordered]
        for future in as_completed(futures):
            result = future.result()
            done += 1
            if done % 10 == 0 or done == len(futures):
                elapsed = time.perf_counter() - started
                print(f"{done}/{len(futures)} episodes, {elapsed / 60:.1f} min elapsed", flush=True)
    print(json.dumps({"episodes": done, "minutes": round((time.perf_counter() - started) / 60, 1), "out": str(out_dir)}))


def _relabel_all(out_dir: Path, workers: int):
    paths = sorted(str(p) for p in (out_dir / "episodes").glob("*.npz") if not p.name.endswith(".tmp.npz"))
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for done, _ in enumerate(as_completed([pool.submit(_relabel_worker, p) for p in paths]), 1):
            if done % 50 == 0 or done == len(paths):
                print(f"relabelled {done}/{len(paths)}, {(time.perf_counter() - started) / 60:.1f} min", flush=True)
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["label_code"] = label_code_fingerprint()
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
