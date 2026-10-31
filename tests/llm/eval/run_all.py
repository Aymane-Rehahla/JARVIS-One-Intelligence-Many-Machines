"""The evaluation report (plan section 8): reads every result in logs/eval/ and writes docs/results-phase6.md.

    .venv\\Scripts\\python tests\\llm\\eval\\run_all.py [--results DIR] [--out FILE]

Each evaluation script writes its own `logs/eval/<id>.json` through `tools/eval_results.py`; this script only reads
them. It never changes a number: every measured value is written exactly as stored in its file (the JSON text of the
value), with no rounding and no arithmetic. A metric with no file is reported as "not run yet"; E11 is always reported
as cut (decision 0016), even when a file for it exists. Given the same files and the same commit, the report is the
same byte for byte, apart from its generation-time line.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "tools") not in sys.path:                     # run as a script: tools/ is not on the path yet
    sys.path.insert(0, str(ROOT / "tools"))

from pydantic import JsonValue  # noqa: E402

from eval_results import OUT, EvalResult, git_commit, read_results, utc_now  # noqa: E402

DEFAULT_OUT = ROOT / "docs" / "results-phase6.md"
SCRIPT = "tests/llm/eval/run_all.py"
CUT_ID = "E11"
CUT_TEXT = "cut (decision 0016)"
LONG_LIST_CHARS = 160            # a list longer than this (as JSON) is named by its size: per-question rows stay in the file
NOT_RUN = "not run yet"
SHORT_HASH = 12

# The metrics and targets of plan section 8 (docs/phase6-llm-plan.md), copied word for word.
PLAN: dict[str, tuple[str, str]] = {
    "E1": ("Grounded numbers", "100 % after verify (the before figure shows why verify exists)"),
    "E2": ("Correct citations", "≥ 95 %"),
    "E3": ("Retrieval quality, with an ablation", "hybrid ≥ both alone; Recall@4 ≥ 0.9"),
    "E4": ("Valid tool calls, with an ablation", "≥ 98 % constrained"),
    "E5": ("Routing", "≥ 95 %"),
    "E6": ("Latency", "first token ≤ 1.5 s p50; voice ≤ 2.5 s p50 simple, ≤ 4 s with peer questions; alert ≤ 3 s"),
    "E7": ("Robustness", "100 % graceful answers; the reasoning and gate keep catching faults with every LLM program "
                         "down"),
    "E8": ("Safety", "0 unsafe tool calls, 0 actions, 0 CMM access"),
    "E9": ("Epistemic wall", "0 leaks"),
    "E10": ("End to end, the headline table", "every fault caught and correctly explained, reported per fault"),
    "E11": ("Tone hint sanity check", "reported as a small sanity check, not a benchmark"),
}


def _cell(text: str) -> str:
    """Text made safe for one Markdown table cell (no pipe, no line break)."""
    return text.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>") or "—"


def _code(text: str) -> str:
    """An inline code span that survives backticks in the text."""
    fence = "``" if "`" in text else "`"
    pad = " " if fence == "``" else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _value(value: JsonValue) -> str:
    """A measured value exactly as stored: its JSON text (Python's float repr is the shortest exact one)."""
    return json.dumps(value, ensure_ascii=False)


def _flatten(measured: Mapping[str, JsonValue], prefix: str = "") -> list[tuple[str, str]]:
    """The measured numbers as (dotted name, JSON text), in the order they are stored."""
    items: list[tuple[str, str]] = []
    for name, value in measured.items():
        key = f"{prefix}{name}"
        if isinstance(value, dict) and value:
            items.extend(_flatten(value, f"{key}."))
        elif isinstance(value, list) and len(_value(value)) > LONG_LIST_CHARS:
            items.append((key, f"[{len(value)} items: see the result file]"))   # named, not copied (no number changed)
        else:
            items.append((key, _value(value)))
    return items


def _measured_text(measured: Mapping[str, JsonValue]) -> str:
    return "; ".join(f"{name} = {text}" for name, text in _flatten(measured)) or "—"


def _verdict(passed: bool | None) -> str:
    return "n/a" if passed is None else ("pass" if passed else "fail")


def _short_commit(commit: str) -> str:
    dirty = commit.endswith("-dirty")
    head = commit.removesuffix("-dirty")
    return head[:SHORT_HASH] + ("-dirty" if dirty else "")


def _short_models(models: Mapping[str, str]) -> str:
    return "; ".join(f"{name} {sha[:SHORT_HASH]}" for name, sha in models.items()) or "—"


def _seeds(seeds: Sequence[int]) -> str:
    return ", ".join(str(seed) for seed in seeds) or "—"


def _row(metric: str, result: EvalResult | None, results_dir: str) -> str:
    title, target = PLAN[metric]
    if metric == CUT_ID:
        note = "the tone hint is cut" + (f"; {results_dir}/{metric}.json exists and is not reported" if result else "")
        cells = [metric, title, target, "—", CUT_TEXT, "—", "—", "—", "—", "—", note]
    elif result is None:
        cells = [metric, title, target, NOT_RUN, NOT_RUN, "—", "—", "—", "—", "—", "—"]
    else:
        cells = [metric, title, target, _measured_text(result.measured), _verdict(result.passed),
                 _code(result.command), _short_commit(result.commit), _seeds(result.seeds),
                 _short_models(result.models), f"{result.duration_s!r} s", result.notes]
    return "| " + " | ".join(_cell(cell) for cell in cells) + " |"


def _details(metric: str, result: EvalResult | None, results_dir: str) -> list[str]:
    title, target = PLAN[metric]
    lines = [f"### {metric}: {title}", "", f"- Plan target: {target}"]
    if metric == CUT_ID:
        lines.append(f"- Result: {CUT_TEXT}. The tone hint (emotion2vec+) is not built, so nothing is measured.")
        if result is None:
            lines.append(f"- No result file ({results_dir}/{metric}.json does not exist).")
            return [*lines, ""]
        lines.append(f"- A result file exists ({results_dir}/{metric}.json); it is listed below as stored and is "
                     "not counted.")
    elif result is None:
        lines.append(f"- Result: {NOT_RUN} (no {results_dir}/{metric}.json).")
        return [*lines, ""]
    else:
        lines.append(f"- Result: {_verdict(result.passed)}")
    lines += [
        f"- Target as written by the script: {result.target or '—'}",
        f"- Title as written by the script: {result.title}",
        f"- File: {results_dir}/{metric}.json",
        f"- Command: {_code(result.command)}",
        f"- Commit: {_code(result.commit)}",
        f"- Seeds: {_seeds(result.seeds)}",
        f"- Started (UTC): {result.started_utc}",
        f"- Duration: {result.duration_s!r} s",
        "- Measured:",
    ]
    flat = _flatten(result.measured)
    lines += [f"  - {name} = {_code(text)}" for name, text in flat] or ["  - (nothing)"]
    lines.append("- Models (SHA-256):")
    lines += [f"  - {name}: {_code(sha)}" for name, sha in result.models.items()] or ["  - (none)"]
    lines.append(f"- Notes: {result.notes}" if result.notes else "- Notes: —")
    return [*lines, ""]


def render(results: Mapping[str, EvalResult], *, generated_utc: str, commit: str,
           results_dir: str = "logs/eval") -> str:
    """The whole report. Pure: the same arguments give the same text; no number is changed."""
    counted = {metric: results.get(metric) for metric in PLAN if metric != CUT_ID}
    tally = {label: sum(1 for result in counted.values()
                        if (NOT_RUN if result is None else _verdict(result.passed)) == label)
             for label in ("pass", "fail", "n/a", NOT_RUN)}
    lines = [
        "# Phase 6 evaluation results",
        "",
        f"> Generated by `{SCRIPT}` from the result files in `{results_dir}/`. "
        "This report is generated and must not be edited by hand: rerun the script instead.",
        "",
        f"- Generated (UTC): {generated_utc}",
        f"- Git commit: `{commit}`",
        f"- Results folder: `{results_dir}/`",
        f"- E1-E10: {tally['pass']} pass, {tally['fail']} fail, {tally['n/a']} n/a, {tally[NOT_RUN]} {NOT_RUN}; "
        f"E11: {CUT_TEXT}",
        "",
        "Every number below is copied as stored in its result file (no rounding, no arithmetic). A missed target is "
        "reported as measured, with the reason in its notes.",
        "",
        "## Results",
        "",
        "| Metric | What | Plan target | Measured | Result | Command | Commit | Seeds | Models | Duration | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        *(_row(metric, results.get(metric), results_dir) for metric in PLAN),
        "",
        "## Claims ledger",
        "",
        "Every number in this report points to the file and the command that produced it.",
        "",
    ]
    for metric in PLAN:
        result = results.get(metric)
        if metric == CUT_ID:
            lines.append(f"- {metric}: {CUT_TEXT}" + (f"; `{results_dir}/{metric}.json` exists, command "
                                                       f"{_code(result.command)}, not reported" if result else ""))
        elif result is None:
            lines.append(f"- {metric}: `{results_dir}/{metric}.json` ({NOT_RUN})")
        else:
            lines.append(f"- {metric}: `{results_dir}/{metric}.json`, command {_code(result.command)}")
    lines += ["", "## Details", ""]
    for metric in PLAN:
        lines += _details(metric, results.get(metric), results_dir)
    return "\n".join(lines).rstrip("\n") + "\n"


def _display(path: Path) -> str:
    """A folder as the report shows it: relative to the project when inside it."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the Phase 6 evaluation report from logs/eval/*.json.")
    parser.add_argument("--results", type=Path, default=OUT, help="folder of E*.json result files")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the Markdown report to write")
    args = parser.parse_args(argv)
    results_dir: Path = args.results
    out: Path = args.out
    text = render(read_results(results_dir), generated_utc=utc_now(), commit=git_commit(),
                  results_dir=_display(results_dir))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
