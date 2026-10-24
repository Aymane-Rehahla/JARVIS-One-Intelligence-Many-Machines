"""Use the nodes' own number check (nodes/incubator-1/llm/verify.py) to score replies: the same rule as production.

The node folders have hyphens in their names, so verify.py is loaded from its path once and wrapped in a typed call.
"""
from __future__ import annotations

import functools
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from persona_search.models import NumberCheck, ToolEvidence

ROOT = Path(__file__).resolve().parents[2]
VERIFY_PATHS = (ROOT / "nodes" / "incubator-1" / "llm" / "verify.py",)
MODULE_NAME = "jarvis_llm_verify"


@functools.cache
def load_verify() -> ModuleType:
    """Load llm/verify.py of incubator-1 (once). Raises FileNotFoundError if no copy exists."""
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]
    path = next((p for p in VERIFY_PATHS if p.is_file()), None)
    if path is None:
        raise FileNotFoundError(f"llm/verify.py not found; looked in: {', '.join(str(p) for p in VERIFY_PATHS)}")
    spec = importlib.util.spec_from_file_location(MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def check_numbers(reply: str, evidence: Sequence[ToolEvidence] | None) -> NumberCheck:
    """Run verify_reply on a reply against the prompt's tool results (none means every number and time fails), and
    states_farm_time for the farm-time criterion."""
    verify = load_verify()
    tools = [item.model_dump(mode="json") for item in evidence or ()]
    text = reply[:verify.MAX_REPLY_CHARS]
    report = verify.verify_reply(text, tools)
    unverified = [str(number.text) for number in report.unmatched] + [f"time {t.text}" for t in report.unmatched_times]
    return NumberCheck(ok=bool(report.ok), verified=len(report.matched), unverified=unverified,
                       ignored=len(report.ignored), times_checked=int(report.times_checked),
                       farm_time_stated=verify.states_farm_time(text, tools))
