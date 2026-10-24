"""Each node's knowledge (`nodes/<node>/memory/rom/*.md`): headers, owner approval and what may be indexed (owner's point 3).

Every document starts with a TOML header between `+++` lines: node, title, kind, version, status (draft | approved)
and its sources with links. A document may be indexed for search only when the **owner** approved it: its header says
`status = "approved"` and its SHA-256 matches the one recorded at approval in `rom/approvals.json`. Any later change
sends it back to draft until the owner approves it again, and changed content needs a higher version number.

Domain guides may cite only the public sources the owner approved (`docs/knowledge/sources-approval.json`).

Approving is the owner's act: the `approve` and `approve-sources` commands refuse to run without an interactive terminal
and a typed confirmation, so no script or agent can approve on the owner's behalf.

Usage (from the project folder):
    python tools/knowledge.py status                         every document, its version and its effective status
    python tools/knowledge.py check [--fix]                  exit 1 on a header problem or an approved document that
                                                             changed; --fix sends changed documents back to draft
    python tools/knowledge.py approve <file> --by owner      the OWNER approves a document (all identical shared copies)
    python tools/knowledge.py approve-sources --ids I1,H2,A3 --by owner
                                                             the OWNER approves sources from the proposal by their IDs
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ROOT = Path(__file__).resolve().parents[1]
NODES_DIR = ROOT / "nodes"
KNOWLEDGE_DIR = ROOT / "docs" / "knowledge"
PROPOSAL_NAME = "sources-proposal.md"
SOURCES_APPROVAL_NAME = "sources-approval.json"
APPROVALS_NAME = "approvals.json"
HEADER = re.compile(r"\A\+\+\+\r?\n(?P<toml>.*?)\r?\n\+\+\+\r?\n", re.DOTALL)
STATUS_LINE = re.compile(r'^status = "(draft|approved)"$', re.MULTILINE)
PROPOSAL_ROW = re.compile(r"^\|\s*(?P<id>[A-Z]\d+)\s*\|(?P<rest>.*)$", re.MULTILINE)
URL = re.compile(r"https?://[^\s|)`]+")


class Source(BaseModel):
    """One source of a document, with a link (a URL for public sources, a relative path for the simulation)."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3)
    link: str = Field(min_length=1)


class DocMeta(BaseModel):
    """The TOML header of a knowledge document."""

    model_config = ConfigDict(extra="forbid")
    node: str = Field(min_length=1)
    title: str = Field(min_length=3)
    kind: Literal["machine manual", "domain guide", "shared"]
    version: int = Field(ge=1)
    status: Literal["draft", "approved"]
    sources: list[Source] = Field(min_length=1)       # per-kind link rules are checked in `problems()`

    def nodes(self) -> list[str] | None:
        """The nodes this document is for; None means all of them."""
        return None if self.node.strip() == "all" else [n.strip() for n in self.node.split(",") if n.strip()]


class Approval(BaseModel):
    """What the owner approved: one exact version of one file."""

    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: int = Field(ge=1)
    approved_utc: str
    by: str = Field(min_length=1)


class Approvals(BaseModel):
    """`rom/approvals.json`: what is approved now, and every approval ever given (for the version rule)."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    approved: dict[str, Approval] = Field(default_factory=dict[str, Approval])
    history: dict[str, list[Approval]] = Field(default_factory=dict[str, list[Approval]])


class SourcesApproval(BaseModel):
    """`docs/knowledge/sources-approval.json`: the sources the owner approved for domain guides."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    proposal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: dict[str, list[str]]                     # source ID -> its URLs, as written in the proposal
    approved_utc: str
    by: str = Field(min_length=1)


@dataclass(frozen=True)
class Document:
    """A parsed knowledge document."""

    path: Path
    meta: DocMeta
    body: str


