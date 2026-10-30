"""tests/llm/eval/run_all.py: the evaluation report copies every number as stored and never touches the real report."""
from __future__ import annotations

from pathlib import Path

from eval_results import EvalResult, read_results, write_result
from run_all import CUT_TEXT, DEFAULT_OUT, NOT_RUN, PLAN, main, render

COMMIT = "0123456789abcdef0123456789abcdef01234567-dirty"
SHA_A = "a" * 64
SHA_B = "b" * 64
FAILED_NOTES = "hybrid ranked below vector-only on 2 of 20 | the fix is in 6F"


def result(**change: object) -> EvalResult:
    fields: dict[str, object] = dict(
        id="E3", title="Retrieval quality", target="hybrid >= both alone; Recall@4 >= 0.9", passed=True,
        measured={"hybrid": {"recall_at_4": 0.9333333, "mrr": 0.8125}, "questions": 20},
        command=r".venv\Scripts\python tests\llm\eval\e3_retrieval.py --seed 7", commit=COMMIT, seeds=[7, 11],
        models={"embeddings/bge-small-en-v1.5/model.onnx": SHA_A}, started_utc="2026-10-30T10:00:00+00:00",
        duration_s=12.25)
    return EvalResult.model_validate(fields | change)


def write_fakes(folder: Path) -> None:
    write_result(result(), folder)                                                          # passed
    write_result(result(id="E4", title="Valid tool calls", target=">= 98 % constrained", passed=False,
                        measured={"constrained": 0.9666667, "free": 0.71, "calls": 300},
                        command="python tests/llm/eval/e4_tools.py", seeds=[3],
                        models={"llm/qwen.gguf": SHA_B}, notes=FAILED_NOTES), folder)          # failed, with notes
    write_result(result(id="E6", title="Latency", target="first token <= 1.5 s p50", passed=None,
                        measured={"first_token_s": {"p50": 1.2345678, "p95": 3.0}, "runs": 50},
                        command="python tests/llm/eval/e6_latency.py", seeds=[], models={}), folder)  # undecided
    write_result(result(id="E11", title="Tone hint", target="sanity check", passed=True,
                        measured={"calm_ok": 3}, command="python tests/llm/eval/e11_tone.py"), folder)


def row(report: str, metric: str) -> str:
    rows = [line for line in report.splitlines() if line.startswith(f"| {metric} |")]
    assert len(rows) == 1, (metric, rows)
    return rows[0]


def without_time(body: str) -> list[str]:
    return [line for line in body.splitlines() if not line.startswith("- Generated (UTC): ")]


def rendered(folder: Path) -> str:
    return render(read_results(folder), generated_utc="2026-10-30T12:00:00+00:00", commit=COMMIT,
                  results_dir="logs/eval")


def test_every_metric_has_one_row_and_missing_ones_are_not_run_yet(tmp_path: Path) -> None:
    write_fakes(tmp_path)
    report = rendered(tmp_path)
    assert list(PLAN) == [f"E{n}" for n in range(1, 12)]
    for metric in PLAN:
        line = row(report, metric)
        assert PLAN[metric][1].replace("|", "\\|") in line                                # the plan's target
        if metric in ("E1", "E2", "E5", "E7", "E8", "E9", "E10"):
            assert NOT_RUN in line
            assert f"- {metric}: `logs/eval/{metric}.json` ({NOT_RUN})" in report             # claims ledger
        else:
            assert NOT_RUN not in line
    assert "E1-E10: 1 pass, 1 fail, 1 n/a, 7 not run yet" in report


def test_numbers_appear_exactly_as_stored(tmp_path: Path) -> None:
    write_fakes(tmp_path)
    report = rendered(tmp_path)
    e3, e4, e6 = row(report, "E3"), row(report, "E4"), row(report, "E6")
    assert "hybrid.recall_at_4 = 0.9333333" in e3 and "hybrid.mrr = 0.8125" in e3 and "questions = 20" in e3
    assert "0.933333 " not in report and "0.93 " not in report and "93.3" not in report
    assert "constrained = 0.9666667" in e4 and "free = 0.71" in e4 and "calls = 300" in e4
    assert "first_token_s.p50 = 1.2345678" in e6 and "first_token_s.p95 = 3.0" in e6 and "runs = 50" in e6
    assert "| 12.25 s |" in e3
    assert "  - hybrid.recall_at_4 = `0.9333333`" in report                                    # details section


