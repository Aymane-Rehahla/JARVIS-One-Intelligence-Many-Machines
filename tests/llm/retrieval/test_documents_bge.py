"""Real BGE retrieval finds a manual passage that implies an HVAC fault without repeating the query wording."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from llm.documents import BgeEmbedder, Documents

ROOT = Path(__file__).resolve().parents[3]
MANUAL = ROOT / "nodes" / "hvac" / "memory" / "rom" / "hvac_manual.md"
MODEL = ROOT / "models" / "embeddings" / "bge-small-en-v1.5" / "model.onnx"
TOKENIZER = ROOT / "models" / "embeddings" / "bge-small-en-v1.5" / "tokenizer.json"
QUERY = "the HVAC is struggling"


def fixture_rom(tmp_path: Path) -> Path:
    """Copy the real machine manual and create only temporary source and approval records."""
    rom = tmp_path / "nodes" / "hvac" / "memory" / "rom"
    rom.mkdir(parents=True)
    manual = rom / MANUAL.name
    text = MANUAL.read_text(encoding="utf-8").replace('status = "draft"', 'status = "approved"', 1)
    manual.write_text(text, encoding="utf-8")
    root = tmp_path / "_dev" / "farm_poc_snapshot"
    for source in ("devices/hvac.py", "controllers/hvac_controller.py"):
        target = root / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("temporary source fixture\n", encoding="utf-8")
    approval = {"schema_version": 1, "approved": {manual.name: {
        "sha256": hashlib.sha256(manual.read_bytes()).hexdigest(), "version": 1,
        "approved_utc": "2026-10-27T00:00:00Z", "by": "fixture"}}, "history": {}}
    (rom / "approvals.json").write_text(json.dumps(approval), encoding="utf-8")
    return rom


def relevant_passage(result: dict[str, Any]) -> bool:
    text = str(result["text"]).casefold()
    return "compressor" in text and "struggling" not in text


def test_real_bge_finds_hvac_manual_passage_and_records_ranks(tmp_path: Path) -> None:
    if not MODEL.is_file() or not TOKENIZER.is_file():
        pytest.skip("BGE model.onnx or tokenizer.json is missing")
    rom = fixture_rom(tmp_path)
    embedder = BgeEmbedder()
    documents = Documents(rom, tmp_path / "docs_index.sqlite", embedder)
    try:
        build_started = time.perf_counter()
        assert documents.rebuild()
        build_seconds = time.perf_counter() - build_started

        keyword_ids = documents._keyword(QUERY)
        vector_ids = documents._vector(QUERY)
        query_started = time.perf_counter()
        hybrid = documents.search(QUERY, 4)
        query_seconds = time.perf_counter() - query_started
        matches = [item for item in hybrid if relevant_passage(item)]
        assert matches, "the top four hybrid results must contain the compressor passage without the query word"
        match = matches[0]
        candidates = documents._db.execute("SELECT id,file,section,text FROM passages WHERE file=? AND section=?",
                                           (match["file"], match["section"])).fetchall()
        passage_id = next(int(row["id"]) for row in candidates if str(row["text"])[:1200] == match["text"])
        keyword_rank = keyword_ids.index(passage_id) + 1 if passage_id in keyword_ids else None
        vector_rank = vector_ids.index(passage_id) + 1 if passage_id in vector_ids else None
        hybrid_rank = next(index for index, item in enumerate(hybrid, start=1) if item == match)
        evidence = {"query": QUERY, "embedder_id": embedder.model_id, "build_seconds": build_seconds,
                    "query_seconds": query_seconds, "matched_file": match["file"],
                    "matched_section": match["section"], "keyword_rank": keyword_rank,
                    "vector_rank": vector_rank, "hybrid_rank": hybrid_rank}
        logs = ROOT / "logs"
        logs.mkdir(exist_ok=True)
        (logs / "retrieval_hvac.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, sort_keys=True))
    finally:
        documents.close()
