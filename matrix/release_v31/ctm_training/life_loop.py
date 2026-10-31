"""Farm-life training for four fully independent Jarvises (no rule brain in any Jarvis).

    python -m ctm_training.life_loop simulate --workers 10     # the farm-year, recorded as training examples
    python -m ctm_training.life_loop pack                       # per-brain arrays (streamed to disk)
    python -m ctm_training.life_loop train                      # rounds, exam vs the rules, RESULTS.md, release v3

Design (owner, 2026-10-25):
- **In the farm, every Jarvis is its XL CTM alone**: its verdict (+ its decision layer) drives the owner messages,
  calls and protective actions through the safety gate; no rule brain runs inside a Jarvis. The brains never
  read their memory notes, and there is no DANGER net.
- **Rule brains only exist in the simulator, as the teacher** that records the training examples (the scenario
  library and the farm-year, ``farm_poc/scenarios/life.py``).
- **Training (each round, three brains side by side):** a fixed 50/50 mix of library fault episodes and farm-life
  days, ``train.LIFE_LOSS``: false alarms (quiet-time penalty), self-reliance (notes hidden), and the echo penalty
  (an alarm while a neighbour alarms but this machine's own situation is normal).
- **Judging (each round):** the four AIs run fresh episodes by themselves (closed loop: fault episodes + farm-life
  days); the rule brains run the same episodes for the bar to beat. The decision layer is tuned on the AIs' runs.
  A brain is kept only if all hold:
  1. false alarms per farm-life day drop by at least 25 %;
  2. ESCALATE and DANGER recall on the fault episodes do not drop at all;
  3. the pooled score does not drop.
  A brain that fails keeps its previous best and stops; the others go on. Test episodes are never used before the exam.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import copy
from dataclasses import replace
import json
from pathlib import Path
import time

import torch

from farm_poc.scenarios.life import SPLITS, life_day, year

from .b1 import tune_joint
from .data import BRAINS, Layout, Split, pack_folder, without_notes
from .decide import Decider
from .model import BrainConfig
from .practice import CheckpointBrains, simulate, specs
from .release import freeze
from .run import EXAM_FIRST_VARIANT, SIZES
from .score import evaluate, predict
from .train import LIFE_LOSS, load_brain, train

LIFE = Path(r"F:\TestbedRuns\life-v1")
PACKED = Path(r"F:\TestbedRuns\ctm-packed-v3")
RUNS = Path(r"F:\TestbedRuns\ctm-training-v2\runs")
START = "xl-b2"     # B2: already practised judging without its notes
JUDGE_FIRST_VARIANT = 60000


def log(text):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}"
    print(line, flush=True)
    with open(LIFE / "life.log", "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


# ------------------------------------------------------------------ data
def do_simulate(workers):
    """Every day of the year, recorded exactly like the scenario library (rule brains as the teacher, twins for
    fault days, the unchanged label policy). Each split goes to its own folder; finished days are skipped."""
    for split in ("judge", "test", "train"):
        started = time.time()
        log(f"simulating the {split} days")
        simulate(year(split), LIFE / split, None, workers, log=log)
        log(f"{split} days done in {(time.time() - started) / 3600:.1f} h")


def do_pack():
    log("packing the training days")
    pack_folder(LIFE / "train" / "episodes", LIFE / "packed", split="train")
    log("packed")


# ------------------------------------------------------------------ closed-loop runs: the four AIs alone
def judge_specs(smoke=False):
    """Fresh fault episodes + farm-life judging days (every second month); never used for training."""
    faults = specs("cljudge", JUDGE_FIRST_VARIANT, 4 if smoke else 30, seed=11)
    days = [life_day(d, "judge") for d in SPLITS["judge"][::6]][: 1 if smoke else None]
    return _shorten(faults + days, smoke)


def exam_specs(smoke=False):
    faults = specs("clexam", EXAM_FIRST_VARIANT, 4 if smoke else 60, seed=99)
    days = [life_day(d, "test") for d in SPLITS["test"][::3]][: 1 if smoke else None]
    return _shorten(faults + days, smoke)


def _shorten(spec_list, smoke):
    if smoke:
        for s in spec_list:
            s.duration_ticks = min(s.duration_ticks, 600)
    return spec_list


def drive(spec_list, folder: Path, checkpoints, decide, workers):
    """The four Jarvises run the farm with these brains (``checkpoints``; None = the rule brains, for the bar)."""
    factory = CheckpointBrains(checkpoints, PACKED, decide=decide, hide_notes=True) if checkpoints else None
    simulate(spec_list, folder, factory, workers, log=log)
    pack_folder(folder / "episodes", folder / "packed", split="val")
    return folder / "packed"


def parts(packed: Path, machine: str) -> dict:
    """The run split into its farm-life days and its fault episodes (same arrays, two indexes)."""
    whole = Split(packed, machine, "val")
    life, faults = copy.copy(whole), copy.copy(whole)
    life.index = [r for r in whole.index if r["family"].startswith("life")]
    faults.index = [r for r in whole.index if not r["family"].startswith("life")]
    return {"life": life, "faults": faults}


def false_per_day(summary) -> float:
    return round(sum(summary[lv]["false_per_day"] or 0.0 for lv in ("WATCHING", "ESCALATE_TO_USER", "DANGER")), 3)


def metrics(life, faults, pooled_score) -> dict:
    return {"score": pooled_score, "life_false_per_day": false_per_day(life["summary"]),
            "esc_recall": faults["summary"]["ESCALATE_TO_USER"]["recall"],
            "danger_recall": faults["summary"]["DANGER"]["recall"], "faults_score": faults["score"],
            "life_score": life["score"]}


def judged(machine, checkpoint, packed, tag, loss):
    """Tune the decision layer on the AIs' own closed-loop run (replayed, notes hidden) and report it."""
    result = tune_joint(machine, checkpoint, PACKED, parts(packed, machine), tag, loss=loss, log=lambda t: None)
    out = metrics(result["parts"]["life"], result["parts"]["faults"], result["b1"]["score"])
    return {**out, "checkpoint": str(checkpoint), "decider": result["decider"], "tag": tag}


