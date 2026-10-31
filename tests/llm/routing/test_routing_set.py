"""The E5 routing set is well formed: schema, at least 40 questions, unique ids and texts, valid machines, farm_wide
consistent with the machines, and every category present. It never runs route(): the score is E5's job
(tests/llm/eval/e5_routing.py), not a test's.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from contracts import NODE_IDS, NodeId

SET = Path(__file__).resolve().parent / "routing_set.json"
Category = Literal["direct", "multi", "implied", "default", "both_incubators", "farm_wide", "typo", "ambiguous"]
CATEGORIES: tuple[Category, ...] = ("direct", "multi", "implied", "default", "both_incubators", "farm_wide", "typo",
                                    "ambiguous")


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^r\d{2,3}$")
    category: Category
    text: str = Field(min_length=1)
    machines: list[NodeId] = Field(min_length=1)
    farm_wide: bool
    why: str = Field(min_length=1)


class RoutingSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    about: str = Field(min_length=1)
    questions: list[Question]


def load_set() -> RoutingSet:
    return RoutingSet.model_validate(json.loads(SET.read_text(encoding="utf-8")))


def test_at_least_forty_questions_with_unique_ids_and_texts() -> None:
    questions = load_set().questions
    assert len(questions) >= 40
    ids = [question.id for question in questions]
    texts = [" ".join(question.text.casefold().split()) for question in questions]
    assert len(set(ids)) == len(ids)
    assert len(set(texts)) == len(texts)


def test_machines_are_node_ids_without_repeats() -> None:
    for question in load_set().questions:
        assert set(question.machines) <= set(NODE_IDS), question.id
        assert len(set(question.machines)) == len(question.machines), question.id


def test_farm_wide_lists_all_four_machines_and_only_then() -> None:
    for question in load_set().questions:
        assert question.farm_wide == (set(question.machines) == set(NODE_IDS)), question.id
        assert question.farm_wide == (question.category == "farm_wide") or question.category in ("typo", "ambiguous"), \
            question.id


def test_every_category_is_covered_and_every_machine_is_named_directly() -> None:
    questions = load_set().questions
    assert {question.category for question in questions} == set(CATEGORIES)
    direct = {machine for question in questions if question.category == "direct" for machine in question.machines}
    assert direct == set(NODE_IDS)
    assert sum(question.category == "typo" for question in questions) >= 3
    assert sum(question.category == "ambiguous" for question in questions) >= 3
