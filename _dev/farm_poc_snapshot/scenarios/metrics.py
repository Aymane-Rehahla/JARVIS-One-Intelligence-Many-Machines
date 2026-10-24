"""Scoring a node brain against the labels: fault caught, false alarms, speed.

One set of definitions scores the rule-based baseline (recorded in every episode)
and the trained CTMs (Phase 5) on the same recorded inputs, so the comparison is
like for like. A tick is one simulated second.

For each level L (WATCHING, ESCALATE_TO_USER, DANGER), "at L" means L or higher:

- **incident**: a run of ticks the labels put at L (gaps under ``MERGE_GAP`` joined).
- **caught**: the brain reaches L inside the incident's window, which opens at the
  start of the enclosing alarm (WATCHING) incident minus ``EARLY_TOLERANCE`` (the
  label onset trails the first visible change by up to ~15 ticks) and closes at
  the incident's end. Escalating earlier than the labels, inside the same alarm,
  is allowed.
- **delay_s**: brain's first tick at L in that window minus the incident's first
  tick (negative = earlier than the labels).
- **false**: a run of brain ticks at L outside every incident window extended by
  ``LATE_TOLERANCE`` (a brain may keep watching for a few minutes after the labels
  relax). Runs under ``MERGE_GAP`` apart count once.
- **interference**: a false escalation or DANGER while the labels say
  HANDLED_BY_MACHINE: exactly what the owner asked Jarvis not to do.
- **calls**: fire brigade / civil protection made by the brain but never by the
  labels (wrong) or the reverse (missed), per episode.
"""
from __future__ import annotations

import numpy as np

from ..nodes.contracts import ASSESSMENTS, COMMUNICATIONS

STEP_S = 1.0
EARLY_TOLERANCE = 15
LATE_TOLERANCE = 300
MERGE_GAP = 60
LEVELS = ("WATCHING", "ESCALATE_TO_USER", "DANGER")
_RANK = {name: n for n, name in enumerate(ASSESSMENTS)}
_HANDLED = _RANK["HANDLED_BY_MACHINE"]
_CALLS = {COMMUNICATIONS.index("call_fire_brigade"): "fire_brigade",
          COMMUNICATIONS.index("call_civil_protection"): "civil_protection"}
_NOTIFY = COMMUNICATIONS.index("notify_owner")


