"""tools/eval_results.py: the one result shape every evaluation script writes (plan section 8)."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from eval_results import EvalResult, git_commit, model_hashes, read_results, utc_now, write_result


BGE = "embeddings/bge-small-en-v1.5/model.onnx"


def result(**change: object) -> EvalResult:
    fields: dict[str, object] = dict(id="E3", title="Retrieval quality", target="hybrid >= both alone; Recall@4 >= 0.9",
                                     passed=True, measured={"hybrid": {"recall_at_4": 0.95, "mrr": 0.81}},
                                     command="python tests/llm/eval/e3_retrieval.py", commit=git_commit(), seeds=[7],
                                     models=model_hashes([BGE]), started_utc=utc_now(), duration_s=1.5)
    return EvalResult.model_validate(fields | change)


def test_a_result_is_written_and_read_back(tmp_path: Path) -> None:
    assert write_result(result(), tmp_path) == tmp_path / "E3.json"
    rerun = result(passed=False, notes="hybrid ranked below vector-only on 2 of 20")
    write_result(rerun, tmp_path)                                               # a rerun replaces the earlier file
    assert read_results(tmp_path) == {"E3": rerun}


def test_the_model_hash_comes_from_the_manifest() -> None:
    (path, sha), = model_hashes([BGE]).items()
    assert path == BGE and len(sha) == 64
    with pytest.raises(KeyError, match="0 entries"):
        model_hashes(["no-such-model.gguf"])
    with pytest.raises(KeyError, match="2 entries"):                  # bge-small's and Kokoro's: name the path
        model_hashes(["model.onnx"])


def test_unknown_metrics_and_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        result(id="E12")
    with pytest.raises(ValidationError):
        result(surprise=1)