def rule_bar(packed: Path, machine: str) -> dict:
    p = parts(packed, machine)
    life, faults = evaluate(p["life"]), evaluate(p["faults"])
    return metrics(life, faults, evaluate(Split(packed, machine, "val"))["score"])


def guards(new, old):
    fewer = new["life_false_per_day"] <= 0.75 * old["life_false_per_day"] or new["life_false_per_day"] == 0.0
    kept = all(n is None or o is None or n >= o - 1e-9 for n, o in ((new["esc_recall"], old["esc_recall"]),
                                                                   (new["danger_recall"], old["danger_recall"])))
    return {"false alarms down 25 %": fewer, "no recall lost": kept, "score not lower": new["score"] >= old["score"]}


# ------------------------------------------------------------------ one round, one brain (own process)
class _FileLog:
    def __init__(self, path):
        self.path = str(path)

    def __call__(self, text):
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")


def _round_job(machine, init_from, out_dir, life_packed, steps, threads, smoke):
    torch.set_num_threads(threads)
    config = replace(BrainConfig(machine), **next(s for s in SIZES if s[0] == "xl")[2])
    limit = 4 if smoke else None
    val = without_notes(Split(PACKED, machine, "val", max_length=4000, limit=limit), Layout(machine))
    return train(config, PACKED, Path(out_dir), device="cpu", steps=steps, batch=16 if smoke else 64, chunk=16 if smoke else 64,
                 eval_every=max(1, steps // 2), lr=3.5e-4, train_split=Split(PACKED, machine, "train"), val_split=val,
                 init_from=init_from, log=_FileLog(Path(out_dir).parent / f"{Path(out_dir).name}.log"),
                 mix_split=Split(Path(life_packed), machine, "train", mmap=True), **LIFE_LOSS)


# ------------------------------------------------------------------ the whole run
def do_train(rounds, steps, threads, workers, smoke, out_name):
    life_packed = LIFE / ("packed_smoke" if smoke else "packed")
    runs = LIFE / ("runs_smoke" if smoke else "runs")
    prefix = "smoke_" if smoke else ""
    runs.mkdir(parents=True, exist_ok=True)
    state_path = runs / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"history": {m: [] for m in BRAINS}}
    save = lambda: state_path.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    judge = judge_specs(smoke)
    if "rules" not in state:
        log("the bar: the rule brains run the judging episodes")
        rules = drive(judge, runs / "judge_rules", None, False, workers)
        state["rules"] = {m: rule_bar(rules, m) for m in BRAINS}
        save()
    if "current" not in state:
        log("start: the four B2 Jarvises run the judging episodes by themselves")
        checkpoints = {m: str(RUNS / m / START / "best.pt") for m in BRAINS}
        packed = drive(judge, runs / "judge_r0", checkpoints, False, workers)
        state["current"] = {m: {**judged(m, checkpoints[m], packed, f"{prefix}cl_r0", LIFE_LOSS), "round": 0} for m in BRAINS}
        for m in BRAINS:
            state["history"][m].append({"round": 0, "new": state["current"][m], "kept": True})
        state["active"] = list(BRAINS)
        save()
    for r in range(1, rounds + 1):
        if not state["active"] or state.get("rounds_done", 0) >= r:
            continue
        active = list(state["active"])
        log(f"round {r}: {', '.join(active)} fine-tune side by side ({steps} steps)")
        with ProcessPoolExecutor(max_workers=len(active)) as pool:
            jobs = {m: pool.submit(_round_job, m, state["current"][m]["checkpoint"], str(runs / m / f"round{r}"),
                                   str(life_packed), steps, threads, smoke) for m in active}
            trained = {m: job.result() for m, job in jobs.items()}
        log(f"round {r}: the four Jarvises run the judging episodes by themselves")
        checkpoints = {m: (trained[m]["checkpoint"] if m in trained else state["current"][m]["checkpoint"]) for m in BRAINS}
        packed = drive(judge, runs / f"judge_r{r}", checkpoints, False, workers)
        for machine in active:
            new = {**judged(machine, checkpoints[machine], packed, f"{prefix}cl_r{r}", LIFE_LOSS), "round": r}
            old = state["current"][machine]
            checks = guards(new, old)
            keep = all(checks.values())
            state["history"][machine].append({"round": r, "new": new, "checks": checks, "kept": keep})
            if keep:
                state["current"][machine] = new
            else:
                state["active"].remove(machine)
            log(f"round {r} {machine}: score {old['score']} -> {new['score']} (rules {state['rules'][machine]['score']}), "
                f"false/day {old['life_false_per_day']} -> {new['life_false_per_day']} "
                f"(rules {state['rules'][machine]['life_false_per_day']}), ESC recall {old['esc_recall']} -> "
                f"{new['esc_recall']}, DANGER recall {old['danger_recall']} -> {new['danger_recall']}: "
                f"{'KEPT' if keep else 'dropped, stops'} {checks}")
        state["rounds_done"] = r
        save()
    for machine in BRAINS:   # the shipped decision layer: the one tuned for the shipped checkpoint (cached)
        c = state["current"][machine]
        packed = runs / f"judge_r{c['round']}" / "packed"
        judged(machine, c["checkpoint"], packed, c["tag"], LIFE_LOSS)
    if "exam" not in state:
        exam = exam_specs(smoke)
        log("exam: the four shipped Jarvises run unseen episodes by themselves (decision layers on)")
        ai = drive(exam, runs / "exam_ai", {m: state["current"][m]["checkpoint"] for m in BRAINS}, True, workers)
        log("exam: the rule brains run the same episodes")
        rules = drive(exam, runs / "exam_rules", None, False, workers)
        state["exam"] = {}
        for m in BRAINS:
            p_ai, p_rule = parts(ai, m), parts(rules, m)
            ckpt = state["current"][m]["checkpoint"]
            replay = predict(load_brain(Path(ckpt), PACKED), Split(ai, m, "val"), "cpu",
                             decider=Decider.for_checkpoint(ckpt), hide_notes=True)
            state["exam"][m] = {"ai": {k: evaluate(v) for k, v in p_ai.items()} | {"all": evaluate(Split(ai, m, "val"))},
                                "rules": {k: evaluate(v) for k, v in p_rule.items()} | {"all": evaluate(Split(rules, m, "val"))},
                                "link_accuracy": evaluate(Split(ai, m, "val"), replay)["link_accuracy"]}
        save()
    results = write_results(state, runs)
    out = LIFE.parent / out_name
    if not out.exists():
        n = freeze(out, {m: state["current"][m]["checkpoint"] for m in BRAINS}, results, PACKED, README_V3)
        log(f"released {n} files to {out}")
    log("farm-life training finished")


