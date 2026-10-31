"""E4, valid tool calls with an ablation (plan section 8, P2 section 5): the share of the 2B model's tool calls that
parse and pass their schema, with llama.cpp's constrained tool calls and with free JSON in the text.

In: the real 2B server (incubator-1's model, a free port, under the RAM guard), the persona's system prompt, the
agent's own tool schemas (`Agent.tool_schemas`: the read-only tools, JSON-schema'd from the tools' input models), and
54 owner questions ( that each need a tool, built from the templates below; no farm data is given, so the
model has to ask for it). For each question:
- **constrained:** the tools go in the request's `tools` (llama.cpp `--jinja` tool calling); every returned call is
  checked: a known tool, arguments that parse as JSON, and that pass the tool's input model;
- **free JSON:** no `tools`; the prompt lists the tools and asks for one JSON object `{"tool": ..., "arguments": ...}`
  in the text; the first JSON object in the reply is checked the same way.
Valid share = valid calls / calls made (a reply with no call is counted apart). Target: >= 98 % constrained.
Out: `logs/eval/E4.json`.

Run (loads the 2B model): `.venv\\Scripts\\python tests\\llm\\eval\\e4_tool_calls.py`
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
for _extra in (ROOT / "tools", ROOT / "nodes" / "incubator-1", ROOT / "tests" / "llm", ROOT / "launcher"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from eval_results import EvalResult, git_commit, utc_now, write_result  # noqa: E402
from contracts import load_node_config  # noqa: E402
from llm.agent import LLM_TOOLS  # noqa: E402
from llm.engines import server_spec  # noqa: E402
from llm.jarvis_speaker import load_persona  # noqa: E402
from llm.tools import ConfirmTokens, ToolRegistry  # noqa: E402

TEMPLATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("get_trend", ("Show me how incubator one's temperature changed over the last {n} minutes.",
                   "What was the humidity trend in the incubator during the past {n} minutes?",
                   "Has the incubator's heater output gone up in the last {n} minutes?")),
    ("list_events", ("List the warnings from the last {n} minutes.", "Which alarms were raised in the past {n} minutes?",
                     "Show me every critical event of the last {n} minutes.")),
    ("get_incident", ("Tell me about the current incident on incubator one.", "Is there an open incident right now?",
                      "What is the state of the current incident?")),
    ("search_documents", ("What does the manual say about the heater relay?", "Find the manual's section on turning "
                          "the eggs.", "Look up what the manual says about humidity alarms.")),
    ("ask_peer", ("Ask the HVAC how its supply fan is doing.", "Ask animal protection whether there is smoke.",
                  "Ask incubator two for its temperature.")),
    ("recall_conversation", ("What did we say earlier about the fan?", "Remind me what I asked you about the heater "
                             "yesterday.", "Find our earlier conversation about humidity.")),
)
MINUTES = (10, 30, 60)


def questions() -> list[tuple[str, str]]:
    """Each template three times: with 10, 30 and 60 minutes, or as asked, with "Please," and with "JARVIS,"."""
    found: list[tuple[str, str]] = []
    for tool, texts in TEMPLATES:
        for text in texts:
            if "{n}" in text:
                found += [(tool, text.format(n=n)) for n in MINUTES]
            else:
                found += [(tool, text), (tool, f"Please, {text[0].lower()}{text[1:]}"),
                          (tool, f"JARVIS, {text[0].lower()}{text[1:]}")]
    return found


def check(registry: ToolRegistry, name: str, arguments: Any) -> str | None:
    """None when the call is valid; else why not."""
    if name not in LLM_TOOLS:
        return f"unknown tool {name!r}"
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        registry.tools[name].input_model.model_validate(args)
    except (ValueError, TypeError) as error:
        return f"{type(error).__name__}: {str(error)[:120]}"
    return None


def first_json(text: str) -> Any:
    start = text.find("{")
    while start >= 0:
        depth = 0
        for end in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[end], 0)
            if depth == 0:
                try:
                    return json.loads(text[start:end + 1])
                except ValueError:
                    break
        start = text.find("{", start + 1)
    return None


async def run() -> dict[str, Any]:
    from test_engines_live import start_server, stop_server, wait_for_health  # pyright: ignore[reportMissingImports]
    from live_rig import free_port  # pyright: ignore[reportMissingImports]
    folder = Path(tempfile.mkdtemp())
    ports = folder / "ports.toml"
    ports.write_text(f'schema_version = 1\nhost = "127.0.0.1"\nnats = 1\nmatrix_api = 2\ngateway = 3\nwebsite = 5173\n'
                     f'[llm]\nincubator-1 = {free_port()}\nincubator-2 = 1\nhvac = 2\nanimal-protection = 3\n',
                     encoding="utf-8")
    spec = server_spec("incubator-1", ports)
    process, job, output = start_server(spec)
    registry = ToolRegistry(load_node_config(ROOT / "nodes" / "incubator-1" / "node.toml"), None, None, None,  # type: ignore[arg-type]
                            None, None, ConfirmTokens(b"e4"))  # type: ignore[arg-type]
    schemas = [{"type": "function", "function": {"name": n, "description": f"JARVIS read-only tool {n}",
                                                 "parameters": registry.tools[n].input_model.model_json_schema()}}
               for n in LLM_TOOLS]
    system = load_persona().system_prompt + "\n\nYou have no farm data yet: call a tool to get it."
    listing = "\n".join(f"- {s['function']['name']}: arguments {json.dumps(s['function']['parameters'])}" for s in schemas)
    rows: list[dict[str, Any]] = []
    try:
        client, _ = await wait_for_health(spec, process)
        for expected, question in questions():
            constrained = await client.chat([{"role": "system", "content": system}, {"role": "user", "content": question}],
                                            tools=schemas, max_tokens=160, temperature=0.2, seed=7, deadline_s=60.0)
            calls = [(c.get("function", {}).get("name", ""), c.get("function", {}).get("arguments", ""))
                     for c in constrained.tool_calls]
            free = await client.chat([{"role": "system", "content": system + "\n\nTools:\n" + listing +
                                       "\n\nReply with only one JSON object: {\"tool\": <name>, \"arguments\": {...}}."},
                                      {"role": "user", "content": question}],
                                     max_tokens=160, temperature=0.2, seed=7, deadline_s=60.0)
            parsed = first_json(free.text)
            free_call = (parsed.get("tool", ""), parsed.get("arguments", {})) if isinstance(parsed, dict) else None
            rows.append({"question": question, "expected": expected,
                         "constrained": [{"tool": n, "problem": check(registry, n, a), "chose_expected": n == expected}
                                         for n, a in calls],
                         "free": None if free_call is None else {"tool": free_call[0],
                                                                  "problem": check(registry, *free_call),
                                                                  "chose_expected": free_call[0] == expected},
                         "free_text": free.text[:200] if free_call is None else None})
            print(f"{expected:20} constrained {[(r['tool'], r['problem'] is None) for r in rows[-1]['constrained']]}"
                  f" free {rows[-1]['free'] and (rows[-1]['free']['tool'], rows[-1]['free']['problem'] is None)}",
                  flush=True)
        await client.close()
    finally:
        stop_server(process, job, output)
    return {"rows": rows}


def main() -> int:
    started_utc, started = utc_now(), time.monotonic()
    rows = asyncio.run(run())["rows"]
    constrained = [c for r in rows for c in r["constrained"]]
    free = [r["free"] for r in rows if r["free"] is not None]
    share = (sum(c["problem"] is None for c in constrained) / len(constrained)) if constrained else None
    measured = {
        "questions": len(rows),
        "constrained": {"calls": len(constrained), "valid": sum(c["problem"] is None for c in constrained),
                        "valid_share": round(share, 4) if share is not None else None,
                        "questions_without_a_call": sum(not r["constrained"] for r in rows),
                        "expected_tool_chosen": sum(any(c["chose_expected"] for c in r["constrained"]) for r in rows)},
        "free_json": {"calls": len(free), "valid": sum(f["problem"] is None for f in free),
                      "valid_share": round(sum(f["problem"] is None for f in free) / len(free), 4) if free else None,
                      "questions_without_a_call": sum(r["free"] is None for r in rows),
                      "expected_tool_chosen": sum(bool(f["chose_expected"]) for f in free)},
        "rows": rows}
    path = write_result(EvalResult(
        id="E4", title="Valid tool calls, with an ablation", target=">= 98 % constrained",
        passed=None if share is None else share >= 0.98, measured=json.loads(json.dumps(measured)),
        command=".venv\\Scripts\\python tests\\llm\\eval\\e4_tool_calls.py", commit=git_commit(), seeds=[7],
        models={}, started_utc=started_utc, duration_s=round(time.monotonic() - started, 1),
        notes="The 2B model alone with the persona and the agent's tool schemas; temperature 0.2, seed 7."))
    print(f"E4 constrained {measured['constrained']} free {measured['free_json']} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
