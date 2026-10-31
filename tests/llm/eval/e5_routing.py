"""E5, routing (plan section 8): the share of owner questions sent to the right node(s).

In: `tests/llm/routing/routing_set.json`, owner questions each with the machines that must answer it, written from
the questions' meaning and the ROM documents before route() was run on them.
Out: `logs/eval/E5.json` through `write_result`, and a summary on stdout.

Code under test: `llm.routing.route(text)` of nodes/incubator-1 with its default speaking node (incubator-1, the node
that talks to the owner). A question is correct when the set of `route(text).machines` equals the set of expected
machines exactly (one machine too many or too few is a miss). The share is reported overall and per category, and
every miss is listed with what route() returned. Whether route() flagged the question farm-wide is reported too, but
only the machines decide the pass. No language model, no network: route() is code. Nothing here is tuned on this
set, and routing.py is not changed to raise the score.

Run: `.venv\\Scripts\\python tests\\llm\\eval\\e5_routing.py`
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[3]
for _extra in (ROOT / "tools", ROOT / "nodes" / "incubator-1"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from pydantic import BaseModel, ConfigDict, JsonValue  # noqa: E402

from contracts import NodeId  # noqa: E402
from eval_results import EvalResult, file_sha256, git_commit, utc_now, write_result  # noqa: E402
from llm.routing import Route, route  # noqa: E402

SET = ROOT / "tests" / "llm" / "routing" / "routing_set.json"
COMMAND = r".venv\Scripts\python tests\llm\eval\e5_routing.py"
TARGET = ">= 95 % of questions sent to the right node(s)"
PASS_SHARE = 0.95
SELF_NODE: NodeId = "incubator-1"


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    category: Literal["direct", "multi", "implied", "default", "both_incubators", "farm_wide", "typo", "ambiguous"]
    text: str
    machines: list[NodeId]
    farm_wide: bool
    why: str


class RoutingSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    about: str
    questions: list[Question]


@dataclass(frozen=True)
class Outcome:
    question: Question
    routed: Route

    @property
    def correct(self) -> bool:
        return set(self.routed.machines) == set(self.question.machines)

    @property
    def farm_wide_agrees(self) -> bool:
        return self.routed.farm_wide == self.question.farm_wide


def share(outcomes: list[Outcome]) -> float:
    return round(sum(outcome.correct for outcome in outcomes) / len(outcomes), 4) if outcomes else 0.0


def main() -> int:
    started_utc = utc_now()
    started = time.perf_counter()
    routing_set = RoutingSet.model_validate(json.loads(SET.read_text(encoding="utf-8")))
    outcomes = [Outcome(question, route(question.text, self_node=SELF_NODE)) for question in routing_set.questions]
    categories = list(dict.fromkeys(question.category for question in routing_set.questions))
    per_category: dict[str, JsonValue] = {}
    for category in categories:
        inside = [outcome for outcome in outcomes if outcome.question.category == category]
        per_category[category] = {"questions": len(inside), "correct": sum(o.correct for o in inside),
                                  "share": share(inside)}
    misses: list[JsonValue] = [
        {"id": o.question.id, "category": o.question.category, "text": o.question.text,
         "expected": list[JsonValue](o.question.machines), "routed": list[JsonValue](o.routed.machines),
         "routed_farm_wide": o.routed.farm_wide, "why": o.question.why}
        for o in outcomes if not o.correct]
    overall = share(outcomes)
    passed = overall >= PASS_SHARE
    measured: dict[str, JsonValue] = {
        "questions": len(outcomes),
        "correct": sum(outcome.correct for outcome in outcomes),
        "share_correct": overall,
        "per_category": per_category,
        "farm_wide_flag_agrees": round(sum(o.farm_wide_agrees for o in outcomes) / len(outcomes), 4),
        "misses": misses,
        "self_node": SELF_NODE,
        "routing_set_sha256": file_sha256(SET),
        "routing_py_sha256": file_sha256(ROOT / "nodes" / "incubator-1" / "llm" / "routing.py"),
    }
    notes = (
        f"{len(outcomes)} owner questions in {len(categories)} categories, expectations written from meaning and the "
        "ROM documents before route() ran; nothing tuned on them and routing.py unchanged by this measurement. "
        f"Correct = set(route(text, self_node={SELF_NODE!r}).machines) == set(expected machines). Pass rule: overall "
        f"share >= {PASS_SHARE}. farm_wide_flag_agrees is reported only, not part of the pass rule."
    )
    result = EvalResult(id="E5", title="Routing", target=TARGET, passed=passed, measured=measured, command=COMMAND,
                        commit=git_commit(), seeds=[], models={}, started_utc=started_utc,
                        duration_s=round(time.perf_counter() - started, 3), notes=notes)
    path = write_result(result)
    print(f"E5 {'PASSED' if passed else 'FAILED'}  {measured['correct']}/{len(outcomes)} = {overall:.3f}  -> {path}")
    for category in categories:
        cell = per_category[category]
        assert isinstance(cell, dict)
        print(f"  {category:<16} {cell['correct']}/{cell['questions']}  {cell['share']}")
    for o in outcomes:
        if not o.correct:
            print(f"  MISS {o.question.id} [{o.question.category}] {o.question.text!r}: expected "
                  f"{sorted(o.question.machines)}, routed {list(o.routed.machines)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