# ------------------------------------------------------------------ RESULTS.md
def write_results(state, runs) -> Path:
    def row(r):
        s = r["summary"]
        w, e, d = s["WATCHING"], s["ESCALATE_TO_USER"], s["DANGER"]
        pc = lambda v: "-" if v is None else f"{v:.0%}"
        return (f"{r['score']} | {pc(w['recall'])} | {w['false_per_day']} | {pc(e['recall'])} | {e['delay_median_s']} | "
                f"{e['false_per_day']} | {pc(d['recall'])} | {d['delay_median_s']} | {d['false_per_day']}")
    head = ("| brain | score | WATCH recall | WATCH false/day | ESC recall | ESC delay s | ESC false/day | DANGER recall | "
            "DANGER delay s | DANGER false/day |\n|---|---|---|---|---|---|---|---|---|---|")
    lines = ["# Phase 5 results: four fully independent XL CTM Jarvises", "",
             "## The design (owner's decisions, 2026-10-25)", "",
             "- **Every Jarvis is its XL CTM alone** (256 neurons, 16 thinking steps, 32-step trace), with its decision "
             "layer. Its verdict drives the owner messages, calls and protective actions through the safety gate.",
             "- **No rule brain in any Jarvis.** The brains never read their memory notes, and there is no DANGER net.",
             "- **The neighbours are the other AIs.** Rule brains appear only in the simulator, as the teacher that "
             "records training examples, and as the comparison below.",
             "- **The farm-year:** 365 simulated 24-hour days with an Algiers-like seasonal climate, day and night, "
             "daily owner activity, and a realistic fault on about 15 % of days. 292 days train, and the judging and "
             "exam days are kept apart. The Matrix code was not changed; incubators keep its fixed start at day 14.",
             "- **Training rounds:** the three brains train side by side on a fixed 50/50 mix of fault episodes and "
             "farm life, with three penalties: alarms where nothing is wrong, alarms copied from a neighbour while this "
             "machine's own situation is normal, and notes hidden (no hints).",
             "- **Each round is judged by the four AIs running fresh episodes by themselves.** A brain is kept only if all hold:",
             "  - false alarms per farm-life day drop by at least 25 %;",
             "  - no ESCALATE or DANGER recall is lost;",
             "  - the score does not drop.",
             "- **Against cheating:** a silent brain scores about 0, the fixed mix keeps faults in every batch, the recall "
             "guard refuses any loss, and the exam episodes were never used before the exam.", "",
             "## Rounds (the four AIs running the judging episodes by themselves)", "",
             "| brain | round | score | false alarms per farm-life day | ESC recall | DANGER recall | kept |",
             "|---|---|---|---|---|---|---|"]
    for m in BRAINS:
        rb = state["rules"][m]
        lines.append(f"| {m} | rule brains (the bar) | {rb['score']} | {rb['life_false_per_day']} | {rb['esc_recall']} | "
                     f"{rb['danger_recall']} | - |")
        for h in state["history"][m]:
            n = h["new"]
            kept = "start (B2)" if h["round"] == 0 else ("yes" if h["kept"] else "no: " + ", ".join(k for k, v in h["checks"].items() if not v))
            lines.append(f"| {m} | {h['round']} | {n['score']} | {n['life_false_per_day']} | {n['esc_recall']} | "
                         f"{n['danger_recall']} | {kept} |")
    lines.append("")
    for part, title in (("all", "Exam: everything (unseen fault episodes + farm-life test days)"),
                        ("faults", "Exam: unseen fault episodes"), ("life", "Exam: unseen farm-life days (calm life, seasons)")):
        lines += [f"## {title}", "", "Four AIs running the farm alone vs the rule brains on the same episodes.", "", head]
        for m in BRAINS:
            e = state["exam"][m]
            if e["ai"][part]["summary"]:
                lines.append(f"| {m}: four AIs | {row(e['ai'][part])} |")
                lines.append(f"| {m}: rule brains | {row(e['rules'][part])} |")
        lines.append("")
    lines += ["Linking: how often each AI names the right cause when it alarms (exam runs replayed): " +
              ", ".join(f"{m} {state['exam'][m]['link_accuracy']}" for m in BRAINS) + ".", "",
              "## Compute", "",
              "- **Simulation:** CPU, 10 parallel workers.",
              "- **Training:** three brains side by side, 2 threads each; more threads per brain was measured slower.",
              "- **RX 580:** measured about 5× slower than one CPU trainer and out of its 8 GB at the training batch. Not used.",
              "- **Live:** four XL Jarvises at about 30 ticks per second, 30× real time.", "",
              "Scores: `farm_poc/scenarios/metrics.py` and the fixed composite in `ctm_training/score.py` (higher is better)."]
    text = "\n".join(lines) + "\n"
    path = runs / "RESULTS.md"
    path.write_text(text, encoding="utf-8")
    if runs.name == "runs":
        (Path(__file__).resolve().parent / "RESULTS.md").write_text(text, encoding="utf-8")
    return path


