"""The decision layer between a brain's assessment head and what Jarvis says (B1, no retraining).

The first v2 brains caught faults as well as the rules but raised 12-50 false "watching" alarms per
node-day, against the rules' 1-2. Two causes sit in training, one in decoding:

- the loss weights rare classes up (``train._class_weight``) and alarm episodes are drawn 3-10x as often
  as quiet ones (``data.Streams``): the brain learned that alarms are far more common than they are;
- ``model.decode`` took a plain per-tick argmax, so one flickering tick outside an incident is a new
  false alarm.

The layer fixes both at inference, the way plant alarm systems do (ISA-18.2):

1. **Prior correction** (post-hoc logit adjustment): ``logits - tau * log(effective training prior /
   real label prior)``. ``tau`` = 0 changes nothing; 1 undoes the training distortion exactly.
2. **Ordinal threshold:** a level L >= WATCHING is proposed only when P(level >= L) >= ``theta``.
3. **On-delay:** WATCHING and ESCALATE are raised only after being proposed ``k`` ticks in a row
   (ESCALATE at most 10, so a real escalation is never slowed by more than 10 s). DANGER has none.
4. **Off-delay (hold):** a raised level is held for the labels' own hold time (60 / 120 / 300 s), so a
   flicker stays one event, as the labels teach.

The settings are tuned per brain on the validation split (``tune``) and saved as ``decide.json`` next to
the checkpoint. ``Decider.step`` is the live, causal form; ``Decider.levels`` the same rule vectorised
over recorded logits (tested equal).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import itertools
import json
from pathlib import Path

import numpy as np
import torch

from .data import _onset_weights

NORMAL, HANDLED, WATCHING, ESCALATE, DANGER = range(5)
HOLD_S = {WATCHING: 60, ESCALATE: 120, DANGER: 300}   # the labels' own holds (scenarios/labels.py)
MAX_ESCALATE_DELAY = 10


@dataclass
class Decider:
    tau: float = 0.0
    theta: float = 0.5
    k_watch: int = 1
    k_escalate: int = 1
    correction: list = field(default_factory=lambda: [0.0] * 5)   # log(effective training prior / real prior)
    hold: bool = True

    def __post_init__(self):
        if self.k_escalate > MAX_ESCALATE_DELAY:
            raise ValueError(f"ESCALATE on-delay is capped at {MAX_ESCALATE_DELAY} ticks")

    # ------------------------------------------------------------ shared pieces
    def _k(self, level):
        return {WATCHING: self.k_watch, ESCALATE: self.k_escalate, DANGER: 1}[level]

    def _hold(self, level):
        return HOLD_S[level] if self.hold else 1

    def proposals(self, logits: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """[N, 5] assessment logits -> (proposed level, calm level NORMAL/HANDLED), before on/off delays."""
        adjusted = logits - self.tau * np.asarray(self.correction, dtype=np.float64)
        p = np.exp(adjusted - adjusted.max(-1, keepdims=True))
        p /= p.sum(-1, keepdims=True)
        at_least = np.cumsum(p[:, ::-1], axis=1)[:, ::-1]           # P(level >= L)
        base = np.where(p[:, HANDLED] > p[:, NORMAL], HANDLED, NORMAL)
        out = base.copy()
        for level in (WATCHING, ESCALATE, DANGER):
            out = np.where(at_least[:, level] >= self.theta, level, out)
        return out, base

    # ------------------------------------------------------------ offline (tuning, scoring)
    def levels(self, logits: np.ndarray, starts: np.ndarray) -> np.ndarray:
        """Decided levels for recorded logits [N, 5]; ``starts``: bool [N], True at each episode's first tick."""
        return self.from_proposals(*self.proposals(logits), starts)

    def from_proposals(self, proposed: np.ndarray, base: np.ndarray, starts: np.ndarray) -> np.ndarray:
        idx = np.arange(proposed.size)
        episode_start = np.maximum.accumulate(np.where(starts, idx, 0))
        out = base.copy()
        for level in (WATCHING, ESCALATE, DANGER):
            on = proposed >= level
            last_off = np.maximum.accumulate(np.where(~on, idx, -1))          # last tick not proposing >= level
            streak = idx - np.maximum(last_off, episode_start - 1)
            confirmed = on & (streak >= self._k(level))
            seen = np.cumsum(confirmed)
            window_start = np.maximum(idx - self._hold(level) + 1, episode_start)
            before = np.where(window_start > 0, seen[np.maximum(window_start - 1, 0)], 0)
            held = (seen - before) > 0
            out = np.where(held, level, out)
        return out

    # ------------------------------------------------------------ live (causal, batched)
    def initial_state(self, batch: int) -> dict:
        return {"streak": np.zeros((batch, 5), np.int64), "since": np.full((batch, 5), 10 ** 9, np.int64)}

    def step(self, logits, state: dict):
        """One tick for a batch: logits [B, 5] (tensor or array) -> (levels [B], state)."""
        if isinstance(logits, torch.Tensor):
            logits = logits.detach().float().cpu().numpy()
        proposed, out = self.proposals(np.asarray(logits, dtype=np.float64))
        streak, since = state["streak"].copy(), state["since"] + 1
        for level in (WATCHING, ESCALATE, DANGER):
            on = proposed >= level
            streak[:, level] = np.where(on, streak[:, level] + 1, 0)
            confirmed = on & (streak[:, level] >= self._k(level))
            since[:, level] = np.where(confirmed, 0, since[:, level])
            out = np.where(since[:, level] < self._hold(level), level, out)
        return out, {"streak": streak, "since": since}

    # ------------------------------------------------------------ files
    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Decider":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def for_checkpoint(cls, checkpoint) -> "Decider | None":
        path = Path(checkpoint).with_name("decide.json")
        return cls.load(path) if path.exists() else None