def parse_document(path: Path) -> Document:
    """Read and validate one document. Raises ValueError (no header, bad TOML) or pydantic.ValidationError."""
    text = path.read_text(encoding="utf-8")
    match = HEADER.match(text)
    if not match:
        raise ValueError(f"{path.name}: no +++ TOML header")
    try:
        raw = tomllib.loads(match.group("toml"))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path.name}: header is not valid TOML: {error}") from error
    return Document(path, DocMeta.model_validate(raw), text[match.end():])


def documents(rom: Path) -> list[Path]:
    """The knowledge documents of one node (Markdown files only; approvals.json is not a document)."""
    return sorted(p for p in rom.glob("*.md") if p.is_file())


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def node_of(rom: Path) -> str:
    """The node a rom/ folder belongs to (nodes/<node>/memory/rom)."""
    return rom.parent.parent.name


def load_approvals(rom: Path) -> Approvals:
    path = rom / APPROVALS_NAME
    if not path.exists():
        return Approvals()
    return Approvals.model_validate_json(path.read_text(encoding="utf-8"))


def save_approvals(rom: Path, approvals: Approvals) -> None:
    text = json.dumps(approvals.model_dump(), indent=2, sort_keys=True) + "\n"
    (rom / APPROVALS_NAME).write_text(text, encoding="utf-8", newline="\n")


def effective_status(path: Path, approvals: Approvals | None = None) -> Literal["draft", "approved"]:
    """`approved` only when the header says so and the file is byte-for-byte the version the owner approved."""
    approvals = approvals if approvals is not None else load_approvals(path.parent)
    record = approvals.approved.get(path.name)
    if record is None or parse_document(path).meta.status != "approved":
        return "draft"
    return "approved" if sha256_file(path) == record.sha256 else "draft"


def indexable(rom: Path) -> list[Path]:
    """The documents search may index: only the owner-approved, unchanged ones (used by llm/documents.py, step 6B)."""
    approvals = load_approvals(rom)
    return [path for path in documents(rom) if not problems(path) and effective_status(path, approvals) == "approved"]


def _set_status(path: Path, status: Literal["draft", "approved"]) -> None:
    text = path.read_text(encoding="utf-8")
    match = HEADER.match(text)
    if not match or not STATUS_LINE.search(match.group("toml")):
        raise ValueError(f"{path.name}: header has no status line")
    header = STATUS_LINE.sub(f'status = "{status}"', match.group("toml"), count=1)
    path.write_text(f"+++\n{header}\n+++\n{text[match.end():]}", encoding="utf-8", newline="\n")


def copies(path: Path, nodes_dir: Path | None = None) -> list[Path]:
    """Every copy of a document: itself, plus the same file in the other nodes it is meant for (shared documents)."""
    nodes_dir = nodes_dir or path.parents[3]
    meta = parse_document(path).meta
    wanted = meta.nodes()
    found = {path.resolve()}
    for rom in rom_dirs(nodes_dir):
        candidate = rom / path.name
        if candidate.is_file() and (wanted is None or node_of(rom) in wanted):
            found.add(candidate.resolve())
    return sorted(found)


