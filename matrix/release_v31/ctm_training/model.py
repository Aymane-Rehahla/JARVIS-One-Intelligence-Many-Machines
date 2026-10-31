"""The Testbed node brain: perception -> backbone -> Sakana CTM core (unmodified) -> heads.

The original CTM source is imported, never edited. Two things are configured on our
instance only:
- its backbone is replaced by an identity, because our own perception layer and backbone
  compute the features once per farm tick (the CTM would otherwise recompute them on
  every internal step);
- on DirectML, ``nn.GLU`` is swapped for the identical formula a * sigmoid(b), because the
  RX 580 backend has no GLU kernel and would bounce every call through the CPU.

Each farm tick the core thinks ``k`` internal steps. We call it one step at a time, so the
output after every step is available: Sakana's certainty-based loss needs it, and adaptive
thinking can stop early. The maths is identical to one call with ``iterations=k``.

Upgrades (each can be switched off, to prove it is worth keeping):
- U1 ``certainty_loss``: longer thinking scored with Sakana's loss (best step + most certain step).
- U2 ``perception``: z-scores against learned normal, changes over 10 s/1 min/10 min/30 min, time of day.
- U3 ``expectation``: predict the next own readings; the surprise is fed back as input.
- U4 ``long_horizon``: training only (whole episodes from tick 0, longer chunks).
- U5 ``adaptive``: think 2 steps when calm, all steps when surprised or alarmed.
- U6 ``twin_head``: predict what a healthy machine would read now (the fault-free twin).
- U7 ``harm_head``: predict the time until the situation becomes dangerous.

Always on: the ``source`` head, which names where the cause is (itself, another machine, the
weather outside, the site), so a node can link what it sees to what the others see.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from pathlib import Path
import sys

import torch
from torch import nn

from farm_poc.nodes.contracts import ASSESSMENTS, COMMUNICATIONS, DANGER_ACTIONS, DIAGNOSES, SOURCES

UPSTREAM = Path(__file__).resolve().parents[1] / "base ctm" / "continuous-thought-machines-main"
TIMESCALES_S = (10.0, 60.0, 600.0, 1800.0)
Z_CLIP = 8.0


def upstream_ctm():
    if str(UPSTREAM) not in sys.path:
        sys.path.insert(0, str(UPSTREAM))
    from models.ctm_rl import ContinuousThoughtMachineRL
    return ContinuousThoughtMachineRL


@dataclass
class BrainConfig:
    machine: str
    d_model: int = 96
    iterations: int = 6
    memory_length: int = 12
    n_synch: int = 16
    d_input: int = 64
    deep_nlms: bool = True
    memory_hidden: int = 4
    certainty_loss: bool = False
    perception: bool = True     # linking needs the trends of its own and its peers' readings
    expectation: bool = False
    long_horizon: bool = False
    adaptive: bool = False
    twin_head: bool = False
    harm_head: bool = False
    calm_iterations: int = 2

    @classmethod
    def baseline(cls, machine):
        """The Phase 2 brain as it was: 3 thinking steps, a 4-step trace, linear neuron models."""
        return cls(machine, d_model=48, iterations=3, memory_length=4, n_synch=12, d_input=32, deep_nlms=False,
                   memory_hidden=2, perception=False)

    def as_dict(self):
        return asdict(self)


class GLUCompat(nn.Module):
    """nn.GLU's exact formula, built from ops every backend has."""

    def __init__(self, dim=-1):
        super().__init__()
        self.dim = dim

    def forward(self, x):
        a, b = x.chunk(2, dim=self.dim)
        return a * torch.sigmoid(b)


def swap_glu(module: nn.Module) -> nn.Module:
    for name, child in module.named_children():
        if isinstance(child, nn.GLU):
            setattr(module, name, GLUCompat(child.dim))
        else:
            swap_glu(child)
    return module


