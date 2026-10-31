"""Train one brain configuration on its packed data.

Stability, learned the hard way in the first run (scores swung from -8 to +0.6 between checkpoints):
- a modest learning rate with warm-up and cosine decay;
- an exponential moving average (EMA) of the weights, which is what gets scored and saved;
- a run is judged on the mean of its last three validations (``score``), not one lucky snapshot;
  the best single checkpoint is still kept for use (``best_score``).
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import time

import numpy as np
import torch
from torch import nn

from .data import Layout, Split, Streams, quiet_ticks
from .model import BrainConfig, TestbedBrain, swap_glu
from .score import evaluate, predict

ASSESSMENT_PRIOR = np.array([0.72, 0.02, 0.13, 0.11, 0.02])  # training-split label shares (Phase 3 report)
DANGER_EXTRA = 2.0          # missing a danger costs more than any other mistake
HARM_CAP_S = 1800.0
EMA_DECAY = 0.998
SCORED_VALIDATIONS = 3
# B2, the fine-tune for the live farm:
# - false alarms: episodes at natural rates (DANGER still 3x: it must not be forgotten), flatter class
#   weights, and a penalty on any alarm where the scoring would count it false;
# - self-reliance: half the streams hide the node's own notes (``data.NOTE_FEATURES``), which in the
#   rule-driven recordings carry the rules' decisions; the brain must reach its verdict from the evidence.
B2_LOSS = {"alarm_weight": 1.0, "danger_weight": 3.0, "class_power": -0.25, "quiet_penalty": 1.0, "hide_notes": 0.5}
# Farm life: B2 plus the echo penalty: raising an alarm while a neighbour is alarming but this machine's own
# situation is normal (label NORMAL) costs extra. It corrects the habit of echoing a neighbour's alarm
# without evidence here; a neighbour whose problem really shows up here (a linked case) is not punished.
LIFE_LOSS = {**B2_LOSS, "echo_penalty": 2.0}


def make_brain(config: BrainConfig, packed: Path, device):
    stats = np.load(packed / config.machine / "stats.npz")
    brain = TestbedBrain(config, Layout(config.machine), {"mean": stats["mean"], "std": stats["std"]})
    if str(device).startswith("privateuseone"):  # DirectML: no native GLU kernel
        swap_glu(brain)
    return brain.to(device)


def _batch(split: Split, rows, device, quiet=None, hidden=None, notes=None, peer_alarm=None):
    """``hidden`` [B] bool: streams whose own notes (``notes`` columns) are blanked, as before a first decision.
    ``peer_alarm``: the neighbours' alarm-flag columns; adds ``peer_alarm`` [B, L], 1 where any neighbour alarms."""
    a = split.arrays
    take = lambda key, dtype=torch.float32: torch.tensor(a[key][rows], dtype=dtype, device=device)
    values = a["values"][rows].astype(np.float32)
    if hidden is not None and hidden.any():
        values[np.ix_(np.flatnonzero(hidden), np.arange(values.shape[1]), notes)] = 0.0
    batch = {"values": torch.tensor(values, device=device), "validity": take("validity"), "tod": take("tod"),
             "twin": take("twin"), "twin_valid": take("twin_valid"), "labels": take("labels", torch.long),
             "harm": torch.tensor(split.harm[rows], device=device), "weight": torch.tensor(split.onset_weight[rows], device=device)}
    if quiet is not None:
        batch["quiet"] = torch.tensor(quiet[rows], dtype=torch.float32, device=device)
    if hidden is not None:
        batch["notes_hidden"] = torch.tensor(hidden, dtype=torch.float32, device=device)
    if peer_alarm is not None:
        batch["peer_alarm"] = torch.tensor((values[:, :, peer_alarm] > 0.5).any(-1), dtype=torch.float32, device=device)
    return batch


def _own_values(brain, twin_own):
    """Place the twin's own-numeric readings into a full feature row so ``own_z`` can normalise them."""
    full = torch.zeros(twin_own.shape[0], brain.layout.n_features, device=twin_own.device)
    full[:, brain.own] = twin_own
    return full


