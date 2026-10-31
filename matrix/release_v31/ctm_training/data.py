"""Training data for the three node brains: packed arrays, normal statistics, and episode streams.

``pack`` turns the scenario dataset (one .npz per episode) into flat per-brain arrays,
one set per split, so a training run loads its whole split into RAM in seconds:

    packed/<brain>/<split>_values.npy      float16 [N, F]   the exact CTM input values
    packed/<brain>/<split>_validity.npy    uint8   [N, F]
    packed/<brain>/<split>_twin.npy        float16 [N, O]   the fault-free twin's own numeric readings
    packed/<brain>/<split>_twin_valid.npy  uint8   [N, O]
    packed/<brain>/<split>_labels.npy      int8    [N, 5]   assessment, diagnosis, action, communication, source
    packed/<brain>/<split>_rule.npy        int8    [N, 4]   the rule brain's outputs (baseline)
    packed/<brain>/<split>_tod.npy         float32 [N]      hour of day
    packed/<brain>/<split>_index.json      one row per (episode, node): start row, length, family
    packed/<brain>/stats.npz               mean/std of every feature over labelled-NORMAL training ticks

The incubator brain gets both incubators' rows (one model, trained on both).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np

from farm_poc.nodes.contracts import MACHINE_TYPES
from farm_poc.nodes.encoders import NodeEncoderV1

BRAINS = {"incubator": ("incubator-1", "incubator-2"), "hvac": ("hvac",), "animal_protection": ("animal-protection",)}
SPLITS = ("train", "val", "test", "novel_test", "normal_day_test")
WATCHING, DANGER = 2, 4
# The node's own notes: memory inputs that exist only because this node already decided something (an
# open incident, a notification, a call, an action, a recovery). The dataset was recorded with the rule
# brains driving, so there they carry the *rules'* decisions: a brain can learn "incident open, so escalate"
# instead of reading the evidence, and then stays silent in the live farm, where the notes are its own.
# B2 hides them on part of the training streams (encoded value 0 = a node with no incident, still valid).
# Kept: sensor_lost_while_hot and water_dry_pumping_min, which come from the readings, not from decisions.
NOTE_FEATURES = tuple(f"memory.{name}" for name in (
    "incident_active", "incident_age_h", "recurrence_24h", "handled_by_machine", "peers_same_incident",
    "owner_notified", "owner_acknowledged", "fire_brigade_called", "civil_protection_called",
    "protective_action_taken", "recovered_recently", "minutes_since_owner_notified", "owner_notified_priority"))


class Layout:
    """Which encoder columns are numbers, flags, own-machine numbers (the world-model targets), and notes."""

    def __init__(self, brain: str):
        encoder = NodeEncoderV1(BRAINS[brain][0])
        self.names, self.kinds = encoder.feature_names, encoder.feature_kinds
        self.schema_hash = encoder.schema_hash
        self.numeric = np.array([i for i, k in enumerate(self.kinds) if k == "number"])
        self.flags = np.array([i for i, k in enumerate(self.kinds) if k != "number"])
        self.own_numeric = np.array([i for i, (n, k) in enumerate(zip(self.names, self.kinds))
                                     if k == "number" and n.startswith("own.")])
        self.notes = np.array([i for i, n in enumerate(self.names) if n in NOTE_FEATURES])
        # a neighbour's broadcast says it is alarming (its WATCHING / ESCALATE / DANGER flag)
        self.peer_alarm = np.array([i for i, n in enumerate(self.names) if n.startswith("peer.")
                                    and n.split(".")[-1] in ("assessment=WATCHING", "assessment=ESCALATE_TO_USER",
                                                             "assessment=DANGER")])
        self.n_features = len(self.names)


def without_notes(split: "Split", layout: Layout) -> "Split":
    """A copy of ``split`` whose inputs never show the node's own notes: the brain judged on evidence alone,
    as it is in the live farm before its own first decision (B2's validation)."""
    hidden = copy.copy(split)
    hidden.arrays = dict(split.arrays)
    hidden.arrays["values"] = split.arrays["values"].copy()
    hidden.arrays["values"][:, layout.notes] = 0
    return hidden


def _episode_rows(data, meta, node, layout):
    key = node.replace("-", "_")
    values = data[f"{key}__values"]
    validity = np.unpackbits(data[f"{key}__validity"], axis=1)[:, :layout.n_features]
    if f"{key}__twin_values" in data:
        twin = data[f"{key}__twin_values"][:, layout.own_numeric]
        twin_valid = np.unpackbits(data[f"{key}__twin_validity"], axis=1)[:, :layout.n_features][:, layout.own_numeric]
    else:  # no fault: the healthy reading is the reading itself
        twin, twin_valid = values[:, layout.own_numeric], validity[:, layout.own_numeric]
    labels = np.stack([data[f"{key}__label_{n}"] for n in ("assessment", "diagnosis", "action", "communication", "source")], axis=1)
    hours = (float(meta["spec"]["config"].get("weather", {}).get("start_hour", 0.0))
             + np.arange(values.shape[0], dtype=np.float64) / 3600.0) % 24.0
    return {"values": values, "validity": validity, "twin": twin, "twin_valid": twin_valid,
            "labels": labels.astype(np.int8), "rule": data[f"{key}__rule"].astype(np.int8), "tod": hours.astype(np.float32)}


ARRAYS = ("values", "validity", "twin", "twin_valid", "labels", "rule", "tod")


def _pack_split(episodes, nodes, layout, target: Path, split: str) -> None:
    """Writes the arrays straight to disk episode by episode (a farm-year does not fit in RAM). The episode
    lengths come from the metadata, so one pass suffices; the files are ordinary .npy files."""
    present = []
    for folder, episode_id in episodes:
        if (folder / f"{episode_id}.npz").exists():
            present.append((folder, episode_id, json.loads((folder / f"{episode_id}.json").read_text(encoding="utf-8"))))
    if not present:
        return
    total = sum(int(meta["ticks"]) for _, _, meta in present) * len(nodes)
    own = len(layout.own_numeric)
    shapes = {"values": ((total, layout.n_features), np.float16), "validity": ((total, layout.n_features), np.uint8),
              "twin": ((total, own), np.float16), "twin_valid": ((total, own), np.uint8), "labels": ((total, 5), np.int8),
              "rule": ((total, 4), np.int8), "tod": ((total,), np.float32)}
    out = {k: np.lib.format.open_memmap(target / f"{split}_{k}.npy", mode="w+", dtype=dtype, shape=shape)
           for k, (shape, dtype) in shapes.items()}
    index, start = [], 0
    for folder, episode_id, meta in present:
        data = np.load(folder / f"{episode_id}.npz")
        for node in nodes:
            part = _episode_rows(data, meta, node, layout)
            length = part["values"].shape[0]
            for k, v in part.items():
                out[k][start:start + length] = v
            index.append({"episode_id": episode_id, "node": node, "family": meta["family"], "start": start, "length": length})
            start += length
    for array in out.values():
        array.flush()
    (target / f"{split}_index.json").write_text(json.dumps(index), encoding="utf-8")


def pack(dataset: Path, out: Path) -> None:
    """Pack every split of every brain from the scenario dataset (skips brains already packed)."""
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    for brain, nodes in BRAINS.items():
        target = out / brain
        if (target / "stats.npz").exists():
            continue
        target.mkdir(parents=True, exist_ok=True)
        layout = Layout(brain)
        for split in SPLITS:
            episodes = [(dataset / "episodes", e["episode_id"]) for e in manifest["episodes"] if e["split"] == split]
            _pack_split(episodes, nodes, layout, target, split)
        _write_stats(target, layout)


def pack_folder(folder: Path, out: Path, split: str = "train") -> None:
    """Pack every episode in a folder (practice rounds, exam runs) as one split of every brain."""
    for brain, nodes in BRAINS.items():
        target = out / brain
        if (target / f"{split}_index.json").exists():
            continue
        target.mkdir(parents=True, exist_ok=True)
        episodes = [(folder, p.stem) for p in sorted(folder.glob("*.json"))]
        _pack_split(episodes, nodes, Layout(brain), target, split)


def _write_stats(target: Path, layout: Layout) -> None:
    """Mean and spread of every feature over training ticks labelled NORMAL: "what normal looks like"."""
    values = np.load(target / "train_values.npy", mmap_mode="r")
    validity = np.load(target / "train_validity.npy", mmap_mode="r")
    labels = np.load(target / "train_labels.npy", mmap_mode="r")
    normal = np.flatnonzero(labels[:, 0] == 0)
    sample = normal[np.linspace(0, len(normal) - 1, min(len(normal), 400_000)).astype(np.int64)]
    v = values[sample].astype(np.float64)
    ok = validity[sample].astype(bool)
    count = ok.sum(axis=0).clip(min=1)
    mean = np.where(ok, v, 0.0).sum(axis=0) / count
    std = np.sqrt(np.where(ok, (v - mean) ** 2, 0.0).sum(axis=0) / count)
    np.savez(target / "stats.npz", mean=mean.astype(np.float32), std=np.maximum(std, 2e-3).astype(np.float32),
             schema_hash=np.array(layout.schema_hash))


class Split:
    """One split of one brain, loaded into RAM."""

    def __init__(self, packed: Path, brain: str, split: str, extra=(), limit=None, max_length=None, mmap=False):
        """``extra``: more packed folders whose same-named split is appended (practice rounds).
        ``limit``: keep only that many (episode, node) rows, for smoke tests.
        ``max_length``: keep only episodes up to that many ticks (fast validation; the exam keeps all).
        ``mmap``: read the big arrays from disk as needed instead of loading them (the farm-life year)."""
        parts = [packed / brain] + [Path(p) / brain for p in extra]
        parts = [p for p in parts if (p / f"{split}_index.json").exists()]
        if mmap and len(parts) > 1:
            raise ValueError("mmap reads one packed folder")
        self.index, arrays, offset = [], {k: [] for k in ARRAYS}, 0
        for folder in parts:
            for row in json.loads((folder / f"{split}_index.json").read_text(encoding="utf-8")):
                self.index.append({**row, "start": row["start"] + offset})
            for k in ARRAYS:
                arrays[k].append(np.load(folder / f"{split}_{k}.npy",
                                         mmap_mode="r" if mmap and k in ("values", "validity", "twin", "twin_valid") else None))
            offset += arrays["values"][-1].shape[0]
        self.arrays = {k: (v[0] if len(v) == 1 else np.concatenate(v)) for k, v in arrays.items()}
        if max_length:
            self.index = [row for row in self.index if row["length"] <= max_length]
        if limit:
            self.index = self.index[:limit]
        self.harm = _seconds_to_danger(self.arrays["labels"][:, 0], self.index)
        self.onset_weight = _onset_weights(self.arrays["labels"][:, 0], self.index)

    def __len__(self):
        return len(self.index)


def _seconds_to_danger(assessment, index, cap=1800.0):
    """Per tick: seconds until the labels next say DANGER in the same episode (``cap`` if never)."""
    out = np.full(assessment.shape, cap, dtype=np.float32)
    for row in index:
        a = assessment[row["start"]:row["start"] + row["length"]]
        nxt, seen = np.full(a.shape, np.inf), np.inf
        for t in range(len(a) - 1, -1, -1):
            if a[t] == DANGER:
                seen = t
            nxt[t] = seen - t
        out[row["start"]:row["start"] + row["length"]] = np.minimum(nxt, cap)
    return out


def _onset_weights(assessment, index, window=120, boost=3.0):
    """The first two minutes after each labelled alarm starts weigh more: that is where speed is won."""
    weight = np.ones(assessment.shape, dtype=np.float32)
    for row in index:
        a = assessment[row["start"]:row["start"] + row["length"]] >= WATCHING
        starts = np.flatnonzero(a & ~np.concatenate([[False], a[:-1]]))
        for s in starts:
            weight[row["start"] + s:row["start"] + min(row["length"], s + window)] = boost
    return weight


def quiet_ticks(assessment, index):
    """Per tick: True where the labels say NORMAL outside every incident window, exactly the ticks where
    ``metrics.score_node`` counts any alarm as false (B2's quiet-time penalty targets these)."""
    from farm_poc.scenarios.metrics import EARLY_TOLERANCE, LATE_TOLERANCE, runs
    quiet = assessment == 0
    for row in index:
        a = assessment[row["start"]:row["start"] + row["length"]]
        for start, end in runs(a >= WATCHING):
            lo, hi = max(0, start - EARLY_TOLERANCE), min(len(a), end + 1 + LATE_TOLERANCE)
            quiet[row["start"] + lo:row["start"] + hi] = False
    return quiet


class Streams:
    """``batch`` parallel streams walking through episodes chunk by chunk, as a live node does.

    Each stream keeps its recurrent state between chunks. ``from_start``: probability that a new
    episode is entered at tick 0 (state built from the very beginning); otherwise at a random tick,
    with the first ``warmup`` ticks excluded from the loss while the state settles.
    Episodes with any labelled alarm are drawn ``alarm_weight`` times as often as quiet ones, and
    episodes with a labelled DANGER ``danger_weight`` times: dangers are rare and must not be missed.
    ``hide_notes``: probability that a stream walks its episode with the node's own notes hidden
    (``NOTE_FEATURES``); ``last_hidden`` says which streams of the last chunk had them hidden.
    """

    def __init__(self, split: Split, batch: int, chunk: int, rng: np.random.Generator, from_start=0.3,
                 warmup=64, alarm_weight=3.0, danger_weight=10.0, hide_notes=0.0):
        self.split, self.batch, self.chunk, self.rng = split, batch, chunk, rng
        self.from_start, self.warmup = from_start, warmup
        self.hide_notes = hide_notes
        self.hidden = np.zeros(batch, dtype=bool)
        self.last_hidden = self.hidden.copy()
        a = split.arrays["labels"][:, 0]
        weights = np.array([danger_weight if (a[r["start"]:r["start"] + r["length"]] == DANGER).any() else
                            alarm_weight if (a[r["start"]:r["start"] + r["length"]] > 0).any() else 1.0
                            for r in split.index])
        self.p = weights / weights.sum()
        self.episode = np.zeros(batch, dtype=np.int64)
        self.pos = np.zeros(batch, dtype=np.int64)
        self.settle = np.zeros(batch, dtype=np.int64)  # ticks of warm-up left
        self.fresh = np.ones(batch, dtype=bool)
        for b in range(batch):
            self._enter(b)

    def _enter(self, b):
        e = int(self.rng.choice(len(self.p), p=self.p))
        length = self.split.index[e]["length"]
        start_at_zero = self.rng.random() < self.from_start or length <= self.chunk
        self.episode[b] = e
        self.pos[b] = 0 if start_at_zero else int(self.rng.integers(0, max(1, length - self.chunk)))
        self.settle[b] = 0 if start_at_zero else self.warmup
        self.fresh[b] = True
        if self.hide_notes:   # drawn only when used, so runs without it keep their random sequence
            self.hidden[b] = self.rng.random() < self.hide_notes

    def next(self):
        """Global row indices [B, L], a loss mask [B, L], and which streams start a new episode (state reset)."""
        rows = np.zeros((self.batch, self.chunk), dtype=np.int64)
        mask = np.zeros((self.batch, self.chunk), dtype=np.float32)
        reset = self.fresh.copy()
        self.fresh[:] = False
        self.last_hidden = self.hidden.copy()   # a whole episode walk keeps one setting: it is a state of mind
        for b in range(self.batch):
            info = self.split.index[self.episode[b]]
            offsets = self.pos[b] + np.arange(self.chunk)
            inside = offsets < info["length"]
            rows[b] = info["start"] + np.minimum(offsets, info["length"] - 1)
            live = inside.copy()
            if self.settle[b] > 0:
                quiet = min(self.settle[b], self.chunk)
                live[:quiet] = False
                self.settle[b] -= quiet
            mask[b] = live
            self.pos[b] += self.chunk
            if self.pos[b] >= info["length"]:
                self._enter(b)   # the next chunk starts a new episode with a reset state
        return rows, mask, reset
