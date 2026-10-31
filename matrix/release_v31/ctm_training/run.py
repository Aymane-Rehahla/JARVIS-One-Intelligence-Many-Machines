"""Phase 5, end to end and unattended.

    python -m ctm_training.run --dataset F:/TestbedRuns/scenario-library-v2 --work F:/TestbedRuns/ctm-training-v1

Stages, each skipped when its results already exist (so re-running the same command resumes):
1. pack the dataset into per-brain arrays;
2. per brain: the Phase 2 brain (B0), then deeper thinking, then upgrades U1-U7 one at a time, each kept
   only if it passes the fixed acceptance rule; then bigger sizes; then a longer final training;
3. practice rounds in the live Matrix (DAgger) with fine-tuning;
4. the exam: open-loop on test, novel test and 24 h days, and closed-loop farm runs, trained brains vs rules.

Progress: ``<work>/STATUS.md`` (rewritten as it goes). Results: ``<work>/RESULTS.md`` and
``ctm_training/RESULTS.md``. To stop cleanly, create ``<work>/STOP``; run the command again to resume.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
import time

import numpy as np
import torch

from .b1 import tune_checkpoint
from .data import BRAINS, Layout, Split, pack, pack_folder, without_notes
from .decide import Decider
from .model import BrainConfig
from .practice import CheckpointBrains, simulate, specs
from .score import evaluate, predict
from .train import B2_LOSS, load_brain, train

# Sizes first (perception on: linking needs trends), then upgrades on the winning size, most valuable first.
SIZES = (("medium", "size medium: 96 neurons, 6 thinking steps, 12-step trace", {}),
         ("large", "size large: 128 neurons, 8 thinking steps, 16-step trace",
          {"d_model": 128, "iterations": 8, "memory_length": 16, "n_synch": 24}),
         ("xl", "size extra-large: 256 neurons, 16 thinking steps, 32-step trace",
          {"d_model": 256, "iterations": 16, "memory_length": 32, "n_synch": 32, "d_input": 96}))
# The extra-large brain copies 256 neurons x 32-step traces on every thinking step: memory traffic grows
# faster than the batch, so it trains on smaller batches (13 s/step at 64 vs 108 s/step at 256) for longer.
SIZE_TRAINING = {"xl": {"batch": 64, "steps_factor": 2.0, "eval_factor": 2.0}}
UPGRADES = (("twin_head", "U6 healthy-twin head: what a healthy machine would read"),
            ("expectation", "U3 world model: expect the next readings, feel surprise"),
            ("certainty_loss", "U1 Sakana's certainty loss over the thinking steps"),
            ("adaptive", "U5 adaptive thinking: think harder when surprised"),
            ("long_horizon", "U4 whole episodes from tick 0, longer memory of training"))
ACCEPT_MARGIN, SIZE_MARGIN = 0.005, 0.01
ASSESSMENT_RANK = {"NORMAL": 0, "HANDLED_BY_MACHINE": 1, "WATCHING": 2, "ESCALATE_TO_USER": 3, "DANGER": 4}


class Stopped(Exception):
    pass


class Status:
    def __init__(self, work: Path, tag: str = ""):
        self.work, self.started, self.lines, self.decisions, self.stage_name = work, time.time(), [], {}, ""
        self.suffix = f"-{tag}" if tag else ""   # parallel per-brain processes keep separate files
        self.log_file = open(work / f"run{self.suffix}.log", "a", encoding="utf-8")
        self._written = 0.0

    def log(self, text):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}"
        print(line, flush=True)
        self.log_file.write(line + "\n")
        self.log_file.flush()
        self.lines = (self.lines + [line])[-25:]
        if (self.work / "STOP").exists():
            raise Stopped()
        if time.time() - self._written > 20:
            self.write()

    def stage(self, name):
        self.stage_name = name
        self.log(f"== {name}")
        self.write()

    def write(self):
        hours = (time.time() - self.started) / 3600
        rows = ["# Phase 5 training status", "", f"- Stage: **{self.stage_name}**", f"- Running for {hours:.1f} h",
                "- To stop cleanly: create a file named `STOP` in this folder. Run the same command again to resume.", ""]
        for brain, entries in self.decisions.items():
            rows += [f"## {brain}", "", "| run | validation score | decision |", "|---|---|---|"]
            rows += [f"| {e['run']} | {e['score']} | {e['decision']} |" for e in entries] + [""]
        rows += ["## Latest log lines", "", "```", *self.lines, "```"]
        (self.work / f"STATUS{self.suffix}.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
        self._written = time.time()

    def decide(self, brain, run, score, decision):
        self.decisions.setdefault(brain, []).append({"run": run, "score": score, "decision": decision})
        self.log(f"{brain}: {run} -> {score} ({decision})")


def accepted(new, best, margin):
    """The fixed rule: a better composite, without losing dangers or adding false alarms.

    False alarms count as one total (escalations + dangers): a brain that turns a false DANGER into a false
    escalation has not added one. The first v2 run checked escalations alone and dropped the extra-large
    Animal Protection brain (fewer false alarms in total, the only CTM above zero) for that trade."""
    if new["score"] < best["score"] + margin:
        return False
    false_now = new["summary"]["ESCALATE_TO_USER"]["false_per_day"] + new["summary"]["DANGER"]["false_per_day"]
    false_before = best["summary"]["ESCALATE_TO_USER"]["false_per_day"] + best["summary"]["DANGER"]["false_per_day"]
    if false_now <= 0.5 * false_before:
        return True  # recall bought with crying wolf is not worth guarding: halving false alarms wins
    for level in ("ESCALATE_TO_USER", "DANGER"):
        a, b = new["summary"][level]["recall"], best["summary"][level]["recall"]
        if a is not None and b is not None and a < b - 0.02:
            return False
    return false_now <= false_before * 1.1 + 0.05


def keep_awake():
    """Ask Windows not to sleep while this process runs (released automatically when it exits)."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)