def chunk_loss(brain, batch, mask, state, class_weight, steps, quiet_penalty=0.0, echo_penalty=0.0):
    """Loss over one chunk [B, L], carrying ``state``; returns (loss, state, logs).

    ``quiet_penalty`` (B2): extra -log(1 - P(alarm)) on ticks where any alarm would be scored false.
    ``echo_penalty`` (farm life): the same on ticks where a neighbour alarms but this node's label is NORMAL."""
    cfg = brain.config
    labels = batch["labels"]
    total = torch.zeros((), device=mask.device)
    logs = {"assessment": 0.0}
    chunk = mask.shape[1]
    ce = nn.functional.cross_entropy
    for t in range(chunk):
        out, state = brain.tick(batch["values"][:, t], batch["validity"][:, t], batch["tod"][:, t], state, steps)
        m = mask[:, t] * batch["weight"][:, t]
        a = labels[:, t, 0]
        if cfg.certainty_loss:   # Sakana: average the best internal step and the most certain one
            per_step = torch.stack([ce(s, a, weight=class_weight, reduction="none") for s in out["assessment_steps"]])
            probs = torch.softmax(out["assessment_steps"], -1)
            certainty = 1 + (probs * probs.clamp_min(1e-9).log()).sum(-1) / math.log(probs.shape[-1])
            chosen = per_step.gather(0, certainty.argmax(0, keepdim=True)).squeeze(0)
            assess = 0.5 * (per_step.min(0).values + chosen)
        else:
            assess = ce(out["assessment"], a, weight=class_weight, reduction="none")
        loss = assess
        if quiet_penalty and "quiet" in batch:
            p_alarm = torch.softmax(out["assessment"], -1)[:, 2:].sum(-1)
            loss = loss - quiet_penalty * batch["quiet"][:, t] * torch.log1p(-p_alarm.clamp(max=1 - 1e-6))
        if echo_penalty and "peer_alarm" in batch:
            p_alarm = torch.softmax(out["assessment"], -1)[:, 2:].sum(-1)
            echo = batch["peer_alarm"][:, t] * (a == 0).float()
            loss = loss - echo_penalty * echo * torch.log1p(-p_alarm.clamp(max=1 - 1e-6))
        alarm = (a > 0).float()
        # With its notes hidden a node cannot know what it already did (notified, called, acted): those
        # heads only learn where the notes are shown, as they always are once the node has decided live.
        seen = 1.0 - batch["notes_hidden"] if "notes_hidden" in batch else 1.0
        loss = loss + 0.5 * alarm * ce(out["diagnosis"], labels[:, t, 1], reduction="none")
        loss = loss + 0.5 * alarm * ce(out["source"], labels[:, t, 4], reduction="none")
        loss = loss + 0.5 * seen * (a == 4).float() * 2.0 * ce(out["action"], labels[:, t, 2], reduction="none")
        loss = loss + 0.5 * seen * (a >= 3).float() * ce(out["communication"], labels[:, t, 3], reduction="none")
        if "healthy" in out:
            target = brain.own_z(_own_values(brain, batch["twin"][:, t]))
            valid = batch["twin_valid"][:, t]
            loss = loss + 0.2 * ((out["healthy"] - target) ** 2 * valid).sum(1) / valid.sum(1).clamp(min=1)
        if "harm" in out:
            target = torch.log1p(batch["harm"][:, t]) / math.log1p(HARM_CAP_S)
            loss = loss + 0.1 * nn.functional.smooth_l1_loss(torch.sigmoid(out["harm"]), target, reduction="none")
        if "expected_next" in out and t + 1 < chunk:
            nxt = brain.own_z(batch["values"][:, t + 1])
            valid = batch["validity"][:, t + 1][:, brain.own] * mask[:, t + 1, None]
            loss = loss + 0.2 * ((out["expected_next"] - nxt) ** 2 * valid).sum(1) / valid.sum(1).clamp(min=1)
        total = total + (loss * m).sum()
        logs["assessment"] += float((assess * m).sum().detach())
    denom = mask.sum().clamp(min=1.0)
    logs = {k: v / float(denom) for k, v in logs.items()}
    return total / denom, state, logs


def _class_weight(device, power=-0.5):
    w = ASSESSMENT_PRIOR ** power
    w = w / (w * ASSESSMENT_PRIOR).sum()
    w[4] *= DANGER_EXTRA
    return torch.tensor(w, dtype=torch.float32, device=device)


