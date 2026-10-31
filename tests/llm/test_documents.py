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


SOURCE_LINK = "../../../../_dev/farm_poc_snapshot/devices/hvac.py"


def write_doc(rom: Path, name: str, body: str) -> Path:
    """An approved ROM document with the given Markdown body (its source is make_rom's copied HVAC device)."""
    path = rom / name
    path.write_text(f'+++\nnode = "hvac"\ntitle = "Guide"\nkind = "shared"\nversion = 1\nstatus = "approved"\n'
                    f'sources = [{{ title = "device", link = "{SOURCE_LINK}" }}]\n+++\n{body}', encoding="utf-8")
    return path


def insert_passages(documents: Documents, texts: list[str]) -> None:
    with documents._db:
        documents._db.execute("INSERT INTO documents VALUES(?,?,?,?,?)", ("manual.md", "a" * 64, 1, "Manual", "shared"))
        for ordinal, text in enumerate(texts, start=1):
            documents._db.execute("INSERT INTO passages(file,section,ordinal,text,tokens) VALUES(?,?,?,?,?)",
                                  ("manual.md", "Manual", ordinal, text, len(text.split())))


def test_search_returns_a_whole_indexed_passage_longer_than_1200_characters(tmp_path: Path) -> None:
    """Bug (a): results were cut to 1,200 characters although passages are bounded by tokens when split."""
    rom = make_rom(tmp_path)
    words = " ".join(f"compressor{index:03d}" for index in range(250))          # 3,249 characters, 325 tokens
    document = write_doc(rom, "long.md", f"# Guide\n\n## Long section\n\n{words} the very end.\n")
    write_approvals(rom, [document])
    documents = Documents(rom, tmp_path / "docs_index.sqlite", HashEmbedder(), tokenizer_path=None)
    assert documents.rebuild()
    stored = [passage.text for passage in split_document(document, tokenizer_path=None)]
    assert len(stored) == 1 and len(stored[0]) > 1200 and stored[0].endswith("the very end.")
    results = documents.search("compressor000", 4)
    assert [item["text"] for item in results] == stored
    assert results[0]["truncated"] is False
    documents.close()


def test_search_marks_a_pathological_passage_it_cuts_for_the_bounded_tool_output(tmp_path: Path) -> None:
    """A passage the splitter could not bound (one 9,000-character word) is cut to MAX_RESULT_CHARS, never silently."""
    from llm.documents import MAX_RESULT_CHARS

    rom = make_rom(tmp_path)
    documents = Documents(rom, tmp_path / "docs_index.sqlite", HashEmbedder(), tokenizer_path=None)
    insert_passages(documents, ["x" * 9000])
    documents._keyword = types.MethodType(lambda self, query: [1], documents)
    documents._vector = types.MethodType(lambda self, query: [1], documents)
    [result] = documents.search("x", 4)
    assert result["truncated"] is True and result["text"] == "x" * MAX_RESULT_CHARS
    documents.close()


def test_keyword_query_drops_stopwords_but_never_becomes_empty(tmp_path: Path) -> None:
    """Bug (b): "what is the compressor" matched a passage made only of stopwords."""
    rom = make_rom(tmp_path)
    documents = Documents(rom, tmp_path / "docs_index.sqlite", HashEmbedder(), tokenizer_path=None)
    insert_passages(documents, ["what is the use of it, and is it the one?", "the compressor stays off for 300 s"])
    assert documents._keyword("what is the compressor") == [2]
    assert documents._keyword("What's the COMPRESSOR doing?") == [2]
    assert sorted(documents._keyword("what is it")) == [1]           # only stopwords: they are kept
    documents.close()


def test_a_heading_without_its_own_section_never_becomes_a_passage(tmp_path: Path) -> None:
    """Bug (c): each document's H1 title became a tiny passage of its own (the title and its one-line lead), a
    distractor. A heading's lead text before its first subheading now goes with that first subsection; the heading's
    words stay in the section path. A document with no subheadings keeps its single section."""
    path = tmp_path / "guide.md"
    path.write_text('+++\ntitle = "Guide"\nkind = "shared"\nversion = 1\n+++\n\n# Guide\n\n'
                    "*Derived from the model.* This guide wins.\n\n## Part A\n\nBody A.\n\n## Part B\n\nBody B.\n",
                    encoding="utf-8")
    passages = split_document(path, tokenizer_path=None)
    assert [passage.section for passage in passages] == ["Guide > Part A", "Guide > Part B"]
    assert passages[0].text == "*Derived from the model.* This guide wins.\n\nBody A."
    bare = tmp_path / "bare.md"
    bare.write_text('+++\ntitle = "Guide"\nkind = "shared"\nversion = 1\n+++\n# Guide\n\n## Part A\n\nBody A.\n',
                    encoding="utf-8")
    assert [(p.section, p.text) for p in split_document(bare, tokenizer_path=None)] == [("Guide > Part A", "Body A.")]
    single = tmp_path / "single.md"
    single.write_text('+++\ntitle = "Notes"\nkind = "shared"\nversion = 1\n+++\n# Notes\n\nOnly text.\n',
                      encoding="utf-8")
    assert [(p.section, p.text) for p in split_document(single, tokenizer_path=None)] == [("Notes", "Only text.")]


def test_no_rom_document_with_subheadings_has_a_title_only_passage() -> None:
    for path in sorted((ROOT / "nodes").glob("*/memory/rom/*.md")):
        passages = split_document(path)
        sections = {passage.section for passage in passages}
        if any(" > " in section for section in sections):
            assert all(" > " in section for section in sections), path


def test_token_counter_falls_back_with_a_log_on_a_broken_tokenizer_file(tmp_path: Path,
                                                                         caplog: pytest.LogCaptureFixture) -> None:
    """Bug (d): every exception was swallowed silently. Now the expected load errors fall back and are logged."""
    broken = tmp_path / "tokenizer.json"
    broken.write_text("not json", encoding="utf-8")
    with caplog.at_level("WARNING", logger="jarvis.documents"):
        counter = _TokenCounter(broken)
    assert counter.count("one two three") == 4                      # the word estimate: ceil(3 * 1.3)
    assert any("tokenizer" in record.getMessage() and record.levelname == "WARNING" for record in caplog.records)


def test_token_counter_logs_when_the_tokenizers_package_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                    caplog: pytest.LogCaptureFixture) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "tokenizers", None)             # makes `from tokenizers import ...` raise
    path = tmp_path / "tokenizer.json"
    path.write_text("{}", encoding="utf-8")
    with caplog.at_level("WARNING", logger="jarvis.documents"):
        counter = _TokenCounter(path)
    assert counter.count("one two") == 3
    assert any("tokenizer" in record.getMessage() for record in caplog.records)


def test_token_counter_does_not_hide_an_unexpected_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tokenizers

    def broken(path: str) -> object:
        raise ValueError("a programming error, not a bad tokenizer file")

    monkeypatch.setattr(tokenizers.Tokenizer, "from_file", staticmethod(broken))
    path = tmp_path / "tokenizer.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="programming error"):
        _TokenCounter(path)
