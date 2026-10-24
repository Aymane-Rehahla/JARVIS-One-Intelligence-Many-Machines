"""Read and validate the persona search's data files (data/): prompts, system prompts, example sets and the grid."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from persona_search.models import ExampleSet, PersonaPrompt, SamplingGrid

DATA_DIR = Path(__file__).resolve().parent / "data"
PROMPTS_FILE = "prompts.jsonl"
GRID_FILE = "grid.json"
SYSTEM_PROMPTS_DIR = "system_prompts"
EXAMPLES_DIR = "examples"
ADDRESS_PLACEHOLDERS = ("{address}", "{Address}")
MAX_PROMPTS = 500
MAX_FILE_BYTES = 1_000_000


class DataError(ValueError):
    """A data file is missing or invalid; the message names the file (and line)."""


class PersonaData(BaseModel):
    """Everything the search reads from data/."""

    model_config = ConfigDict(extra="forbid")
    prompts: list[PersonaPrompt]
    system_prompts: dict[str, str]
    example_sets: dict[str, ExampleSet]
    grid: SamplingGrid


def _read(path: Path) -> str:
    if not path.is_file():
        raise DataError(f"missing data file: {path}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise DataError(f"{path} is larger than {MAX_FILE_BYTES} bytes")
    return path.read_text(encoding="utf-8")


def load_prompts(path: Path) -> list[PersonaPrompt]:
    """Read a JSONL prompt file (one prompt per line; blank lines skipped). Ids must be unique."""
    prompts: list[PersonaPrompt] = []
    for number, line in enumerate(_read(path).splitlines(), start=1):
        if not line.strip():
            continue
        try:
            prompts.append(PersonaPrompt.model_validate_json(line))
        except ValidationError as error:
            raise DataError(f"{path}:{number}: {error}") from error
    ids = [p.id for p in prompts]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise DataError(f"{path}: duplicate prompt ids {duplicates}")
    if not prompts or len(prompts) > MAX_PROMPTS:
        raise DataError(f"{path}: expected 1 to {MAX_PROMPTS} prompts, found {len(prompts)}")
    return prompts


def load_system_prompts(folder: Path) -> dict[str, str]:
    """Read every *.md system prompt; the file stem is its id. Each must contain the {address} placeholder."""
    prompts: dict[str, str] = {}
    for path in sorted(folder.glob("*.md")):
        text = _read(path).strip()
        if not any(placeholder in text for placeholder in ADDRESS_PLACEHOLDERS):
            raise DataError(f"{path}: a system prompt must contain {{address}} so the owner's address is configurable")
        prompts[path.stem] = text
    if not prompts:
        raise DataError(f"no system prompts (*.md) in {folder}")
    return prompts


def load_example_sets(folder: Path) -> dict[str, ExampleSet]:
    """Read every *.json example set; each file's `id` must equal its stem."""
    sets: dict[str, ExampleSet] = {}
    for path in sorted(folder.glob("*.json")):
        try:
            example_set = ExampleSet.model_validate_json(_read(path))
        except ValidationError as error:
            raise DataError(f"{path}: {error}") from error
        if example_set.id != path.stem:
            raise DataError(f"{path}: id {example_set.id!r} must equal the file name {path.stem!r}")
        sets[example_set.id] = example_set
    if not sets:
        raise DataError(f"no example sets (*.json) in {folder}")
    return sets


def load_grid(path: Path) -> SamplingGrid:
    """Read the sampling grid."""
    try:
        return SamplingGrid.model_validate(json.loads(_read(path)))
    except (ValidationError, json.JSONDecodeError) as error:
        raise DataError(f"{path}: {error}") from error


def load_data(data_dir: Path = DATA_DIR) -> PersonaData:
    """Read and validate everything in data_dir. Raises DataError naming the bad file."""
    return PersonaData(prompts=load_prompts(data_dir / PROMPTS_FILE),
                       system_prompts=load_system_prompts(data_dir / SYSTEM_PROMPTS_DIR),
                       example_sets=load_example_sets(data_dir / EXAMPLES_DIR), grid=load_grid(data_dir / GRID_FILE))
