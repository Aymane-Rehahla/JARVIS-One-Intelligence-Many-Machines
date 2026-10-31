"""B1: tune the decision layer on validation for each finished brain, and write the comparison table.

    python -m ctm_training.b1 record --brain hvac --size xl          # one brain's raw outputs (slow part)
    python -m ctm_training.b1 tune                                   # every recorded brain: grid, decide.json, table

``record`` saves ``val_outputs.npz`` next to the checkpoint; ``tune`` never re-runs a brain.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

import numpy as np
import torch

from .data import Split
from .decide import Decider, apply, prior_correction, record, split_predictions, tune
from .score import evaluate, evaluate_many
from .train import load_brain

PACKED = Path(r"F:\TestbedRuns\ctm-packed-v3")
RUNS = Path(r"F:\TestbedRuns\ctm-training-v2\runs")
VAL_MAX_LENGTH = 4000
JOBS = (("incubator", "xl"), ("incubator", "large"), ("hvac", "xl"), ("animal_protection", "xl"))
LEVELS = ("WATCHING", "ESCALATE_TO_USER", "DANGER")


def _row(name, result, decider=None):
    s = result["summary"]
    row = {"run": name, "score": result["score"], "link": result.get("link_accuracy")}
    for lv in LEVELS:
        row[f"{lv}.false_per_day"] = s[lv]["false_per_day"]
        row[f"{lv}.recall"] = s[lv]["recall"]
    for lv in ("ESCALATE_TO_USER", "DANGER"):
        row[f"{lv}.delay_median_s"] = s[lv]["delay_median_s"]
        row[f"{lv}.delay_p90_s"] = s[lv]["delay_p90_s"]
    if decider:
        row["settings"] = f"tau {decider.tau} theta {decider.theta} k_watch {decider.k_watch} k_esc {decider.k_escalate}"
    return row


def tune_checkpoint(brain_name, checkpoint, packed, split, tag="val", loss=None, log=print):
    """Replay (once) and tune the decision layer for one checkpoint on ``split``.

    ``split``: the tuning episodes: rule-driven validation (``tag`` "val", the first B1), or closed-loop
    runs where the trained brains drove the farm themselves (the honest choice: the memory inputs are
    their own). ``packed``: the original packed folder (normal statistics, training priors).
    Writes ``<tag>_outputs.npz``, ``<tag>_b1.json`` and ``<tag>_grid.json`` next to the checkpoint, and
    leaves ``decide.json`` (what the live farm uses) set to this tuning: removed when no setting keeps
    the raw brain's ESCALATE and DANGER recall (the plain argmax is then used). Cached on re-runs.

    The result also says how often the replay agrees with the decisions recorded live in ``split``
    (meaningful when this checkpoint drove those episodes: 1.0 up to float16 storage of the inputs).
    ``loss``: the training settings the checkpoint was last trained with (for the prior correction)."""
    folder = Path(checkpoint).parent
    done = folder / f"{tag}_b1.json"
    if done.exists():
        result = json.loads(done.read_text(encoding="utf-8"))
    else:
        path = folder / f"{tag}_outputs.npz"
        if path.exists():
            recorded = dict(np.load(path))
        else:
            log(f"replaying {folder.name} on {tag} for the decision layer")
            recorded = record(load_brain(Path(checkpoint), Path(packed)), split)
            np.savez_compressed(path, **recorded)
        score_fn = lambda decisions: evaluate(split, split_predictions(decisions, recorded, split))
        raw_levels = recorded["logits"].argmax(-1)
        raw = score_fn(apply(raw_levels, recorded["heads"]))
        prior = {k: v for k, v in (loss or {}).items() if k in ("alarm_weight", "danger_weight", "class_power")}
        correction = prior_correction(Path(packed), brain_name, **prior)
        decider, best, grid = tune(recorded["logits"], recorded["heads"], recorded["starts"], score_fn, correction,
                                   raw["summary"], log=log)
        (folder / f"{tag}_grid.json").write_text(json.dumps(grid), encoding="utf-8")
        live = np.concatenate([split.arrays["rule"][info["start"]:info["start"] + info["length"], 0] for info in split.index])
        decided = decider.levels(recorded["logits"], recorded["starts"]) if best else raw_levels
        result = {"tag": tag, "raw": raw, "b1": best or raw, "decider": asdict(decider) if best else None,
                  "checkpoint": str(checkpoint), "ticks": int(live.size),
                  "replay_agrees_raw": round(float((raw_levels == live).mean()), 5),
                  "replay_agrees_b1": round(float((decided == live).mean()), 5)}
        done.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    if result["decider"]:
        Decider(**result["decider"]).save(folder / "decide.json")
    else:
        (folder / "decide.json").unlink(missing_ok=True)
    return result


def tune_joint(brain_name, checkpoint, packed, splits: dict, tag, loss=None, hide_notes=True, log=print):
    """``tune_checkpoint`` over several judging splits pooled ({name: Split}, e.g. library validation and the
    farm-life judging days), with the brain's memory notes hidden (the farm-life design). Also returns each
    split's own evaluation with the chosen layer, for the round guards. Cached by ``tag``; writes decide.json."""
    folder = Path(checkpoint).parent
    done = folder / f"{tag}_b1.json"
    if done.exists():
        result = json.loads(done.read_text(encoding="utf-8"))
    else:
        brain, recs = None, {}
        for name, split in splits.items():
            path = folder / f"{tag}_{name}_outputs.npz"
            if path.exists():
                recs[name] = dict(np.load(path))
            else:
                log(f"replaying {folder.name} on {name} ({tag}) for the decision layer")
                brain = brain or load_brain(Path(checkpoint), Path(packed))
                recs[name] = record(brain, split, hide_notes=hide_notes)
                np.savez_compressed(path, **recs[name])
        joined = {k: np.concatenate([recs[n][k] for n in splits]) for k in ("logits", "heads", "starts")}

        def pieces(decisions):
            out, start = [], 0
            for name, split in splits.items():
                n = len(recs[name]["logits"])
                out.append((name, split, split_predictions(decisions[start:start + n], recs[name], split)))
                start += n
            return out

        score_fn = lambda decisions: evaluate_many([(s, p) for _, s, p in pieces(decisions)])
        raw_levels = joined["logits"].argmax(-1)
        raw = score_fn(apply(raw_levels, joined["heads"]))
        prior = {k: v for k, v in (loss or {}).items() if k in ("alarm_weight", "danger_weight", "class_power")}
        decider, best, grid = tune(joined["logits"], joined["heads"], joined["starts"], score_fn,
                                   prior_correction(Path(packed), brain_name, **prior), raw["summary"], log=log)
        (folder / f"{tag}_grid.json").write_text(json.dumps(grid), encoding="utf-8")
        levels = decider.levels(joined["logits"], joined["starts"]) if best else raw_levels
        parts = {name: evaluate(split, preds) for name, split, preds in pieces(apply(levels, joined["heads"]))}
        result = {"tag": tag, "raw": raw, "b1": best or raw, "decider": asdict(decider) if best else None,
                  "parts": parts, "checkpoint": str(checkpoint), "hide_notes": hide_notes}
        done.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    if result["decider"]:
        Decider(**result["decider"]).save(folder / "decide.json")
    else:
        (folder / "decide.json").unlink(missing_ok=True)
    return result


