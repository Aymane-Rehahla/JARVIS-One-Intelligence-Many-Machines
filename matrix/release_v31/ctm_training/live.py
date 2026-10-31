"""A trained brain inside a live Jarvis node (``NodeRuntime(..., brains=...)``).

Each node gets its own instance and its own recurrent state; the two incubators load the same
weights into two independent instances. The brain only proposes: the safety gate still decides.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import torch

from farm_poc.nodes.contracts import ASSESSMENTS, COMMUNICATIONS, DANGER_ACTIONS, DIAGNOSES, SOURCES, NodeOutput
from farm_poc.nodes.ctm_brain import _kind_for
from farm_poc.nodes.rule_brain import RuleBrain

from .decide import Decider, apply, head_choices
from .model import decode
from .train import load_brain


class TrainedNodeBrain:
    name = "ctm"

    def __init__(self, brain, start_hour: float = 0.0, decider: Decider | None = None, danger_net: bool = False,
                 hide_notes: bool = False, hold_calls: bool = False):
        """``decider``: the tuned decision layer (``decide.json`` next to the checkpoint); None = plain argmax.

        ``hold_calls`` (off unless asked for): once this brain itself asks for the fire brigade or civil
        protection at DANGER, the request stays on for as long as it stays at DANGER (on ticks where it would
        otherwise say nothing). The gate still checks the evidence every tick and drops duplicates; dropping
        below DANGER drops the request. The brain cannot see whether a call went through (notes are hidden),
        so without this it asks for a few seconds, often before the evidence is there, and forgets.

        ``danger_net`` (off unless asked for): the rule brain runs beside the CTM as a fixed safety
        interlock; when the rules see DANGER and the CTM does not, the rules' DANGER decision is used and
        the output says so (``brain="ctm+rule-danger-net"``, evidence ``safety_net=rules``). Scores with
        the net on are never reported as the CTM's own."""
        self.brain, self.machine = brain, brain.config.machine
        self.start_hour = float(start_hour)
        self.decider = decider
        self.rules = RuleBrain() if danger_net else None
        self.hide_notes = hide_notes   # farm-life design: the memory notes (made by the rules' decisions) are never read
        self.hold_calls = hold_calls
        self.reset()
        self.last = {}

    @classmethod
    def from_checkpoint(cls, checkpoint: Path, packed: Path, start_hour: float = 0.0, danger_net: bool = False,
                        hide_notes: bool = False, hold_calls: bool = False):
        return cls(load_brain(checkpoint, packed, "cpu"), start_hour, Decider.for_checkpoint(checkpoint), danger_net,
                   hide_notes, hold_calls)

    def reset(self):
        self.state = self.brain.initial_state(1, "cpu")
        self.decision_state = self.decider.initial_state(1) if self.decider else None
        self.held_call = None

    @torch.no_grad()
    def decide(self, ctx, encoded) -> NodeOutput:
        values = torch.tensor([encoded.values], dtype=torch.float32)
        if self.hide_notes:
            values[:, self.brain.layout.notes] = 0.0
        validity = torch.tensor([encoded.validity], dtype=torch.float32)
        tod = torch.tensor([(self.start_hour + ctx.time_s / 3600.0) % 24.0])
        out, self.state = self.brain.adaptive_tick(values, validity, tod, self.state)
        if self.decider:
            level, self.decision_state = self.decider.step(out["assessment"], self.decision_state)
            a, d, act, comm, src = (int(v) for v in apply(level, head_choices(out))[0])
        else:
            a, d, act, comm, src = (int(v) for v in decode(out, self.machine)[0])
        held = False
        if self.hold_calls:
            if ASSESSMENTS[a] != "DANGER":
                self.held_call = None
            elif COMMUNICATIONS[comm].startswith("call_"):
                self.held_call = comm
            elif self.held_call is not None and COMMUNICATIONS[comm] == "none":
                comm, held = self.held_call, True
        probabilities = torch.softmax(out["assessment"], -1)[0]
        assessment, diagnosis = ASSESSMENTS[a], DIAGNOSES[self.machine][d]
        evidence = [f"certainty={float(probabilities.max()):.3f}"]
        if src:   # where this node places the cause: itself, another machine, outside, or the site
            evidence.append(f"cause={SOURCES[src]}")
        if "harm" in out:
            evidence.append(f"minutes_to_danger={float(np.expm1(torch.sigmoid(out['harm'])[0] * np.log1p(1800.0)) / 60):.1f}")
        if bool(self.state["focus"][0]):
            evidence.append("focused")
        if held:
            evidence.append("call_held")
        self.last = {"probabilities": probabilities.tolist(), "evidence": evidence, "cause": SOURCES[src], "net": False}
        if self.rules is not None and assessment != "DANGER":
            interlock = self.rules.decide(ctx)
            if interlock.assessment == "DANGER":   # the fixed safety layer underneath the learned one
                self.last["net"] = True
                return replace(interlock, evidence=tuple(interlock.evidence) + ("safety_net=rules", *evidence),
                               brain="ctm+rule-danger-net")
        return NodeOutput(ctx.node_id, ctx.tick, assessment, diagnosis, float(probabilities.max()),
                          _kind_for(self.machine, diagnosis) if assessment != "NORMAL" else None, "",
                          DANGER_ACTIONS[self.machine][act], COMMUNICATIONS[comm], tuple(evidence),
                          {f"assessment.{n}": float(p) for n, p in zip(ASSESSMENTS, probabilities)}, self.name)