def test_pass_fail_and_undecided_with_command_commit_seeds_and_models(tmp_path: Path) -> None:
    write_fakes(tmp_path)
    report = rendered(tmp_path)
    e3, e4, e6 = row(report, "E3"), row(report, "E4"), row(report, "E6")
    assert "| pass |" in e3 and "| fail |" in e4 and "| n/a |" in e6
    assert r"`.venv\Scripts\python tests\llm\eval\e3_retrieval.py --seed 7`" in e3
    assert "| 0123456789ab-dirty |" in e3 and "| 7, 11 |" in e3
    assert f"embeddings/bge-small-en-v1.5/model.onnx {SHA_A[:12]}" in e3
    assert f"embeddings/bge-small-en-v1.5/model.onnx: `{SHA_A}`" in report                    # full hash in details
    assert f"llm/qwen.gguf: `{SHA_B}`" in report
    assert f"- Commit: `{COMMIT}`" in report
    assert "- E3: `logs/eval/E3.json`, command `python" not in report                      # its own command, not E4's
    assert r"- E3: `logs/eval/E3.json`, command `.venv\Scripts\python tests\llm\eval\e3_retrieval.py --seed 7`" \
        in report


def test_a_failed_metric_shows_its_notes(tmp_path: Path) -> None:
    write_fakes(tmp_path)
    report = rendered(tmp_path)
    assert FAILED_NOTES.replace("|", "\\|") in row(report, "E4")                           # a pipe cannot break the row
    assert f"- Notes: {FAILED_NOTES}" in report


def test_e11_is_cut_even_with_a_file(tmp_path: Path) -> None:
    write_fakes(tmp_path)
    with_file = row(rendered(tmp_path), "E11")
    assert CUT_TEXT == "cut (decision 0016)"
    assert CUT_TEXT in with_file and "| pass |" not in with_file and "calm_ok" not in with_file
    assert "logs/eval/E11.json exists and is not reported" in with_file
    (tmp_path / "E11.json").unlink()
    without = rendered(tmp_path)
    assert CUT_TEXT in row(without, "E11") and "exists" not in row(without, "E11")
    assert f"- E11: {CUT_TEXT}\n" in without


def test_the_header_and_determinism(tmp_path: Path) -> None:
    write_fakes(tmp_path)
    report = rendered(tmp_path)
    assert report == rendered(tmp_path)
    assert "- Generated (UTC): 2026-10-30T12:00:00+00:00" in report
    assert f"- Git commit: `{COMMIT}`" in report
    assert "must not be edited by hand" in report
    assert render({}, generated_utc="t", commit=COMMIT).count(NOT_RUN) >= 10


def test_main_writes_only_the_given_file(tmp_path: Path) -> None:
    results, out = tmp_path / "results", tmp_path / "report" / "results-phase6.md"
    write_fakes(results)
    real_before = DEFAULT_OUT.stat().st_mtime_ns if DEFAULT_OUT.exists() else None
    assert main(["--results", str(results), "--out", str(out)]) == 0
    real_after = DEFAULT_OUT.stat().st_mtime_ns if DEFAULT_OUT.exists() else None
    assert real_after == real_before                                                      # the real report untouched
    text = out.read_text(encoding="utf-8")
    assert "hybrid.recall_at_4 = 0.9333333" in text and CUT_TEXT in text
    assert all(f"| {metric} |" in text for metric in PLAN)
    assert "\r\n" not in out.read_bytes().decode("utf-8")
    generated = [line for line in text.splitlines() if line.startswith("- Generated (UTC): ")]
    assert len(generated) == 1
    again = tmp_path / "again.md"
    assert main(["--results", str(results), "--out", str(again)]) == 0
    assert without_time(again.read_text(encoding="utf-8")) == without_time(text)                       # deterministic but the time