def head_choices(out) -> np.ndarray:
    """The non-assessment heads' choices, before any level gating: [B, 4] int8."""
    diagnosis = out["diagnosis"].clone()
    diagnosis[:, 0] = -1e9                      # NONE is implied by NORMAL, never chosen otherwise
    picks = [diagnosis.argmax(-1), out["action"].argmax(-1), out["communication"].argmax(-1), out["source"].argmax(-1)]
    return torch.stack(picks, -1).cpu().numpy().astype(np.int8)


@torch.no_grad()
def record(brain, split, device="cpu", group=128, chunk=512, hide_notes=False):
    """Run a brain over a split from tick 0, as deployed, keeping the raw assessment logits.

    Returns {"logits" [M, 5], "heads" [M, 4], "starts" [M], "offsets" [len(split.index)]}: the rows of
    ``split.index`` laid end to end, so any Decider can be scored on them without re-running the brain.
    ``hide_notes``: the brain never sees its memory notes (``data.NOTE_FEATURES``), as in the farm-life design."""
    brain.eval()
    lengths = np.array([info["length"] for info in split.index])
    offsets = np.concatenate([[0], np.cumsum(lengths)[:-1]])
    logits = np.zeros((lengths.sum(), 5), np.float32)
    heads = np.zeros((lengths.sum(), 4), np.int8)
    order = sorted(range(len(split.index)), key=lambda i: split.index[i]["length"])
    a = split.arrays
    for g in range(0, len(order), group):
        members = order[g:g + group]
        infos = [split.index[i] for i in members]
        longest = max(info["length"] for info in infos)
        state = brain.initial_state(len(members), device)
        lg = np.zeros((len(members), longest, 5), np.float32)
        hd = np.zeros((len(members), longest, 4), np.int8)
        for t0 in range(0, longest, chunk):
            span = min(chunk, longest - t0)
            rows = np.stack([info["start"] + np.minimum(np.arange(t0, t0 + span), info["length"] - 1) for info in infos])
            values = torch.tensor(a["values"][rows], dtype=torch.float32, device=device)
            if hide_notes:
                values[:, :, brain.layout.notes] = 0.0
            validity = torch.tensor(a["validity"][rows], dtype=torch.float32, device=device)
            tod = torch.tensor(a["tod"][rows], dtype=torch.float32, device=device)
            for t in range(span):
                out, state = brain.adaptive_tick(values[:, t], validity[:, t], tod[:, t], state)
                lg[:, t0 + t] = out["assessment"].float().cpu().numpy()
                hd[:, t0 + t] = head_choices(out)
        for slot, i in enumerate(members):
            n = lengths[i]
            logits[offsets[i]:offsets[i] + n] = lg[slot, :n]
            heads[offsets[i]:offsets[i] + n] = hd[slot, :n]
    starts = np.zeros(lengths.sum(), bool)
    starts[offsets] = True
    brain.train()
    return {"logits": logits, "heads": heads, "starts": starts, "offsets": offsets}