def do_record(brain_name, size, threads):
    torch.set_num_threads(threads)
    folder = RUNS / brain_name / size
    split = Split(PACKED, brain_name, "val", max_length=VAL_MAX_LENGTH)
    started = time.time()
    recorded = record(load_brain(folder / "best.pt", PACKED), split)
    np.savez_compressed(folder / "val_outputs.npz", **recorded)
    print(f"{brain_name}/{size}: recorded {recorded['logits'].shape[0]} ticks in {(time.time() - started) / 60:.1f} min", flush=True)


def do_tune(out: Path):
    table, splits = {}, {}
    for brain_name, size in JOBS:
        folder = RUNS / brain_name / size
        if not (folder / "val_outputs.npz").exists():
            print(f"{brain_name}/{size}: not recorded yet, skipped")
            continue
        split = splits.setdefault(brain_name, Split(PACKED, brain_name, "val", max_length=VAL_MAX_LENGTH))
        rows = table.setdefault(brain_name, [_row("rule brain", evaluate(split))])
        started = time.time()
        result = tune_checkpoint(brain_name, folder / "best.pt", PACKED, split,
                                 log=lambda text: print(f"{brain_name}/{size}{text}", flush=True))
        rows.append(_row(f"{size} raw", result["raw"]))
        rows.append(_row(f"{size} + B1", result["b1"], Decider(**result["decider"])) if result["decider"] else
                    {"run": f"{size} + B1", "score": "no setting met the recall floors"})
        print(f"{brain_name}/{size}: tuned in {(time.time() - started) / 60:.1f} min", flush=True)
    out.write_text(json.dumps(table, indent=1), encoding="utf-8")
    lines = ["# B1 validation table (episodes up to 4,000 ticks)", ""]
    cols = ["run", "score", *[f"{lv}.false_per_day" for lv in LEVELS], *[f"{lv}.recall" for lv in LEVELS],
            "ESCALATE_TO_USER.delay_median_s", "ESCALATE_TO_USER.delay_p90_s", "DANGER.delay_median_s", "DANGER.delay_p90_s",
            "link", "settings"]
    for brain_name, rows in table.items():
        lines += [f"## {brain_name}", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        lines += ["| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows] + [""]
    out.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("record", "tune"))
    parser.add_argument("--brain")
    parser.add_argument("--size")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--out", type=Path, default=RUNS.parent / "B1-table.json")
    args = parser.parse_args()
    if args.command == "record":
        do_record(args.brain, args.size, args.threads)
    else:
        do_tune(args.out)


if __name__ == "__main__":
    main()
