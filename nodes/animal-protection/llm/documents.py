"""Incremental approved-ROM document index with FTS5, normalized vectors and reciprocal-rank hybrid retrieval.

In: `<node>/memory/rom/*.md`, its owner approval hashes and an embedder. Out: `<data_dir>/docs_index.sqlite`, bounded
document metadata, passages and ranked search results. The module reads ROM only; it never opens the CMM. Tests:
`tests/llm/test_documents.py` and `tests/llm/retrieval/test_documents_bge.py`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
import tomllib
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np

from contracts import DocumentInfo

ROOT = Path(__file__).resolve().parents[3]
EMBEDDINGS_DIR = ROOT / "models" / "embeddings" / "bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
MAX_CACHE = 256
RRF_K = 60
HEADER_RE = re.compile(r"\A\+\+\+\r?\n(?P<toml>.*?)\r?\n\+\+\+\r?\n", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)
SEVERITY = {"info": 0, "warning": 1, "critical": 2}


class Embedder(Protocol):
    """Embedding backend contract used for passages and prefixed queries."""

    model_id: str
    dim: int

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


@dataclass(frozen=True)
class Passage:
    """One bounded text passage and the metadata retained for citations."""

    file: str
    title: str
    version: int
    sha256: str
    section: str
    ordinal: int
    text: str
    tokens: int


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_header(path: Path) -> tuple[dict[str, Any], str] | None:
    try:
        text = path.read_text(encoding="utf-8")
        match = HEADER_RE.match(text)
        if match is None:
            return None
        return tomllib.loads(match.group("toml")), text[match.end():]
    except (OSError, tomllib.TOMLDecodeError, UnicodeError):
        return None


def indexable(rom: Path) -> list[Path]:
    """Return approved, unchanged documents, matching `tools/knowledge.indexable` without importing tools code."""
    try:
        approval_file = rom / "approvals.json"
        approvals = json.loads(approval_file.read_text(encoding="utf-8")) if approval_file.exists() else {}
        approved = approvals.get("approved", {})
        if not isinstance(approved, Mapping):
            return []
    except (OSError, json.JSONDecodeError):
        return []
    found: list[Path] = []
    for path in sorted(rom.glob("*.md")):
        parsed = _parse_header(path)
        if parsed is None:
            continue
        meta, _ = parsed
        kind = meta.get("kind")
        node = str(meta.get("node", ""))
        wanted = None if node.strip() == "all" else [part.strip() for part in node.split(",") if part.strip()]
        if meta.get("status") != "approved" or type(meta.get("version")) is not int or meta["version"] < 1:
            continue
        if path.parent.name == "rom" and wanted is not None and path.parent.parent.parent.name not in wanted:
            continue
        sources = meta.get("sources")
        if not isinstance(sources, list) or not sources:
            continue
        invalid_sources = False
        for source in sources:
            if not isinstance(source, Mapping) or not isinstance(source.get("link"), str):
                invalid_sources = True
                break
            link = source["link"]
            public = link.startswith(("https://", "http://"))
            if kind == "domain guide" and not public:
                invalid_sources = True
                break
            if not public and not (rom / link).exists():
                invalid_sources = True
                break
            if kind == "domain guide" and public:
                sources_approval_path = ROOT / "docs" / "knowledge" / "sources-approval.json"
                try:
                    source_approval = json.loads(sources_approval_path.read_text(encoding="utf-8"))
                    allowed = {url.strip().rstrip("/").lower() for urls in source_approval.get("approved", {}).values()
                               for url in urls}
                except (OSError, json.JSONDecodeError):
                    allowed = set()
                if link.strip().rstrip("/").lower() not in allowed:
                    invalid_sources = True
                    break
        if invalid_sources:
            continue
        record = approved.get(path.name)
        if isinstance(record, Mapping) and record.get("sha256") == _sha(path):
            found.append(path)
    return found


class _TokenCounter:
    def __init__(self, tokenizer_path: Path | None) -> None:
        self._tokenizer: Any | None = None
        if tokenizer_path is not None and tokenizer_path.is_file():
            try:
                from tokenizers import Tokenizer
                self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
            except (ImportError, Exception):
                self._tokenizer = None

    def count(self, text: str) -> int:
        if self._tokenizer is not None:
            return len(self._tokenizer.encode(text).ids)
        return math.ceil(len(WORD_RE.findall(text)) * 1.3)


def _blocks(text: str) -> list[str]:
    """Split section content into paragraph and contiguous table blocks."""
    lines = text.splitlines()
    result: list[str] = []
    current: list[str] = []
    table_state: bool | None = None
    for line in lines:
        if not line.strip():
            if current:
                result.append("\n".join(current).strip())
                current = []
            table_state = None
            continue
        is_table = line.lstrip().startswith("|")
        if current and table_state != is_table:
            result.append("\n".join(current).strip())
            current = []
        current.append(line.rstrip())
        table_state = is_table
    if current:
        result.append("\n".join(current).strip())
    return [block for block in result if block]


def _word_chunks(text: str, counter: _TokenCounter, maximum: int = 500) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join((*current, word))
        if current and counter.count(candidate) > maximum:
            chunks.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        chunks.append(" ".join(current))
    return chunks


def _table_chunks(block: str, counter: _TokenCounter, maximum: int = 500) -> list[str]:
    rows = block.splitlines()
    if len(rows) < 2:
        return _word_chunks(block, counter, maximum)
    header = rows[:2]
    data_rows = rows[2:]
    if counter.count(block) <= maximum:
        return [block]
    chunks: list[str] = []
    current = list(header)
    for row in data_rows:
        candidate = "\n".join((*current, row))
        if len(current) > len(header) and counter.count(candidate) > maximum:
            chunks.append("\n".join(current))
            current = [*header, row]
        else:
            current.append(row)
    if len(current) > len(header):
        chunks.append("\n".join(current))
    return chunks or [block]


def split_document(path: Path, *, tokenizer_path: Path | None = EMBEDDINGS_DIR / "tokenizer.json") -> list[Passage]:
    """Drop the TOML header and split by heading, paragraph and table-row boundaries."""
    parsed = _parse_header(path)
    if parsed is None:
        return []
    meta, body = parsed
    title = str(meta.get("title", path.stem))
    version = int(meta.get("version", 1))
    digest = _sha(path)
    counter = _TokenCounter(tokenizer_path)
    headings: list[str] = []
    section_blocks: list[tuple[str, list[str]]] = []
    active_content: list[str] = []

    def flush() -> None:
        if active_content:
            section_blocks.append((" > ".join(headings) or title, _blocks("\n".join(active_content))))
            active_content.clear()

    for line in body.splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            headings[:] = headings[:level - 1]
            headings.append(heading.group(2).strip())
        else:
            active_content.append(line)
    flush()

    passages: list[Passage] = []
    ordinal = 0
    for section, blocks in section_blocks:
        pieces: list[str] = []
        for block in blocks:
            block_parts = _table_chunks(block, counter) if block.lstrip().startswith("|") else _word_chunks(block, counter)
            for part in block_parts:
                candidate = "\n\n".join((*pieces, part))
                if pieces and counter.count(candidate) > 500:
                    text = "\n\n".join(pieces)
                    ordinal += 1
                    passages.append(Passage(path.name, title, version, digest, section, ordinal, text,
                                            counter.count(text)))
                    pieces = [part]
                else:
                    pieces.append(part)
        if pieces:
            text = "\n\n".join(pieces)
            ordinal += 1
            passages.append(Passage(path.name, title, version, digest, section, ordinal, text, counter.count(text)))
    return passages


class HashEmbedder:
    """Deterministic signed-hash embeddings for fast unit tests, normalized to unit length."""

    def __init__(self, dim: int = 64) -> None:
        if dim < 1:
            raise ValueError("dim must be positive")
        self.dim = dim
        self.model_id = f"hash:{dim}"

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        values = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in WORD_RE.findall(text.lower()):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] & 1 else -1.0
                values[row, index] += sign
        return _normalise(values)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([QUERY_PREFIX + text])[0]


class BgeEmbedder:
    """CPU ONNX BGE small encoder with CLS pooling and query-only search prefix."""

    def __init__(self, model_path: Path = EMBEDDINGS_DIR / "model.onnx",
                 tokenizer_path: Path = EMBEDDINGS_DIR / "tokenizer.json", *, manifest_path: Path = ROOT / "models" / "MANIFEST.json") -> None:
        self.dim = 384
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        model_relative = model_path.resolve().relative_to((ROOT / "models").resolve()).as_posix()
        model_hash = manifest["files"][model_relative]["sha256"]
        self.model_id = f"bge-small-en-v1.5:{model_hash}"
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self.session: Any | None = None
        self.tokenizer: Any | None = None

    def _load(self) -> None:
        """Load the CPU model only when an approved document or query needs an embedding."""
        if self.session is not None and self.tokenizer is not None:
            return
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if not self.model_path.is_file() or not self.tokenizer_path.is_file():
            raise FileNotFoundError("BGE model.onnx and tokenizer.json are required")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(str(self.model_path), sess_options=options,
                                            providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
        self.tokenizer.enable_truncation(max_length=512)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        self._load()
        assert self.session is not None and self.tokenizer is not None
        outputs: list[np.ndarray] = []
        for offset in range(0, len(texts), 16):
            batch = list(texts[offset:offset + 16])
            encodings = self.tokenizer.encode_batch(batch)
            max_length = max((len(item.ids) for item in encodings), default=1)
            ids = np.zeros((len(batch), max_length), dtype=np.int64)
            mask = np.zeros((len(batch), max_length), dtype=np.int64)
            token_types = np.zeros((len(batch), max_length), dtype=np.int64)
            for row, item in enumerate(encodings):
                length = len(item.ids)
                ids[row, :length] = item.ids
                mask[row, :length] = item.attention_mask
                token_types[row, :length] = item.type_ids
            inputs = {input_meta.name: values for input_meta, values in
                      zip(self.session.get_inputs(), (ids, mask, token_types), strict=False)}
            hidden = cast(np.ndarray, self.session.run(None, inputs)[0])
            outputs.append(hidden[:, 0, :])
        if not outputs:
            return np.empty((0, self.dim), dtype=np.float32)
        return _normalise(np.concatenate(outputs, axis=0))

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([QUERY_PREFIX + text])[0]


def _normalise(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    norms = np.linalg.norm(array, axis=-1, keepdims=True)
    return np.divide(array, np.maximum(norms, 1e-12), dtype=np.float32)


class Documents:
    """Maintain one node-local SQLite FTS/vector index with incremental rebuilds and a bounded search LRU."""

    def __init__(self, rom: Path, index_path: Path, embedder: Embedder, *,
                 tokenizer_path: Path | None = EMBEDDINGS_DIR / "tokenizer.json",
                 logger: logging.Logger | None = None) -> None:
        self.rom = rom
        self.index_path = index_path
        self.embedder = embedder
        self.tokenizer_path = tokenizer_path
        self.logger = logger or logging.getLogger("jarvis.documents")
        self.cache: OrderedDict[tuple[str, int], list[dict[str, Any]]] = OrderedDict()
        self._lock = threading.RLock()
        index_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(index_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._init_db()

    def _init_db(self) -> None:
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS meta(schema_version INTEGER NOT NULL, embedder_id TEXT NOT NULL, "
            "built_utc TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS documents(file TEXT PRIMARY KEY, sha256 TEXT NOT NULL, version INTEGER NOT NULL, "
            "title TEXT NOT NULL, kind TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS passages(id INTEGER PRIMARY KEY, file TEXT NOT NULL REFERENCES documents(file) "
            "ON DELETE CASCADE, section TEXT NOT NULL, ordinal INTEGER NOT NULL, text TEXT NOT NULL, tokens INTEGER NOT NULL, "
            "UNIQUE(file,ordinal));"
            "CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(text, content='passages', content_rowid='id', "
            "tokenize='porter unicode61');"
            "CREATE TRIGGER IF NOT EXISTS passages_ai AFTER INSERT ON passages BEGIN INSERT INTO passages_fts(rowid,text) "
            "VALUES(new.id,new.text); END;"
            "CREATE TRIGGER IF NOT EXISTS passages_ad AFTER DELETE ON passages BEGIN INSERT INTO passages_fts "
            "(passages_fts,rowid,text) VALUES('delete',old.id,old.text); END;"
            "CREATE TABLE IF NOT EXISTS vectors(passage_id INTEGER PRIMARY KEY REFERENCES passages(id) ON DELETE CASCADE, "
            "dim INTEGER NOT NULL, vector BLOB NOT NULL);")
        meta = self._db.execute("SELECT schema_version,embedder_id,built_utc FROM meta LIMIT 1").fetchone()
        if meta is None:
            self._db.execute("INSERT INTO meta VALUES(?,?,?)", (1, self.embedder.model_id, ""))
            self._db.commit()
        elif int(meta["schema_version"]) > 1:
            raise RuntimeError(f"document index schema {meta['schema_version']} is newer than supported schema 1")

    @property
    def generation(self) -> str:
        with self._lock:
            row = self._db.execute("SELECT built_utc FROM meta LIMIT 1").fetchone()
            return str(row[0]) if row else ""

    def _infos(self) -> list[DocumentInfo]:
        return [DocumentInfo(file=row["file"], title=row["title"], kind=row["kind"], version=row["version"],
                             sha256=row["sha256"]) for row in self._db.execute("SELECT * FROM documents ORDER BY file")]

    def infos(self) -> list[DocumentInfo]:
        """Return metadata for currently indexed documents."""
        with self._lock:
            return self._infos()

    def status(self) -> str:
        """Describe whether approved documents are available to search."""
        with self._lock:
            count = self._db.execute("SELECT count(*) FROM documents").fetchone()[0]
            return "ready" if count else "no approved documents"

    def rebuild(self) -> bool:
        """Incrementally index changed approved documents and return whether the index changed."""
        with self._lock:
            return self._rebuild()

    def _rebuild(self) -> bool:
        """Perform the rebuild while the public method owns the SQLite lock."""
        eligible = {path.name: path for path in indexable(self.rom)}
        existing = {row["file"]: dict(row) for row in self._db.execute("SELECT * FROM documents")}
        meta = self._db.execute("SELECT embedder_id FROM meta LIMIT 1").fetchone()
        embedder_changed = bool(meta and meta[0] != self.embedder.model_id)
        changed = embedder_changed or set(eligible) != set(existing)
        with self._db:
            for name in set(existing) - set(eligible):
                self._db.execute("DELETE FROM documents WHERE file=?", (name,))
            for name, path in eligible.items():
                parsed = _parse_header(path)
                if parsed is None:
                    continue
                header, _ = parsed
                digest = _sha(path)
                version = int(header["version"])
                if name in existing and existing[name]["sha256"] == digest and existing[name]["version"] == version:
                    continue
                changed = True
                self._db.execute("DELETE FROM documents WHERE file=?", (name,))
                self._db.execute("INSERT INTO documents(file,sha256,version,title,kind) VALUES(?,?,?,?,?)",
                                 (name, digest, version, str(header["title"]), str(header["kind"])))
                passages = split_document(path, tokenizer_path=self.tokenizer_path)
                self._db.executemany("INSERT INTO passages(file,section,ordinal,text,tokens) VALUES(?,?,?,?,?)",
                                     [(name, p.section, p.ordinal, p.text, p.tokens) for p in passages])
            if embedder_changed:
                self._db.execute("DELETE FROM vectors")
            if changed:
                rows = self._db.execute("SELECT passages.id,passages.text FROM passages LEFT JOIN vectors "
                                        "ON vectors.passage_id=passages.id WHERE vectors.passage_id IS NULL "
                                        "ORDER BY passages.id").fetchall()
                for offset in range(0, len(rows), 16):
                    batch = rows[offset:offset + 16]
                    vectors = self.embedder.embed([row["text"] for row in batch])
                    if vectors.shape != (len(batch), self.embedder.dim):
                        raise ValueError("embedder returned an unexpected vector shape")
                    self._db.executemany("INSERT OR REPLACE INTO vectors(passage_id,dim,vector) VALUES(?,?,?)",
                                         [(row["id"], self.embedder.dim,
                                           np.asarray(vector, dtype="<f4").tobytes()) for row, vector in zip(batch, vectors)])
                self._db.execute("UPDATE meta SET embedder_id=?,built_utc=?",
                                 (self.embedder.model_id, datetime.now(timezone.utc).isoformat(timespec="milliseconds")))
        if changed:
            self.cache.clear()
        return changed

    @staticmethod
    def _fts_query(query: str) -> str:
        return " OR ".join('"' + word.replace('"', '""') + '"' for word in query.split())

    def _keyword(self, query: str) -> list[int]:
        fts = self._fts_query(query[:1000])
        if not fts:
            return []
        try:
            rows = self._db.execute("SELECT rowid,bm25(passages_fts) AS score FROM passages_fts "
                                    "WHERE passages_fts MATCH ? ORDER BY score LIMIT 20", (fts,)).fetchall()
            return [int(row["rowid"]) for row in rows]
        except sqlite3.Error:
            return []

    def _vector(self, query: str) -> list[int]:
        rows = self._db.execute("SELECT passage_id,dim,vector FROM vectors ORDER BY passage_id").fetchall()
        if not rows:
            return []
        query_vector = np.asarray(self.embedder.embed_query(query), dtype=np.float32).reshape(-1)
        vectors = np.stack([np.frombuffer(row["vector"], dtype="<f4", count=int(row["dim"])) for row in rows])
        if vectors.shape[1] != query_vector.size:
            return []
        scores = vectors @ query_vector
        indexes = np.argsort(-scores, kind="stable")[:20]
        return [int(rows[int(index)]["passage_id"]) for index in indexes]

    def search(self, query: str, k: int = 4) -> list[dict[str, Any]]:
        """Return 1–8 hybrid results, merging top-20 keyword/vector rankings with RRF and a bounded LRU."""
        with self._lock:
            return self._search(query, k)

    def _search(self, query: str, k: int) -> list[dict[str, Any]]:
        """Search while the public method owns the SQLite lock."""
        if not 1 <= k <= 8:
            raise ValueError("k must be from 1 to 8")
        normalized = " ".join(query.casefold().split())[:1000]
        if not normalized:
            return []
        key = (normalized, k)
        if key in self.cache:
            self.cache.move_to_end(key)
            return [dict(item) for item in self.cache[key]]
        keyword = self._keyword(normalized)
        vector = self._vector(normalized)
        scores: dict[int, float] = {}
        sources: dict[int, set[str]] = {}
        for label, ranking in (("keyword", keyword), ("vector", vector)):
            for rank, passage_id in enumerate(ranking, start=1):
                scores[passage_id] = scores.get(passage_id, 0.0) + 1.0 / (RRF_K + rank)
                sources.setdefault(passage_id, set()).add(label)
        ordered = sorted(scores, key=lambda passage_id: (-scores[passage_id], passage_id))[:k]
        results: list[dict[str, Any]] = []
        for rank, passage_id in enumerate(ordered, start=1):
            row = self._db.execute("SELECT passages.file,documents.title,documents.version,documents.sha256,"
                                   "passages.section,passages.text FROM passages JOIN documents USING(file) "
                                   "WHERE passages.id=?", (passage_id,)).fetchone()
            if row is None:
                continue
            found = sources[passage_id]
            how = "both" if len(found) == 2 else next(iter(found))
            results.append({"file": row["file"], "title": row["title"], "version": row["version"],
                            "sha256": row["sha256"], "section": row["section"], "text": row["text"][:1200],
                            "score": scores[passage_id], "rank": rank, "how": how})
        self.cache[key] = results
        self.cache.move_to_end(key)
        while len(self.cache) > MAX_CACHE:
            self.cache.popitem(last=False)
        return [dict(item) for item in results]

    def close(self) -> None:
        """Close the SQLite index."""
        with self._lock:
            self._db.close()


def escaped_query(query: str) -> str:
    """Expose the same safe FTS term quoting used by the conversation store."""
    return " OR ".join('"' + word.replace('"', '""') + '"' for word in query.split())