def approve(path: Path, by: str, nodes_dir: Path | None = None) -> Approval:
    """The owner approves a document; identical copies in other nodes are approved together.

    Raises ValueError when the copies differ, when the header has problems, or when content changed since an earlier
    approval without a higher version number.
    """
    group = copies(path, nodes_dir)
    if len({p.read_bytes() for p in group}) != 1:
        raise ValueError(f"{path.name}: the copies in {', '.join(node_of(p.parent) for p in group)} differ")
    issues = [issue for p in group for issue in problems(p)]
    if issues:
        raise ValueError("; ".join(issues))
    meta = parse_document(path).meta
    for p in group:
        last = (load_approvals(p.parent).history.get(p.name) or [None])[-1]
        draft_bytes = p.read_bytes()
        if last is not None and meta.version <= last.version and \
                hashlib.sha256(_with_status(draft_bytes, "approved")).hexdigest() != last.sha256:
            raise ValueError(f"{p.name}: the content changed since version {last.version} was approved; "
                             f"raise the version above {last.version} first")
    record: Approval | None = None
    for p in group:
        _set_status(p, "approved")
        record = Approval(sha256=sha256_file(p), version=meta.version,
                          approved_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"), by=by)
        approvals = load_approvals(p.parent)
        approvals.approved[p.name] = record
        approvals.history.setdefault(p.name, []).append(record)
        save_approvals(p.parent, approvals)
    assert record is not None
    return record


def _with_status(data: bytes, status: Literal["draft", "approved"]) -> bytes:
    text = data.decode("utf-8")
    match = HEADER.match(text)
    if not match:
        return data
    header = STATUS_LINE.sub(f'status = "{status}"', match.group("toml"), count=1)
    return f"+++\n{header}\n+++\n{text[match.end():]}".encode("utf-8")


def changed_approvals(rom: Path) -> list[Path]:
    """Documents whose header says approved but that no longer match what the owner approved."""
    approvals = load_approvals(rom)
    changed: list[Path] = []
    for path in documents(rom):
        if problems(path):
            continue                                   # an invalid header is reported by `problems`, not here
        if parse_document(path).meta.status == "approved" and effective_status(path, approvals) != "approved":
            changed.append(path)
    return changed


def demote(path: Path) -> None:
    """Send a changed document back to draft and forget its current approval (the history is kept)."""
    _set_status(path, "draft")
    approvals = load_approvals(path.parent)
    approvals.approved.pop(path.name, None)
    save_approvals(path.parent, approvals)


# ---------------------------------------------------------------------------------------------- sources
def proposal_sources(proposal: Path) -> dict[str, list[str]]:
    """The proposal's sources: ID -> the URLs in its table row."""
    text = proposal.read_text(encoding="utf-8")
    return {m.group("id"): URL.findall(m.group("rest")) for m in PROPOSAL_ROW.finditer(text) if URL.search(m.group("rest"))}


def load_sources_approval(knowledge_dir: Path | None = None) -> SourcesApproval | None:
    path = (knowledge_dir or KNOWLEDGE_DIR) / SOURCES_APPROVAL_NAME
    if not path.exists():
        return None
    return SourcesApproval.model_validate_json(path.read_text(encoding="utf-8"))


def approve_sources(ids: list[str], by: str, knowledge_dir: Path | None = None) -> SourcesApproval:
    """The owner approves sources from the proposal by their IDs (recorded with the proposal's hash)."""
    knowledge_dir = knowledge_dir or KNOWLEDGE_DIR
    proposal = knowledge_dir / PROPOSAL_NAME
    available = proposal_sources(proposal)
    unknown = [i for i in ids if i not in available]
    if unknown:
        raise ValueError(f"not in the proposal: {', '.join(unknown)}")
    record = SourcesApproval(proposal_sha256=sha256_file(proposal), approved={i: available[i] for i in ids},
                             approved_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"), by=by)
    text = json.dumps(record.model_dump(), indent=2, sort_keys=True) + "\n"
    (knowledge_dir / SOURCES_APPROVAL_NAME).write_text(text, encoding="utf-8", newline="\n")
    return record


def _normal_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def problems(path: Path, knowledge_dir: Path | None = None) -> list[str]:
    """Header problems of one document: invalid header, wrong node, missing local sources, unapproved public sources."""
    try:
        document = parse_document(path)
    except (ValueError, ValidationError) as error:
        return [str(error).splitlines()[0]]
    found: list[str] = []
    meta = document.meta
    folder_node = node_of(path.parent)
    wanted = meta.nodes()
    if path.parent.name == "rom" and wanted is not None and folder_node not in wanted:
        found.append(f"{path.name}: header says node {meta.node!r} but it sits in {folder_node}")
    approved_urls: set[str] | None = None
    if meta.kind == "domain guide":
        approval = load_sources_approval(knowledge_dir)
        approved_urls = {_normal_url(u) for urls in approval.approved.values() for u in urls} if approval else set()
    for source in meta.sources:
        public = source.link.startswith(("https://", "http://"))
        if meta.kind == "domain guide" and not public:
            found.append(f"{path.name}: a domain guide must cite public sources (got {source.link})")
        if approved_urls is not None and public and _normal_url(source.link) not in approved_urls:
            found.append(f"{path.name}: source not approved by the owner: {source.link}")
        if not public and not (path.parent / source.link).exists():
            found.append(f"{path.name}: source link does not exist: {source.link}")
    return found


def rom_dirs(nodes_dir: Path | None = None) -> list[Path]:
    nodes_dir = nodes_dir or NODES_DIR
    return sorted(p / "memory" / "rom" for p in nodes_dir.iterdir() if (p / "memory" / "rom").is_dir())


# ---------------------------------------------------------------------------------------------- command line
def owner_confirms(what: str, *, read: Callable[[str], str] = input, interactive: bool | None = None) -> bool:
    """Only a person at a terminal can approve: refuse without one, then ask them to type the confirmation."""
    if not (sys.stdin.isatty() if interactive is None else interactive):
        print("refused: approving needs the owner at an interactive terminal (no script or agent may approve)")
        return False
    return read(f"Type APPROVE to approve {what}: ").strip() == "APPROVE"


def main(argv: list[str] | None = None, *, confirm: Callable[[str], bool] = owner_confirms) -> int:
    """Command-line entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--nodes", type=Path, default=NODES_DIR, help="the nodes folder (default: the project's)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", parents=[common])
    approve_cmd = sub.add_parser("approve", parents=[common])
    approve_cmd.add_argument("file", type=Path)
    approve_cmd.add_argument("--by", required=True, help="who approves (the owner)")
    sources_cmd = sub.add_parser("approve-sources", parents=[common])
    sources_cmd.add_argument("--ids", required=True, help="comma-separated source IDs from the proposal, e.g. I1,H2")
    sources_cmd.add_argument("--by", required=True, help="who approves (the owner)")
    check_cmd = sub.add_parser("check", parents=[common])
    check_cmd.add_argument("--fix", action="store_true", help="demote changed documents to draft")
    args = parser.parse_args(argv)
    if args.command == "approve":
        group = copies(args.file, args.nodes)
        if not confirm(f"{args.file.name} ({len(group)} cop{'y' if len(group) == 1 else 'ies'})"):
            return 2
        try:
            record = approve(args.file, args.by, args.nodes)
        except ValueError as error:
            print(f"not approved: {error}")
            return 2
        print(f"approved {args.file.name} version {record.version} by {record.by} in {len(group)} node(s)")
        return 0
    if args.command == "approve-sources":
        ids = [i.strip() for i in args.ids.split(",") if i.strip()]
        if not confirm(f"sources {', '.join(ids)}"):
            return 2
        try:
            record = approve_sources(ids, args.by)
        except ValueError as error:
            print(f"not approved: {error}")
            return 2
        print(f"approved {len(record.approved)} sources by {record.by}")
        return 0
    header_problems = unfixed_changes = 0
    for rom in rom_dirs(args.nodes):
        node = node_of(rom)
        valid = []
        for path in documents(rom):
            issues = problems(path)
            header_problems += len(issues)
            for issue in issues:
                print(f"PROBLEM {node}/{issue}")
            if not issues:
                valid.append(path)
        if args.command == "status":
            approvals = load_approvals(rom)
            for path in valid:
                version = parse_document(path).meta.version
                print(f"{node:18s} {path.name:32s} v{version}  {effective_status(path, approvals)}")
            continue
        for path in changed_approvals(rom):
            print(f"CHANGED since approval: {node}/{path.name}")
            if args.fix:
                demote(path)
                print("  -> back to draft")
            else:
                unfixed_changes += 1
    return 1 if header_problems or unfixed_changes else 0


if __name__ == "__main__":
    sys.exit(main())
