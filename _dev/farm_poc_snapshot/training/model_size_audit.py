"""Read-only measurement of saved Jarvis checkpoints from the 10-day trial.

This module loads trusted local PyTorch artifacts and never performs optimizer
steps. Run from the repository root with ``python -m farm_poc.training.model_size_audit``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import torch

from farm_poc.cognitive_runtime.agent import CTMRuntimeAgent, CTMRuntimeConfig
from farm_poc.cognitive_runtime.world_learning import NextObservationHead


DEFAULT_TRIAL = Path("runs/jarvis-10-day-20261016")
DEFAULT_OUTPUT = Path("runs/model-size-audit-20261016")
CHANGE_TOLERANCE = 1e-8


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tensor_summary(value: Any) -> dict[str, int]:
    tensors: list[torch.Tensor] = []

    def visit(item: Any) -> None:
        if torch.is_tensor(item):
            tensors.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return {"tensor_count": len(tensors), "elements": sum(t.numel() for t in tensors),
            "bytes": sum(t.numel() * t.element_size() for t in tensors)}


def _component_state(checkpoint: dict[str, Any], *, online: bool) -> dict[str, dict[str, torch.Tensor]]:
    if online:
        return {"ctm_core": checkpoint["ctm_state_dict"],
                "decision_head": checkpoint["decision_head_state_dict"],
                "diagnosis_head": checkpoint["diagnosis_head_state_dict"],
                "next_observation_head": checkpoint["prediction_head_state_dict"]}
    state = checkpoint["state_dict"]
    result = {"ctm_core": {}, "decision_head": {}, "diagnosis_head": {}, "next_observation_head": {}}
    for name, tensor in state.items():
        for component, prefix in (("ctm_core", "ctm."), ("decision_head", "decision_head."),
                                  ("diagnosis_head", "diagnosis_head.")):
            if name.startswith(prefix):
                result[component][name[len(prefix):]] = tensor
                break
    return result


def compare_weights(before: dict[str, torch.Tensor], after: dict[str, torch.Tensor],
                    tolerance: float = CHANGE_TOLERANCE) -> dict[str, Any]:
    """Compare same-named, same-shaped floating tensors using absolute tolerance."""
    if set(before) != set(after):
        raise ValueError("weight names differ")
    count = total = 0
    squared_diff = squared_before = squared_after = 0.0
    maximum_absolute_delta = 0.0
    for name in sorted(before):
        left, right = before[name], after[name]
        if left.shape != right.shape:
            raise ValueError(f"weight shape differs for {name}")
        if not left.is_floating_point() or not right.is_floating_point():
            continue
        delta = (right.detach().double().cpu() - left.detach().double().cpu()).reshape(-1)
        old = left.detach().double().cpu().reshape(-1)
        new = right.detach().double().cpu().reshape(-1)
        count += int((delta.abs() > tolerance).sum())
        total += delta.numel()
        squared_diff += float(delta.square().sum())
        squared_before += float(old.square().sum())
        squared_after += float(new.square().sum())
        if delta.numel():
            maximum_absolute_delta = max(maximum_absolute_delta, float(delta.abs().max()))
    diff_norm = math.sqrt(squared_diff)
    before_norm, after_norm = math.sqrt(squared_before), math.sqrt(squared_after)
    return {"changed_elements": count, "total_elements": total,
            "changed_fraction": count / total if total else 0.0,
            "absolute_l2_difference": diff_norm,
            "relative_l2_difference": diff_norm / before_norm if before_norm else None,
            "before_l2_norm": before_norm, "after_l2_norm": after_norm,
            "relative_weight_norm_change": ((after_norm - before_norm) / before_norm) if before_norm else None,
            "max_absolute_element_delta": maximum_absolute_delta,
            "absolute_tolerance": tolerance}


def _component_inventory(agent: CTMRuntimeAgent, components: dict[str, dict[str, torch.Tensor]]) -> dict[str, Any]:
    parameter_names = {name for name, _ in agent.named_parameters()}
    result = {}
    for component, state in components.items():
        param_names = {key for key in state if (component == "ctm_core" and f"ctm.{key}" in parameter_names)
                       or (component != "ctm_core" and f"{component}.{key}" in parameter_names)}
        # The predictor is not a child of CTMRuntimeAgent, so its trainable
        # status is defined by its own module and all its state tensors are
        # parameters in this implementation (Linear weight and bias).
        if component == "next_observation_head":
            param_names = set(state)
        parameters = [state[key] for key in param_names]
        result[component] = {
            "parameter_elements": sum(t.numel() for t in parameters),
            "parameter_tensor_bytes": sum(t.numel() * t.element_size() for t in parameters),
            "state_tensor_elements_including_buffers": sum(t.numel() for t in state.values()),
            "state_tensor_bytes_including_buffers": sum(t.numel() * t.element_size() for t in state.values()),
            "state_tensor_count": len(state),
        }
    return result


def _online_hash(components: dict[str, dict[str, torch.Tensor]]) -> str:
    # Mirrors OnlineWorldLearner._weight_hash exactly, including module order.
    digest = hashlib.sha256()
    for component in ("ctm_core", "decision_head", "diagnosis_head", "next_observation_head"):
        for name, tensor in sorted(components[component].items()):
            digest.update(name.encode("utf-8"))
            digest.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def measure(trial_dir: Path) -> dict[str, Any]:
    supervised_path = trial_dir / "supervised" / "jarvis-decision-checkpoint.pt"
    online_path = trial_dir / "online-world-learning-checkpoint.pt"
    supervised = torch.load(supervised_path, map_location="cpu", weights_only=False)
    online = torch.load(online_path, map_location="cpu", weights_only=False)
    runtime_config = CTMRuntimeConfig(**online["runtime_config"])
    agent = CTMRuntimeAgent(runtime_config)
    agent._materialize()
    agent.load_state_dict(supervised["state_dict"], strict=True)
    before = _component_state(supervised, online=False)
    after = _component_state(online, online=True)
    online_components = _component_inventory(agent, after)
    trainable_parameters = list(agent.named_parameters())
    # Prediction head is a learned module in the online training objective, not
    # part of the task-facing decision/diagnosis inference interface.
    prediction_parameters = sum(t.numel() for t in after["next_observation_head"].values())
    prediction_bytes = sum(t.numel() * t.element_size() for t in after["next_observation_head"].values())
    current_task_params = sum(t.numel() for _, t in trainable_parameters)
    current_task_bytes = sum(t.numel() * t.element_size() for _, t in trainable_parameters)
    current_all_params = current_task_params + prediction_parameters
    current_all_bytes = current_task_bytes + prediction_bytes

    per_component_change = {}
    comparable = {}
    for component in ("ctm_core", "decision_head", "diagnosis_head"):
        per_component_change[component] = compare_weights(before[component], after[component])
    supervised_hash_match = None
    reconstructed_online_initial_hash = None
    try:
        # OnlineWorldLearner seeded Torch before creating this head; recreate
        # that initial head and verify it against the saved pre-update digest.
        torch.manual_seed(int(online["online_config"]["seed"]))
        initial_prediction_head = NextObservationHead(agent.ctm.synch_representation_size_out,
                                                       agent.encoder.dimension)
        initial_components = {key: dict(value) for key, value in before.items()}
        initial_components["next_observation_head"] = initial_prediction_head.state_dict()
        reconstructed_online_initial_hash = _online_hash(initial_components)
        supervised_hash_match = reconstructed_online_initial_hash == online.get("initial_weight_hash")
        if supervised_hash_match:
            per_component_change["next_observation_head"] = compare_weights(
                initial_components["next_observation_head"], after["next_observation_head"])
    except Exception as error:  # keep audit output useful if RNG/init reconstruction is incompatible
        per_component_change["next_observation_head"] = {"available": False,
                                                           "reason": f"could not reconstruct seeded initial head: {error}"}

    parameter_groups = {
        "task_runtime_ctm_plus_decision_and_diagnosis_heads": {
            "parameter_elements": current_task_params, "parameter_bytes": current_task_bytes},
        "online_prediction_head": {"parameter_elements": prediction_parameters,
                                    "parameter_bytes": prediction_bytes},
        "all_online_trainable_parameters": {"parameter_elements": current_all_params,
                                            "parameter_bytes": current_all_bytes},
    }
    cmm = online.get("cmm_state", {})
    cmm_working = cmm.get("working", [])
    cmm_categories = {"working": len(cmm_working), "episodic": len(cmm.get("episodic", [])),
                      "semantic": len(cmm.get("semantic", {})),
                      "procedural": len(cmm.get("procedural", {})),
                      "mandatory_rule": len(cmm.get("mandatory_rules", {}))}
    cmm_kinds: dict[str, int] = {}
    for entry in cmm_working:
        cmm_kinds[entry.get("kind", "unknown")] = cmm_kinds.get(entry.get("kind", "unknown"), 0) + 1
    cmm_persisted_json_bytes = len(json.dumps(cmm, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    finite_by_component = {}
    max_abs_by_component = {}
    for component, state in after.items():
        floats = [tensor.detach().float() for tensor in state.values() if tensor.is_floating_point()]
        finite_by_component[component] = all(bool(torch.isfinite(tensor).all()) for tensor in floats)
        max_abs_by_component[component] = max((float(tensor.abs().max()) for tensor in floats if tensor.numel()), default=0.0)

    artifacts = {}
    for label, path in (("supervised_checkpoint", supervised_path), ("online_checkpoint", online_path),
                        ("online_report", trial_dir / "online-learning-report.json"),
                        ("supervised_report", trial_dir / "supervised" / "supervised-training-report.json")):
        artifacts[label] = {"path": str(path), "exists": path.exists(),
                            "size_bytes": path.stat().st_size if path.exists() else None,
                            "sha256": sha256_file(path) if path.exists() else None}

    optimizer_supervised = _tensor_summary(supervised.get("optimizer_state", {}))
    optimizer_online = _tensor_summary(online.get("optimizer_state_dict", {}))
    recurrent = _tensor_summary(online.get("recurrent_state"))
    state_bytes = sum(t.numel() * t.element_size() for group in after.values() for t in group.values())
    report_path = trial_dir / "online-learning-report.json"
    saved_report = json.loads(report_path.read_text(encoding="utf-8"))
    progress = json.loads((trial_dir / "progress-report.json").read_text(encoding="utf-8"))
    supervised_report = json.loads((trial_dir / "supervised" / "supervised-training-report.json").read_text(encoding="utf-8"))
    hypothesis_writes = sum(progress.get("alarm_decision_counts", {}).get(label, 0)
                            for label in ("INVESTIGATE", "DIAGNOSE", "VERIFY"))
    online_cmm_writes = int(online.get("observations_processed", 0)) + hypothesis_writes
    return {
        "schema_version": 1,
        "methodology": {"optimizer_steps_performed": 0, "tolerance_for_changed_elements": CHANGE_TOLERANCE,
                        "weight_diff": "same named, same-shaped floating parameter/state tensors; changed iff abs(delta)>1e-8",
                        "relative_l2": "||after-before||_2 / ||before||_2",
                        "model_weight_bytes": "parameter tensor bytes; buffers and checkpoint payload reported separately",
                        "memory_size": "CMM record count in saved checkpoint; serialized CMM payload byte count is an approximate JSON diagnostic"},
        "artifacts": artifacts,
        "architecture": {"runtime_config": online["runtime_config"],
                          "encoder_version": online["encoder_manifest"].get("encoder_version"),
                          "encoder_schema_hash": online["encoder_manifest"].get("schema_hash"),
                          "decision_classes": len(after["decision_head"]["weight"]),
                          "diagnosis_classes": len(after["diagnosis_head"]["weight"])},
        "model_size": {"components": online_components,
                       "all_model_tensors_finite": all(finite_by_component.values()),
                       "finite_by_component": finite_by_component,
                       "maximum_absolute_weight_by_component": max_abs_by_component,
                       "parameter_groups": parameter_groups,
                       "current_task_model_mib": current_task_bytes / (1024 ** 2),
                       "all_online_trainable_mib": current_all_bytes / (1024 ** 2),
                       "online_checkpoint_model_tensor_bytes_including_buffers": state_bytes,
                       "online_checkpoint_model_tensor_mib_including_buffers": state_bytes / (1024 ** 2),
                       "full_online_checkpoint_bytes": online_path.stat().st_size,
                       "full_online_checkpoint_mib": online_path.stat().st_size / (1024 ** 2),
                       "checkpoint_remaining_bytes_after_tensor_payloads": online_path.stat().st_size - state_bytes - optimizer_online["bytes"] - recurrent["bytes"],
                       "online_optimizer_state": optimizer_online,
                       "supervised_optimizer_state": optimizer_supervised,
                       "saved_recurrent_state": recurrent},
        "training_changes": {"initial_pre_supervision_checkpoint": {"available": False,
                              "reason": "No compatible pre-supervised model checkpoint is present in the trial artifacts; the saved supervised checkpoint is post-training."},
                             "supervised_to_online": {"architecture_and_shapes_match": True,
                                 "component_changes": per_component_change,
                                 "online_initial_hash_reconstruction_matches_saved": supervised_hash_match,
                                 "reconstructed_online_initial_hash": reconstructed_online_initial_hash,
                                 "saved_online_initial_weight_hash": online.get("initial_weight_hash"),
                                 "online_final_weight_hash": saved_report.get("model", {}).get("current_trainable_weights_sha256")}},
        "online_run": {"status": saved_report.get("status"),
                       "observations_processed": online.get("observations_processed"),
                       "episodes": 1,
                       "episode_id": online.get("episode_id"),
                       "optimizer_updates": online.get("optimizer_updates"),
                       "prediction_error_count": online.get("loss_count"),
                       "prediction_error_mean": (online.get("loss_sum", 0) / online.get("loss_count", 1)) if online.get("loss_count") else None,
                       "prediction_error_recent_1000_mean": sum(online.get("recent_losses", [])) / max(1, len(online.get("recent_losses", []))),
                       "errors": saved_report.get("errors", []),
                       "objective": saved_report.get("objective"),
                       "decision_and_diagnosis_heads_updated_online": False},
        "cmm": {"record_counts_by_category": cmm_categories, "retained_working_records_by_kind": cmm_kinds,
                "counter_total_entries_created": cmm.get("counter"), "serialized_state_json_bytes": cmm_persisted_json_bytes,
                "counter_delta_during_trial": cmm.get("counter"),
                "counter_delta_basis": "A fresh runtime agent is created for the trial; its state_dict loader restores model tensors only and leaves the CMM empty.",
                "trial_online_observation_writes": online.get("observations_processed"),
                "trial_online_hypothesis_writes_from_decision_counts": hypothesis_writes,
                "trial_online_writes_reconcile_to_counter": online_cmm_writes == cmm.get("counter"),
                "retention_limits": {"working": cmm.get("working_limit"), "episodic": cmm.get("episodic_limit")},
                "episode_id_saved": cmm.get("episode_id"),
                "persistent_semantic_or_procedural_growth": bool(cmm_categories["semantic"] or cmm_categories["procedural"] or cmm_categories["mandatory_rule"])},
        "behavior_evidence": {"supervised_accuracy_before": 0.987012987012987,
                              "supervised_accuracy_after": 0.9696969696969697,
                              "held_out_diagnosis_accuracy": 0.0, "held_out_diagnosis_support": 1,
                              "trial_corrected_detections": "8/24 (post-trial audit metric)",
                              "false_alarm_incidents": 136,
                              "interpretation": "No demonstrated decision improvement; supervised held-out decision accuracy decreased. Online objective trained prediction, not decision or diagnosis heads."},
        "supervised_training_evidence": {"label_provenance": supervised_report.get("label_provenance"),
                                         "decision_head_changed_reported": supervised_report.get("training", {}).get("decision_head_changed"),
                                         "diagnosis_head_changed_reported": supervised_report.get("training", {}).get("diagnosis_head_changed"),
                                         "initial_to_supervised_exact_weight_delta_available": False,
                                         "initial_checkpoint_reason": "No pre-supervised checkpoint was saved; the report contains booleans and metrics, not initial weight tensors."},
        "limitations": ["No compatible pre-supervised checkpoint: exact A-to-B parameter change is unavailable.",
                        "The one held-out diagnosis case cannot establish diagnostic ability.",
                        "Online prediction loss is in-sample streaming error and is not held-out generalization.",
                        "CMM working records are bounded operational context, not learned neural parameters or automatically promoted durable knowledge."]}


def render_markdown(report: dict[str, Any]) -> str:
    size = report["model_size"]
    online = report["online_run"]
    changes = report["training_changes"]["supervised_to_online"]["component_changes"]
    lines = ["# Jarvis model size and learning audit", "",
             "Read-only measurements of the completed 10-day trial. No checkpoint was modified and no optimizer step was run.", "",
             "## Model size", "",
             f"- CTM + decision + diagnosis task model: {size['parameter_groups']['task_runtime_ctm_plus_decision_and_diagnosis_heads']['parameter_elements']:,} parameters; {size['parameter_groups']['task_runtime_ctm_plus_decision_and_diagnosis_heads']['parameter_bytes']:,} bytes ({size['current_task_model_mib']:.4f} MiB).",
             f"- Including the online next-observation head: {size['parameter_groups']['all_online_trainable_parameters']['parameter_elements']:,} parameters; {size['parameter_groups']['all_online_trainable_parameters']['parameter_bytes']:,} bytes ({size['all_online_trainable_mib']:.4f} MiB).",
             f"- Full online checkpoint: {size['full_online_checkpoint_bytes']:,} bytes ({size['full_online_checkpoint_mib']:.4f} MiB); this includes optimizer and runtime/training payload, not just model weights.",
             f"- Online optimizer tensors: {size['online_optimizer_state']['tensor_count']} tensors, {size['online_optimizer_state']['bytes']:,} bytes. Supervised checkpoint optimizer tensors: {size['supervised_optimizer_state']['tensor_count']} tensors, {size['supervised_optimizer_state']['bytes']:,} bytes.", "",
             "## Weight changes", "",
             "- A pre-supervised checkpoint was not found, so exact initialization-to-supervised weight deltas cannot be measured.",
             f"- Supervised-to-online seeded initialization reconstruction matched the saved hash: {report['training_changes']['supervised_to_online']['online_initial_hash_reconstruction_matches_saved']}."]
    for component, delta in changes.items():
        if "changed_elements" in delta:
            lines.append(f"- {component}: {delta['changed_elements']:,}/{delta['total_elements']:,} elements changed (>{delta['absolute_tolerance']:g}); relative L2 delta {delta['relative_l2_difference']:.6g}; relative norm change {delta['relative_weight_norm_change']:.6g}.")
    lines += ["", "## Trial learning and CMM", "",
              f"- {online['observations_processed']:,} observations; {online['optimizer_updates']:,} online optimizer updates on consecutive next-observation prediction transitions.",
              f"- Saved mean pre-update prediction error: {online['prediction_error_mean']:.8g}; last-1000 saved mean: {online['prediction_error_recent_1000_mean']:.8g}. These are streaming training metrics, not held-out evaluation.",
              f"- Decision and diagnosis heads updated online: {online['decision_and_diagnosis_heads_updated_online']}.",
             f"- CMM retained counts: {report['cmm']['record_counts_by_category']}; {report['cmm']['counter_delta_during_trial']:,} entries created during this run, reconciling observations + decision hypotheses: {report['cmm']['trial_online_writes_reconcile_to_counter']}. Working memory is capped at {report['cmm']['retention_limits']['working']} records; semantic/procedural/rule counts are zero.", "",
              "## Evidence of useful behavior change", "",
              "The available supervised held-out decision accuracy moved from 98.70% to 96.97%; held-out diagnosis was 0/1 and only one diagnostic class exists. The post-trial audit counted 8/24 corrected fault detections and 136 false-alarm incidents. There is no evidence that online prediction updates improved decision behavior; those updates did not train the decision or diagnosis heads.", "",
              "## Reproduction", "",
              "From repository root: `python -m farm_poc.training.model_size_audit`. This loads only the saved supervised and online checkpoints, performs tensor accounting and comparisons in memory, and writes this report and its JSON sibling. Numerical change counts use absolute element tolerance 1e-8.", "",
              "Detailed hashes, checkpoint inventory, component bytes, and limitations are in `model-size-report.json`.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial-dir", type=Path, default=DEFAULT_TRIAL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = measure(args.trial_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "model-size-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    (args.output_dir / "model-size-report.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"report_json": str(args.output_dir / "model-size-report.json"),
                      "report_markdown": str(args.output_dir / "model-size-report.md"),
                      "parameters": report["model_size"]["parameter_groups"],
                      "online_updates": report["online_run"]["optimizer_updates"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
