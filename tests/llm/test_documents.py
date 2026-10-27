"""Approved ROM indexing, splitting, embeddings, hybrid ranking and cache invalidation."""
from __future__ import annotations

import hashlib
import json
import shutil
import types
from pathlib import Path
from typing import Sequence

import pytest

import knowledge
from llm.documents import (QUERY_PREFIX, Documents, HashEmbedder, Passage, _TokenCounter, indexable,
                           split_document)

ROOT = Path(__file__).resolve().parents[2]
MANUAL = ROOT / "nodes" / "hvac" / "memory" / "rom" / "hvac_manual.md"


class SpyEmbedder(HashEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.passage_texts: list[str] = []
        self.queries: list[str] = []

    def embed(self, texts: Sequence[str]):
        self.passage_texts.extend(texts)
        return super().embed(texts)

    def embed_query(self, text: str):
        self.queries.append(text)
        return super().embed_query(text)


def make_rom(tmp_path: Path) -> Path:
    """Create a temporary HVAC ROM and source tree containing copied project manual inputs."""
    rom = tmp_path / "nodes" / "hvac" / "memory" / "rom"
    rom.mkdir(parents=True)
    device = tmp_path / "_dev" / "farm_poc_snapshot" / "devices" / "hvac.py"
    controller = tmp_path / "_dev" / "farm_poc_snapshot" / "controllers" / "hvac_controller.py"
    device.parent.mkdir(parents=True)
    controller.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "_dev" / "farm_poc_snapshot" / "devices" / "hvac.py", device)
    shutil.copyfile(ROOT / "_dev" / "farm_poc_snapshot" / "controllers" / "hvac_controller.py", controller)
    return rom


def copy_manual(rom: Path, name: str, *, approved: bool) -> Path:
    text = MANUAL.read_text(encoding="utf-8")
    if approved:
        text = text.replace('status = "draft"', 'status = "approved"', 1)
    path = rom / name
    path.write_text(text, encoding="utf-8")
    return path


def write_approvals(rom: Path, paths: list[Path]) -> None:
    records = {path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "version": 1,
                           "approved_utc": "2026-10-27T00:00:00Z", "by": "fixture"} for path in paths}
    (rom / "approvals.json").write_text(json.dumps({"schema_version": 1, "approved": records, "history": {}}),
                                         encoding="utf-8")


def test_indexable_matches_knowledge_approval_rules_and_rejects_edits(tmp_path: Path) -> None:
    rom = make_rom(tmp_path)
    approved = copy_manual(rom, "approved.md", approved=True)
    draft = copy_manual(rom, "draft.md", approved=False)
    edited = copy_manual(rom, "edited.md", approved=True)
    write_approvals(rom, [approved, edited])
    edited.write_text(edited.read_text(encoding="utf-8") + "\nUnapproved edit.\n", encoding="utf-8")
    assert indexable(rom) == knowledge.indexable(rom) == [approved]


def test_splitting_keeps_headings_tables_and_token_bounds(tmp_path: Path) -> None:
    rom = make_rom(tmp_path)
    path = copy_manual(rom, "hvac_manual.md", approved=False)
    passages = split_document(path)
    assert passages and all(item.tokens <= 520 for item in passages)
    assert all("+++" not in item.text for item in passages)
    assert any(item.section.endswith("Normal operation") for item in passages)
    table_passages = [item for item in passages if "| Zone | Temperature setpoint |" in item.text]
    assert table_passages
    assert all("| Zone | Temperature setpoint |" in item.text for item in table_passages)


def test_long_tables_repeat_header_and_keep_rows_whole(tmp_path: Path) -> None:
    path = tmp_path / "long.md"
    path.write_text('+++\ntitle = "Guide"\nkind = "shared"\nversion = 1\n+++\n## Records\n\n'
                    '| Name | Detail |\n| --- | --- |\n' +
                    "".join(f"| row-{index} | " + "detail " * 18 + "|\n" for index in range(80)),
                    encoding="utf-8")
    parts = split_document(path)
    assert len(parts) > 1 and all(part.tokens <= 520 for part in parts)
    assert all("| Name | Detail |" in part.text and "| --- | --- |" in part.text for part in parts)
    assert all(line.count("|") >= 3 for part in parts for line in part.text.splitlines() if line.startswith("|"))


