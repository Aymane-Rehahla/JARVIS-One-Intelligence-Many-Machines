"""E8 code checks: the node's LLM layer (llm/*.py and comms/mcp.py) stays outside the CMM, the reasoning, the safety
gate and the farm, and every tool keeps one of the plan's three permission levels.

`violations()` scans one source file. Each rule is also proven against a bad sample (review M6: a rule that is never
shown to catch anything proves nothing), including the indirect routes: importlib.import_module, __import__, f-string
subjects and farm_subject().
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
NODE = ROOT / "nodes" / "incubator-1"
FORBIDDEN_IMPORT_ROOTS = {"memory", "safety", "reasoning", "matrix", "farm_poc_snapshot"}
FORBIDDEN_STRINGS = {"cmm.sqlite", "working.py", "submit_command"}
PERMISSIONS = {"read", "ask", "owner-confirmed"}


def _root(module: str) -> str:
    return module.split(".", 1)[0]


def _text_start(node: ast.expr) -> str | None:
    """The leading constant text of a string or f-string expression, if any."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        value = node.values[0].value
        return value if isinstance(value, str) else None
    return None


def violations(source: str) -> list[str]:
    """Every boundary rule the source breaks, as short reasons."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [f"imports {alias.name}" for alias in node.names if _root(alias.name) in FORBIDDEN_IMPORT_ROOTS]
        elif isinstance(node, ast.ImportFrom) and node.module and _root(node.module) in FORBIDDEN_IMPORT_ROOTS:
            found.append(f"imports from {node.module}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found += [f"mentions {text}" for text in FORBIDDEN_STRINGS if text in node.value]
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            first = _text_start(node.args[0]) if node.args else None
            if name in ("import_module", "__import__") and first is not None and _root(first) in FORBIDDEN_IMPORT_ROOTS:
                found.append(f"dynamically imports {first}")
            if name == "publish" and first is not None and first.startswith("farm."):
                found.append(f"publishes to {first}")
            if name == "farm_subject":
                found.append("builds a farm.* subject")
            if name == "submit_command":
                found.append("calls submit_command")
    return found


def tool_permissions(source: str) -> list[tuple[str, str]]:
    """(name, permission) of every Tool(...) defined in the source."""
    return [(ast.literal_eval(node.args[0]), ast.literal_eval(node.args[1])) for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Tool"
            and len(node.args) >= 2]


def llm_layer_files() -> list[Path]:
    return [*sorted((NODE / "llm").glob("*.py")), NODE / "comms" / "mcp.py"]


def test_the_llm_layer_stays_inside_its_boundaries() -> None:
    files = llm_layer_files()
    assert len(files) >= 9 and all(path.is_file() for path in files)
    problems = {path.name: found for path in files if (found := violations(path.read_text(encoding="utf-8")))}
    assert problems == {}


def test_every_tool_has_a_plan_permission_and_only_acknowledge_is_owner_confirmed() -> None:
    tools = tool_permissions((NODE / "llm" / "tools.py").read_text(encoding="utf-8"))
    assert len(tools) == 9 and {permission for _, permission in tools} <= PERMISSIONS
    assert [name for name, permission in tools if permission == "owner-confirmed"] == ["acknowledge_alert"]


@pytest.mark.parametrize("bad", [
    "import memory.cmm",
    "from reasoning.rule_brain import RuleBrain",
    "from safety import gate",
    "import importlib\nimportlib.import_module('memory.working')",
    "__import__('matrix.engine')",
    "path = 'data/cmm.sqlite'",
    "engine.submit_command('hvac', 'stop')",
    "await link.publish('farm.hvac.status', message)",
    "await link.publish(f'farm.{node}.events', message)",
    "subject = farm_subject('hvac', 'events')",
])
def test_each_rule_catches_its_bad_sample(bad: str) -> None:
    assert violations(bad), bad


def test_ordinary_code_passes() -> None:
    assert violations("import json\nfrom llm.audit import Auditor\nawait link.publish('jarvis.hvac.audit', r)") == []
