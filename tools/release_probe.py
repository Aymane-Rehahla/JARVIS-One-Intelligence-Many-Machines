"""Find exactly which code the trained brains need (P3 step 2, decision 0015).

Run it with the training's Python and `-B` (no .pyc is written anywhere), giving the release folder and the upstream
CTM folder on the command line; nothing here names them. It builds the brains the two ways JARVIS uses them, from the
release's own code:
  - the exam's harness: `farm_poc.scenarios.episode.build` with a `ctm_training.practice.CheckpointBrains` factory
    (decide=True, hide_notes=True, hold_calls=True; one torch thread);
  - the release README's live recipe: `TrainedNodeBrain.from_checkpoint` per node and a `NodeRuntime` whose nodes keep
    a `CognitiveMemory` (CMM) in a temporary folder;
steps each for TICKS farm ticks, and writes the list of every module file it imported and every file it opened under
`code_snapshot/` and under the upstream folder, as paths relative to those two roots, to --out (JSON).
The upstream folder is put on sys.path first, the way `ctm_training/model.py` does with its own path.

Usage: <the training's Python> -B tools/release_probe.py --release DIR --upstream DIR --out FILE [--ticks N]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

TICKS = 30
FAMILY = "heater_relay_stuck"                   # any ordinary library spec will do; a faulted one runs more code
VARIANT = 9


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ticks", type=int, default=TICKS)
    args = parser.parse_args(argv)
    if not sys.flags.dont_write_bytecode:
        print("run this with -B: no .pyc may be written into the release or the training's folders", file=sys.stderr)
        return 2
    snapshot = (args.release / "code_snapshot").resolve()
    upstream = args.upstream.resolve()
    roots = {"code_snapshot": snapshot, "upstream": upstream}
    opened: set[tuple[str, str]] = set()

    def relative(path: object) -> tuple[str, str] | None:
        try:
            resolved = Path(str(path)).resolve()
        except (OSError, ValueError):
            return None
        for name, root in roots.items():
            if resolved.is_relative_to(root):
                return name, resolved.relative_to(root).as_posix()
        return None

    def audit(event: str, event_args: tuple[object, ...]) -> None:
        if event == "open" and event_args and isinstance(event_args[0], (str, Path)):
            found = relative(event_args[0])
            if found is not None:
                opened.add(found)
    sys.addaudithook(audit)
    sys.path.insert(0, str(upstream))
    sys.path.insert(0, str(snapshot))

    import torch
    torch.set_num_threads(1)
    from ctm_training.fix_loop import ENGINES
    from ctm_training.live import TrainedNodeBrain
    from ctm_training.practice import CheckpointBrains
    from farm_poc.nodes.cmm import CognitiveMemory
    from farm_poc.nodes.contracts import MACHINE_TYPES, NODE_IDS
    from farm_poc.nodes.runtime import NodeRuntime
    from farm_poc.matrix.engine import MatrixEngine
    from farm_poc.scenarios.episode import build
    from farm_poc.scenarios.library import _make

    brains_dir = args.release / "brains"
    checkpoints = {machine: brains_dir / machine / name for machine, name in ENGINES.items()}
    spec = _make(FAMILY, VARIANT, False)
    factory = CheckpointBrains(checkpoints, brains_dir, decide=True, hide_notes=True, hold_calls=True)
    _, runtime = build(spec, with_faults=True, brain_factory=factory)
    for _ in range(args.ticks):
        runtime.step()

    start_hour = float(spec.config.get("weather", {}).get("start_hour", 0.0))
    with tempfile.TemporaryDirectory() as folder:
        engine = MatrixEngine(seed=spec.seed, config=dict(spec.config))
        brains = {node: TrainedNodeBrain.from_checkpoint(checkpoints[MACHINE_TYPES[node]], brains_dir, start_hour,
                                                         hide_notes=True, hold_calls=True) for node in NODE_IDS}
        live = NodeRuntime(engine, modes={node: "real" for node in NODE_IDS}, brains=brains,
                           memory_factory=lambda node: CognitiveMemory(node, Path(folder) / f"{node}.sqlite"))
        for _ in range(args.ticks):
            live.step()
        for node in live.nodes.values():
            close = getattr(node.memory, "close", None)
            if callable(close):
                close()

    modules: set[tuple[str, str]] = set()
    for module in list(sys.modules.values()):
        found = relative(getattr(module, "__file__", None) or "")
        if found is not None:
            modules.add(found)
    files = {name: sorted({path for root, path in modules | opened if root == name}) for name in roots}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"family": FAMILY, "variant": VARIANT, "ticks": args.ticks,
                                    "torch": torch.__version__, "python": sys.version.split()[0], "files": files},
                                   indent=1), encoding="utf-8")
    print(f"{len(files['code_snapshot'])} files from code_snapshot, {len(files['upstream'])} from upstream")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