def _hours_left(args):
    return (args.select_until - time.time()) / 3600 if args.select_until else float("inf")


def _estimate_hours(seconds_by_size, size):
    """Hours a training of ``size`` should take, from the runs measured so far (or measured speed ratios)."""
    ratio = {"medium": 1.0, "large": 1.8, "xl": 7.0}
    if size in seconds_by_size:
        return seconds_by_size[size] / 3600
    if seconds_by_size:
        known, secs = next(iter(seconds_by_size.items()))
        return secs / 3600 * ratio[size] / ratio[known]
    return 0.0


def size_of(config: BrainConfig) -> str:
    return "xl" if config.d_model >= 256 else "large" if config.d_model >= 128 else "medium"


def _training(size, args, common):
    extra = SIZE_TRAINING.get(size, {})
    return {**common, "batch": extra.get("batch", args.batch), "steps": int(args.steps * extra.get("steps_factor", 1)),
            "eval_every": int(args.eval_every * extra.get("eval_factor", 1))}


def select_brain(machine, packed, runs, status, device, args):
    train_split = Split(packed, machine, "train")
    val_split = Split(packed, machine, "val", limit=args.limit, max_length=args.val_max_length)
    rule = evaluate(val_split)
    status.decide(machine, "rule brain (reference)", rule["score"], "baseline to beat")
    common = dict(device=device, steps=args.steps, batch=args.batch, chunk=args.chunk, eval_every=args.eval_every,
                  train_split=train_split, val_split=val_split, log=status.log)
    status.stage(f"{machine}: Phase 2 brain (reference)")
    reference = train(BrainConfig.baseline(machine), packed, runs / "B0", **common)
    status.decide(machine, "Phase 2 brain (reference)", reference["score"], "reference")
    if machine in args.base_size:
        return wrap_up(machine, packed, runs, status, args, common, val_split, rule, reference)
    seconds, config, best, size = {}, None, None, "medium"

    def fits(candidate_size, steps=None):
        """Room for this training plus the final training of the best brain so far."""
        need = _estimate_hours(seconds, candidate_size) + _estimate_hours(seconds, size if best else candidate_size)
        return need <= _hours_left(args)

    for name, text, dims in SIZES:
        candidate = replace(BrainConfig(machine), **dims)
        if best is not None and not fits(name, args.steps):
            status.decide(machine, text, "-", "skipped: would not finish in time")
            continue
        status.stage(f"{machine}: {text}")
        result = train(candidate, packed, runs / name, **_training(name, args, common))
        seconds[name] = result["seconds"]
        if best is None or accepted(result, best, SIZE_MARGIN):
            config, best, size = candidate, result, name
            status.decide(machine, text, result["score"], "kept" if name != "medium" else "starting point")
        else:
            status.decide(machine, text, result["score"], "dropped")
    for name, text in UPGRADES:
        if not fits(size, args.steps):
            status.decide(machine, text, "-", "skipped: would not finish in time")
            continue
        candidate = replace(config, **{name: True})
        status.stage(f"{machine}: {text}")
        result = train(candidate, packed, runs / f"{size}-{name}", **_training(size, args, common))
        if accepted(result, best, ACCEPT_MARGIN):
            config, best = candidate, result
            status.decide(machine, text, result["score"], "kept")
        else:
            status.decide(machine, text, result["score"], "dropped")
    status.stage(f"{machine}: final training of the winning brain (continues from its best checkpoint)")
    plan = _training(size, args, common)
    final = train(config, packed, runs / "final", **{**plan, "steps": args.final_steps or plan["steps"], "lr": 3.5e-4},
                  init_from=best["checkpoint"])
    if final["score"] < best["score"]:
        final = best   # longer training did not help: keep the selected brain
    kept = [n for n, _ in UPGRADES if getattr(config, n)] or "none"
    status.decide(machine, "final", final["score"], f"final ({size}, kept upgrades: {kept})")
    return {"config": config.as_dict(), "final": final, "rule": rule, "reference": reference}