def runs(mask, gap=MERGE_GAP):
    """[(start, end_inclusive)] of True runs, joining runs separated by fewer than ``gap`` False ticks."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > gap)
    starts = np.concatenate([[idx[0]], idx[breaks + 1]])
    ends = np.concatenate([idx[breaks], [idx[-1]]])
    return list(zip(starts.tolist(), ends.tolist()))


def _window_mask(length, spans):
    mask = np.zeros(length, dtype=bool)
    for start, end in spans:
        mask[max(0, start):min(length, end + 1)] = True
    return mask


def score_node(label, pred):
    """label: dict with int arrays ``assessment``/``diagnosis``/``communication`` [T];
    pred: int array [T, 4] (assessment, diagnosis, action, communication), the rule-output layout."""
    label_a = np.asarray(label["assessment"], dtype=np.int64)
    pred_a = np.asarray(pred[:, 0], dtype=np.int64)
    ticks = label_a.size
    alarms = runs(label_a >= _RANK["WATCHING"])
    result = {"ticks": int(ticks)}
    for level in LEVELS:
        rank = _RANK[level]
        incidents = runs(label_a >= rank)
        windows = []
        for start, end in incidents:
            enclosing = next((a for a, b in alarms if a <= start <= b), start)
            windows.append((enclosing - EARLY_TOLERANCE, end))
        caught, delays = 0, []
        for (start, _), (open_at, close_at) in zip(incidents, windows):
            hits = np.flatnonzero(pred_a[max(0, open_at):close_at + 1] >= rank)
            if hits.size:
                caught += 1
                delays.append(float((max(0, open_at) + hits[0] - start) * STEP_S))
        allowed = _window_mask(ticks, [(a, b + LATE_TOLERANCE) for a, b in windows])
        false_mask = (pred_a >= rank) & ~allowed
        false_runs = runs(false_mask)
        entry = {"incidents": len(incidents), "caught": caught, "delays_s": delays,
                 "false_events": len(false_runs), "false_ticks": int(false_mask.sum())}
        if rank >= _RANK["ESCALATE_TO_USER"]:
            handled = label_a == _HANDLED
            entry["interference_events"] = sum(1 for a, b in false_runs if handled[a:b + 1].any())
        result[level] = entry
    both = (label_a >= _HANDLED) & (pred_a >= _HANDLED)
    label_d = np.asarray(label["diagnosis"], dtype=np.int64)
    result["diagnosis_ticks"] = int(both.sum())
    result["diagnosis_correct"] = int((label_d[both] == np.asarray(pred[:, 1])[both]).sum())
    confusion = np.zeros((len(ASSESSMENTS), len(ASSESSMENTS)), dtype=np.int64)
    np.add.at(confusion, (label_a, pred_a), 1)
    result["confusion"] = confusion.tolist()
    label_calls = {_CALLS[c] for c in np.unique(label["communication"]) if c in _CALLS}
    pred_calls = {_CALLS[c] for c in np.unique(pred[:, 3]) if c in _CALLS}
    result["calls"] = {"labels": sorted(label_calls), "brain": sorted(pred_calls),
                       "wrong": sorted(pred_calls - label_calls), "missed": sorted(label_calls - pred_calls)}
    result["notify_runs"] = {"labels": len(runs(np.asarray(label["communication"]) == _NOTIFY, gap=0)),
                             "brain": len(runs(pred[:, 3] == _NOTIFY, gap=0))}
    return result


def summarise(scores):
    """Aggregate a list of ``score_node`` results into headline numbers."""
    if not scores:
        return {}
    ticks = sum(s["ticks"] for s in scores)
    days = ticks * STEP_S / 86400.0
    out = {"node_episodes": len(scores), "node_days": round(days, 2)}
    for level in LEVELS:
        incidents = sum(s[level]["incidents"] for s in scores)
        caught = sum(s[level]["caught"] for s in scores)
        delays = [d for s in scores for d in s[level]["delays_s"]]
        false_events = sum(s[level]["false_events"] for s in scores)
        entry = {"incidents": incidents, "caught": caught,
                 "recall": round(caught / incidents, 4) if incidents else None,
                 "delay_median_s": round(float(np.median(delays)), 1) if delays else None,
                 "delay_p90_s": round(float(np.percentile(delays, 90)), 1) if delays else None,
                 "false_events": false_events,
                 "false_per_day": round(false_events / days, 3) if days else None}
        if level != "WATCHING":
            entry["interference_events"] = sum(s[level].get("interference_events", 0) for s in scores)
        out[level] = entry
    confusion = np.sum([np.array(s["confusion"]) for s in scores], axis=0)
    f1 = []
    for k in range(len(ASSESSMENTS)):
        tp = confusion[k, k]
        precision = tp / confusion[:, k].sum() if confusion[:, k].sum() else 0.0
        recall = tp / confusion[k, :].sum() if confusion[k, :].sum() else 0.0
        if confusion[k, :].sum():
            f1.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    out["assessment_macro_f1"] = round(float(np.mean(f1)), 4) if f1 else None
    out["tick_accuracy"] = round(float(np.trace(confusion) / confusion.sum()), 4)
    diag_ticks = sum(s["diagnosis_ticks"] for s in scores)
    out["diagnosis_accuracy"] = round(sum(s["diagnosis_correct"] for s in scores) / diag_ticks, 4) if diag_ticks else None
    out["wrong_calls"] = sum(len(s["calls"]["wrong"]) for s in scores)
    out["missed_calls"] = sum(len(s["calls"]["missed"]) for s in scores)
    out["needed_calls"] = sum(len(s["calls"]["labels"]) for s in scores)
    return out
