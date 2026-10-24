"""Label QA and rule-baseline scores for a built scenario dataset.

    python -m farm_poc.scenarios.report --data runs/scenario-library-v2

Writes ``report.json`` and ``REPORT.md`` into the dataset directory. The baseline
numbers are what the trained CTMs (Phase 5) must beat; they are scored with the
same metrics (metrics.py) on the same recorded inputs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import numpy as np

from ..nodes.contracts import ASSESSMENTS, COMMUNICATIONS, MACHINE_TYPES, NODE_IDS
from .metrics import score_node, summarise

_ALARM = ASSESSMENTS.index("WATCHING")
_ESCALATE = ASSESSMENTS.index("ESCALATE_TO_USER")
_DANGER = ASSESSMENTS.index("DANGER")
_HANDLED = ASSESSMENTS.index("HANDLED_BY_MACHINE")
NORMAL_FAMILIES = ("normal", "normal_day_24h")
SPLITS = ("train", "val", "test", "novel_test", "normal_day_test")


def iter_episodes(data_dir: Path):
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["episodes"]:
        meta_path = data_dir / "episodes" / f"{entry['episode_id']}.json"
        if meta_path.exists() and meta_path.with_suffix(".npz").exists():
            yield json.loads(meta_path.read_text(encoding="utf-8")), np.load(meta_path.with_suffix(".npz"))


def node_labels(arrays, node):
    key = node.replace("-", "_")
    return {name: arrays[f"{key}__label_{name}"] for name in ("assessment", "diagnosis", "action", "communication", "reason")}


def injection_tick(spec):
    ticks = [f.get("inject_tick", f.get("tick", f.get("from_tick"))) for f in spec["faults"]]
    ticks = [t for t in ticks if t is not None]
    return min(ticks) if ticks else None


_CALL_CODES = {COMMUNICATIONS.index("call_fire_brigade"): "fire_brigade",
               COMMUNICATIONS.index("call_civil_protection"): "civil_protection"}


def _calls(communication):
    return {_CALL_CODES[c] for c in np.unique(communication) if c in _CALL_CODES}


def _pct(values, q):
    return round(float(np.percentile(values, q)), 1) if values else None


class _FamilyQA:
    def __init__(self):
        self.episodes = 0
        self.reached = defaultdict(Counter)   # machine -> level -> episodes
        self.onset_delays = []
        self.changes = 0
        self.node_hours = 0.0
        self.reasons = Counter()
        self.calls = Counter()
        self.non_normal_ticks = 0
        self.ticks = 0

    def add(self, meta, arrays):
        self.episodes += 1
        reasons = meta["reasons"]
        first = None
        for node in NODE_IDS:
            labels = node_labels(arrays, node)
            a = labels["assessment"].astype(np.int64)
            machine = MACHINE_TYPES[node]
            for name, rank in (("handled", _HANDLED), ("alarm", _ALARM), ("escalate", _ESCALATE), ("danger", _DANGER)):
                if (a == rank).any() if name == "handled" else (a >= rank).any():
                    self.reached[machine][name] += 1
            hits = np.flatnonzero(a >= _HANDLED)
            if hits.size:
                first = hits[0] if first is None else min(first, hits[0])
            self.changes += int(np.count_nonzero(np.diff(a)))
            self.node_hours += a.size / 3600.0
            self.non_normal_ticks += int((a > 0).sum())
            self.ticks += a.size
            for index, count in zip(*np.unique(labels["reason"], return_counts=True)):
                if reasons[index] != "normal":
                    self.reasons[reasons[index]] += int(count)
            for name in ("fire_brigade", "civil_protection"):
                if (labels["communication"] == COMMUNICATIONS.index(f"call_{name}")).any():
                    self.calls[name] += 1
        inject = injection_tick(meta["spec"])
        if first is not None and inject is not None:
            self.onset_delays.append(float(first - inject))

    def as_dict(self):
        return {"episodes": self.episodes,
                "reached": {m: dict(c) for m, c in self.reached.items()},
                "label_onset_after_fault_s": {"median": _pct(self.onset_delays, 50), "p90": _pct(self.onset_delays, 90),
                                              "seen": len(self.onset_delays)},
                "label_changes_per_node_hour": round(self.changes / self.node_hours, 2) if self.node_hours else None,
                "non_normal_tick_share": round(self.non_normal_ticks / self.ticks, 4) if self.ticks else None,
                "top_reasons": dict(self.reasons.most_common(8)), "episodes_with_calls": dict(self.calls)}


def build_report(data_dir: Path):
    qa = defaultdict(_FamilyQA)
    by_split_machine = defaultdict(list)
    by_family = defaultdict(list)
    normal_only = defaultdict(list)
    rule_calls = Counter()
    episode_calls = defaultdict(Counter)
    for meta, arrays in iter_episodes(data_dir):
        family, split = meta["family"], meta["split"]
        qa[family].add(meta, arrays)
        for call in meta["outcome"]["calls"]:
            rule_calls[(family, call["service"])] += 1
        # Calls per episode, whichever node makes them: one brigade call for the farm is what matters.
        needed = set().union(*(_calls(node_labels(arrays, n)["communication"]) for n in NODE_IDS))
        made = set().union(*(_calls(arrays[f"{n.replace('-', '_')}__rule"][:, 3]) for n in NODE_IDS))
        episode_calls[split].update(needed=len(needed), made=len(made), wrong=len(made - needed),
                                    missed=len(needed - made))
        for node in NODE_IDS:
            score = score_node(node_labels(arrays, node), arrays[f"{node.replace('-', '_')}__rule"])
            machine = MACHINE_TYPES[node]
            by_split_machine[(split, machine)].append(score)
            if split in ("test", "novel_test"):
                by_family[(family, machine)].append(score)
            if family in NORMAL_FAMILIES:
                normal_only[machine].append(score)
    report = {
        "data": str(data_dir),
        "label_qa": {family: q.as_dict() for family, q in sorted(qa.items())},
        "rule_baseline": {
            "by_split": {f"{split}/{machine}": summarise(scores) for (split, machine), scores in sorted(by_split_machine.items())},
            "normal_only": {machine: summarise(scores) for machine, scores in sorted(normal_only.items())},
            "test_by_family": {f"{family}/{machine}": summarise(scores) for (family, machine), scores in sorted(by_family.items())},
            "calls_made": {f"{family}/{service}": n for (family, service), n in sorted(rule_calls.items())},
            "episode_calls": {split: dict(c) for split, c in sorted(episode_calls.items())},
        },
    }
    return report


def _fmt(value):
    return "-" if value is None else str(value)


def _pc(value):
    return "-" if value is None else f"{value:.0%}"


def to_markdown(report):
    lines = ["# Scenario library v1: label QA and rule baseline", "", f"Data: `{report['data']}`", "",
             "## Label QA by family", "",
             "| family | episodes | label onset after fault (median / p90 s) | changes per node-hour | non-NORMAL ticks | top reasons |",
             "|---|---|---|---|---|---|"]
    for family, q in report["label_qa"].items():
        onset = q["label_onset_after_fault_s"]
        reasons = ", ".join(f"{k} ({v})" for k, v in list(q["top_reasons"].items())[:4])
        lines.append(f"| {family} | {q['episodes']} | {_fmt(onset['median'])} / {_fmt(onset['p90'])} | "
                     f"{_fmt(q['label_changes_per_node_hour'])} | {_pc(q['non_normal_tick_share'])} | {reasons} |")
    lines += ["", "## Rule baseline", "",
              "Recall = labelled incidents the rule brain also reached at that level; delay = seconds after the labels "
              "(negative = earlier); false = alarms outside every labelled incident, per node-day.", "",
              "| split / machine | node-days | WATCH recall | WATCH delay med | WATCH false/day | ESC recall | ESC delay med | "
              "ESC false/day | DANGER recall | DANGER false | interference | macro-F1 | wrong calls | missed calls |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, s in report["rule_baseline"]["by_split"].items():
        if not s:
            continue
        w, e, d = s["WATCHING"], s["ESCALATE_TO_USER"], s["DANGER"]
        lines.append(f"| {key} | {s['node_days']} | {_pc(w['recall'])} | {_fmt(w['delay_median_s'])} | {_fmt(w['false_per_day'])} | "
                     f"{_pc(e['recall'])} | {_fmt(e['delay_median_s'])} | {_fmt(e['false_per_day'])} | {_pc(d['recall'])} | "
                     f"{d['false_events']} | {e['interference_events'] + d['interference_events']} | {_fmt(s['assessment_macro_f1'])} | "
                     f"{s['wrong_calls']} | {s['missed_calls']} |")
    lines += ["", "The per-node call columns charge a call to the node that made it; the next table counts calls per "
              "episode, whichever node made them.", "",
              "| split | emergency calls needed | made | wrong | missed |", "|---|---|---|---|---|"]
    for split, c in report["rule_baseline"]["episode_calls"].items():
        lines.append(f"| {split} | {c.get('needed', 0)} | {c.get('made', 0)} | {c.get('wrong', 0)} | {c.get('missed', 0)} |")
    lines += ["", "### False alarms on normal operation only", "",
              "| machine | node-days | WATCH false/day | ESC false/day | DANGER false events |", "|---|---|---|---|---|"]
    for machine, s in report["rule_baseline"]["normal_only"].items():
        lines.append(f"| {machine} | {s['node_days']} | {_fmt(s['WATCHING']['false_per_day'])} | "
                     f"{_fmt(s['ESCALATE_TO_USER']['false_per_day'])} | {s['DANGER']['false_events']} |")
    lines += ["", "### Test + novel test, by family", "",
              "| family / machine | WATCH recall | WATCH delay med | ESC recall | ESC delay med | DANGER recall | false events (W/E/D) |",
              "|---|---|---|---|---|---|---|"]
    for key, s in report["rule_baseline"]["test_by_family"].items():
        w, e, d = s["WATCHING"], s["ESCALATE_TO_USER"], s["DANGER"]
        if not (w["incidents"] or w["false_events"]):
            continue
        lines.append(f"| {key} | {_pc(w['recall'])} | {_fmt(w['delay_median_s'])} | {_pc(e['recall'])} | "
                     f"{_fmt(e['delay_median_s'])} | {_pc(d['recall'])} | {w['false_events']}/{e['false_events']}/{d['false_events']} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="runs/scenario-library-v2")
    args = parser.parse_args(argv)
    data_dir = Path(args.data)
    report = build_report(data_dir)
    (data_dir / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (data_dir / "REPORT.md").write_text(to_markdown(report), encoding="utf-8")
    print(f"wrote {data_dir / 'report.json'} and {data_dir / 'REPORT.md'}")


if __name__ == "__main__":
    main()