def _b1_entry(result, tuned, **extra):
    """A shipped brain: its checkpoint, scored as it runs (with its decision layer) on its tuning episodes."""
    return {**result, "raw_score": tuned["raw"]["score"], "score": tuned["b1"]["score"], "summary": tuned["b1"]["summary"],
            "link_accuracy": tuned["b1"].get("link_accuracy"), "decider": tuned["decider"], "tuned_on": tuned.get("tag"),
            **extra}


def _recalls_kept(new, old):
    for level in ("ESCALATE_TO_USER", "DANGER"):
        a, b = new[level]["recall"], old[level]["recall"]
        if a is not None and b is not None and a < b - 1e-9:
            return False
    return True


# Fresh closed-loop episodes, by variant number (``practice.specs``): no range overlaps another.
TUNE_FIRST_VARIANT, CHECK_FIRST_VARIANT, EXAM_FIRST_VARIANT = 50000, 25000, 90000
SMOKE_TICKS = 600


def _specs(tag, first_variant, count, seed, args):
    """``practice.specs``; the smoke test cuts every episode to ``SMOKE_TICKS`` (a pipeline check, not a score)."""
    out = specs(tag, first_variant, count, seed)
    if args.smoke:
        for spec in out:
            spec.duration_ticks = min(spec.duration_ticks, SMOKE_TICKS)
    return out


