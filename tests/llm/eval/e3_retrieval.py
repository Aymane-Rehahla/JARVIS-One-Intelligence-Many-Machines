"""E3, retrieval quality with an ablation (plan section 8): Recall@4 and MRR for keyword-only, vector-only and hybrid.

In: `tests/llm/retrieval/retrieval_set.json` and every node's ROM documents, copied into a temporary folder and
approved there by this script (as tests/conftest.py does); the real `rom/approvals.json` is never read or written.
Out: `logs/eval/E3.json` through `write_result`, and a summary on stdout.

Each question runs on its machine's node index (all five ROM documents of that node, so the shared documents compete).
The three modes are the three rankings documents.py itself computes for one query:
- keyword-only: `Documents._keyword` (FTS5 BM25, top 20), on the query normalised exactly as `Documents.search` does;
- vector-only: `Documents._vector` (bge-small cosine, top 20), same normalised query;
- hybrid: `Documents.search(query, 8)`, the public reciprocal-rank fusion of those two rankings.
The two single-mode rankings are private methods: they are the exact pieces the hybrid fuses, and re-writing their SQL
here could drift from them. The script re-fuses them with documents.RRF_K and checks the result equals `search()`.

A hit is a passage whose (file, section) equals the expected one. Recall@4: share of questions with a hit in the top 4.
MRR: mean of 1/rank over the top 8 (0 when not in the top 8; 8 is the most `search()` returns). Nothing here is tuned
on this set.

Run: `.venv\\Scripts\\python tests\\llm\\eval\\e3_retrieval.py`
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[3]
for _extra in (ROOT / "tools", ROOT / "nodes" / "incubator-1"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from pydantic import BaseModel, ConfigDict, JsonValue  # noqa: E402

from eval_results import EvalResult, file_sha256, git_commit, model_hashes, utc_now, write_result  # noqa: E402
from llm.documents import RRF_K, BgeEmbedder, Documents  # noqa: E402

SET = ROOT / "tests" / "llm" / "retrieval" / "retrieval_set.json"
MODEL_FILES = ["embeddings/bge-small-en-v1.5/model.onnx", "embeddings/bge-small-en-v1.5/tokenizer.json"]
COMMAND = r".venv\Scripts\python tests\llm\eval\e3_retrieval.py"
TARGET = "hybrid >= both alone; Recall@4 >= 0.9"
MODES = ("keyword", "vector", "hybrid")
RECALL_AT = 4
MRR_DEPTH = 8
Mode = Literal["keyword", "vector", "hybrid"]
Key = tuple[str, str]


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    file: str
    section: str


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    machine: str
    style: Literal["paraphrased", "lexical"]
    question: str
    expected: Expected


class RetrievalSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    about: str
    machines: dict[str, list[str]]
    questions: list[Question]


@dataclass(frozen=True)
class Outcome:
    """One question on one node: the expected passage's rank in each mode (None when not ranked) and each top 1."""

    question: Question
    node: str
    ranks: dict[Mode, int | None]
    top1: dict[Mode, Key | None]
    fusion_matches_search: bool


def approved_rom_copy(node: str, destination: Path) -> Path:
    """Copy a node's ROM documents byte for byte into `<destination>/nodes/<node>/memory/rom`, mark them approved,
    copy their local sources to the same relative place, and approve exactly the copied bytes in the copy only."""
    source_rom = ROOT / "nodes" / node / "memory" / "rom"
    rom = destination / "nodes" / node / "memory" / "rom"
    rom.mkdir(parents=True)
    approved: dict[str, JsonValue] = {}
    for source in sorted(source_rom.glob("*.md")):
        content = source.read_bytes().replace(b'status = "draft"', b'status = "approved"', 1)
        target = rom / source.name
        target.write_bytes(content)
        header = tomllib.loads(content.decode("utf-8").split("+++", 2)[1])
        for record in header.get("sources", []):
            link = str(record["link"])
            if link.startswith(("https://", "http://")):
                continue
            original = (source_rom / link).resolve()
            copied = destination / original.relative_to(ROOT)
            # copyfile, never copy2: the snapshot's files can be read-only, and copied modes would block overwrite
            # and the temporary folder's clean-up
            if original.is_dir():
                shutil.copytree(original, copied, dirs_exist_ok=True, copy_function=shutil.copyfile,
                                ignore=shutil.ignore_patterns("__pycache__"))
            elif not copied.exists():
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, copied)
        approved[target.name] = {"sha256": file_sha256(target), "version": int(header["version"]),
                                 "approved_utc": "2026-10-30T00:00:00Z", "by": "E3 test fixture"}
    (rom / "approvals.json").write_text(json.dumps({"schema_version": 1, "approved": approved, "history": {}}),
                                        encoding="utf-8")
    return rom


