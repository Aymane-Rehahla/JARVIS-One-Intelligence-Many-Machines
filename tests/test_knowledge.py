"""tools/knowledge.py and the knowledge documents themselves (owner's point 3)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from knowledge import (APPROVALS_NAME, PROPOSAL_NAME, approve, approve_sources, changed_approvals, copies, demote,
                       documents, effective_status, indexable, load_approvals, main, owner_confirms, parse_document,
                       problems, proposal_sources, rom_dirs)

ROOT = Path(__file__).resolve().parents[1]
NODES = ("incubator-1", "incubator-2", "hvac", "animal-protection")
SHARED = ("owner_rules.md", "safety_actions.md", "farm_layout.md", "contacts.md")
MANUAL = {"incubator-1": "incubator_manual.md", "incubator-2": "incubator_manual.md", "hvac": "hvac_manual.md",
          "animal-protection": "animal_protection_manual.md"}


def rom(node: str, nodes_dir: Path = ROOT / "nodes") -> Path:
    return nodes_dir / node / "memory" / "rom"


def _yes(_: str) -> bool:
    return True


@pytest.mark.parametrize("node", NODES)
def test_every_node_has_its_manual_and_the_shared_documents(node: str) -> None:
    names = {p.name for p in documents(rom(node))}
    assert {MANUAL[node], *SHARED} <= names
    assert parse_document(rom(node) / MANUAL[node]).meta.kind == "machine manual"


@pytest.mark.parametrize("node", NODES)
def test_every_document_has_a_valid_header_for_its_own_node(node: str) -> None:
    for path in documents(rom(node)):
        assert problems(path) == [], problems(path)


def test_the_shared_documents_are_identical_in_all_four_nodes() -> None:
    for name in SHARED:
        assert len({(rom(node) / name).read_bytes() for node in NODES}) == 1, name
    assert (rom("incubator-1") / MANUAL["incubator-1"]).read_bytes() == \
           (rom("incubator-2") / MANUAL["incubator-2"]).read_bytes()


def test_manuals_carry_the_owner_provenance_line() -> None:
    for node in NODES:
        body = parse_document(rom(node) / MANUAL[node]).body
        assert "Derived from the farm simulation model, reviewed by the owner." in body


def test_the_owners_approvals_match_the_documents() -> None:
    """The owner approved the 20 manuals (31 Oct): every approved document is unchanged since and indexable, and
    nothing is approved without the owner's record."""
    for directory in rom_dirs():
        assert changed_approvals(directory) == []
        approved = {p.name for p in documents(directory) if parse_document(p).meta.status == "approved"}
        assert approved == set(load_approvals(directory).approved)
        assert {p.name for p in indexable(directory)} == approved


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A copy of every node's rom/ whose simulation links resolve from the temporary folder, back to drafts with no
    approvals (as before the owner approved), so each test approves for itself."""
    nodes = tmp_path / "nodes"
    for node in NODES:
        shutil.copytree(rom(node), rom(node, nodes))
        (rom(node, nodes) / APPROVALS_NAME).unlink(missing_ok=True)
        for path in documents(rom(node, nodes)):
            text = path.read_text(encoding="utf-8").replace("../../../../_dev/", (ROOT / "_dev").as_posix() + "/")
            text = text.replace('status = "approved"', 'status = "draft"', 1)
            path.write_text(text, encoding="utf-8", newline="\n")
    return nodes


def test_approval_lets_a_document_be_indexed_and_any_change_sends_it_back(sandbox: Path) -> None:
    manual = rom("hvac", sandbox) / "hvac_manual.md"
    assert indexable(manual.parent) == []
    record = approve(manual, by="owner", nodes_dir=sandbox)
    assert parse_document(manual).meta.status == "approved" and record.by == "owner"
    assert indexable(manual.parent) == [manual]
    manual.write_text(manual.read_text(encoding="utf-8").replace("about 850 kPa", "about 851 kPa"), encoding="utf-8")
    assert effective_status(manual) == "draft" and indexable(manual.parent) == []
    assert changed_approvals(manual.parent) == [manual]
    demote(manual)
    assert parse_document(manual).meta.status == "draft"
    assert "hvac_manual.md" not in load_approvals(manual.parent).approved
    assert len(load_approvals(manual.parent).history["hvac_manual.md"]) == 1        # the history is kept


def test_changed_content_needs_a_higher_version_to_be_approved_again(sandbox: Path) -> None:
    manual = rom("hvac", sandbox) / "hvac_manual.md"
    approve(manual, by="owner", nodes_dir=sandbox)
    demote(manual)
    approve(manual, by="owner", nodes_dir=sandbox)                 # same bytes again: allowed
    demote(manual)
    manual.write_text(manual.read_text(encoding="utf-8").replace("about 35 Pa", "about 36 Pa"), encoding="utf-8")
    with pytest.raises(ValueError, match="raise the version"):
        approve(manual, by="owner", nodes_dir=sandbox)
    manual.write_text(manual.read_text(encoding="utf-8").replace("version = 1", "version = 2"), encoding="utf-8")
    assert approve(manual, by="owner", nodes_dir=sandbox).version == 2


def test_a_shared_document_is_approved_in_all_four_nodes_at_once(sandbox: Path) -> None:
    group = copies(rom("hvac", sandbox) / "owner_rules.md", sandbox)
    assert len(group) == 4
    approve(rom("hvac", sandbox) / "owner_rules.md", by="owner", nodes_dir=sandbox)
    assert len({p.read_bytes() for p in group}) == 1                # still identical after approval
    assert all(effective_status(p) == "approved" for p in group)


def test_copies_that_differ_are_not_approved(sandbox: Path) -> None:
    other = rom("incubator-2", sandbox) / "contacts.md"
    other.write_text(other.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")
    with pytest.raises(ValueError, match="differ"):
        approve(rom("hvac", sandbox) / "contacts.md", by="owner", nodes_dir=sandbox)
    assert not (rom("hvac", sandbox) / APPROVALS_NAME).exists()


def test_a_header_saying_approved_without_a_record_is_still_a_draft(sandbox: Path) -> None:
    manual = rom("hvac", sandbox) / "hvac_manual.md"
    manual.write_text(manual.read_text(encoding="utf-8").replace('status = "draft"', 'status = "approved"'),
                      encoding="utf-8")
    assert effective_status(manual) == "draft" and indexable(manual.parent) == []


def test_a_document_in_the_wrong_node_is_a_problem(sandbox: Path) -> None:
    moved = rom("hvac", sandbox) / "incubator_manual.md"
    shutil.copy(rom("incubator-1", sandbox) / "incubator_manual.md", moved)
    assert any("sits in hvac" in issue for issue in problems(moved))


def _guide(directory: Path, link: str) -> Path:
    guide = directory / "ventilation_guide.md"
    guide.write_text('+++\nnode = "hvac"\ntitle = "Ventilation guide"\nkind = "domain guide"\nversion = 1\n'
                     f'status = "draft"\nsources = [ {{ title = "A public source", link = "{link}" }} ]\n+++\n# x\n',
                     encoding="utf-8")
    return guide


def test_domain_guides_may_cite_only_owner_approved_sources(sandbox: Path, tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    shutil.copy(ROOT / "docs" / "knowledge" / PROPOSAL_NAME, knowledge / PROPOSAL_NAME)
    available = proposal_sources(knowledge / PROPOSAL_NAME)
    assert {"I1", "H1", "A1"} <= set(available)
    url = available["H1"][0]
    guide = _guide(rom("hvac", sandbox), url)
    assert any("not approved" in issue for issue in problems(guide, knowledge))      # nothing approved yet
    approve_sources(["H1"], by="owner", knowledge_dir=knowledge)
    assert problems(guide, knowledge) == []
    other = _guide(rom("incubator-1", sandbox), "https://example.org/not-proposed")
    assert any("not approved" in issue for issue in problems(other, knowledge))
    with pytest.raises(ValueError, match="not in the proposal"):
        approve_sources(["Z9"], by="owner", knowledge_dir=knowledge)


def test_the_source_proposal_gives_every_source_an_id_and_a_public_link() -> None:
    sources = proposal_sources(ROOT / "docs" / "knowledge" / PROPOSAL_NAME)
    expected = {f"I{i}" for i in range(1, 7)} | {f"H{i}" for i in range(1, 7)} | {f"A{i}" for i in range(1, 10)}
    assert set(sources) == expected
    assert all(urls and all(url.startswith("https://") for url in urls) for urls in sources.values())


def test_a_local_link_in_a_guide_is_a_problem(tmp_path: Path) -> None:
    guide = _guide(tmp_path, "notes.md")
    assert any("must cite public sources" in issue for issue in problems(guide))
    no_header = tmp_path / "plain.md"
    no_header.write_text("# no header\n", encoding="utf-8")
    assert "no +++ TOML header" in problems(no_header)[0]


def test_approving_needs_a_person_at_a_terminal(capsys: pytest.CaptureFixture[str]) -> None:
    assert not owner_confirms("x", interactive=False)
    assert "refused" in capsys.readouterr().out
    assert owner_confirms("x", interactive=True, read=lambda _: "APPROVE")
    assert not owner_confirms("x", interactive=True, read=lambda _: "yes")
    assert owner_confirms("x", interactive=True, read=lambda _: " approve ")      # the owner typed it in lower case


def test_the_approve_command_refuses_without_the_owner(sandbox: Path) -> None:
    manual = rom("hvac", sandbox) / "hvac_manual.md"
    assert main(["approve", str(manual), "--by", "owner", "--nodes", str(sandbox)],
                confirm=lambda _: owner_confirms("x", interactive=False)) == 2
    assert parse_document(manual).meta.status == "draft"
    assert main(["approve", str(manual), "--by", "owner", "--nodes", str(sandbox)], confirm=_yes) == 0
    assert effective_status(manual) == "approved"


def test_check_fix_demotes_changed_documents(sandbox: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manual = rom("hvac", sandbox) / "hvac_manual.md"
    approve(manual, by="owner", nodes_dir=sandbox)
    assert main(["check", "--nodes", str(sandbox)]) == 0
    manual.write_text(manual.read_text(encoding="utf-8") + "\nan unapproved edit\n", encoding="utf-8")
    assert main(["check", "--nodes", str(sandbox)]) == 1
    assert "CHANGED since approval" in capsys.readouterr().out
    assert main(["check", "--fix", "--nodes", str(sandbox)]) == 0
    assert parse_document(manual).meta.status == "draft"


def test_the_status_command_lists_every_document(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert out.count("incubator_manual.md") == 2 and "hvac_manual.md" in out and out.count(" approved") == 20   # the owner approved all 20 (31 Oct)
    assert APPROVALS_NAME not in out
