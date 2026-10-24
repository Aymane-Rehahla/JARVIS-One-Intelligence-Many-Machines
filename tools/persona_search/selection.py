"""Seeded subsets that fit a time budget: configurations spread over every prompt/example pair, prompts over categories."""
from __future__ import annotations

import itertools
import random
from collections.abc import Callable, Sequence
from typing import TypeVar

from persona_search.models import PersonaConfig, PersonaPrompt, SamplingGrid, Sampling

T = TypeVar("T")


def round_robin(items: Sequence[T], group: Callable[[T], str], limit: int, rng: random.Random) -> list[T]:
    """Take `limit` items, one group at a time in a shuffled order, so every group is represented before repeats."""
    groups: dict[str, list[T]] = {}
    for item in items:
        groups.setdefault(group(item), []).append(item)
    keys = sorted(groups)
    rng.shuffle(keys)
    for key in keys:
        rng.shuffle(groups[key])
    chosen: list[T] = []
    for index in range(max((len(v) for v in groups.values()), default=0)):
        for key in keys:
            if len(chosen) >= limit:
                return chosen
            if index < len(groups[key]):
                chosen.append(groups[key][index])
    return chosen


def build_configs(system_prompts: Sequence[str], example_sets: Sequence[str], grid: SamplingGrid) -> list[PersonaConfig]:
    """Every combination of system prompt, example set and sampling setting, sorted by id."""
    settings = [Sampling(temperature=t, top_p=p, min_p=m, repeat_penalty=r)
                for t, p, m, r in itertools.product(grid.temperature, grid.top_p, grid.min_p, grid.repeat_penalty)]
    configs = [PersonaConfig(id=f"{s}+{e}+{sampling.label}", system_prompt=s, examples=e, sampling=sampling)
               for s in sorted(system_prompts) for e in sorted(example_sets) for sampling in settings]
    return sorted(configs, key=lambda c: c.id)


def select_configs(configs: Sequence[PersonaConfig], limit: int | None, seed: int) -> list[PersonaConfig]:
    """All configurations, or a seeded subset of `limit` spread over every (system prompt, example set) pair."""
    if limit is None or limit >= len(configs):
        return list(configs)
    if limit < 1:
        raise ValueError(f"--limit must be at least 1, got {limit}")
    chosen = round_robin(configs, lambda c: f"{c.system_prompt}+{c.examples}", limit, random.Random(f"{seed}:configs"))
    return sorted(chosen, key=lambda c: c.id)


def select_prompts(prompts: Sequence[PersonaPrompt], limit: int | None, seed: int,
                   salt: str = "prompts") -> list[PersonaPrompt]:
    """All prompts, or a seeded subset of `limit` spread over the categories, kept in file order."""
    if limit is None or limit >= len(prompts):
        return list(prompts)
    if limit < 1:
        raise ValueError(f"the prompt limit must be at least 1, got {limit}")
    chosen = {p.id for p in round_robin(prompts, lambda p: p.category, limit, random.Random(f"{seed}:{salt}"))}
    return [p for p in prompts if p.id in chosen]
