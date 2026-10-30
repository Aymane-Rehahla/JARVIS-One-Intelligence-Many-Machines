"""Export what the trained JARVIS brains really are and really do, for the website's mind pages.

Run it with the Python that has torch (the one the brains were trained with; never install into it), with -B:

    <python> -B tools/export_mind.py --release <release folder> --upstream <upstream CTM folder> --out interface/web/public/mind

Read-only on both folders: no .pyc (-B), every database in a temporary folder, output written to a temporary
folder, scanned, and only then moved to --out. Never write either input path into a file.

It loads the three release engines (structure, parameter counts, the learned synchronisation decays), runs one
ordinary library episode with the trained brains built the way the release README builds them (one torch thread,
notes hidden, held calls on, a CMM per node), keeps a few real thoughts of every node, replays each one step by step
(checked equal to what the brain computed), and dumps every node's CMM after the run. Nothing is invented: every
number comes from the weights or from that run. MIND.md says what each file holds.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import sys
import tempfile
import time

FORMAT = 1
ENGINES = {"incubator": "reasoning_engine_INCv3.1.pt", "hvac": "reasoning_engine_HVACv3.1.pt",
           "animal_protection": "reasoning_engine_APv3.1.pt"}
TOP_K = 6          # strongest incoming influences kept per neuron (each neuron is fed by all 256)
TOP_INPUTS = 12    # most unusual readings kept per thought
CALM_BEFORE = 30   # the calm thought: this many ticks before the first fault
FORBIDDEN = ("thir" + "duni", ":\\users", ":/users", "f:\\", "f:/")


def sig(x: float, digits: int = 4) -> float:
    value = float(f"{float(x):.{digits}g}")
    if not math.isfinite(value):
        raise ValueError(f"not a finite number: {x}")
    return value


def sig_list(values, digits: int = 4) -> list:
    if hasattr(values, "tolist"):
        values = values.tolist()
    return [sig_list(v, digits) if isinstance(v, list) else sig(v, digits) for v in values]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count(module) -> int:
    return sum(p.numel() for p in module.parameters())


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--family", default="overvoltage")
    parser.add_argument("--variant", type=int, default=None,
                        help="library variant; default: the first ordinary overvoltage variant hitting both incubators")
    args = parser.parse_args()
    if not sys.flags.dont_write_bytecode:
        parser.error("run with python -B (no .pyc may be written into the release)")
    sys.path[:0] = [str(args.release / "code_snapshot"), str(args.upstream)]

    import numpy as np
    import torch

    torch.set_num_threads(1)
    from ctm_training.decide import Decider
    from ctm_training.live import TrainedNodeBrain
    from ctm_training.train import load_brain
    from farm_poc.matrix.engine import MatrixEngine
    from farm_poc.nodes import NODE_IDS, NodeRuntime
    from farm_poc.nodes.cmm import CognitiveMemory
    from farm_poc.nodes.contracts import ASSESSMENTS, COMMUNICATIONS, DANGER_ACTIONS, DIAGNOSES, MACHINE_TYPES, SOURCES
    from farm_poc.scenarios.episode import _apply_initial, _benign, _faults
    from farm_poc.scenarios.library import _make

    engines = {machine: args.release / "brains" / machine / name for machine, name in ENGINES.items()}
    loaded = {machine: load_brain(path, args.release / "brains", "cpu") for machine, path in engines.items()}

    # ------------------------------------------------------------------ architecture (from the weights)
    def architecture(machine: str) -> dict:
        brain, core = loaded[machine], loaded[machine].core
        cfg, layout = brain.config, brain.layout
        steps, memory, n_synch = cfg.iterations, cfg.memory_length, cfg.n_synch
        direct = {name: p.numel() for name, p in core.named_parameters(recurse=False)}
        # Which neurons the output synchronisation really reads: the RL core slices the last n_synch neurons.
        trace = torch.randn(1, core.d_model, memory, generator=torch.Generator().manual_seed(0))
        with torch.no_grad():
            used = core.compute_synchronisation(trace)
            decay = torch.exp(-torch.arange(memory - 1, -1, -1).float().unsqueeze(1)
                              * core.decay_params_out.clamp(0, 4).unsqueeze(0))
            mask = core.diagonal_mask_out
            def sync_of(idx):
                s = trace.permute(0, 2, 1)[:, :, idx]
                return (decay.unsqueeze(0) * (s.unsqueeze(-1) * s.unsqueeze(-2))[:, :, mask]).sum(1) / decay.sum(0).sqrt()
            last = list(range(core.d_model - n_synch, core.d_model))
            first = list(range(n_synch))
            reads_last = bool(torch.allclose(used, sync_of(last), atol=1e-6))
            reads_first = bool(torch.allclose(used, sync_of(first), atol=1e-6))
        if not reads_last or reads_first:
            raise RuntimeError(f"{machine}: could not confirm which neurons the synchronisation reads")
        pairs = [[last[i], last[j]] for i in range(n_synch) for j in range(n_synch) if bool(mask[i, j])]
        rates = core.decay_params_out.detach().clamp(0, 4)
        return {
            "machine": machine,
            "engine_file": ENGINES[machine],
            "engine_sha256": sha256(engines[machine]),
            "config": cfg.as_dict(),
            "neurons": core.d_model,
            "internal_steps_per_tick": steps,
            "adaptive_thinking": bool(cfg.adaptive),
            "memory_length": memory,
            "memory_meaning": (f"each neuron keeps its last {memory} pre-activations; with {steps} steps every tick the "
                               f"window holds the previous tick's {steps} steps and this tick's {steps}"
                               if memory == 2 * steps else f"each neuron keeps its last {memory} pre-activations"),
            "inputs": {"features": layout.n_features, "numeric": len(layout.numeric), "flags": len(layout.flags),
                       "own_numeric": len(layout.own_numeric), "notes_hidden": len(layout.notes),
                       "perception_width": brain.backbone[0].in_features, "backbone_output": cfg.d_input},
            "parameters": {
                "total": count(brain),
                "perception_backbone": count(brain.backbone),
                "ctm_core": count(core),
                "ctm_synapses": count(core.synapses),
                "ctm_neuron_level_models": count(core.trace_processor),
                "ctm_learned_start_and_decay": sum(direct.values()),
                "heads": {name: count(getattr(brain, name)) for name in
                          ("assessment", "diagnosis", "action", "communication", "source")},
            },
            "synapses": {
                "kind": "dense two-block MLP shared by all neurons (every neuron feeds every neuron)",
                "blocks": [[str(m) for m in core.synapses if not isinstance(m, torch.nn.Dropout)]],
                "input": f"{cfg.d_input} features of this tick + the {core.d_model} neurons' last post-activations",
            },
            "neuron_level_models": {
                "kind": "one private MLP per neuron over its own pre-activation history",
                "per_neuron_weights": (count(core.trace_processor) - 2) // core.d_model,
                "shape": f"{memory} -> {2 * cfg.memory_hidden} (GLU) -> {cfg.memory_hidden} -> 2 (GLU) -> 1",
            },
            "synchronisation": {
                "neurons": last,
                "pairs": len(pairs),
                "pair_list": pairs,
                "decay_rate": sig_list(rates, 4),
                "meaning": ("the output is how strongly pairs of these neurons fire together over the window, "
                            "each pair with its own learned decay (0 = all moments count equally, 4 = only the latest)"
                            + ("; in this brain every decay is exactly 0, so all moments count equally for every pair"
                               if not bool(core.decay_params_out.detach().any()) else "")),
            },
            "heads": {
                "assessment": list(ASSESSMENTS),
                "diagnosis": list(DIAGNOSES[machine]),
                "action": list(DANGER_ACTIONS[machine]),
                "communication": list(COMMUNICATIONS),
                "source": list(SOURCES),
            },
            "decision_layer": json.loads((engines[machine].parent / "decide.json").read_text(encoding="utf-8")),
        }

    arch = {machine: architecture(machine) for machine in ENGINES}

    # ------------------------------------------------------------------ the episode
    if args.variant is None:
        variant = next(v for v in range(200)
                       if _make(args.family, v, False).tags.get("pattern") == "shared")
    else:
        variant = args.variant
    spec = _make(args.family, variant, False)
    start_hour = float(spec.config.get("weather", {}).get("start_hour", 0.0))

    class Recorder(TrainedNodeBrain):
        """The release's brain, unchanged; it only remembers the last thought's input and output."""

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.ms: list[float] = []
            self.times: list[float] = []
            self.brain.core.register_forward_hook(self._hook)

        def _hook(self, module, inputs, output):
            features, (trace, act) = inputs[0], inputs[1]
            self.last_core = (features.clone(), trace.clone(), act.clone(), output[0].clone())

        def decide(self, ctx, encoded):
            self.last_encoded = (np.array(encoded.values, dtype=np.float32), np.array(encoded.validity, dtype=np.float32))
            self.times.append(float(ctx.time_s))
            started = time.perf_counter()
            out = super().decide(ctx, encoded)
            self.ms.append((time.perf_counter() - started) * 1000.0)
            return out

    work = Path(tempfile.mkdtemp(prefix="jarvis-mind-"))
    try:
        brains = {node: Recorder(copy.deepcopy(loaded[MACHINE_TYPES[node]]), start_hour,
                                 Decider.for_checkpoint(engines[MACHINE_TYPES[node]]), False,
                                 hide_notes=True, hold_calls=True) for node in NODE_IDS}
        engine = MatrixEngine(seed=spec.seed, config=copy.deepcopy(spec.config))
        _apply_initial(engine, spec)
        runtime = NodeRuntime(engine, modes={n: "real" for n in NODE_IDS}, brains=brains,
                              memory_factory=lambda n: CognitiveMemory(n, work / f"{n}.sqlite"))
        _benign(engine, runtime, spec)
        _faults(engine, runtime, spec)
        names = {n: list(runtime.nodes[n].encoder.feature_names) for n in NODE_IDS}

        first_fault = min(f["inject_tick"] for f in spec.faults)
        calm_tick = max(0, first_fault - CALM_BEFORE)
        timeline = {n: [] for n in NODE_IDS}
        top = {n: 0 for n in NODE_IDS}
        raw: dict[str, dict[str, dict]] = {n: {} for n in NODE_IDS}
        started = time.perf_counter()
        for t in range(int(spec.duration_ticks)):
            results = runtime.step()
            for n in NODE_IDS:
                out = results[n]["output"]
                rank = ASSESSMENTS.index(out.assessment)
                timeline[n].append([rank, round(float(out.confidence), 3)])
                moment = None
                if t == calm_tick:
                    moment = "calm"
                elif rank > top[n]:
                    moment = "first_alarm" if top[n] == 0 else "peak"
                top[n] = max(top[n], rank)
                if moment:
                    b = brains[n]
                    raw[n][moment] = {"tick": t, "time_s": b.times[-1], "core": b.last_core, "encoded": b.last_encoded,
                                      "output": out, "probabilities": list(b.last["probabilities"])}
        run_seconds = time.perf_counter() - started
        times = brains[NODE_IDS[0]].times
        tick_seconds = times[1] - times[0]

        # ------------------------------------------------------------------ replay each kept thought
        def replay(node: str, kept: dict) -> dict:
            brain = brains[node].brain
            core = brain.core
            features, trace, act, expected = kept["core"]
            steps = brain.config.iterations
            posts, pres, probs, certainty, change, recurrent, sensory, jac = [], [], [], [], [], [], [], []
            tr, ac = trace, act
            core.iterations = 1
            try:
                for _ in range(steps):
                    prev = ac[:, :, -1]
                    with torch.enable_grad():
                        jr = torch.autograd.functional.jacobian(
                            lambda p: core.synapses(torch.cat((features, p.unsqueeze(0)), -1))[0], prev[0])
                        jf = torch.autograd.functional.jacobian(
                            lambda x: core.synapses(torch.cat((x.unsqueeze(0), prev), -1))[0], features[0])
                    with torch.no_grad():
                        sync, (tr, ac) = core(features, (tr, ac))
                        p = torch.softmax(brain.assessment(sync), -1)[0]
                    post = ac[0, :, -1]
                    jac.append(jr.detach())
                    recurrent.append(jr.norm(dim=1))
                    sensory.append(jf.norm(dim=1))
                    pres.append(tr[0, :, -1])
                    posts.append(post)
                    probs.append(p)
                    entropy = -(p * p.clamp_min(1e-9).log()).sum() / math.log(len(p))
                    certainty.append(1.0 - float(entropy))
                    change.append(float((post - prev[0]).pow(2).mean().sqrt()))
            finally:
                core.iterations = 1
            gap = float((sync - expected).abs().max())
            if gap > 1e-5:
                raise RuntimeError(f"{node} tick {kept['tick']}: the step-by-step replay differs by {gap}")
            if not torch.equal(tr[:, :, : trace.shape[-1] - steps], trace[:, :, steps:]):
                raise RuntimeError(f"{node}: the history window did not slide by {steps}")
            with torch.no_grad():
                heads = {h: torch.softmax(getattr(brain, h)(sync), -1)[0] for h in ("diagnosis", "source")}
            machine = MACHINE_TYPES[node]
            values, validity = kept["encoded"]
            values = values.copy()
            values[brain.layout.notes] = 0.0
            numeric = brain.layout.numeric
            z = ((values[numeric] - brain.mean.numpy()[numeric]) / brain.std.numpy()[numeric]).clip(-8, 8)
            z = z * (validity[numeric] > 0.5)
            order = np.argsort(-np.abs(z))[:TOP_INPUTS]
            out = kept["output"]
            return {
                "tick": kept["tick"],
                "farm_time_s": kept["time_s"],
                "hour_of_day": round((start_hour + kept["time_s"] / 3600.0) % 24.0, 4),
                "decided": {"assessment": out.assessment, "diagnosis": out.diagnosis,
                            "confidence": round(float(out.confidence), 4), "action": out.action,
                            "communication": out.communication, "evidence": list(out.evidence)},
                "assessment_probabilities": sig_list(kept["probabilities"]),
                "per_step": {
                    "assessment_probabilities": sig_list(torch.stack(probs)),
                    "certainty": sig_list(certainty),
                    "rms_change": sig_list(change),
                    "recurrent_drive": sig_list(torch.stack(recurrent), 3),
                    "sensory_drive": sig_list(torch.stack(sensory), 3),
                },
                "history_pre": sig_list(tr[0], 3),
                "history_post": sig_list(ac[0], 3),
                "synchronisation": sig_list(sync[0]),
                "top_diagnoses": [[DIAGNOSES[machine][i], sig(heads["diagnosis"][i])]
                                  for i in heads["diagnosis"].argsort(descending=True)[:3].tolist()],
                "top_sources": [[SOURCES[i], sig(heads["source"][i])]
                                for i in heads["source"].argsort(descending=True)[:3].tolist()],
                "unusual_inputs": [[names[node][numeric[i]], sig(z[i], 3)] for i in order.tolist() if abs(z[i]) > 0],
                "replay_max_difference": gap,
                "_jacobians": jac,
            }

        ctm_files = {}
        for node in NODE_IDS:
            thoughts = {moment: replay(node, kept) for moment, kept in raw[node].items()}
            stacked = torch.stack([j.abs() for t in thoughts.values() for j in t["_jacobians"]]).mean(0)
            sources = stacked.topk(TOP_K, dim=1).indices          # [target, K]
            edges = [[int(s), int(t)] for t in range(sources.shape[0]) for s in sources[t].tolist()]
            src = torch.tensor([e[0] for e in edges])
            dst = torch.tensor([e[1] for e in edges])
            for t in thoughts.values():
                jac = t.pop("_jacobians")
                t["per_step"]["edge_weight"] = [sig_list(j[dst, src], 3) for j in jac]
            brain = brains[node]
            ctm_files[node] = {
                "node": node,
                "machine": MACHINE_TYPES[node],
                "edges": edges,
                "edges_meaning": (f"for each neuron, the {TOP_K} neurons whose last output most changes its next "
                                  "pre-activation (mean |d pre_j / d post_i| over the kept thoughts, by autograd "
                                  "through the real synapses); every neuron is in fact fed by all 256"),
                "thoughts": thoughts,
                "latency_ms": {"n": len(brain.ms), "p50": round(statistics.median(brain.ms), 3),
                               "p95": round(percentile(brain.ms, 0.95), 3), "mean": round(statistics.fmean(brain.ms), 3)},
            }

        # ------------------------------------------------------------------ the CMMs after the run
        def cmm(node: str) -> dict:
            db = runtime.nodes[node].memory.db
            db.commit()
            def rows(sql, limit=200):
                cur = db.execute(sql)
                cols = [c[0] for c in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchmany(limit)]
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            episodes = rows("SELECT * FROM episodes ORDER BY id")
            for episode in episodes:
                episode["ticks_kept"] = [
                    [r["time_s"], r["assessment"], r["diagnosis"], r["source"]]
                    for r in rows(f"SELECT time_s, assessment, diagnosis, source FROM episode_ticks "
                                  f"WHERE episode_id = {int(episode['id'])} ORDER BY time_s", 5000)]
                example = db.execute("SELECT readings FROM episode_ticks WHERE episode_id = ? ORDER BY time_s LIMIT 1",
                                     (episode["id"],)).fetchone()
                episode["example_readings"] = json.loads(example[0]) if example and example[0] else None
            # The normal profile lives in RAM and reaches the disk at the nightly sleep, so read it where it is.
            memory = runtime.nodes[node].memory
            profile = [{"hour": h, "key": k, "n": n, "mean": mean, "m2": m2}
                       for (h, k), (n, mean, m2) in sorted(memory._profile.items(), key=lambda kv: (kv[0][1], kv[0][0]))]
            return {
                "node": node,
                "tables": {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables},
                "episodes": episodes,
                "facts": rows("SELECT * FROM facts ORDER BY id"),
                "hypotheses": rows("SELECT * FROM hypotheses ORDER BY id"),
                "procedures": rows("SELECT * FROM procedures ORDER BY id"),
                "profile_source": "RAM (the CMM writes it to disk at its 02:00 sleep, which this run did not reach)",
                "profile": [{"hour": r["hour"], "key": r["key"], "n": r["n"], "mean": sig(r["mean"]),
                             "spread": sig(math.sqrt(r["m2"] / (r["n"] - 1))) if r["n"] > 1 else None} for r in profile],
                "rom": rows("SELECT key, kind, text FROM rom ORDER BY key"),
            }

        cmm_files = {node: cmm(node) for node in NODE_IDS}

        # ------------------------------------------------------------------ write, scan, publish
        stage = work / "mind"
        (stage / "ctm").mkdir(parents=True)
        (stage / "cmm").mkdir()
        episode = {"family": args.family, "variant": variant, "duration_ticks": int(spec.duration_ticks),
                   "start_hour": start_hour, "tick_seconds": tick_seconds, "pattern": spec.tags.get("pattern"),
                   "faults": [{k: v for k, v in f.items()} for f in spec.faults],
                   "calm_tick": calm_tick, "seconds_to_run": round(run_seconds, 1)}
        files = {
            "architecture.json": arch,
            "timeline.json": {"tick_seconds": tick_seconds, "start_hour": start_hour, "assessments": list(ASSESSMENTS),
                              "episode": episode, "nodes": timeline},
            **{f"ctm/{n}.json": ctm_files[n] for n in NODE_IDS},
            **{f"cmm/{n}.json": cmm_files[n] for n in NODE_IDS},
        }
        manifest = {
            "format": FORMAT,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "real: the release v3.1 brains, one recorded episode; nothing in this folder is illustrative",
            "release": "v3.1",
            "python": platform.python_version(),
            "torch": torch.__version__,
            "torch_threads": torch.get_num_threads(),
            "recipe": "release README: TrainedNodeBrain(hide_notes=True, hold_calls=True), decide.json, CognitiveMemory",
            "episode": episode,
            "engines": {m: {"file": ENGINES[m], "sha256": arch[m]["engine_sha256"]} for m in ENGINES},
            "files": sorted(files),
        }
        files["manifest.json"] = manifest
        for name, payload in files.items():
            (stage / name).write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        for path in stage.rglob("*.json"):
            text = path.read_text(encoding="utf-8").lower()
            for word in FORBIDDEN:
                if word in text:
                    raise RuntimeError(f"{path.name} contains a forbidden word or path; nothing was published")
        if args.out.exists():
            shutil.rmtree(args.out)
        shutil.copytree(stage, args.out)
        for path in sorted(args.out.rglob("*.json")):
            print(f"{path.relative_to(args.out)}  {path.stat().st_size:,} bytes")
        for node in NODE_IDS:
            kept = ", ".join(f"{m} @ tick {t['tick']} ({t['decided']['assessment']})"
                             for m, t in ctm_files[node]["thoughts"].items())
            print(f"{node}: {kept}; one thought p50 {ctm_files[node]['latency_ms']['p50']} ms")
        print(f"episode {args.family} variant {variant}: {int(spec.duration_ticks)} ticks in {run_seconds:.0f} s")
        return 0
    finally:
        for node in NODE_IDS if "runtime" in locals() else ():
            try:
                runtime.nodes[node].memory.db.close()
            except Exception:
                pass
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    raise SystemExit(main())
