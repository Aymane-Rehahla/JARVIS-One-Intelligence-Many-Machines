"""The node's LLM layer remains outside private memory and every tool remains read-only and permissioned."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NODE = ROOT / "nodes" / "incubator-1"
FORBIDDEN_IMPORT_ROOTS = {"memory", "safety", "reasoning", "matrix", "farm_poc_snapshot"}
FORBIDDEN_STRINGS = {"cmm.sqlite", "working.py", "submit_command"}
PERMISSIONS = {"read", "ask", "owner-confirmed"}


def test_static_imports_publish_paths_and_permissions_stay_inside_boundaries() -> None:
    paths = sorted((NODE / "llm").glob("*.py"))
    mcp_path = NODE / "comms" / "mcp.py"
    if mcp_path.is_file():
        paths.append(mcp_path)
    tool_definitions: list[tuple[str, str]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                modules = []
            assert not any(module.split(".", 1)[0] in FORBIDDEN_IMPORT_ROOTS for module in modules), path
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not any(forbidden in node.value for forbidden in FORBIDDEN_STRINGS), path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "publish":
                if node.args:
                    with_value = node.args[0]
                    if isinstance(with_value, ast.Constant) and isinstance(with_value.value, str):
                        assert not with_value.value.startswith("farm."), path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Tool" and len(node.args) >= 2:
                name = ast.literal_eval(node.args[0])
                permission = ast.literal_eval(node.args[1])
                tool_definitions.append((name, permission))
    assert len(tool_definitions) == 9
    assert all(permission in PERMISSIONS for _, permission in tool_definitions)
    assert [name for name, permission in tool_definitions if permission == "owner-confirmed"] == ["acknowledge_alert"]