class TestbedBrain(nn.Module):
    def __init__(self, config: BrainConfig, layout, stats):
        super().__init__()
        self.config, self.layout = config, layout
        self.register_buffer("mean", torch.tensor(stats["mean"]))
        self.register_buffer("std", torch.tensor(stats["std"]))
        self.register_buffer("numeric", torch.tensor(layout.numeric, dtype=torch.long))
        self.register_buffer("flags", torch.tensor(layout.flags, dtype=torch.long))
        self.register_buffer("own", torch.tensor(layout.own_numeric, dtype=torch.long))
        self.register_buffer("alpha", torch.tensor([1.0 - math.exp(-1.0 / t) for t in TIMESCALES_S]))
        n_num, n_own, n_all = len(layout.numeric), len(layout.own_numeric), layout.n_features
        width = (n_num + len(layout.flags) + n_all + n_num * len(TIMESCALES_S) + 2) if config.perception else 2 * n_all
        if config.expectation:
            width += n_own
        d = config.d_input
        self.backbone = nn.Sequential(nn.Linear(width, 2 * d), nn.GLU(), nn.LayerNorm(d),
                                      nn.Linear(d, 2 * d), nn.GLU(), nn.LayerNorm(d))
        CTM = upstream_ctm()
        self.core = CTM(iterations=1, d_model=config.d_model, d_input=d, n_synch_out=config.n_synch, synapse_depth=1,
                        memory_length=config.memory_length, deep_nlms=config.deep_nlms,
                        memory_hidden_dims=config.memory_hidden, do_layernorm_nlm=False,
                        backbone_type="classic-control-backbone", neuron_select_type="first-last")
        self.core.backbone = nn.Identity()   # features come from our backbone, once per farm tick
        with torch.no_grad():
            self.core(torch.zeros(1, d), self._fresh_core(1, "cpu"))   # materialise the lazy synapse layer
        rep = self.core.synch_representation_size_out
        machine = config.machine
        self.assessment = nn.Linear(rep, len(ASSESSMENTS))
        self.diagnosis = nn.Linear(rep, len(DIAGNOSES[machine]))
        self.action = nn.Linear(rep, len(DANGER_ACTIONS[machine]))
        self.communication = nn.Linear(rep, len(COMMUNICATIONS))
        self.source = nn.Linear(rep, len(SOURCES))
        self.expect = nn.Linear(rep, n_own) if config.expectation else None
        self.twin = nn.Linear(rep, n_own) if config.twin_head else None
        self.harm = nn.Linear(rep, 1) if config.harm_head else None

    # ------------------------------------------------------------ state
    def _fresh_core(self, batch, device):
        return (self.core.start_trace.unsqueeze(0).expand(batch, -1, -1).to(device),
                self.core.start_activated_trace.unsqueeze(0).expand(batch, -1, -1).to(device))

    def initial_state(self, batch, device):
        n_num, n_own = len(self.layout.numeric), len(self.layout.own_numeric)
        return {"core": self._fresh_core(batch, device),
                "ema": torch.zeros(batch, n_num, len(TIMESCALES_S), device=device),
                "seen": torch.zeros(batch, n_num, dtype=torch.bool, device=device),
                "expected": torch.zeros(batch, n_own, device=device),
                "has_expected": torch.zeros(batch, 1, device=device),
                "focus": torch.ones(batch, dtype=torch.bool, device=device)}

    @staticmethod
    def reset_rows(state, fresh, rows):
        """Replace the state of streams ``rows`` (bool [B]) with a fresh one."""
        out = {}
        for key, value in state.items():
            if key == "core":
                out[key] = tuple(torch.where(rows.view(-1, 1, 1), f, v) for f, v in zip(fresh[key], value))
            else:
                mask = rows.view(-1, *([1] * (value.dim() - 1)))
                out[key] = torch.where(mask, fresh[key], value)
        return out

    @staticmethod
    def detach(state):
        return {k: (tuple(t.detach() for t in v) if k == "core" else v.detach()) for k, v in state.items()}

    # ------------------------------------------------------------ one farm tick
    def own_z(self, values):
        own = self.own
        return ((values[:, own] - self.mean[own]) / self.std[own]).clamp(-Z_CLIP, Z_CLIP)

    def perceive(self, values, validity, tod, state):
        cfg = self.config
        if cfg.perception:
            num, valid_num = self.numeric, validity[:, self.numeric] > 0.5
            # flags as numbers before multiplying: the same values on CPU, and DirectML would otherwise give flags
            z = ((values[:, num] - self.mean[num]) / self.std[num]).clamp(-Z_CLIP, Z_CLIP) * valid_num.to(values.dtype)
            first = valid_num & ~state["seen"]
            ema = torch.where(first.unsqueeze(-1), z.unsqueeze(-1), state["ema"])
            updated = ema + self.alpha * (z.unsqueeze(-1) - ema)
            ema = torch.where(valid_num.unsqueeze(-1), updated, ema)
            deltas = (z.unsqueeze(-1) - ema) * valid_num.unsqueeze(-1).to(values.dtype)
            angle = tod * (2 * math.pi / 24.0)
            parts = [z, values[:, self.flags], validity, deltas.flatten(1), torch.sin(angle)[:, None], torch.cos(angle)[:, None]]
            state = {**state, "ema": ema, "seen": state["seen"] | valid_num}
        else:
            parts = [values, validity]
        if cfg.expectation:
            actual = self.own_z(values)
            valid_own = validity[:, self.own]
            surprise = ((actual - state["expected"]).abs() * valid_own * state["has_expected"]).detach()
            parts.append(surprise.clamp(0, Z_CLIP))
        return torch.cat(parts, dim=1), state

    def think(self, features, core_state, steps, every_step=False):
        """``steps`` internal thinking steps. Synchronisation (the output) is read after every step only
        when something needs it (the certainty loss); otherwise one call thinks all steps, the same maths
        at a fraction of the cost (synchronisation over a long trace is the expensive part)."""
        if not every_step:
            self.core.iterations = steps
            try:
                rep, core_state = self.core(features, core_state)
            finally:
                self.core.iterations = 1
            return [rep], core_state
        reps = []
        for _ in range(steps):
            rep, core_state = self.core(features, core_state)
            reps.append(rep)
        return reps, core_state

    def tick(self, values, validity, tod, state, steps=None):
        """One farm tick for a batch of streams. Returns head outputs and the next state.

        ``assessment_steps`` holds the assessment logits after every internal step [k, B, 5]."""
        cfg = self.config
        x, state = self.perceive(values, validity, tod, state)
        features = self.backbone(x)
        steps = steps or cfg.iterations
        reps, core_state = self.think(features, state["core"], steps, every_step=cfg.certainty_loss)
        out = {"assessment_steps": torch.stack([self.assessment(r) for r in reps])}
        final = reps[-1]
        if cfg.certainty_loss:  # read the decision at the internal step it was most certain about
            probs = torch.softmax(out["assessment_steps"], dim=-1)
            entropy = -(probs * probs.clamp_min(1e-9).log()).sum(-1) / math.log(probs.shape[-1])
            pick = (1 - entropy).argmax(0)
            final = torch.stack(reps)[pick, torch.arange(final.shape[0], device=final.device)]
            out["certainty"] = (1 - entropy).max(0).values
        out.update(assessment=self.assessment(final), diagnosis=self.diagnosis(final), action=self.action(final),
                   communication=self.communication(final), source=self.source(final))
        if self.expect is not None:
            out["expected_next"] = self.expect(final)
        if self.twin is not None:
            out["healthy"] = self.twin(final)
        if self.harm is not None:
            out["harm"] = self.harm(final).squeeze(-1)
        state = {**state, "core": core_state}
        if self.expect is not None:
            state["expected"] = out["expected_next"].detach()
            state["has_expected"] = torch.ones_like(state["has_expected"])
        return out, state

    def adaptive_tick(self, values, validity, tod, state):
        """Inference with adaptive thinking: streams that are calm stop after ``calm_iterations`` steps."""
        cfg = self.config
        if not cfg.adaptive:
            return self.tick(values, validity, tod, state)
        x, state = self.perceive(values, validity, tod, state)
        features = self.backbone(x)
        calm = ~state["focus"]
        if cfg.certainty_loss:   # the most certain step is read, so every step's output is needed
            core_state, reps = state["core"], []
            for step in range(cfg.iterations):
                rep, nxt = self.core(features, core_state)
                if step >= cfg.calm_iterations:  # calm streams keep the state they had after their last step
                    hold = calm.view(-1, 1, 1)
                    nxt = tuple(torch.where(hold, c, n) for c, n in zip(core_state, nxt))
                    rep = torch.where(calm.view(-1, 1), reps[-1], rep)
                core_state = nxt
                reps.append(rep)
        else:   # think the calm steps, then the rest only matters for focused streams
            calm_reps, calm_state = self.think(features, state["core"], cfg.calm_iterations)
            full_reps, full_state = self.think(features, calm_state, cfg.iterations - cfg.calm_iterations)
            core_state = tuple(torch.where(calm.view(-1, 1, 1), c, f) for c, f in zip(calm_state, full_state))
            reps = [torch.where(calm.view(-1, 1), calm_reps[-1], full_reps[-1])]
        out = {"assessment_steps": torch.stack([self.assessment(r) for r in reps])}
        final = reps[-1]
        if cfg.certainty_loss:
            probs = torch.softmax(out["assessment_steps"], dim=-1)
            entropy = -(probs * probs.clamp_min(1e-9).log()).sum(-1) / math.log(probs.shape[-1])
            final = torch.stack(reps)[(1 - entropy).argmax(0), torch.arange(final.shape[0], device=final.device)]
        out.update(assessment=self.assessment(final), diagnosis=self.diagnosis(final), action=self.action(final),
                   communication=self.communication(final), source=self.source(final))
        if self.expect is not None:
            out["expected_next"] = self.expect(final)
        state = {**state, "core": core_state}
        if self.expect is not None:
            surprise = ((self.own_z(values) - state["expected"]).abs() * validity[:, self.own]).mean(1)
            state["expected"], state["has_expected"] = out["expected_next"], torch.ones_like(state["has_expected"])
        else:
            surprise = torch.zeros(values.shape[0], device=values.device)
        # Focus next tick when something is surprising, uncertain, or not normal.
        alarmed = out["assessment"].argmax(-1) > 0
        unsure = torch.softmax(out["assessment"], -1).max(-1).values < 0.9
        state["focus"] = alarmed | unsure | (surprise > 1.0)
        return out, state


def decode(out, machine):
    """Consistent decisions from the heads: a diagnosis and a named cause only when not NORMAL, an action
    only in DANGER, a message only when escalating. Columns: assessment, diagnosis, action, communication, source."""
    assessment = out["assessment"].argmax(-1)
    diagnosis_logits = out["diagnosis"].clone()
    diagnosis_logits[:, 0] = -1e9                      # NONE is implied by NORMAL, never chosen otherwise
    diagnosis = torch.where(assessment > 0, diagnosis_logits.argmax(-1), torch.zeros_like(assessment))
    action = torch.where(assessment == 4, out["action"].argmax(-1), torch.zeros_like(assessment))
    communication = torch.where(assessment >= 3, out["communication"].argmax(-1), torch.zeros_like(assessment))
    source = torch.where(assessment > 0, out["source"].argmax(-1), torch.zeros_like(assessment))
    return torch.stack([assessment, diagnosis, action, communication, source], dim=-1)