def wrap_up(machine, packed, runs, status, args, common, val_split, rule, reference):
    """The owner's wrap-up (2026-10-23/24): no more size or upgrade tests; every Jarvis runs the XL brain.

    Per brain (one process each): the finished size, read from disk, and B2, the fine-tune for the live farm
    (``train.B2_LOSS``: false alarms and self-reliance). During training B2 is validated with the node's own
    notes hidden, so its checkpoint is chosen on evidence alone. Which of the two ships, and its decision
    layer, is decided later where it matters: closed-loop runs in which the four Jarvises drive the farm
    themselves (``closed_loop_ship``)."""
    if len(args.base_size[machine]) != 1:
        raise ValueError(f"--base-size: one size per brain, got {args.base_size[machine]} for {machine}")
    (name,) = args.base_size[machine]
    _, text, dims = next(s for s in SIZES if s[0] == name)
    config = replace(BrainConfig(machine), **dims)
    base = train(config, packed, runs / name, **_training(name, args, common))   # finished: read from disk
    status.decide(machine, f"{text} (finished; rule-driven validation)", base["score"], "base")
    entry = {"config": config.as_dict(), "size": name, "base": base, "b2": None, "final": base, "rule": rule,
             "reference": reference}
    if args.b2:
        plan = _training(name, args, common)
        steps = args.final_steps or 1500
        hours = base["seconds"] / plan["steps"] * steps / 3600 + 1.0     # + validations
        done = (runs / f"{name}-b2" / "result.json").exists()
        if args.b2_until and not done and time.time() + hours * 3600 > args.b2_until:
            status.decide(machine, "B2 fine-tune", "-", f"skipped: ~{hours:.1f} h would end after the cutoff")
        else:
            status.stage(f"{machine}: B2 fine-tune of {name}: false alarms + self-reliance (~{hours:.1f} h)")
            hidden = without_notes(val_split, Layout(machine))
            entry["b2"] = train(config, packed, runs / f"{name}-b2", **{**plan, "steps": steps, "lr": 3.5e-4,
                                "eval_every": max(1, steps // 3), "val_split": hidden},
                                init_from=base["checkpoint"], **B2_LOSS)
            status.decide(machine, "B2 fine-tune (validation with its own notes hidden)", entry["b2"]["score"], "trained")
    return entry


def closed_loop_ship(selection, packed, work, status, args):
    """Which brain each Jarvis ships, and its decision layer, judged where it matters: closed-loop runs in
    which the four Jarvises drive the farm themselves, so their memory inputs are their own decisions.

    Each candidate (the finished size; B2 when trained for every brain) drives the same fresh episodes on
    all four nodes, raw (no decision layer); the layer is tuned on the replayed outputs (``tune_checkpoint``).
    B2 ships for a brain when its tuned score is higher by at least 0.05 without losing ESCALATE or DANGER
    recall; otherwise the finished size ships."""
    tune_specs = _specs("tune", TUNE_FIRST_VARIANT, args.tune_episodes, 7, args)
    variants = ["base"] + (["b2"] if all(selection[m]["b2"] for m in BRAINS) else [])
    tuned = {m: {} for m in BRAINS}
    for variant in variants:
        checkpoints = {m: selection[m][variant]["checkpoint"] for m in BRAINS}
        status.stage(f"closed loop ({variant}): the four Jarvises drive the farm, raw, to tune their decision layer")
        folder = work / f"closed_loop_{variant}"
        simulate(tune_specs, folder, CheckpointBrains(checkpoints, packed, decide=False), args.workers, log=status.log)
        pack_folder(folder / "episodes", work / f"packed_closed_loop_{variant}", split="val")
        for machine in BRAINS:
            split = Split(work / f"packed_closed_loop_{variant}", machine, "val")
            result = tune_checkpoint(machine, checkpoints[machine], packed, split, tag=f"closed_loop_{variant}",
                                     loss=B2_LOSS if variant == "b2" else None, log=status.log)
            tuned[machine][variant] = result
            status.decide(machine, f"{variant} driving the farm: raw {result['raw']['score']}, replay agrees "
                          f"{result['replay_agrees_raw']:.2%} with its live decisions", result["b1"]["score"],
                          "decision layer tuned" if result["decider"] else "no setting kept its recall: raw")
    for machine in BRAINS:
        pick = "base"
        if "b2" in tuned[machine]:
            old, new = tuned[machine]["base"]["b1"], tuned[machine]["b2"]["b1"]
            if new["score"] >= old["score"] + 0.05 and _recalls_kept(new["summary"], old["summary"]):
                pick = "b2"
        chosen = selection[machine][pick]
        # leave the shipped checkpoint's decide.json set to its own closed-loop tuning (cached: no re-run)
        result = tune_checkpoint(machine, chosen["checkpoint"], packed, None, tag=f"closed_loop_{pick}", log=status.log)
        selection[machine]["final"] = _b1_entry(chosen, result, variant=pick, loss=B2_LOSS if pick == "b2" else None)
        status.decide(machine, f"shipped: {pick} + its closed-loop decision layer", result["b1"]["score"], "kept")
    return selection


class _FileLog:
    """A picklable logger for worker processes: appends to a log file and honours the STOP file."""

    def __init__(self, path, work):
        self.path, self.work = str(path), str(work)

    def __call__(self, text):
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
        if (Path(self.work) / "STOP").exists():
            raise Stopped()


def _batch_for(config: dict, args) -> int:
    return SIZE_TRAINING.get(size_of(BrainConfig(**config)), {}).get("batch", args.batch)


def _practice_job(machine, config, packed, out_dir, extra, check, init_from, steps, batch, chunk, threads, work):
    """One brain's wrap-up practice fine-tune, in its own process (it loads its own data: nothing large is
    sent between processes). Logs to ``run-<brain>.log``. Same call as the sequential practice, with B2_LOSS."""
    torch.set_num_threads(threads)
    return train(BrainConfig(**config), Path(packed), Path(out_dir), device="cpu", steps=steps, batch=batch, chunk=chunk,
                 eval_every=max(1, steps // 2), lr=3.5e-4, train_split=Split(Path(packed), machine, "train", extra=extra),
                 val_split=Split(Path(check), machine, "val"), init_from=init_from,
                 log=_FileLog(Path(work) / f"run-{machine}.log", work), **B2_LOSS)


def practice(selection, packed, work, status, device, args):
    """DAgger in the live Matrix: the Jarvises drive fresh episodes themselves and learn from them.

    In the wrap-up (``--base-size``) the shipped brains drive with their decision layers on, training uses
    ``B2_LOSS``, and each round also runs held-out check episodes, driven the same way: the practised brain
    and the previous one are both replayed there with their layers re-tuned, and the better one is kept."""
    checkpoints = {m: selection[m]["final"]["checkpoint"] for m in BRAINS}
    extra = []
    for r in range(1, args.practice_rounds + 1):
        status.stage(f"practice round {r}: the trained brains run the farm")
        folder = work / f"practice{r}"
        simulate(_specs(f"practice{r}", 20000 + 1000 * r, args.practice_episodes, r, args), folder,
                 CheckpointBrains(checkpoints, packed), args.workers, log=status.log)
        pack_folder(folder / "episodes", work / f"packed_practice{r}")
        extra.append(work / f"packed_practice{r}")
        if args.base_size:
            status.stage(f"practice round {r}: held-out check episodes, driven the same way")
            held = work / f"practice{r}_check"
            simulate(_specs(f"check{r}", CHECK_FIRST_VARIANT + 1000 * r, args.check_episodes, 100 + r, args), held,
                     CheckpointBrains(checkpoints, packed), args.workers, log=status.log)
            pack_folder(held / "episodes", work / f"packed_practice{r}_check", split="val")
        trained = {}
        if args.base_size:   # the three fine-tunes side by side (3 x 2 threads), as in B2: ~2x faster than in turn
            status.stage(f"practice round {r}: the three brains learn from their own runs, side by side")
            with ProcessPoolExecutor(max_workers=len(BRAINS)) as pool:
                jobs = {machine: pool.submit(
                    _practice_job, machine, selection[machine]["config"], str(packed),
                    str(work / "runs" / machine / f"practice{r}"), [str(p) for p in extra],
                    str(work / f"packed_practice{r}_check"), selection[machine]["final"]["checkpoint"],
                    args.practice_steps, _batch_for(selection[machine]["config"], args), args.chunk, args.practice_threads,
                    str(work)) for machine in BRAINS}
                trained = {machine: job.result() for machine, job in jobs.items()}
        for machine in BRAINS:
            status.stage(f"practice round {r}: {machine} learns from its own runs")
            config = BrainConfig(**selection[machine]["config"])
            previous = selection[machine]["final"]
            if args.base_size:
                val_split = Split(work / f"packed_practice{r}_check", machine, "val")
            else:
                val_split = Split(packed, machine, "val", limit=args.limit, max_length=args.val_max_length)
            loss = B2_LOSS if args.base_size else {}
            result = trained.get(machine) or train(
                config, packed, work / "runs" / machine / f"practice{r}", device=device, steps=args.practice_steps,
                batch=_batch_for(selection[machine]["config"], args), chunk=args.chunk,
                eval_every=max(1, args.practice_steps // 2), lr=3.5e-4, train_split=Split(packed, machine, "train", extra=extra),
                val_split=val_split, init_from=previous["checkpoint"], log=status.log, **loss)
            if args.base_size:   # both judged with their decision layers on the same held-out closed-loop runs
                tag = f"practice{r}_check"
                old = tune_checkpoint(machine, previous["checkpoint"], packed, val_split, tag=tag,
                                      loss=previous.get("loss"), log=status.log)
                new = tune_checkpoint(machine, result["checkpoint"], packed, val_split, tag=tag, loss=loss, log=status.log)
                previous = _b1_entry(previous, old, variant=previous.get("variant"), loss=previous.get("loss"))
                result = _b1_entry(result, new, variant=f"practice{r}", loss=loss)
                keep = result["score"] >= previous["score"] - 0.002 and _recalls_kept(result["summary"], previous["summary"])
            else:
                keep = result["score"] >= previous["score"] - 0.002
            if keep:
                selection[machine]["final"] = result
                checkpoints[machine] = result["checkpoint"]
                status.decide(machine, f"practice round {r} (previous brain: {previous['score']})", result["score"], "kept")
            else:   # in the wrap-up its decide.json is now the layer re-tuned on these runs
                selection[machine]["final"] = previous
                status.decide(machine, f"practice round {r} (previous brain: {previous['score']})", result["score"],
                              "kept the previous brain")
    return selection


def _with_danger_net(predictions, split):
    """Offline form of ``TrainedNodeBrain(danger_net=True)`` on recorded episodes: where the rule brain
    recorded DANGER and the CTM did not, the rules' decision is used. Reported apart, never as the CTM's."""
    out = {}
    for i, info in enumerate(split.index):
        pred = predictions[i].copy()
        rule = split.arrays["rule"][info["start"]:info["start"] + info["length"]]
        net = (rule[:, 0] == 4) & (pred[:, 0] != 4)
        pred[net, :4] = rule[net]
        out[i] = pred
    return out


def exam(selection, packed, work, status, device, args):
    """Open loop on the held-out splits, then the closed loop: the same fresh farm episodes driven once by
    the rule brains and once by the four trained Jarvises (with their decision layers). The closed loop is
    the honest test: there every input, the brain's own memory included, comes from its own decisions."""
    status.stage("exam: open loop on unseen episodes")
    results = {"open_loop": {}, "closed_loop": {}, "closed_loop_replay": {}}
    max_length = args.val_max_length if args.smoke else None
    for machine in BRAINS:
        checkpoint = Path(selection[machine]["final"]["checkpoint"])
        brain, decider = load_brain(checkpoint, packed, device), Decider.for_checkpoint(checkpoint)
        status.log(f"{machine}: exam with {checkpoint.parent.name}, decision layer {'on' if decider else 'off'}")
        for split in ("test", "novel_test", "normal_day_test"):
            data = Split(packed, machine, split, limit=args.limit, max_length=max_length)
            predictions = predict(brain, data, device, decider=decider)
            entry = {"ctm": evaluate(data, predictions), "rule": evaluate(data)}
            if args.base_size:
                entry["ctm+danger_net"] = evaluate(data, _with_danger_net(predictions, data))
            results["open_loop"].setdefault(machine, {})[split] = entry
            status.log(f"{machine} {split}: CTM {entry['ctm']['score']} vs rule {entry['rule']['score']}")
    exam_specs = _specs("exam", EXAM_FIRST_VARIANT, args.exam_episodes, 99, args)
    checkpoints = {m: selection[m]["final"]["checkpoint"] for m in BRAINS}
    for who, factory in (("rule", None), ("ctm", CheckpointBrains(checkpoints, packed))):
        status.stage(f"exam: closed loop, the {who} brains run the farm")
        folder = work / f"exam_{who}"
        simulate(exam_specs, folder, factory, args.workers, log=status.log)
        pack_folder(folder / "episodes", work / f"packed_exam_{who}", split="exam")
        outcome = _outcomes(folder / "episodes")
        for machine in BRAINS:   # scored on the decisions recorded live, exactly as they happened
            score = evaluate(Split(work / f"packed_exam_{who}", machine, "exam"))
            results["closed_loop"].setdefault(machine, {})[who] = score
        results["closed_loop"].setdefault("farm", {})[who] = outcome
    for machine in BRAINS:   # the live recording keeps no named cause: replay the runs for link accuracy
        checkpoint = Path(checkpoints[machine])
        data = Split(work / "packed_exam_ctm", machine, "exam")
        replayed = predict(load_brain(checkpoint, packed, device), data, device, decider=Decider.for_checkpoint(checkpoint))
        live = np.concatenate([data.arrays["rule"][i["start"]:i["start"] + i["length"], 0] for i in data.index])
        again = np.concatenate([replayed[i][:, 0] for i in range(len(data.index))])
        results["closed_loop_replay"][machine] = {"link_accuracy": evaluate(data, replayed)["link_accuracy"],
                                                  "replay_agrees": round(float((live == again).mean()), 5)}
    return results


DEMO_FAULTS = (("compressor_failure", "hvac"), ("heater_relay_stuck", "incubator-1"),
               ("drinker_pump_failure", "animal-protection"), ("water_cut", "site"))


def demo_checks(selection, packed, args, status):
    """The mentor's test, one seed each (illustrative, not a score): a fault is injected into the running farm
    with the four shipped Jarvises, and again with the rule brains. Per node: when it first watches and first
    escalates, its diagnosis, and the cause it names."""
    from farm_poc.matrix.engine import MatrixEngine
    from farm_poc.matrix.equipment_faults import EquipmentFaultScenario, WaterCutScenario
    from farm_poc.nodes import NODE_IDS, NodeRuntime
    from farm_poc.nodes.contracts import MACHINE_TYPES
    from .live import TrainedNodeBrain
    rows = []
    for fault, device in DEMO_FAULTS:
        for who in ("ctm", "rule"):
            engine = MatrixEngine(seed=11)
            if who == "ctm":
                brains = {n: TrainedNodeBrain.from_checkpoint(Path(selection[MACHINE_TYPES[n].replace("-", "_")]["final"]["checkpoint"]),
                                                               packed) for n in NODE_IDS}
                runtime = NodeRuntime(engine, modes={n: "real" for n in NODE_IDS}, brains=brains)
            else:
                runtime = NodeRuntime(engine)
            if fault == "water_cut":
                engine.start_water_cut(WaterCutScenario(inject_tick=args.demo_inject))
            else:
                engine.start_equipment_fault(EquipmentFaultScenario(fault, device, inject_tick=args.demo_inject))
            first, early = {n: {} for n in NODE_IDS}, {n: False for n in NODE_IDS}
            for tick in range(args.demo_ticks):
                for node, result in runtime.step().items():
                    o = result["output"]
                    rank = ASSESSMENT_RANK[o.assessment]
                    if tick < args.demo_inject:          # nothing is wrong yet: any alarm here is a false one
                        early[node] |= rank >= ASSESSMENT_RANK["WATCHING"]
                        continue
                    for level in ("WATCHING", "ESCALATE_TO_USER", "DANGER"):
                        if rank >= ASSESSMENT_RANK[level] and level not in first[node]:
                            cause = next((e[6:] for e in o.evidence if e.startswith("cause=")), "-")
                            first[node][level] = (tick - args.demo_inject, o.diagnosis, cause)
            for node in NODE_IDS:
                rows.append({"fault": f"{fault} ({device})", "brains": who, "node": node, "alarm_before_fault": early[node],
                             **{level: first[node].get(level) for level in ("WATCHING", "ESCALATE_TO_USER", "DANGER")}})
        status.log(f"demo check: {fault} on {device} done")
    return rows


def _outcomes(folder: Path):
    metas = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")]
    calls = sum(len(m["outcome"]["calls"]) for m in metas)
    false_calls = sum(1 for m in metas for c in m["outcome"]["calls"] if c.get("outcome") == "false_alarm")
    return {"episodes": len(metas), "calls": calls, "false_alarm_calls": false_calls,
            "notifications_per_episode": round(sum(m["outcome"]["notifications"] for m in metas) / max(1, len(metas)), 2),
            "mean_welfare_pct": round(float(np.mean([m["outcome"]["welfare_index_pct"] for m in metas])), 2) if metas else None}


def write_results(work, selection, results, device, status, demo=None, smoke=False):
    def row(summary, score):
        w, e, d = summary["WATCHING"], summary["ESCALATE_TO_USER"], summary["DANGER"]
        pc = lambda v: "-" if v is None else f"{v:.0%}"
        return (f"{score} | {pc(w['recall'])} | {w['delay_median_s']} | {w['false_per_day']} | {pc(e['recall'])} | "
                f"{e['delay_median_s']} | {e['false_per_day']} | {pc(d['recall'])} | {d['false_events']} | "
                f"{e['interference_events'] + d['interference_events']}")
    head = ("| brain | score | WATCH recall | WATCH delay s | WATCH false/day | ESC recall | ESC delay s | ESC false/day | "
            "DANGER recall | DANGER false | interference |\n|---|---|---|---|---|---|---|---|---|---|---|")
    lines = ["# Phase 5 results: trained CTM brains vs the rule brain", "",
             f"Device: {device}. Total time: {(time.time() - status.started) / 3600:.1f} h. "
             "Scores use `farm_poc/scenarios/metrics.py` and the fixed composite in `ctm_training/score.py`.", ""]
    closed = ["## Closed loop: held-out farm runs (the honest test)", "",
              "The same fresh episodes, driven once by the rule brains and once by the four trained Jarvises with "
              "their decision layers. Every input, the brain's own memory included, comes from its own decisions. "
              "Scored on the decisions recorded live.", "", head]
    for machine in BRAINS:
        for who in ("ctm", "rule"):
            r = results["closed_loop"][machine][who]
            if r["summary"]:
                closed.append(f"| {machine} ({who}) | {row(r['summary'], r['score'])} |")
    closed += ["", "| brains running the farm | episodes | emergency calls | false-alarm calls | owner messages per episode | "
               "mean animal welfare |", "|---|---|---|---|---|---|"]
    for who in ("ctm", "rule"):
        o = results["closed_loop"]["farm"][who]
        closed.append(f"| {who} | {o['episodes']} | {o['calls']} | {o['false_alarm_calls']} | "
                      f"{o['notifications_per_episode']} | {o['mean_welfare_pct']} |")
    replay = results.get("closed_loop_replay") or {}
    if replay:
        closed += ["", "Linking in the closed loop (the live recording keeps no named cause, so the runs are replayed; "
                   "the replay's agreement with the live decisions is shown):", "",
                   "| brain | link accuracy | replay agrees with live |", "|---|---|---|"]
        closed += [f"| {m} | {v['link_accuracy']} | {v['replay_agrees']:.2%} |" for m, v in replay.items()]
    lines += closed + [""]
    if demo:
        lines += ["## Demo checks: a fault injected into the running farm (one seed each, illustrative)", "",
                  "From the injection on: seconds after it, diagnosis, and the cause the node names (CTM only). "
                  "\"Alarm before the fault\": the node raised an alarm while nothing was wrong yet (a false alarm); "
                  "a reaction at 0 s may then be that alarm continuing.", "",
                  "| fault | brains | node | alarm before the fault | first WATCHING | first ESCALATE | first DANGER |",
                  "|---|---|---|---|---|---|---|"]
        cell = lambda v: "-" if v is None else f"{v[0]} s, {v[1]}, cause {v[2]}"
        lines += [f"| {d['fault']} | {d['brains']} | {d['node']} | {'yes' if d['alarm_before_fault'] else 'no'} | "
                  f"{cell(d['WATCHING'])} | {cell(d['ESCALATE_TO_USER'])} | {cell(d['DANGER'])} |" for d in demo] + [""]
    for split in ("test", "novel_test", "normal_day_test"):
        lines += [f"## Open loop: {split}", "",
                  "Recorded with the rule brains driving the farm: the brains' memory inputs carry the *rules'* "
                  "decisions, which flatters the CTM. The closed loop above is the fair comparison.", "", head]
        for machine, per in results["open_loop"].items():
            for who in ("ctm", "rule", "ctm+danger_net"):
                r = per[split].get(who)
                if r and r["summary"]:
                    label = "ctm + rule DANGER interlock: not the CTM's own" if who == "ctm+danger_net" else who
                    lines.append(f"| {machine} ({label}) | {row(r['summary'], r['score'])} |")
        lines.append("")
    lines += ["## What was kept", ""]
    for brain, entries in status.decisions.items():
        lines += [f"**{brain}**", "", "| run | score | decision |", "|---|---|---|"]
        lines += [f"| {e['run']} | {e['score']} | {e['decision']} |" for e in entries] + [""]
    text = "\n".join(lines) + "\n"
    (work / "RESULTS.md").write_text(text, encoding="utf-8")
    if not smoke:   # a smoke run never replaces the real results kept with the code
        (Path(__file__).resolve().parent / "RESULTS.md").write_text(text, encoding="utf-8")
    (work / "results.json").write_text(json.dumps({"selection": selection, "results": results, "demo": demo},
                                                  indent=1, default=str), encoding="utf-8")


def choose_device(name):
    if name in ("dml", "gpu"):
        import torch_directml
        return torch_directml.device()
    torch.set_num_threads(max(1, torch.get_num_threads()))
    return torch.device("cpu")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="F:/TestbedRuns/scenario-library-v2")
    parser.add_argument("--work", default="F:/TestbedRuns/ctm-training-v1")
    parser.add_argument("--packed", default=None, help="packed arrays folder (default: <work>/packed)")
    parser.add_argument("--device", default="cpu", choices=("cpu", "dml", "gpu"))
    parser.add_argument("--steps", type=int, default=1500)
    parser.add_argument("--batch", type=int, default=256)
    parser.add_argument("--chunk", type=int, default=64)
    parser.add_argument("--eval-every", type=int, default=500)
    parser.add_argument("--final-steps", type=int, default=None, help="steps of the final (continued) training")
    parser.add_argument("--select-until", default=None, help="'YYYY-MM-DD HH:MM': skip trainings that would end later")
    parser.add_argument("--practice-steps", type=int, default=600)
    parser.add_argument("--practice-threads", type=int, default=2, help="wrap-up: torch threads per parallel practice fine-tune")
    parser.add_argument("--val-max-length", type=int, default=4000,
                        help="validation uses episodes up to this many ticks (the exam uses all)")
    parser.add_argument("--practice-rounds", type=int, default=2)
    parser.add_argument("--practice-episodes", type=int, default=150)
    parser.add_argument("--exam-episodes", type=int, default=120)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--keep-awake", action="store_true")
    parser.add_argument("--only", default=None, help="comma-separated brains (parallel per-brain processes)")
    parser.add_argument("--threads", type=int, default=None, help="torch CPU threads for this process")
    parser.add_argument("--base-size", default="",
                        help="wrap-up: 'incubator=xl|large,hvac=xl,...' ships a finished size (+ decision layer), no more selection")
    parser.add_argument("--b2", action="store_true", help="wrap-up: try the false-alarm fine-tune on the shipped brain")
    parser.add_argument("--b2-until", default=None, help="'YYYY-MM-DD HH:MM': skip B2 if it would end later")
    parser.add_argument("--tune-episodes", type=int, default=60,
                        help="wrap-up: closed-loop episodes the decision layer is tuned on (per candidate brain)")
    parser.add_argument("--check-episodes", type=int, default=40, help="wrap-up: held-out closed-loop episodes per practice round")
    parser.add_argument("--demo-ticks", type=int, default=2400, help="wrap-up: length of each demo check run")
    parser.add_argument("--demo-inject", type=int, default=300, help="wrap-up: tick the demo fault is injected at")
    parser.add_argument("--smoke", action="store_true", help="tiny budgets everywhere: checks the pipeline end to end")
    args = parser.parse_args(argv)
    args.limit = None
    args.select_until = time.mktime(time.strptime(args.select_until, "%Y-%m-%d %H:%M")) if args.select_until else None
    args.b2_until = time.mktime(time.strptime(args.b2_until, "%Y-%m-%d %H:%M")) if args.b2_until else None
    args.base_size = {m: sizes.split("|") for m, sizes in (p.split("=") for p in args.base_size.split(",") if p)}
    if args.smoke:
        args.steps, args.eval_every, args.final_steps, args.batch, args.chunk = 4, 4, 4, 16, 16
        args.practice_steps = 4
        args.practice_rounds, args.practice_episodes, args.exam_episodes, args.workers, args.limit = 1, 2, 2, 2, 4
        args.tune_episodes, args.check_episodes, args.demo_ticks, args.demo_inject = 2, 2, 60, 20
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    (work / "STOP").unlink(missing_ok=True)
    status = Status(work, args.only.replace(",", "+") if args.only else "")
    if args.threads:
        torch.set_num_threads(args.threads)
    if args.keep_awake:
        keep_awake()
    device = choose_device(args.device)
    try:
        status.stage("packing the dataset")
        packed = Path(args.packed) if args.packed else work / "packed"
        pack(Path(args.dataset), packed)
        machines = args.only.split(",") if args.only else list(BRAINS)
        selection = {}
        for machine in machines:
            selection[machine] = select_brain(machine, packed, work / "runs" / machine, status, device, args)
        if set(machines) == set(BRAINS):
            if args.base_size:
                selection = closed_loop_ship(selection, packed, work, status, args)
            selection = practice(selection, packed, work, status, device, args)
            results = exam(selection, packed, work, status, device, args)
            demo = None
            if args.base_size:
                status.stage("demo checks: faults injected into the running farm")
                demo = demo_checks(selection, packed, args, status)
            write_results(work, selection, results, device, status, demo, smoke=args.smoke)
        status.stage("finished")
    except Stopped:
        status.lines.append("stopped on request; run the same command to resume")
        status.write()


if __name__ == "__main__":
    main()
