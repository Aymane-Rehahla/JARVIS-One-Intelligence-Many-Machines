"""The E3 retrieval set is well formed: every expected passage exists, 15-20 questions per machine, no duplicates.

Fast: it splits the ROM documents with documents.py's own splitter (the tokenizer file only, no embedding model).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from llm.documents import split_document

ROOT = Path(__file__).resolve().parents[3]
SET = Path(__file__).resolve().parent / "retrieval_set.json"


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    file: str = Field(min_length=1)
    section: str = Field(min_length=1)


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    machine: str = Field(min_length=1)
    style: Literal["paraphrased", "lexical"]
    question: str = Field(min_length=1)
    expected: Expected


class RetrievalSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    about: str
    machines: dict[str, list[str]]
    questions: list[Question]


def load_set() -> RetrievalSet:
    return RetrievalSet.model_validate(json.loads(SET.read_text(encoding="utf-8")))


def node_passages(node: str) -> set[tuple[str, str]]:
    """(file, section) of every passage documents.py makes from one node's ROM documents."""
    rom = ROOT / "nodes" / node / "memory" / "rom"
    return {(passage.file, passage.section) for path in sorted(rom.glob("*.md")) for passage in split_document(path)}


def test_every_expected_passage_exists_in_every_node_of_its_machine() -> None:
    retrieval_set = load_set()
    passages = {node: node_passages(node) for nodes in retrieval_set.machines.values() for node in nodes}
    assert all(passages.values()), "every node must have ROM passages"
    missing = [(question.id, node) for question in retrieval_set.questions
               for node in retrieval_set.machines[question.machine]
               if (question.expected.file, question.expected.section) not in passages[node]]
    assert missing == []


def test_fifteen_to_twenty_questions_per_machine() -> None:
    retrieval_set = load_set()
    assert set(retrieval_set.machines) == {"incubator", "hvac", "animal-protection"}
    assert {question.machine for question in retrieval_set.questions} == set(retrieval_set.machines)
    for machine in retrieval_set.machines:
        count = sum(question.machine == machine for question in retrieval_set.questions)
        assert 15 <= count <= 20, f"{machine}: {count} questions"


def test_no_duplicate_ids_or_questions_and_mostly_paraphrased() -> None:
    questions = load_set().questions
    ids = [question.id for question in questions]
    texts = [" ".join(question.question.casefold().split()) for question in questions]
    assert len(set(ids)) == len(ids)
    assert len(set(texts)) == len(texts)
    paraphrased = sum(question.style == "paraphrased" for question in questions)
    lexical = len(questions) - paraphrased
    assert lexical >= 1 and paraphrased > lexical