def passage_keys(index_path: Path) -> dict[int, Key]:
    """Passage id -> (file, section), read from the index through a separate read-only connection."""
    connection = sqlite3.connect(f"file:{index_path.as_posix()}?mode=ro", uri=True)
    try:
        return {int(row[0]): (str(row[1]), str(row[2]))
                for row in connection.execute("SELECT id,file,section FROM passages")}
    finally:
        connection.close()


def fuse(keyword: list[int], vector: list[int], k: int) -> list[int]:
    """The reciprocal-rank fusion `Documents.search` applies, re-computed to check the ablation's pieces."""
    scores: dict[int, float] = {}
    for ranking in (keyword, vector):
        for rank, passage_id in enumerate(ranking, start=1):
            scores[passage_id] = scores.get(passage_id, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores, key=lambda passage_id: (-scores[passage_id], passage_id))[:k]


def first_rank(ranking: list[Key], expected: Key) -> int | None:
    return next((rank for rank, key in enumerate(ranking, start=1) if key == expected), None)


def evaluate_node(node: str, questions: list[Question], embedder: BgeEmbedder,
                  work: Path) -> tuple[list[Outcome], float, list[str]]:
    """Build one node's index on approved copies and rank every question in the three modes."""
    rom = approved_rom_copy(node, work / node)
    index_path = work / node / "docs_index.sqlite"
    documents = Documents(rom, index_path, embedder)
    try:
        started = time.perf_counter()
        documents.rebuild()
        build_s = time.perf_counter() - started
        indexed = sorted(info.file for info in documents.infos())
        expected_files = sorted(path.name for path in rom.glob("*.md"))
        if indexed != expected_files:
            raise RuntimeError(f"{node}: indexed {indexed}, expected every ROM document {expected_files}")
        keys = passage_keys(index_path)
        outcomes: list[Outcome] = []
        for question in questions:
            normalized = " ".join(question.question.casefold().split())[:1000]   # as Documents.search does
            keyword_ids = documents._keyword(normalized)
            vector_ids = documents._vector(normalized)
            hybrid = documents.search(question.question, MRR_DEPTH)
            rankings: dict[Mode, list[Key]] = {
                "keyword": [keys[passage_id] for passage_id in keyword_ids],
                "vector": [keys[passage_id] for passage_id in vector_ids],
                "hybrid": [(str(item["file"]), str(item["section"])) for item in hybrid],
            }
            fused = [keys[passage_id] for passage_id in fuse(keyword_ids, vector_ids, MRR_DEPTH)]
            expected: Key = (question.expected.file, question.expected.section)
            outcomes.append(Outcome(
                question=question, node=node,
                ranks={mode: first_rank(rankings[mode], expected) for mode in MODES},
                top1={mode: (rankings[mode][0] if rankings[mode] else None) for mode in MODES},
                fusion_matches_search=fused == rankings["hybrid"]))
        return outcomes, build_s, indexed
    finally:
        documents.close()


def recall(outcomes: list[Outcome], mode: Mode) -> float:
    hits = [outcome for outcome in outcomes if (rank := outcome.ranks[mode]) is not None and rank <= RECALL_AT]
    return round(len(hits) / len(outcomes), 4) if outcomes else 0.0


def mrr(outcomes: list[Outcome], mode: Mode) -> float:
    total = sum(1.0 / rank for outcome in outcomes if (rank := outcome.ranks[mode]) is not None and rank <= MRR_DEPTH)
    return round(total / len(outcomes), 4) if outcomes else 0.0


