"""Practice in the live Matrix, and the closed-loop exam.

Practice (DAgger): the trained brains run the farm themselves on fresh seeds. Every episode
and its fault-free twin are relabelled with the same label policy, so the brains learn from
situations their own decisions created (their own memory, their own notifications, their
own protective actions), which the rule-driven dataset could not show them.

Exam: held-out seeds, run twice: once with the rule brains, once with the trained brains,
each scored against labels from its own twin, plus the farm outcomes (calls, messages, welfare).
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
import copy
import dataclasses
from pathlib import Path
import random

import torch

from farm_poc.nodes.contracts import MACHINE_TYPES
from farm_poc.scenarios.build import build_episode
from farm_poc.scenarios.library import PLAN, EpisodeSpec, _make

from .decide import Decider
from .live import TrainedNodeBrain
from .train import load_brain


class CheckpointBrains:
    """Picklable factory: a fresh trained brain (own state) for every node of every run.

    ``decide``: use the tuned decision layer next to each checkpoint (False: the raw brain, as when
    recording the episodes that layer is tuned on). ``danger_net``: see ``TrainedNodeBrain``."""

    def __init__(self, checkpoints: dict, packed: str, decide: bool = True, danger_net: bool = False,
                 hide_notes: bool = False, hold_calls: bool = False, deciders: dict | None = None):
        self.checkpoints, self.packed = {k: str(v) for k, v in checkpoints.items()}, str(packed)
        self.decide, self.danger_net, self.hide_notes, self.hold_calls = decide, danger_net, hide_notes, hold_calls
        self.deciders = {k: str(v) for k, v in (deciders or {}).items()}   # machine -> decide.json kept elsewhere
        self._loaded = {}

    def __call__(self, node_id, spec):
        machine = MACHINE_TYPES[node_id]
        if machine not in self._loaded:
            torch.set_num_threads(1)
            self._loaded[machine] = load_brain(Path(self.checkpoints[machine]), Path(self.packed), "cpu")
        start_hour = float(spec.config.get("weather", {}).get("start_hour", 0.0))
        decider = None
        if self.decide:
            decider = (Decider.load(Path(self.deciders[machine])) if machine in self.deciders
                       else Decider.for_checkpoint(self.checkpoints[machine]))
        return TrainedNodeBrain(copy.deepcopy(self._loaded[machine]), start_hour, decider, self.danger_net,
                                self.hide_notes, self.hold_calls)


def specs(tag: str, first_variant: int, count: int, seed: int) -> list[EpisodeSpec]:
    """Fresh episodes from the same samplers as the library, families drawn in the library's proportions."""
    rng = random.Random(seed)
    families = [family for family, n in PLAN.items() for _ in range(n)]
    out = []
    for k in range(count):
        family = rng.choice(families)
        spec = _make(family, first_variant + k, False)
        spec.family = family
        spec.split = "train" if tag.startswith("practice") else "exam"
        spec.episode_id = f"{tag}-{family}-{first_variant + k:05d}"
        out.append(spec)
    return out


def _work(spec_dict, out_dir, factory):
    spec = EpisodeSpec(**spec_dict)
    build_episode(spec, Path(out_dir), brain_factory=factory)
    return spec.episode_id


def simulate(spec_list, out_dir: Path, factory, workers: int, log=print):
    """Build every episode (with its twin) with the given brains; already-built ones are skipped."""
    out_dir.mkdir(parents=True, exist_ok=True)
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_work, dataclasses.asdict(s), str(out_dir), factory) for s in spec_list]
        for future in as_completed(futures):
            future.result()
            done += 1
            if done % 20 == 0 or done == len(futures):
                log(f"  simulated {done}/{len(futures)} episodes in {out_dir.name}")
    return out_dir / "episodes"
