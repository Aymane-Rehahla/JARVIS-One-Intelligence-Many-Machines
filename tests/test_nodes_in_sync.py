"""The four nodes hold the same shared code (plan section 1.3: one fix reaches all four), kept so by tools/sync_nodes.py."""
from __future__ import annotations

from pathlib import Path

import sync_nodes


def test_the_four_nodes_hold_the_same_shared_code() -> None:
    assert sync_nodes.main(["--check"]) == 0, "run: python tools/sync_nodes.py"


def test_own_files_and_caches_are_not_shared() -> None:
    for own in ("node.toml", "README.md", "memory/rom/guide.md", "data/incidents.sqlite3", "comms/__pycache__/x.pyc"):
        assert not sync_nodes.is_shared(Path(own)), own
    for shared in ("contracts.py", "comms/README.md", "llm/verify.py", "memory/README.md"):
        assert sync_nodes.is_shared(Path(shared)), shared


def test_sync_copies_changes_and_removes_extras(tmp_path: Path) -> None:
    source, target = tmp_path / "incubator-1", tmp_path / "hvac"
    for folder, files in ((source, {"node.py": "new", "comms/link.py": "x", "node.toml": "id = 1"}),
                          (target, {"node.py": "old", "stale.py": "y", "node.toml": "id = 2"})):
        for rel, text in files.items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(text, encoding="utf-8")
    assert sync_nodes.differences(source, target) == [("comms/link.py", "missing"), ("node.py", "different"),
                                                      ("stale.py", "extra")]
    sync_nodes.sync(source, target)
    assert sync_nodes.differences(source, target) == []
    assert (target / "node.toml").read_text(encoding="utf-8") == "id = 2"           # its own identity is kept
    assert not (target / "stale.py").exists()
