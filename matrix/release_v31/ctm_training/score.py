"""How a brain is judged: the same per-episode scoring as the rule baseline, and one fixed composite.

The composite (higher is better) is fixed before any training, so upgrades cannot be cherry-picked:

    S = recall:   0.2 WATCHING + 0.4 ESCALATE + 0.4 DANGER   (weights renormalised over levels that occur)
      + 0.1 * assessment macro-F1
      - false alarms per node-day: 0.02 WATCHING, 0.15 ESCALATE, 1.0 DANGER
      - 0.3 * interference events per node-day (escalating when the machine handled it)
      - 0.05 * median ESCALATE delay in minutes, capped at 10, divided by 10
      + 0.1 * link accuracy (the cause named correctly, when both labels and brain see a problem)

The link term is what Jarvis is for: seeing what no single machine can, by naming which machine
(or the weather, or the site) caused what this node sees.
"""
from __future__ import annotations

import numpy as np
import torch

from farm_poc.scenarios.metrics import score_node, summarise

from .decide import apply, head_choices
from .model import decode

RECALL_WEIGHTS = {"WATCHING": 0.2, "ESCALATE_TO_USER": 0.4, "DANGER": 0.4}
FALSE_WEIGHTS = {"WATCHING": 0.02, "ESCALATE_TO_USER": 0.15, "DANGER": 1.0}


def composite(summary, link_accuracy=None) -> float:
    if not summary:
        return float("-inf")
    recall = {level: summary[level]["recall"] for level in RECALL_WEIGHTS if summary[level]["recall"] is not None}
    total = sum(RECALL_WEIGHTS[level] for level in recall) or 1.0
    score = sum(RECALL_WEIGHTS[level] * value for level, value in recall.items()) / total
    score += 0.1 * (summary.get("assessment_macro_f1") or 0.0)
    days = max(summary["node_days"], 1e-6)
    for level, weight in FALSE_WEIGHTS.items():
        score -= weight * summary[level]["false_events"] / days
    interference = summary["ESCALATE_TO_USER"]["interference_events"] + summary["DANGER"]["interference_events"]
    score -= 0.3 * interference / days
    delay = summary["ESCALATE_TO_USER"]["delay_median_s"]
    if delay is not None:
        score -= 0.05 * min(max(delay / 60.0, 0.0), 10.0) / 10.0
    if link_accuracy is not None:
        score += 0.1 * link_accuracy
    return round(float(score), 5)


@torch.no_grad()
def predict(brain, split, device, group=128, chunk=512, decider=None, hide_notes=False):
    """Run the brain over every episode of a split from tick 0, as deployed (adaptive thinking included,
    and the decision layer when ``decider`` is given).

    Returns {row index in split.index: int8 [T, 5] decisions (assessment, diagnosis, action, communication, source)}."""
    brain.eval()
    order = sorted(range(len(split.index)), key=lambda i: split.index[i]["length"])
    a = split.arrays
    predictions = {}
    for g in range(0, len(order), group):
        members = order[g:g + group]
        infos = [split.index[i] for i in members]
        longest = max(info["length"] for info in infos)
        state = brain.initial_state(len(members), device)
        decision_state = decider.initial_state(len(members)) if decider else None
        outputs = np.zeros((len(members), longest, 5), dtype=np.int8)
        for t0 in range(0, longest, chunk):
            span = min(chunk, longest - t0)
            rows = np.stack([info["start"] + np.minimum(np.arange(t0, t0 + span), info["length"] - 1) for info in infos])
            values = torch.tensor(a["values"][rows], dtype=torch.float32, device=device)
            if hide_notes:   # the farm-life design: the brain never sees its memory notes
                values[:, :, brain.layout.notes] = 0.0
            validity = torch.tensor(a["validity"][rows], dtype=torch.float32, device=device)
            tod = torch.tensor(a["tod"][rows], dtype=torch.float32, device=device)
            for t in range(span):
                out, state = brain.adaptive_tick(values[:, t], validity[:, t], tod[:, t], state)
                if decider:
                    levels, decision_state = decider.step(out["assessment"], decision_state)
                    outputs[:, t0 + t] = apply(levels, head_choices(out))
                else:
                    outputs[:, t0 + t] = decode(out, brain.config.machine).cpu().numpy()
        for slot, i in enumerate(members):
            predictions[i] = outputs[slot, :split.index[i]["length"]]
    brain.train()
    return predictions


def _collect(split, predictions=None, families=None):
    scores, linked, named = [], 0, 0
    labels = split.arrays["labels"]
    for i, info in enumerate(split.index):
        if families and info["family"] not in families:
            continue
        span = slice(info["start"], info["start"] + info["length"])
        label = {"assessment": labels[span, 0], "diagnosis": labels[span, 1], "communication": labels[span, 3]}
        pred = predictions[i] if predictions is not None else split.arrays["rule"][span]
        scores.append(score_node(label, pred))
        if predictions is not None and labels.shape[1] > 4:
            both = (labels[span, 0] >= 2) & (pred[:, 0] >= 2) & (labels[span, 4] > 0)
            linked += int(both.sum())
            named += int((pred[both, 4] == labels[span, 4][both]).sum())
    return scores, linked, named


def evaluate(split, predictions=None, families=None):
    """Summaries for the brain (``predictions``) or, when None, for the rule baseline stored in the data."""
    return evaluate_many([(split, predictions)], families)


def evaluate_many(pairs, families=None):
    """One summary over several splits' episodes pooled ([(split, predictions or None), ...]): each episode
    counts by its own length, so a farm-life day weighs as much as the time it lasts."""
    scores, linked, named = [], 0, 0
    for split, predictions in pairs:
        s, l, n = _collect(split, predictions, families)
        scores, linked, named = scores + s, linked + l, named + n
    summary = summarise(scores)
    link_accuracy = round(named / linked, 4) if linked else None
    return {"summary": summary, "score": composite(summary, link_accuracy), "link_accuracy": link_accuracy}
