"""After the v3 exam (2026-10-27): the agreed fixes, tested without touching release v3 or any checkpoint.

    python -m ctm_training.fix_loop fresh   --workers 10   # a fresh exam set, rule brains (the bar)
    python -m ctm_training.fix_loop fires   --workers 8    # held calls on the judging fires (found before the exam)
    python -m ctm_training.fix_loop retune                 # incubator decision layer, judging data only, to a copy
    python -m ctm_training.fix_loop reexam  --workers 10   # fixed AIs on the v3 exam and the fresh exam, report

- **Held calls** (``TrainedNodeBrain(hold_calls=True)``): a brain's own fire-brigade / civil-protection request
  stays on while it stays at DANGER; the gate keeps checking the evidence. Found on the judging fires, so
  re-testing on the v3 exam episodes is fair.
- **Incubator retune**: anything changed because of the v3 exam is judged on the fresh exam set, which nobody
  has looked at. The retune uses the judging runs only.
- Everything is written under ``FIX``; checkpoints and their ``decide.json`` files are only read.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
import time

import numpy as np

from farm_poc.scenarios.life import SPLITS, life_day

from .data import BRAINS, Split
from .decide import GRID, Decider, apply, prior_correction, split_predictions
from .life_loop import LIFE, PACKED, exam_specs, judge_specs, parts
from .practice import specs
from .score import evaluate, evaluate_many
from .train import LIFE_LOSS

FIX = LIFE / "fix1"
RUNS = LIFE / "runs"
FRESH_FIRST_VARIANT = 97000   # no overlap: library, 25000 check, 50000 tune, 60000 judge, 90000 exam, 95000 confirm
SHIPPED = {"incubator": RUNS / "incubator" / "round2" / "best.pt", "hvac": RUNS / "hvac" / "round1" / "best.pt",
           "animal_protection": RUNS / "animal_protection" / "round2" / "best.pt"}
RETUNED = FIX / "incubator_decide.json"


def log(text):
    FIX.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}"
    print(line, flush=True)
    with open(FIX / "fix.log", "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def fresh_specs():
    """60 new fault episodes (same samplers and proportions) + the 12 test days the v3 exam did not use."""
    return specs("clexam2", FRESH_FIRST_VARIANT, 60, seed=7) + [life_day(d, "test") for d in SPLITS["test"][1::3]]


def _ai(folder, spec_list, workers, retuned=False):
    deciders = {"incubator": RETUNED} if retuned else None
    return drive_with(spec_list, folder, workers, hold_calls=True, deciders=deciders)


def drive_with(spec_list, folder, workers, **brains):
    from .practice import CheckpointBrains, simulate
    from .data import pack_folder
    factory = CheckpointBrains(SHIPPED, PACKED, decide=True, hide_notes=True, **brains)
    simulate(spec_list, folder, factory, workers, log=log)
    pack_folder(folder / "episodes", folder / "packed", split="val")
    return folder / "packed"


# ------------------------------------------------------------------ fires
def calls(folder: Path) -> list[dict]:
    out = []
    for path in sorted((folder / "episodes").glob("*.json")):
        o = json.loads(path.read_text(encoding="utf-8"))["outcome"]
        out.append({"episode": path.stem, "calls": sorted(f"{c['service']}<-{c['sender'].replace('jarvis-', '')}"
                                                          for c in o["calls"]),
                    "fire_burned_kg": round(o["fire_fuel_burned_kg"] or 0.0, 1), "notifications": o["notifications"],
                    "welfare": round(o["welfare_index_pct"], 2)})
    return out


def do_fires(workers):
    fires = [s for s in judge_specs() if "fire" in s.episode_id]
    log(f"fires: {len(fires)} judging fire episodes, the shipped AIs with held calls")
    _ai(FIX / "fires_held", fires, workers)
    for row in calls(FIX / "fires_held"):
        log(f"  {row}")


# ------------------------------------------------------------------ incubator retune (judging data only)
def _rates(summary):
    return {lv: summary[lv]["false_per_day"] for lv in ("WATCHING", "ESCALATE_TO_USER")}


def do_retune():
    """Same grid and composite as ``decide.tune``, plus the owner's rule: no false escalation on a calm farm day,
    and the fewest false WATCHING flags there, as long as ESCALATE / DANGER recall stays at the raw brain's level.
    Uses the round-2 brain's own replayed judging runs (``cl_r2``), never the exam."""
    folder = SHIPPED["incubator"].parent
    split = parts(RUNS / "judge_r2" / "packed", "incubator")
    recs = {n: dict(np.load(folder / f"cl_r2_{n}_outputs.npz")) for n in split}
    joined = {k: np.concatenate([recs[n][k] for n in split]) for k in ("logits", "heads", "starts")}
    sizes = [len(recs[n]["logits"]) for n in split]

    def judge(levels):
        decisions, start, out = apply(levels, joined["heads"]), 0, {}
        for (name, s), n in zip(split.items(), sizes):
            out[name] = evaluate(s, split_predictions(decisions[start:start + n], recs[name], s))
            start += n
        out["all"] = evaluate_many([(s, split_predictions(decisions[o:o + n], recs[name], s))
                                    for (name, s), n, o in zip(split.items(), sizes, np.cumsum([0] + sizes[:-1]))])
        return out

    raw = judge(joined["logits"].argmax(-1))
    floors = {lv: raw["faults"]["summary"][lv]["recall"] for lv in ("ESCALATE_TO_USER", "DANGER")}
    current = Decider.load(folder / "decide.json")
    correction = prior_correction(PACKED, "incubator",
                                  **{k: v for k, v in LIFE_LOSS.items() if k in ("alarm_weight", "danger_weight", "class_power")})
    rows = []
    for tau, theta, k_watch, k_escalate in itertools.product(GRID["tau"], GRID["theta"], GRID["k_watch"], GRID["k_escalate"]):
        d = Decider(tau, theta, k_watch, k_escalate, correction)
        r = judge(d.levels(joined["logits"], joined["starts"]))
        f = r["faults"]["summary"]
        keeps = all(fl is None or f[lv]["recall"] is None or f[lv]["recall"] >= fl - 1e-9 for lv, fl in floors.items())
        rows.append({"decider": d, "keeps": keeps, "score": r["all"]["score"], "life": _rates(r["life"]["summary"]),
                     "faults": _rates(f), "esc_recall": f["ESCALATE_TO_USER"]["recall"]})
    calm = [r for r in rows if r["keeps"] and r["life"]["ESCALATE_TO_USER"] == 0.0]
    best = min(calm, key=lambda r: (r["life"]["WATCHING"], r["faults"]["ESCALATE_TO_USER"], -r["score"])) if calm else None
    now = judge(current.levels(joined["logits"], joined["starts"]))
    log(f"retune: current layer {current.__dict__} -> score {now['all']['score']}, life {_rates(now['life']['summary'])}, "
        f"faults {_rates(now['faults']['summary'])}")
    if best is None:
        log("retune: no setting keeps the recall and has no false escalation on calm days; nothing written")
        return
    log(f"retune: chosen {best['decider'].__dict__} -> score {best['score']}, life {best['life']}, faults {best['faults']}, "
        f"ESC recall {best['esc_recall']} ({len(calm)} of {len(rows)} settings qualified)")
    best["decider"].save(RETUNED)