def train(config: BrainConfig, packed: Path, out_dir: Path, *, device, steps=1500, batch=256, chunk=64, lr=7e-4,
          eval_every=300, seed=0, train_split=None, val_split=None, init_from=None, log=print,
          alarm_weight=3.0, danger_weight=10.0, class_power=-0.5, quiet_penalty=0.0, hide_notes=0.0, mix_split=None,
          echo_penalty=0.0):
    """Returns {"score" (mean of the last validations), "best_score", "summary", "checkpoint", ...};
    skipped when ``out_dir/result.json`` exists.

    The first four of the last five shape what the brain believes about how common alarms are;
    ``hide_notes`` makes it reach verdicts without its own notes (``data.NOTE_FEATURES``). The defaults
    are the selection runs'; ``B2_LOSS`` is the fine-tune's (false alarms and self-reliance).
    ``mix_split`` (farm life): half of every batch's streams walk it, the other half ``train_split``, a fixed
    50/50 mix however much calm data there is, so faults never become rare enough to ignore."""
    out_dir.mkdir(parents=True, exist_ok=True)
    done = out_dir / "result.json"
    if done.exists():
        return json.loads(done.read_text(encoding="utf-8"))
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    train_split = train_split or Split(packed, config.machine, "train")
    val_split = val_split or Split(packed, config.machine, "val")
    brain = make_brain(config, packed, device)
    if init_from:
        brain.load_state_dict(torch.load(init_from, map_location="cpu", weights_only=False)["state_dict"])
    ema = copy.deepcopy(brain).eval()
    for p in ema.parameters():
        p.requires_grad_(False)
    weight = _class_weight(device, class_power)
    optimiser = torch.optim.AdamW(brain.parameters(), lr=lr, weight_decay=1e-4)
    long_horizon = config.long_horizon
    sampling = dict(alarm_weight=alarm_weight, danger_weight=danger_weight, hide_notes=hide_notes)
    length = 128 if long_horizon else chunk
    half = batch // 2 if mix_split is not None else 0
    streams = Streams(train_split, batch - half, length, rng, from_start=1.0 if long_horizon else 0.3, **sampling)
    quiet = quiet_ticks(train_split.arrays["labels"][:, 0], train_split.index) if quiet_penalty else None
    if mix_split is not None:
        life = Streams(mix_split, half, length, rng, from_start=0.3, **sampling)
        life_quiet = quiet_ticks(mix_split.arrays["labels"][:, 0], mix_split.index) if quiet_penalty else None
    notes = brain.layout.notes if hide_notes else None
    peers = brain.layout.peer_alarm if echo_penalty else None
    state = brain.initial_state(batch, device)
    best, history, started = None, [], time.perf_counter()
    for step in range(1, steps + 1):
        lr_now = lr * min(1.0, step / 100) * (0.05 + 0.95 * 0.5 * (1 + math.cos(math.pi * step / steps)))
        for group in optimiser.param_groups:
            group["lr"] = lr_now
        rows, mask, reset = streams.next()
        data = _batch(train_split, rows, device, quiet, streams.last_hidden if hide_notes else None, notes, peers)
        if mix_split is not None:
            rows2, mask2, reset2 = life.next()
            more = _batch(mix_split, rows2, device, life_quiet, life.last_hidden if hide_notes else None, notes, peers)
            data = {k: torch.cat([data[k], more[k]]) for k in data}
            mask, reset = np.concatenate([mask, mask2]), np.concatenate([reset, reset2])
        reset_t = torch.tensor(reset, device=device)
        state = brain.reset_rows(brain.detach(state), brain.initial_state(batch, device), reset_t)
        thinking = int(rng.integers(config.calm_iterations, config.iterations + 1)) if config.adaptive else None
        loss, state, logs = chunk_loss(brain, data, torch.tensor(mask, device=device), state, weight, thinking,
                                       quiet_penalty, echo_penalty)
        optimiser.zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_(brain.parameters(), 1.0)
        optimiser.step()
        with torch.no_grad():
            for e, p in zip(ema.parameters(), brain.parameters()):
                e.mul_(EMA_DECAY).add_(p.detach(), alpha=1 - EMA_DECAY)
        if step % 50 == 0:
            elapsed = time.perf_counter() - started
            log(f"step {step}/{steps} loss {float(loss.detach()):.4f} assess {logs['assessment']:.4f} "
                f"{elapsed / 60:.1f} min (about {elapsed / step * (steps - step) / 3600:.1f} h left)")
        if step % eval_every == 0 or step == steps:
            result = evaluate(val_split, predict(ema, val_split, device))
            history.append({"step": step, "score": result["score"]})
            log(f"  validation at step {step}: score {result['score']} (link accuracy {result['link_accuracy']})")
            if best is None or result["score"] > best["best_score"]:
                best = {"best_score": result["score"], "summary": result["summary"], "link_accuracy": result["link_accuracy"],
                        "step": step}
                torch.save({"config": config.as_dict(), "state_dict": ema.state_dict(),
                            "schema_hash": Layout(config.machine).schema_hash}, out_dir / "best.pt")
    recent = [h["score"] for h in history[-SCORED_VALIDATIONS:]]
    result = {**best, "score": round(float(np.mean(recent)), 5), "checkpoint": str(out_dir / "best.pt"),
              "seconds": round(time.perf_counter() - started, 1), "node_ticks": steps * batch * streams.chunk,
              "history": history, "config": config.as_dict()}
    done.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return result


def load_brain(checkpoint: Path, packed: Path, device="cpu"):
    saved = torch.load(checkpoint, map_location="cpu", weights_only=False)
    config = BrainConfig(**saved["config"])
    brain = make_brain(config, packed, "cpu")
    brain.load_state_dict(saved["state_dict"])
    if str(device).startswith("privateuseone"):
        swap_glu(brain)
    return brain.to(device).eval()
