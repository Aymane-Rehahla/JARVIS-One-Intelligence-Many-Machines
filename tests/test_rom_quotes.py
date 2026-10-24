"""The shared owner rules and safety list quote the reasoning's code word for word.

The source is the read-only farm snapshot (`_dev/farm_poc_snapshot/nodes/`), read by parsing, never imported. In 6D the
reasoning's own copy replaces it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "_dev" / "farm_poc_snapshot" / "nodes"
NODES = ("incubator-1", "incubator-2", "hvac", "animal-protection")
LABELS = {"incubator": "Incubator", "hvac": "HVAC", "animal_protection": "Animal Protection"}
SAFETY_PREFIX = "Approved protective actions (DANGER only, own machine only): "


def _assigned_literal(path: Path, name: str) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


def _blockquote(text: str, heading: str) -> list[str]:
    """The `> ` lines of the first blockquote under `heading`, joined per paragraph."""
    section = text.split(heading, 1)[1]
    paragraphs, current = [], []
    for line in section.splitlines():
        if line.startswith(">"):
            content = line[1:].strip()
            if content:
                current.append(content)
            elif current:
                paragraphs.append(" ".join(current))
                current = []
        elif current or paragraphs:
            break
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


@pytest.mark.parametrize("node", NODES)
def test_owner_rules_quote_the_code_word_for_word(node: str) -> None:
    rules = _assigned_literal(SNAPSHOT / "cmm.py", "OWNER_RULES")
    text = (ROOT / "nodes" / node / "memory" / "rom" / "owner_rules.md").read_text(encoding="utf-8")
    assert _blockquote(text, "## The rules") == [rules]


@pytest.mark.parametrize("node", NODES)
def test_safety_list_quotes_the_code_word_for_word(node: str) -> None:
    actions = _assigned_literal(SNAPSHOT / "contracts.py", "DANGER_ACTIONS")
    assert isinstance(actions, dict)
    assert f'"{SAFETY_PREFIX}"' in (SNAPSHOT / "cmm.py").read_text(encoding="utf-8"), "the CMM's safety text changed"
    expected = [f"{LABELS[machine]}: {SAFETY_PREFIX}" + ", ".join(a for a in machine_actions if a != "none")
                for machine, machine_actions in actions.items()]
    text = (ROOT / "nodes" / node / "memory" / "rom" / "safety_actions.md").read_text(encoding="utf-8")
    assert _blockquote(text, "## The approved actions, as the reasoning holds them") == expected


def test_every_executable_action_has_a_gate_command_except_close_gates() -> None:
    """The safety list says close_gates is never sent; check the gate still has no command for it."""
    commands = _assigned_literal(SNAPSHOT / "gate.py", "ACTION_COMMANDS")
    actions = _assigned_literal(SNAPSHOT / "contracts.py", "DANGER_ACTIONS")
    assert isinstance(commands, dict) and isinstance(actions, dict)
    listed = {a for machine_actions in actions.values() for a in machine_actions if a != "none"}
    assert listed - set(commands) == {"close_gates"}
    assert re.search(r"\| close_gates \| none \|", (ROOT / "nodes/hvac/memory/rom/safety_actions.md").read_text("utf-8"))