def split_predictions(decisions: np.ndarray, recorded: dict, split) -> dict:
    """Flat decisions [M, 5] -> {row index in split.index: [T, 5]}, the form ``score.evaluate`` takes."""
    return {i: decisions[o:o + info["length"]] for i, (o, info) in enumerate(zip(recorded["offsets"], split.index))}


def apply(decided: np.ndarray, heads: np.ndarray) -> np.ndarray:
    """Decided levels [N] + unconditional head argmaxes [N, 4] (diagnosis without NONE, action,
    communication, source) -> [N, 5] decisions with ``model.decode``'s consistency rules."""
    level = decided.astype(np.int64)
    out = np.zeros((level.size, 5), dtype=np.int8)
    out[:, 0] = level
    out[:, 1] = np.where(level > NORMAL, heads[:, 0], 0)
    out[:, 2] = np.where(level == DANGER, heads[:, 1], 0)
    out[:, 3] = np.where(level >= ESCALATE, heads[:, 2], 0)
    out[:, 4] = np.where(level > NORMAL, heads[:, 3], 0)
    return out


def prior_correction(packed: Path, brain: str, alarm_weight=3.0, danger_weight=10.0, class_power=-0.5,
                     danger_extra=2.0) -> list[float]:
    """log(effective training prior / real prior) per assessment class.

    The effective prior is what the loss saw: label shares weighted by episode sampling
    (``Streams``), the onset boost (``_onset_weights``) and the class weights (``_class_weight``)."""
    from .train import ASSESSMENT_PRIOR
    labels = np.load(Path(packed) / brain / "train_labels.npy", mmap_mode="r")[:, 0].astype(np.int64)
    index = json.loads((Path(packed) / brain / "train_index.json").read_text(encoding="utf-8"))
    onset = _onset_weights(labels, index)
    real, effective = np.zeros(5), np.zeros(5)
    for row in index:
        a = labels[row["start"]:row["start"] + row["length"]]
        w = danger_weight if (a == DANGER).any() else alarm_weight if (a > 0).any() else 1.0
        real += np.bincount(a, minlength=5)
        effective += w * np.bincount(a, weights=onset[row["start"]:row["start"] + row["length"]], minlength=5)
    class_weight = ASSESSMENT_PRIOR ** class_power
    class_weight[DANGER] *= danger_extra
    effective *= class_weight
    real, effective = real / real.sum(), effective / effective.sum()
    return np.log(np.maximum(effective, 1e-9) / np.maximum(real, 1e-9)).round(4).tolist()


GRID = {"tau": (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5), "theta": (0.5, 0.6, 0.7, 0.8, 0.9),
        "k_watch": (1, 5, 10, 20, 30), "k_escalate": (1, 3, 5, 10)}


def tune(logits, heads, starts, score_fn, correction, raw_summary, escalate_recall_floor=None, log=print):
    """Grid search on validation. ``score_fn(decisions [N, 5]) -> evaluate()`` dict.

    Constraints: DANGER and ESCALATE recall no lower than the raw brain's (``raw_summary``), and at
    least ``escalate_recall_floor`` when given. Returns (best Decider, its evaluation, every row)."""
    floors = {lv: raw_summary[lv]["recall"] for lv in ("ESCALATE_TO_USER", "DANGER")}
    if escalate_recall_floor is not None:
        floors["ESCALATE_TO_USER"] = max(floors["ESCALATE_TO_USER"] or 0.0, escalate_recall_floor)
    best, rows = None, []
    for tau, theta in itertools.product(GRID["tau"], GRID["theta"]):
        proposed, base = Decider(tau=tau, theta=theta, correction=correction).proposals(logits)
        for k_watch, k_escalate in itertools.product(GRID["k_watch"], GRID["k_escalate"]):
            decider = Decider(tau, theta, k_watch, k_escalate, correction)
            result = score_fn(apply(decider.from_proposals(proposed, base, starts), heads))
            s = result["summary"]
            ok = all(floor is None or s[lv]["recall"] is None or s[lv]["recall"] >= floor - 1e-9
                     for lv, floor in floors.items())
            rows.append({**asdict(decider), "score": result["score"], "ok": ok})
            if ok and (best is None or result["score"] > best[1]["score"]):
                best = (decider, result)
        log(f"  tuned tau {tau} theta {theta}: best so far {best[1]['score'] if best else None}")
    return best[0] if best else Decider(correction=correction), best[1] if best else None, rows
