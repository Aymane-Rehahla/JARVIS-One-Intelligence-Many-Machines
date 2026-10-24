"""Explicit, bounded, provenance-preserving cognitive memory manager."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from copy import deepcopy
from typing import Any, Iterable


FORBIDDEN_KEYS = frozenset({"hidden_truth", "fault_label", "scenario_id", "scenario_config",
                            "seed", "reward", "evaluation", "evaluator", "physical_state"})


def _reject_private(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"private data cannot enter CMM: {sorted(forbidden)}")
        for item in value.values(): _reject_private(item)
    elif isinstance(value, (list, tuple)):
        for item in value: _reject_private(item)


@dataclass(frozen=True)
class MemoryEntryV1:
    entry_id: str
    category: str
    kind: str
    provenance: str
    simulation_tick: int
    payload: dict[str, Any]
    tags: tuple[str, ...]
    trust: str

    def as_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class MemoryRetrievalV1:
    schema_version: int
    query_tags: tuple[str, ...]
    entries: tuple[MemoryEntryV1, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "query_tags": self.query_tags,
                "entries": [entry.as_dict() for entry in self.entries]}


class CognitiveMemoryManager:
    """CMM v1 uses deterministic records, not neural or unbounded memory."""
    SCHEMA_VERSION = 1
    CATEGORIES = frozenset({"working", "episodic", "semantic", "procedural", "mandatory_rule"})
    KINDS = frozenset({"observed_fact", "hypothesis", "confirmed_outcome", "procedure", "rule", "fact"})

    def __init__(self, *, working_limit: int = 32, episodic_limit: int = 128,
                 enable_writes: bool = True):
        if working_limit < 1 or episodic_limit < 1: raise ValueError("memory limits must be positive")
        self.working_limit, self.episodic_limit = working_limit, episodic_limit
        self.enable_writes = bool(enable_writes)
        self.disabled_write_attempts = 0
        self._working: deque[MemoryEntryV1] = deque(maxlen=working_limit)
        self._episodic: deque[MemoryEntryV1] = deque(maxlen=episodic_limit)
        self._semantic: dict[str, MemoryEntryV1] = {}
        self._procedural: dict[str, MemoryEntryV1] = {}
        self._rules: dict[str, MemoryEntryV1] = {}
        self._episode_id: str | None = None
        self._counter = 0
        self._pending_verification = False

    def begin_episode(self, episode_id: str | None = None) -> None:
        self._working.clear(); self._pending_verification = False; self._episode_id = episode_id

    def _entry(self, category: str, kind: str, payload: dict[str, Any], *, provenance: str,
               simulation_tick: int, tags: Iterable[str] = (), trust: str = "operational") -> MemoryEntryV1:
        if category not in self.CATEGORIES or kind not in self.KINDS: raise ValueError("invalid CMM category or kind")
        if simulation_tick < 0: raise ValueError("simulation tick must be nonnegative")
        _reject_private(payload)
        self._counter += 1
        return MemoryEntryV1(f"cmm-{self._counter:06d}", category, kind, provenance, int(simulation_tick),
                             deepcopy(payload), tuple(sorted(set(tags))), trust)

    def record_observation(self, payload: dict[str, Any], *, simulation_tick: int, tags: Iterable[str] = ()) -> MemoryEntryV1:
        self._require_writes()
        entry = self._entry("working", "observed_fact", payload, provenance="operational_observation",
                            simulation_tick=simulation_tick, tags=tags)
        self._working.append(entry); return entry

    def record_hypothesis(self, payload: dict[str, Any], *, simulation_tick: int, tags: Iterable[str] = ()) -> MemoryEntryV1:
        self._require_writes()
        entry = self._entry("working", "hypothesis", payload, provenance="agent_candidate",
                            simulation_tick=simulation_tick, tags=tags, trust="unconfirmed")
        self._working.append(entry); return entry

    def record_verification(self, payload: dict[str, Any], *, simulation_tick: int, tags: Iterable[str] = ()) -> MemoryEntryV1:
        self._require_writes()
        entry = self._entry("episodic", "confirmed_outcome", payload, provenance="operational_outcome",
                            simulation_tick=simulation_tick, tags=tags, trust="confirmed")
        self._episodic.append(entry); self._pending_verification = False; return entry

    def close_episode(self, *, simulation_tick: int, summary: dict[str, Any]) -> MemoryEntryV1:
        return self.record_verification(summary, simulation_tick=simulation_tick, tags=("episode_closed",))

    def _require_writes(self) -> None:
        if not self.enable_writes:
            self.disabled_write_attempts += 1
            raise PermissionError("CMM writes are disabled for this runtime")

    def add_semantic_fact(self, key: str, payload: dict[str, Any], *, authority: str, simulation_tick: int) -> MemoryEntryV1:
        if authority != "reviewed": raise PermissionError("semantic facts require reviewed authority")
        entry = self._entry("semantic", "fact", payload, provenance="reviewed_knowledge", simulation_tick=simulation_tick, tags=(key,), trust="reviewed")
        self._semantic[key] = entry; return entry

    def add_procedure(self, key: str, payload: dict[str, Any], *, authority: str, simulation_tick: int) -> MemoryEntryV1:
        if authority != "reviewed": raise PermissionError("procedures require reviewed authority")
        entry = self._entry("procedural", "procedure", payload, provenance="reviewed_procedure", simulation_tick=simulation_tick, tags=(key,), trust="reviewed")
        self._procedural[key] = entry; return entry

    def set_mandatory_rule(self, key: str, payload: dict[str, Any], *, authority: str, simulation_tick: int) -> MemoryEntryV1:
        if authority != "governance": raise PermissionError("mandatory rules require governance authority")
        entry = self._entry("mandatory_rule", "rule", payload, provenance="governance", simulation_tick=simulation_tick, tags=(key,), trust="mandatory")
        self._rules[key] = entry; return entry

    def request_verification(self) -> None: self._pending_verification = True

    def retrieve(self, *, query_tags: Iterable[str] = (), categories: Iterable[str] = ("working", "episodic", "semantic", "procedural", "mandatory_rule"), limit: int = 8) -> MemoryRetrievalV1:
        if limit < 0: raise ValueError("limit must be nonnegative")
        requested, tags = set(categories), tuple(sorted(set(query_tags)))
        if not requested <= self.CATEGORIES: raise ValueError("unknown retrieval category")
        entries = list(self._working) + list(self._episodic) + list(self._semantic.values()) + list(self._procedural.values()) + list(self._rules.values())
        scored = [(len(set(tags).intersection(entry.tags)), entry.simulation_tick, entry.entry_id, entry) for entry in entries if entry.category in requested]
        # deterministic: tag relevance, recency, then stable identifier
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return MemoryRetrievalV1(self.SCHEMA_VERSION, tags, tuple(item[-1] for item in scored[:limit]))

    def context_vector(self) -> tuple[float, ...]:
        """Eight bounded summaries, never raw private memory payloads."""
        return (len(self._working) / self.working_limit, len(self._episodic) / self.episodic_limit,
                min(len(self._semantic), 16) / 16, min(len(self._procedural), 16) / 16,
                min(len(self._rules), 16) / 16, float(self._pending_verification),
                sum(entry.kind == "hypothesis" for entry in self._working) / self.working_limit,
                sum(entry.kind == "confirmed_outcome" for entry in self._episodic) / self.episodic_limit)

    def diagnostics(self) -> dict[str, Any]:
        return {"schema_version": self.SCHEMA_VERSION, "episode_active": self._episode_id is not None,
                "working_count": len(self._working), "episodic_count": len(self._episodic),
                "semantic_keys": sorted(self._semantic), "procedural_keys": sorted(self._procedural),
                "mandatory_rule_keys": sorted(self._rules), "pending_verification": self._pending_verification,
                "working_entries": [entry.as_dict() for entry in self._working]}

    def state_dict(self) -> dict[str, Any]:
        """Serializable governed memory state for an explicit runtime checkpoint."""
        return {"schema_version": self.SCHEMA_VERSION, "working_limit": self.working_limit,
                "episodic_limit": self.episodic_limit, "episode_id": self._episode_id,
                "enable_writes": self.enable_writes, "disabled_write_attempts": self.disabled_write_attempts,
                "counter": self._counter, "pending_verification": self._pending_verification,
                "working": [entry.as_dict() for entry in self._working],
                "episodic": [entry.as_dict() for entry in self._episodic],
                "semantic": {key: entry.as_dict() for key, entry in self._semantic.items()},
                "procedural": {key: entry.as_dict() for key, entry in self._procedural.items()},
                "mandatory_rules": {key: entry.as_dict() for key, entry in self._rules.items()}}

    def load_state_dict(self, state: dict[str, Any]) -> None:
        if state.get("schema_version") != self.SCHEMA_VERSION:
            raise ValueError("unsupported CMM checkpoint schema")
        if state.get("working_limit") != self.working_limit or state.get("episodic_limit") != self.episodic_limit:
            raise ValueError("CMM checkpoint retention policy mismatch")
        if bool(state.get("enable_writes", True)) != self.enable_writes:
            raise ValueError("CMM checkpoint write policy mismatch")

        def decode(row: dict[str, Any]) -> MemoryEntryV1:
            required = {"entry_id", "category", "kind", "provenance", "simulation_tick", "payload", "tags", "trust"}
            if set(row) != required:
                raise ValueError("invalid CMM checkpoint entry")
            _reject_private(row["payload"])
            if row["category"] not in self.CATEGORIES or row["kind"] not in self.KINDS:
                raise ValueError("invalid CMM checkpoint provenance")
            return MemoryEntryV1(row["entry_id"], row["category"], row["kind"], row["provenance"],
                                 int(row["simulation_tick"]), deepcopy(row["payload"]),
                                 tuple(row["tags"]), row["trust"])

        working = [decode(row) for row in state.get("working", [])]
        episodic = [decode(row) for row in state.get("episodic", [])]
        semantic = {key: decode(row) for key, row in state.get("semantic", {}).items()}
        procedural = {key: decode(row) for key, row in state.get("procedural", {}).items()}
        rules = {key: decode(row) for key, row in state.get("mandatory_rules", {}).items()}
        if any(entry.category != "mandatory_rule" or entry.trust != "mandatory" for entry in rules.values()):
            raise ValueError("mandatory rule checkpoint failed governance validation")
        self._working.clear(); self._working.extend(working)
        self._episodic.clear(); self._episodic.extend(episodic)
        self._semantic, self._procedural, self._rules = semantic, procedural, rules
        self._episode_id = state.get("episode_id")
        self._counter = int(state.get("counter", 0))
        self.disabled_write_attempts = int(state.get("disabled_write_attempts", 0))
        self._pending_verification = bool(state.get("pending_verification", False))