# ------------------------------------------------------------------ re-exam
def summary_rows(packed: Path) -> dict:
    out = {}
    for m in BRAINS:
        p = parts(packed, m)
        out[m] = {k: evaluate(v)["summary"] for k, v in p.items()} | {"all": evaluate(Split(packed, m, "val"))["summary"]}
        out[m]["score"] = evaluate(Split(packed, m, "val"))["score"]
    return out


def do_fresh(workers):
    log("fresh exam set: the rule brains")
    from .practice import simulate
    from .data import pack_folder
    simulate(fresh_specs(), FIX / "exam2_rules", None, workers, log=log)
    pack_folder(FIX / "exam2_rules" / "episodes", FIX / "exam2_rules" / "packed", split="val")
    log("fresh exam set: done")


def do_reexam(workers):
    retuned = RETUNED.exists()
    log(f"re-exam: held calls on, incubator layer {'retuned' if retuned else 'as shipped'}")
    _ai(FIX / "exam_ai_held", exam_specs(), workers)                  # fire fix only, on the v3 exam (fair: found before)
    _ai(FIX / "exam2_ai", fresh_specs(), workers, retuned=retuned)    # all fixes, on the fresh set
    results = {"v3_exam": {"ai_v3": summary_rows(RUNS / "exam_ai" / "packed"), "ai_held": summary_rows(FIX / "exam_ai_held" / "packed"),
                           "rules": summary_rows(RUNS / "exam_rules" / "packed")},
               "fresh_exam": {"ai_fixed": summary_rows(FIX / "exam2_ai" / "packed"),
                              "rules": summary_rows(FIX / "exam2_rules" / "packed")},
               "outcomes": {"v3_ai": calls(RUNS / "exam_ai"), "v3_ai_held": calls(FIX / "exam_ai_held"),
                            "v3_rules": calls(RUNS / "exam_rules"), "fresh_ai": calls(FIX / "exam2_ai"),
                            "fresh_rules": calls(FIX / "exam2_rules")}}
    (FIX / "reexam.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    log("re-exam: done, see reexam.json")


# ------------------------------------------------------------------ release v3.1
ENGINES = {"incubator": "reasoning_engine_INCv3.1.pt", "hvac": "reasoning_engine_HVACv3.1.pt",
           "animal_protection": "reasoning_engine_APv3.1.pt"}
README_V31 = """# Release v3.1: four fully independent XL CTM Jarvises, held calls on

Frozen {date}. Built from release v3 (unchanged, kept beside it): the same weights and decision layers, the
reasoning engines renamed, and the held-call fix switched on. Installed by the Phase 6 build in step 6D.

| Brain | Engine file | Machines |
|---|---|---|
| incubator | `brains/incubator/reasoning_engine_INCv3.1.pt` | incubator-1 and incubator-2 (one instance each) |
| hvac | `brains/hvac/reasoning_engine_HVACv3.1.pt` | hvac |
| animal_protection | `brains/animal_protection/reasoning_engine_APv3.1.pt` | animal-protection |

Next to each engine: `decide.json` (decision layer), `stats.npz` (normal statistics), `result.json` and the tuning
records. Swapping a brain later = replacing its engine file (same XL size and inputs) and/or its `decide.json`.

## How the brains run: every Jarvis is its CTM, no rule brain

```python
brains = {{n: TrainedNodeBrain.from_checkpoint(release / "brains" / MACHINE_TYPES[n] / ENGINES[MACHINE_TYPES[n]],
                                              release / "brains", start_hour, hide_notes=True, hold_calls=True)
          for n in NODE_IDS}}
runtime = NodeRuntime(engine, modes={{n: "real" for n in NODE_IDS}}, brains=brains,
                      memory_factory=lambda n: CognitiveMemory(n, path / f"{{n}}.sqlite"))
```

**Held calls** (`hold_calls=True`, `ctm_training/live.py`): once a brain itself asks for the fire brigade or civil
protection at DANGER, the request stays on while it stays at DANGER; the safety gate still checks the evidence every
second and drops duplicates. Measured: brigade called in 10 of 11 fresh-exam fires, no wrong call added.

## Results

- `RESULTS.md`: the v3 training rounds and exam (as frozen in v3).
- `docs/exam-v3-report.md`: the exam read in plain words, the held-call fix, and the fresh exam with false alarms
  judged by the farm's ground truth (`ctm_training/fair_score.py`).

Known open issues: Animal Protection's false alerts on a calm farm, civil-protection calls, the incubator's
response to its own stuck heater. See the report.

The engines and the snapshot carry the old project name in metadata and docstrings; step 6D decides how to handle
that. Torch is needed to run the brains.
"""


def do_release():
    from .release import point_release
    out = LIFE.parent / "release-v3.1"
    n = point_release(LIFE.parent / "release-v3", out, ENGINES, README_V31, docs=("exam-v3-report.md",))
    log(f"release: {n} files in {out}")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("fresh", "fires", "retune", "reexam", "release"))
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args(argv)
    {"fresh": lambda: do_fresh(args.workers), "fires": lambda: do_fires(args.workers), "retune": do_retune,
     "reexam": lambda: do_reexam(args.workers), "release": do_release}[args.command]()


if __name__ == "__main__":
    main()