def metrics(outcomes: list[Outcome]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {"questions": len(outcomes)}
    for mode in MODES:
        result[mode] = {f"recall_at_{RECALL_AT}": recall(outcomes, mode), f"mrr_at_{MRR_DEPTH}": mrr(outcomes, mode)}
    return result


def missed(outcomes: list[Outcome], mode: Mode) -> list[JsonValue]:
    return [outcome.question.id for outcome in outcomes
            if (rank := outcome.ranks[mode]) is None or rank > RECALL_AT]


def key_text(key: Key | None) -> str | None:
    return None if key is None else f"{key[0]} :: {key[1]}"


def main() -> int:
    started_utc = utc_now()
    started = time.perf_counter()
    retrieval_set = RetrievalSet.model_validate(json.loads(SET.read_text(encoding="utf-8")))
    models = model_hashes(MODEL_FILES)
    embedder = BgeEmbedder()
    primary: list[Outcome] = []
    secondary: dict[str, list[Outcome]] = {}
    build_seconds: dict[str, JsonValue] = {}
    indexed_documents: dict[str, JsonValue] = {}
    with tempfile.TemporaryDirectory(prefix="jarvis_e3_") as temporary:
        work = Path(temporary)
        for machine, nodes in retrieval_set.machines.items():
            questions = [question for question in retrieval_set.questions if question.machine == machine]
            for position, node in enumerate(nodes):
                outcomes, build_s, indexed = evaluate_node(node, questions, embedder, work)
                build_seconds[node] = round(build_s, 3)
                indexed_documents[node] = list[JsonValue](indexed)
                if position == 0:
                    primary.extend(outcomes)
                else:
                    secondary[node] = outcomes
    measured: dict[str, JsonValue] = {
        "overall": metrics(primary),
        "per_machine": {machine: metrics([outcome for outcome in primary if outcome.question.machine == machine])
                        for machine in retrieval_set.machines},
        "per_style": {style: metrics([outcome for outcome in primary if outcome.question.style == style])
                      for style in ("paraphrased", "lexical")},
        "missed_at_4": {mode: missed(primary, mode) for mode in MODES},
        "fusion_recomputed_equals_search": all(outcome.fusion_matches_search
                                               for outcome in [*primary, *(o for v in secondary.values() for o in v)]),
        "secondary_nodes": {node: {"metrics": metrics(outcomes),
                                   "same_ranks_as_primary": [o.ranks for o in outcomes] == [
                                       p.ranks for p in primary if p.question.machine == outcomes[0].question.machine]}
                            for node, outcomes in secondary.items()},
        "per_question": [{"id": outcome.question.id, "node": outcome.node, "style": outcome.question.style,
                          "ranks": {mode: outcome.ranks[mode] for mode in MODES},
                          "top1": {mode: key_text(outcome.top1[mode]) for mode in MODES}}
                         for outcome in primary],
        "index_build_seconds": build_seconds,
        "indexed_documents": indexed_documents,
        "retrieval_set_sha256": file_sha256(SET),
        "embedder_id": embedder.model_id,
        "rrf_k": RRF_K,
    }
    overall = {mode: (recall(primary, mode), mrr(primary, mode)) for mode in MODES}
    hybrid_not_worse = all(overall["hybrid"][metric] >= overall[other][metric]
                           for other in ("keyword", "vector") for metric in (0, 1))
    recall_ok = overall["hybrid"][0] >= 0.9
    passed = hybrid_not_worse and recall_ok
    measured["rules"] = {"hybrid_not_worse_than_either_on_recall_and_mrr": hybrid_not_worse,
                         "hybrid_recall_at_4_at_least_0_9": recall_ok}
    per_machine_hybrid_ok = {machine: all(
        recall([o for o in primary if o.question.machine == machine], "hybrid")
        >= recall([o for o in primary if o.question.machine == machine], other) for other in ("keyword", "vector"))
        for machine in retrieval_set.machines}
    notes = (
        f"{len(primary)} questions ({sum(o.question.style == 'paraphrased' for o in primary)} paraphrased, "
        f"{sum(o.question.style == 'lexical' for o in primary)} lexical), written from the ROM documents before any "
        "search ran; nothing (RRF_K, depth, set) was tuned on them. Each machine's questions run on its node's index "
        "of all five fixture-approved ROM copies (shared documents compete); incubator questions are scored on "
        "incubator-1 and repeated on incubator-2 (identical ROM) under secondary_nodes. Pass rule: overall hybrid "
        "Recall@4 and MRR@8 each >= keyword-only and >= vector-only, and overall hybrid Recall@4 >= 0.9. "
        f"Per machine, hybrid Recall@4 >= both alone: {per_machine_hybrid_ok}. Keyword-only and vector-only are "
        "documents.py's private _keyword/_vector rankings (top 20) on the query normalised as search() does; their "
        "re-fusion equals search() (fusion_recomputed_equals_search). One expected passage per question, matched by "
        "file and section; a question another passage also answers counts as a miss when only that one is found."
    )
    result = EvalResult(id="E3", title="Retrieval quality, with an ablation", target=TARGET, passed=passed,
                        measured=measured, command=COMMAND, commit=git_commit(), seeds=[], models=models,
                        started_utc=started_utc, duration_s=round(time.perf_counter() - started, 3), notes=notes)
    path = write_result(result)
    print(f"E3 {'PASSED' if passed else 'FAILED'}  ({len(primary)} questions)  -> {path}")
    for scope, outcomes in [("overall", primary), *[(machine, [o for o in primary if o.question.machine == machine])
                                                    for machine in retrieval_set.machines]]:
        cells = "  ".join(f"{mode}: R@4 {recall(outcomes, mode):.3f} MRR {mrr(outcomes, mode):.3f}" for mode in MODES)
        print(f"  {scope:<18} {cells}")
    for mode in MODES:
        print(f"  missed at 4, {mode}: {missed(primary, mode)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
