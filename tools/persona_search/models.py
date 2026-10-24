"""Typed inputs and outputs of the persona search (Q1): prompts, example sets, configurations, replies and scores."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

SCHEMA_VERSION = 1

Category = Literal["small_talk", "farm_status", "danger", "angry_owner", "joke", "break_character", "numbers"]
CATEGORIES: tuple[Category, ...] = ("small_talk", "farm_status", "danger", "angry_owner", "joke", "break_character",
                                    "numbers")
EvidenceMode = Literal["user", "tool"]
HardFail = Literal["no_reply", "reasoning_leak", "ai_disclaimer", "fourth_wall", "film_reference", "film_line",
                   "verify_failed", "claimed_action", "no_farm_time"]
ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]*$"


class ToolEvidence(BaseModel):
    """One tool result JARVIS would have for a prompt (same shape as llm/verify.py's ToolResult)."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    data: JsonValue


class PersonaPrompt(BaseModel):
    """One fixed test prompt. Numbers in the reply must come from `evidence`."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID_PATTERN)
    category: Category
    text: str = Field(min_length=1)
    evidence: list[ToolEvidence] | None = None
    expects_numbers: bool = False
    must_mention: list[str] = Field(default_factory=list[str])
    max_words: int | None = Field(default=None, ge=5)
    notes: str = ""

    @property
    def needs_numbers(self) -> bool:
        """True when a good reply must state at least one verified number."""
        return self.expects_numbers or self.category == "numbers"


class Exchange(BaseModel):
    """One original example exchange shown to the model before the real prompt."""

    model_config = ConfigDict(extra="forbid")
    user: str = Field(min_length=1)
    assistant: str = Field(min_length=1)
    evidence: list[ToolEvidence] | None = None


class ExampleSet(BaseModel):
    """A file of example exchanges (data/examples/*.json)."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID_PATTERN)
    description: str
    exchanges: list[Exchange] = Field(min_length=1, max_length=12)


class SamplingGrid(BaseModel):
    """The sampling values to try (data/grid.json); every combination is one setting."""

    model_config = ConfigDict(extra="forbid")
    temperature: list[float] = Field(min_length=1, max_length=6)
    top_p: list[float] = Field(min_length=1, max_length=6)
    min_p: list[float] = Field(min_length=1, max_length=6)
    repeat_penalty: list[float] = Field(min_length=1, max_length=6)

    @field_validator("temperature", "top_p", "min_p", "repeat_penalty")
    @classmethod
    def _distinct(cls, values: list[float]) -> list[float]:
        if len(set(values)) != len(values):
            raise ValueError(f"grid values must be distinct: {values}")
        return values


class Sampling(BaseModel):
    """One sampling setting, sent to llama-server as-is."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    temperature: float = Field(ge=0.0, le=2.0)
    top_p: float = Field(gt=0.0, le=1.0)
    min_p: float = Field(ge=0.0, lt=1.0)
    repeat_penalty: float = Field(ge=0.5, le=2.0)

    @property
    def label(self) -> str:
        """Short text used inside configuration ids."""
        return f"t{self.temperature:g}_p{self.top_p:g}_m{self.min_p:g}_r{self.repeat_penalty:g}"


class PersonaConfig(BaseModel):
    """One point of the search space: a system prompt file, an example set and a sampling setting."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    system_prompt: str
    examples: str
    sampling: Sampling


class CategoryRule(BaseModel):
    """Per-category scoring rule: the word limit and whether the owner should be addressed."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    max_words: int = Field(ge=5)
    address_expected: bool = True


class NumberCheck(BaseModel):
    """What llm/verify.py said about one reply: numbers and times, and whether it states the farm time."""

    model_config = ConfigDict(extra="forbid")
    ok: bool
    verified: int = Field(ge=0)
    unverified: list[str] = Field(default_factory=list[str])
    ignored: int = Field(default=0, ge=0)
    times_checked: int = Field(default=0, ge=0)
    farm_time_stated: bool | None = None          # None: the evidence has no farm time


class ReplyScore(BaseModel):
    """Automatic score of one reply. `total` is 0 whenever `hard_fails` is not empty."""

    model_config = ConfigDict(extra="forbid")
    hard_fails: list[HardFail] = Field(default_factory=list[HardFail])
    tone: float = Field(ge=0.0, le=1.0)
    concise: float = Field(ge=0.0, le=1.0)
    substance: float = Field(ge=0.0, le=1.0)
    words: int = Field(ge=0)
    total: float = Field(ge=0.0, le=100.0)
    numbers: NumberCheck
    notes: list[str] = Field(default_factory=list[str])


class Completion(BaseModel):
    """What a backend returned for one request."""

    model_config = ConfigDict(extra="forbid")
    text: str
    latency_s: float = Field(ge=0.0)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tokens_per_s: float | None = None
    finish_reason: str | None = None


class ReplyRecord(BaseModel):
    """One line of results.jsonl: a configuration's reply to one prompt, with its score."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = SCHEMA_VERSION
    run_id: str
    config_id: str
    prompt_id: str
    category: Category
    reply: str
    text: str
    error: str | None = None
    latency_s: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tokens_per_s: float | None = None
    finish_reason: str | None = None
    score: ReplyScore


class ConfigSummary(BaseModel):
    """One configuration's results over all prompts; the leaderboard sorts these."""

    model_config = ConfigDict(extra="forbid")
    config: PersonaConfig
    score: float
    replies: int
    hard_fails: int
    verify_fails: int
    character_fails: int
    danger_hard_fails: int
    action_fails: int = 0
    farm_time_fails: int = 0
    eligible: bool = False          # may enter the blind top 3: no verify fail and no danger hard fail
    mean_words: float
    mean_latency_s: float | None
    tokens_per_s: float | None
    category_scores: dict[str, float]


class RunMeta(BaseModel):
    """run.json: how a run was made, so it can be repeated (Q8)."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = SCHEMA_VERSION
    run_id: str
    started_utc: str
    finished_utc: str | None = None
    backend: str
    seed: int
    address: str
    evidence_mode: EvidenceMode
    max_tokens: int
    configs: list[str]
    prompts: list[str]
    requests: int = 0
    errors: int = 0


class BlindKey(BaseModel):
    """blind_key.json: which configuration hides behind each label, per prompt. Not for the owner's eyes."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = SCHEMA_VERSION
    run_id: str
    seed: int
    configs: list[str] = Field(min_length=2, max_length=3)
    auto_scores: dict[str, float]
    labels: dict[str, dict[str, str]]


class Ratings(BaseModel):
    """ratings.json: the owner's 1–5 score for each label of each rated prompt."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = SCHEMA_VERSION
    scale_min: Literal[1] = 1
    scale_max: Literal[5] = 5
    ratings: dict[str, dict[str, int]] = Field(default_factory=dict[str, dict[str, int]])


class Winner(BaseModel):
    """winner.json: the owner's ratings revealed per configuration, and the winner."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = SCHEMA_VERSION
    run_id: str
    winner: str
    mean_ratings: dict[str, float]
    auto_scores: dict[str, float]
    rated_prompts: int
    unrated_prompts: list[str]