def test_query_prefix_is_used_only_for_queries(tmp_path: Path) -> None:
    rom = make_rom(tmp_path)
    manual = copy_manual(rom, "hvac_manual.md", approved=True)
    write_approvals(rom, [manual])
    embedder = SpyEmbedder()
    documents = Documents(rom, tmp_path / "docs_index.sqlite", embedder, tokenizer_path=None)
    assert documents.rebuild()
    assert embedder.passage_texts and all(not text.startswith(QUERY_PREFIX) for text in embedder.passage_texts)
    documents.search("compressor struggling", 4)
    assert embedder.queries == ["compressor struggling"]
    documents.close()


def test_rrf_combines_keyword_and_vector_rankings(tmp_path: Path) -> None:
    rom = make_rom(tmp_path)
    documents = Documents(rom, tmp_path / "docs_index.sqlite", HashEmbedder(), tokenizer_path=None)
    with documents._db:
        documents._db.execute("INSERT INTO documents VALUES(?,?,?,?,?)", ("manual.md", "a" * 64, 1, "Manual", "shared"))
        for ordinal in range(1, 4):
            documents._db.execute("INSERT INTO passages(file,section,ordinal,text,tokens) VALUES(?,?,?,?,?)",
                                  ("manual.md", "Manual", ordinal, f"passage {ordinal}", 2))
    documents._keyword = types.MethodType(lambda self, query: [1, 2, 3], documents)
    documents._vector = types.MethodType(lambda self, query: [2, 3, 1], documents)
    assert [item["text"] for item in documents.search("manual", 3)] == ["passage 2", "passage 1", "passage 3"]
    documents.close()


@pytest.mark.parametrize("query", ['"', "NEAR(", "*", "OR", ")", "", "x" * 1000])
def test_hostile_search_queries_never_raise(tmp_path: Path, query: str) -> None:
    rom = make_rom(tmp_path)
    documents = Documents(rom, tmp_path / "docs_index.sqlite", HashEmbedder(), tokenizer_path=None)
    assert documents.search(query, 4) == []
    documents.close()


def test_cache_hits_and_index_changes_invalidate_it(tmp_path: Path) -> None:
    rom = make_rom(tmp_path)
    manual = copy_manual(rom, "hvac_manual.md", approved=True)
    write_approvals(rom, [manual])
    embedder = SpyEmbedder()
    documents = Documents(rom, tmp_path / "docs_index.sqlite", embedder, tokenizer_path=None)
    documents.rebuild()
    documents.search("compressor", 4)
    query_count = len(embedder.queries)
    documents.search("compressor", 4)
    assert len(embedder.queries) == query_count
    with manual.open("a", encoding="utf-8") as handle:
        handle.write("\nA new approved passage.\n")
    write_approvals(rom, [manual])
    assert documents.rebuild()
    documents.search("compressor", 4)
    assert len(embedder.queries) == query_count + 1
    assert len(documents.cache) == 1
    documents.close()


def test_incremental_rebuild_skips_unchanged_and_removes_missing_files(tmp_path: Path) -> None:
    rom = make_rom(tmp_path)
    manual = copy_manual(rom, "hvac_manual.md", approved=True)
    write_approvals(rom, [manual])
    embedder = SpyEmbedder()
    documents = Documents(rom, tmp_path / "docs_index.sqlite", embedder, tokenizer_path=None)
    assert documents.rebuild()
    embedded = len(embedder.passage_texts)
    assert not documents.rebuild() and len(embedder.passage_texts) == embedded
    (rom / "approvals.json").write_text(json.dumps({"schema_version": 1, "approved": {}, "history": {}}),
                                         encoding="utf-8")
    assert documents.rebuild() and documents.infos() == []
    documents.close()


def test_empty_rom_is_a_valid_empty_index(tmp_path: Path) -> None:
    rom = make_rom(tmp_path)
    documents = Documents(rom, tmp_path / "docs_index.sqlite", HashEmbedder(), tokenizer_path=None)
    assert documents.status() == "no approved documents"
    assert documents.search("anything") == []
    assert documents.infos() == []
    documents.close()
