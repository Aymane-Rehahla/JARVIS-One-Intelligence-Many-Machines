"""E1 (grounded numbers) and E2 (correct citations) on the golden conversations (plan section 8, A13).

In: `tests/llm/golden/golden.json` and the live rig (live_rig.py: the real models, the four nodes, the trained farm
with the supply-fan fault, every node's manual approved by a fixture, the gateway). Every turn goes through
`/ws/chat`. A recorder wraps the agent's own `verify_reply` call in its sentence gate (nothing else changes), so it
sees every sentence the model wrote, before the gate decides, and this turn's evidence.
- **E1 before:** of all numbers and times in the model's sentences (stopped ones and regenerations included), the
  share that matched a tool result of the turn. **E1 after:** the same share in the replies as sent: every done
  text, whatever its outcome (fallbacks and templates included), verified again against its turn's evidence (the
  rig keeps each finished turn's evidence). Target: after = 100 %.
- **E2:** for each citation, does the cited passage contain the reply's claim? Judged automatically and lexically
  (stated plainly in the result): a citation is supported when its passage shares at least SUPPORT_WORDS content
  words (4+ letters, not stopwords, not machine names) with the reply. Target >= 95 %.
- Also per conversation: the must / must-not terms, and that each reply states its farm time.
Out: `logs/eval/E1.json` and `logs/eval/E2.json`.

Run (loads the models): `.venv\\Scripts\\python tests\\llm\\eval\\e1_e2_golden.py`
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_rig import LiveRig, rig_models  # noqa: E402

from eval_results import EvalResult, git_commit, utc_now, write_result  # noqa: E402
from llm import agent as agent_module  # noqa: E402
from llm.verify import verify_reply  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "tests" / "llm" / "golden" / "golden.json"
SUPPORT_WORDS = 3
STOP = set("this that with from have what when where which about there their they them then than into your sir "
           "farm time incubator incubators hvac animal protection machine machines manual says should would could "
           "will been were also only more most some such very just".split())


def sentences(text: str) -> list[str]:
    return [piece for piece in re.split(r"(?<=[.!?])\s+|\n+", text) if piece.strip()]


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOP}


def passages(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    stack: list[Any] = [item.get("data") for item in evidence]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if isinstance(item.get("results"), list):
                found += [r for r in item["results"] if isinstance(r, dict) and "text" in r]
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


async def run() -> dict[str, Any]:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["conversations"]
    seen: list[dict[str, Any]] = []
    original = agent_module.verify_reply

    def recording(text: str, evidence: Any, *args: Any, **kwargs: Any) -> Any:
        report = original(text, evidence, *args, **kwargs)
        seen.append({"sentence": text, "total": report.checked + report.times_checked,
                     "unmatched": len(report.unmatched) + len(report.unmatched_times), "evidence": evidence})
        return report
    agent_module.verify_reply = recording  # type: ignore[assignment]
    rows: list[dict[str, Any]] = []
    try:
        async with LiveRig() as rig:
            for conversation in golden:
                conversation_id = None
                for turn in conversation["turns"]:
                    seen.clear()
                    answer, seconds = await rig.ask(turn, conversation_id)
                    done = answer.done or {}
                    conversation_id = done.get("conversation_id") or conversation_id
                    text = str(done.get("text", ""))
                    # every reply, whatever its outcome (a fallback's template included), against its turn's data
                    evidence = rig.evidence.get(str(done.get("request_id")), seen[-1]["evidence"] if seen else [])
                    after = verify_reply(text, evidence) if text else None
                    cited = []
                    for citation in done.get("citations") or []:
                        texts = [p["text"] for p in passages(evidence) if p.get("file") == citation["file"]
                                 and p.get("section") == citation["section"]]
                        # stricter than the agent's own rule (the whole passage): one sentence of the passage must
                        # share SUPPORT_WORDS words with one sentence of the reply (final review, 31 Oct)
                        shared = max((sorted(words(said) & words(line)) for said in sentences(text)
                                      for passage in texts for line in sentences(passage)), key=len, default=[])
                        cited.append({"file": citation["file"], "section": citation["section"],
                                      "found": bool(texts), "shared": shared[:12],
                                      "supported": len(shared) >= SUPPORT_WORDS})
                    lowered = text.lower()
                    rows.append({
                        "id": conversation["id"], "question": turn, "outcome": done.get("outcome"),
                        "reason": done.get("reason"), "text": text[:700], "seconds": round(seconds, 1),
                        "before_total": sum(s["total"] for s in seen),
                        "before_unmatched": sum(s["unmatched"] for s in seen),
                        "sentences_seen": len(seen),
                        "after_total": (after.checked + after.times_checked) if after else 0,
                        "after_unmatched": (len(after.unmatched) + len(after.unmatched_times)) if after else 0,
                        "citations": cited,
                        "must_missing": [m for m in conversation["must"] if m.lower() not in lowered],
                        "must_not_found": [m for m in conversation["must_not"] if m.lower() in lowered],
                        "states_farm_time": "farm time" in lowered or done.get("outcome") in ("refused", "error")})
                    print(f"{conversation['id']} {done.get('outcome')} before {rows[-1]['before_unmatched']}/"
                          f"{rows[-1]['before_total']} after {rows[-1]['after_unmatched']}/{rows[-1]['after_total']} "
                          f"cites {len(cited)} | {text[:110]!r}", flush=True)
    finally:
        agent_module.verify_reply = original  # type: ignore[assignment]
    return {"rows": rows}


def main() -> int:
    started_utc, started = utc_now(), time.monotonic()
    rows = asyncio.run(run())["rows"]
    before_total = sum(r["before_total"] for r in rows)
    before_ok = before_total - sum(r["before_unmatched"] for r in rows)
    after_total = sum(r["after_total"] for r in rows)
    after_ok = after_total - sum(r["after_unmatched"] for r in rows)
    citations = [c for r in rows for c in r["citations"]]
    supported = sum(c["supported"] for c in citations)
    common: dict[str, Any] = dict(commit=git_commit(), seeds=[7], models=rig_models(), started_utc=started_utc,
                  duration_s=round(time.monotonic() - started, 1),
                  command=".venv\\Scripts\\python tests\\llm\\eval\\e1_e2_golden.py")
    after_share = after_ok / after_total if after_total else 1.0
    write_result(EvalResult(
        id="E1", title="Grounded numbers", target="100 % after verify (the before figure shows why verify exists)",
        passed=after_share == 1.0,
        measured={"before_share": round(before_ok / before_total, 4) if before_total else None,
                  "before_numbers": before_total, "after_share": round(after_share, 4), "after_numbers": after_total,
                  "replies": len(rows), "answered": sum(r["outcome"] == "answered" for r in rows),
                  "states_farm_time": sum(r["states_farm_time"] for r in rows),
                  "must_missing": {r["id"]: r["must_missing"] for r in rows if r["must_missing"]},
                  "must_not_found": {r["id"]: r["must_not_found"] for r in rows if r["must_not_found"]},
                  "rows": rows}, **common,
        notes="12 golden conversations (13 turns) on the trained farm with the supply-fan fault. 'Before' counts every "
              "sentence the model offered to the gate, regenerations included."))
    share = supported / len(citations) if citations else None
    write_result(EvalResult(
        id="E2", title="Correct citations", target=">= 95 %", passed=None if share is None else share >= 0.95,
        measured={"citations": len(citations), "supported": supported,
                  "share": round(share, 4) if share is not None else None, "support_words": SUPPORT_WORDS,
                  "rows": [{"id": r["id"], "citations": r["citations"]} for r in rows]}, **common,
        notes="Judged automatically and lexically, not by a person: a citation is supported when one sentence of "
              f"its passage shares at least {SUPPORT_WORDS} content words with one sentence of the reply. The agent "
              "itself cites a passage only when the whole passage shares 3 distinctive words with the reply, so this "
              "judge is stricter than the agent's rule but of the same kind; the number of citations is small."))
    print(f"E1 before {before_ok}/{before_total} after {after_ok}/{after_total}; E2 {supported}/{len(citations)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