README_V3 = """# Phase 5 release v3: four fully independent XL CTM Jarvises

Frozen {date}. Installed by the Phase 6 build in step 6D; release v2 stays as it was.

- `brains/<brain>/`: `best.pt` (XL weights + config), `decide.json` (decision layer), `result.json`, tuning
  records (`*_b1.json`), `stats.npz` (normal statistics). Both incubators run their own instance of `incubator`.
- `code_snapshot/`: the exact code that trained and runs them. `memory/cmm.py`: the node memory.
- `RESULTS.md`: design, rounds, exam vs the rules. `MANIFEST.json`: SHA-256 of every file.

## How the brains run: every Jarvis is its CTM, no rule brain

```python
brains = {{n: TrainedNodeBrain.from_checkpoint(release / "brains" / MACHINE_TYPES[n] / "best.pt", release / "brains",
                                              start_hour, hide_notes=True) for n in NODE_IDS}}
runtime = NodeRuntime(engine, modes={{n: "real" for n in NODE_IDS}}, brains=brains,
                      memory_factory=lambda n: CognitiveMemory(n, path / f"{{n}}.sqlite"))
output = runtime.step()["hvac"]["output"]     # the Jarvis's verdict; the safety gate checks its actions
```

The checkpoints and snapshot carry the old project name in metadata and docstrings; step 6D decides how to
handle that (see the Phase 6 plan). Torch is needed to run the brains.
"""


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("simulate", "pack", "train"))
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--steps", type=int, default=800)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--smoke", action="store_true", help="tiny budgets: checks the run end to end")
    args = parser.parse_args(argv)
    LIFE.mkdir(parents=True, exist_ok=True)
    if args.command == "simulate":
        do_simulate(args.workers)
    elif args.command == "pack":
        do_pack()
    else:
        do_train(args.rounds, 4 if args.smoke else args.steps, args.threads, 3 if args.smoke else args.workers,
                 args.smoke, "release-smoke" if args.smoke else "release-v3")


if __name__ == "__main__":
    main()
